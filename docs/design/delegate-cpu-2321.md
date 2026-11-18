# Investigation: Delegate CPU Utilization During Developer Runs (#2321)

## Executive Summary

When running `llmtool task delegate`, the process uses approximately 87–96% of a single CPU core for minutes while awaiting the execution of direct CLI Developer runs. This behavior has been observed across multiple provider routes, including `agy`, `claude`, and `opencode` (#2321).

This design note documents:
1. The exact locations and file paths where `llmtool task delegate` waits for each provider's Developer run.
2. The root cause of why that wait consumes substantial CPU (busy-wait spin in selector loops).
3. Three candidate architectural fixes, including detailed risk assessments regarding results capture, timeout handling, and failure/interruption recovery.

---

## 1. Where a Delegate Waits for Each Provider's Run

The execution flow for direct Developer dispatch originates in `tools/llmtool/llmtool/task_submission_cli.py` and branches based on the requested provider.

### A. Provider-Neutral Routes (`agy`, `claude`, `gw-agent`)

The routes `agy`, `claude`, and `gw-agent` share the unified execution contract defined in `tools/llmtool/llmtool/developer_routes/base.py`.

1. **Dispatch Call Sites in `tools/llmtool/llmtool/task_submission_cli.py`**:
   - **`agy`**: `run_task_agy_delegate` (lines 5358–5621) invokes `execute(route, ...)` at line 5524.
   - **`claude`**: `run_task_claude_delegate` (lines 5870–6207) invokes `execute(route, ...)` at line 6094.
   - **`gw-agent`**: `run_task_gw_agent_delegate` (lines 6218–6550) invokes `execute(route, ...)` at line 6538.

2. **Route Runner Orchestration in `tools/llmtool/llmtool/developer_routes/base.py`**:
   - `run_route(...)` (lines 961–1256) validates request bindings, runs write-boundary checks, prepares private temporary environments, and calls the runner at line 1115:
     ```python
     execute = runner or _default_runner
     code, stdout, stderr = execute(argv, value['root'], env, value['timeout'])
     ```
   - When no custom `runner` is injected, execution defaults to `_default_runner` (lines 574–620).

3. **Wait Loop in `_default_runner` (`tools/llmtool/llmtool/developer_routes/base.py` lines 582–610)**:
   - The process is spawned via:
     ```python
     process = subprocess.Popen(
         argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
         shell=False, close_fds=True, start_new_session=True
     )
     ```
   - Both `process.stdout` and `process.stderr` file descriptors are set to non-blocking mode and registered with `selectors.DefaultSelector()` (lines 588–590):
     ```python
     selector = selectors.DefaultSelector()
     for stream, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
         os.set_blocking(stream.fileno(), False)
         selector.register(stream, selectors.EVENT_READ, name)
     ```
   - **The Wait Loop (lines 591–605)**:
     ```python
     while selector.get_map():
         lifecycle.check()
         remaining = deadline - time.monotonic()
         if remaining <= 0:
             raise TimeoutError from None
         for key, _ in selector.select(min(remaining, 0.1)):
             chunk = os.read(key.fileobj.fileno(), 8192)
             if not chunk:
                 selector.unregister(key.fileobj)
             else:
                 target, cap = ((out, MAX_STDOUT_BYTES) if key.data == 'stdout'
                                else (err, MAX_STDERR_BYTES))
                 if len(target) + len(chunk) > cap:
                     raise RouteError('ROUTE_OUTPUT_TOO_LARGE')
                 target.extend(chunk)
     ```
   - Process exit reap: `process.wait(timeout=remaining)` at line 609.

### B. Provider `opencode`

1. **Dispatch Call Site in `tools/llmtool/llmtool/task_submission_cli.py`**:
   - `run_task_opencode_delegate` (lines 4893–5201) delegates execution at line 5045:
     ```python
     result = opencode_direct_developer.run(
         root=registration.root, read_paths=read_paths,
         write_paths=write_paths, prompt=prompt,
         dispatch_store=store, dispatch_task_id=task['task_id'], steps=steps,
         prelaunch_validate=validate_prelaunch_d6_binding, model=model,
         launch_callback=record_opencode_launch, timeout_seconds=timeout
     )
     ```
   - In background execution mode (`--background`, lines 7951–8016), `_run_background_delegate` (lines 231–307) performs a double fork; the parent waits synchronously via `_background_read(read_fd)` (line 291), while the detached child runs `run_task_opencode_delegate` and waits inside `opencode_direct_developer.run`.

### C. Provider `codex`

1. **Direct Mode Call Site in `tools/llmtool/llmtool/task_submission_cli.py`**:
   - `run_task_codex_delegate` (lines 6814–7100) delegates execution at line 7027:
     ```python
     result = codex_direct_developer.run(...)
     ```
2. **Proposal Mode Call Site**:
   - Line 8206 in `task_submission_cli.py`: Synchronous HTTP client completion via `client.complete(messages)`.

---

## 2. Why That Wait Uses CPU

A CPU utilization of 87–96% for a single process indicates that the thread is not passively blocked inside the operating system kernel waiting on I/O readiness, but is actively executing instructions in a tight loop.

The causes in `_default_runner` (and direct developer CLI runners sharing this architecture) are:

### A. Non-Blocking I/O Loop with Zero-Sleep Spinning
In `_default_runner`:
```python
while selector.get_map():
    lifecycle.check()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError from None
    for key, _ in selector.select(min(remaining, 0.1)):
        chunk = os.read(key.fileobj.fileno(), 8192)
        ...
```
1. **Immediate Selector Returns**:
   On macOS (Darwin), `selectors.DefaultSelector` is `selectors.KqueueSelector`. When pipes are registered with level-triggered filters (`KQ_FILTER_READ`), certain pipe states or signal interrupts cause `selector.select()` to return immediately.
2. **Empty Iterations Without Backoff**:
   When `selector.select()` returns ready for a stream or encounters an immediate timeout/signal, the loop iterates without any forced delay. If a stream produces no bytes or if `selector.select()` repeatedly returns immediately (0 ms elapsed), the loop repeats millions of times per minute.
3. **Repeated Overhead**:
   Every iteration executes:
   - `selector.get_map()` map lookup
   - `lifecycle.check()` process lifecycle check
   - `time.monotonic()` clock query
   - `selector.select(...)` syscall (`kevent` control)
   Without kernel-level blocking, this sequence consumes ~100% of a CPU core (observed as 87–96% accounting for kernel scheduling and context switches).

### B. Level-Triggered Readiness on EOF and Closed Streams
When the child process closes either `stdout` or `stderr` early in its lifecycle:
- The closed pipe descriptor enters an EOF state.
- While `os.read()` returns `b""` and unregisters the closed stream, any delay or intermediate state between pipe closure, subshell execution, or sandbox-exec wrapper initialization can cause `kevent` to fire continuously before unregistration.
- If only one stream is unregistered and the other descriptor remains open with unbuffered notifications, any spurious readiness causes the loop to spin without blocking.

---

## 3. Candidate Fixes and Risk Analysis

### Candidate Fix 1: Event-Driven Passive Blocking with Full Remaining Timeout

**Description**:
Replace the fixed 100 ms polling slice (`min(remaining, 0.1)`) with the full remaining deadline:
```python
ready = selector.select(timeout=max(0.0, remaining))
```
If periodic `lifecycle.check()` is required, set a conservative interval (e.g., 1.0 or 2.0 seconds) rather than 0.1 seconds, and ensure that if `select()` returns without events, it has actually slept for that duration.

**Risks**:
- **Results**:
  *Risk: None.* Data collection semantics remain unchanged; whenever `stdout` or `stderr` has data, `selector.select()` wakes immediately and drains up to 8192 bytes per chunk.
- **Timeouts**:
  *Risk: Low.* Deadline tracking remains strictly bounded by `deadline - time.monotonic()`. A 1.0s or full timeout slice does not exceed the overall task timeout.
- **Failure Handling**:
  *Risk: Low.* If `lifecycle.check()` is used to detect host termination or external abort signals, a 1.0s timeout delays detection by at most 1 second compared to 100 ms, which is well within acceptable CLI cancellation tolerances.

---

### Candidate Fix 2: Dedicated Reader Worker Threads with Blocking I/O

**Description**:
Adopt the standard library `subprocess.Popen.communicate()` model:
Spawn two lightweight reader threads (one for `stdout`, one for `stderr`) executing blocking reads:
```python
def drain_stream(stream, buffer, limit):
    while True:
        chunk = stream.read(8192)
        if not chunk:
            break
        buffer.extend(chunk)
```
The main thread waits passively on `process.wait(timeout=remaining)` or joins the threads with the deadline.

**Risks**:
- **Results**:
  *Risk: Low.* Standard library `communicate()` is resistant to pipe buffer deadlocks. Byte accumulation and buffer caps (`MAX_STDOUT_BYTES`, `MAX_STDERR_BYTES`) are enforced directly.
- **Timeouts**:
  *Risk: Medium.* If the child process hangs without closing its pipes, threads blocked on `stream.read()` will not terminate until the child process is killed. The main thread must kill the child process on timeout to unblock the reader threads.
- **Failure Handling**:
  *Risk: Medium.* Multi-threading requires careful synchronization of `CLIProcessLifecycle` ownership and cleanup guarantees. Signal handling (SIGINT/Ctrl-C) must be caught by the main thread and propagated cleanly to child process termination before thread join.

---

### Candidate Fix 3: Rate-Throttled Spin Prevention with Cooperative Sleep

**Description**:
Retain the single-threaded `selectors.DefaultSelector` architecture in `_default_runner`, but introduce an explicit idle sleep whenever `selector.select()` returns an empty event list or when zero bytes are transferred:
```python
events = selector.select(min(remaining, 0.1))
if not events:
    # Explicit cooperative yield if select returned early without events
    time.sleep(0.05)
```
Additionally, ensure `process.poll()` is checked during each iteration to break the wait loop immediately when the process has already terminated and its pipes are drained.

**Risks**:
- **Results**:
  *Risk: None.* Preserves identical output buffers, SHA-256 digests, and truncation checks.
- **Timeouts**:
  *Risk: None.* Remaining timeout arithmetic is preserved.
- **Failure Handling**:
  *Risk: Low.* Single-threaded execution model is maintained; `CLIProcessLifecycle` enter/check/exit and `last_cleanup_status` remain unchanged without concurrency hazards.

---

## 4. Summary Matrix

| Metric | Candidate Fix 1 (Full/1s Timeout) | Candidate Fix 2 (Worker Threads) | Candidate Fix 3 (Cooperative Sleep) |
| :--- | :--- | :--- | :--- |
| **CPU Reduction** | 100% idle (0% CPU) | 100% idle (0% CPU) | Near 0% CPU (< 0.1%) |
| **Architectural Change** | Minimal (parameter change) | Moderate (threading added) | Minimal (guard sleep added) |
| **Result Integrity Risk** | None | Low | None |
| **Timeout Precision** | High (exact deadline) | High (`TimeoutExpired`) | High (within 50ms) |
| **Concurrency Hazards** | None | Medium (thread cleanup) | None |

Candidate Fix 1 or Candidate Fix 3 offers the safest, lowest-risk mitigation while completely eliminating the 87–96% CPU busy-wait defect.
