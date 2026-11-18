# Investigation: Delegate CPU Utilization During Developer Runs (#2321)

## Executive Summary

When running `llmtool task delegate`, the process is observed to use substantial CPU (reported in #2321 as "most of a CPU core for minutes" [UNVERIFIED: informal observation; precise percentage unmeasured by baseline instrumentation]) while awaiting direct CLI Developer executions. This behavior has been reported across multiple provider routes, including `agy`, `claude`, and `opencode`.

This technical design note documents:
1. The exact locations (with file and line references) where `llmtool task delegate` waits for each provider's Developer run, including a dedicated analysis of how `opencode` differs from the provider-neutral routes.
2. The root cause analysis, strictly separating confirmed source code facts from hypotheses, and enumerating the empirical diagnostic evidence required to prove the CPU consumption mechanism.
3. Candidate architectural fixes, analyzing risks to result integrity, timeout accuracy, cancellation responsiveness (assessing cancellation-delay risk across timeout variants), and process group ownership/descendant cleanup under `CLIProcessLifecycle`.

---

## 1. Where a Delegate Waits for Each Provider's Run

The execution flow for direct Developer dispatch originates in `tools/llmtool/llmtool/task_submission_cli.py` and branches based on the requested provider.

### A. Provider-Neutral Routes (`agy`, `claude`, `gw-agent`)

The routes `agy`, `claude`, and `gw-agent` share the unified execution contract implemented in `tools/llmtool/llmtool/developer_routes/base.py`.

1. **Dispatch Call Sites in `tools/llmtool/llmtool/task_submission_cli.py`**:
   - **`agy`**: `run_task_agy_delegate` (lines 5359–5621) invokes `execute(route, ...)` at line 5525.
   - **`claude`**: `run_task_claude_delegate` (lines 5870–6220) invokes `execute(route, ...)` at line 6202.
   - **`gw-agent`**: `run_task_gw_agent_delegate` (lines 6218–6670) invokes `execute(route, ...)` at line 6649.

2. **Route Runner Orchestration in `tools/llmtool/llmtool/developer_routes/base.py`**:
   - `run_route(...)` (lines 961–1256) prepares private temporary environments, verifies write boundaries, and invokes the runner at line 1115:
     ```python
     execute = runner or _default_runner
     code, stdout, stderr = execute(argv, value['root'], env, value['timeout'])
     ```
   - When no custom runner is injected, execution uses `_default_runner` (lines 574–620).

3. **Wait Loop in `_default_runner` (`tools/llmtool/llmtool/developer_routes/base.py` lines 582–610)**:
   - Spawns the CLI child process with `stdin=subprocess.DEVNULL`:
     ```python
     process = subprocess.Popen(
         argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
         shell=False, close_fds=True, start_new_session=True
     )
     lifecycle.own(process)
     ```
   - Sets `stdout` and `stderr` to non-blocking mode and registers both with `selectors.DefaultSelector()` (lines 588–590):
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
   - **Process Exit Reap (lines 606–609)**:
     After all registered streams reach EOF and are unregistered from the selector, `_default_runner` reaps the child leader process directly via:
     ```python
     remaining = deadline - time.monotonic()
     if remaining <= 0:
         raise TimeoutError from None
     process.wait(timeout=remaining)
     ```

---

### B. Provider `opencode`

The `opencode` route implements a dedicated direct CLI adapter in `tools/llmtool/llmtool/opencode_direct_developer.py` rather than using `_default_runner`.

1. **Dispatch Call Site in `tools/llmtool/llmtool/task_submission_cli.py`**:
   - `run_task_opencode_delegate` (lines 4893–5201) delegates execution at line 5046:
     ```python
     result = opencode_direct_developer.run(
         root=registration.root, read_paths=read_paths,
         write_paths=write_paths, prompt=prompt,
         dispatch_store=store, dispatch_task_id=task['task_id'], steps=steps,
         prelaunch_validate=validate_prelaunch_d6_binding, model=model,
         launch_callback=record_opencode_launch, timeout_seconds=timeout
     )
     ```
   - In background execution mode (`--background`, lines 7951–8016), `_run_background_delegate` (lines 231–307) performs a double fork; the parent process waits synchronously via `_background_read(read_fd)` (line 291), while the detached child executes `run_task_opencode_delegate` and enters `opencode_direct_developer.run`.

2. **Wait Loop in `opencode_direct_developer.py` (lines 850–1050)**:
   - Spawns the CLI child process with `stdin=subprocess.PIPE` (lines 939–942):
     ```python
     process = subprocess.Popen(
         argv, cwd=str(root), env=env,
         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
         stderr=subprocess.PIPE, bufsize=0, shell=False,
         close_fds=True, start_new_session=True
     )
     lifecycle.own(process)
     ```
   - **Stream Registration (lines 954–960)**:
     Unlike `_default_runner`, `opencode_direct_developer.py` registers `stdin` in addition to `stdout` and `stderr`:
     ```python
     selector = selectors.DefaultSelector()
     for stream, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
         os.set_blocking(stream.fileno(), False)
         selector.register(stream, selectors.EVENT_READ, name)
     os.set_blocking(process.stdin.fileno(), False)
     selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
     ```
   - **The Wait Loop (lines 964–1020)**:
     ```python
     while selector.get_map():
         lifecycle.check()
         remaining = work_deadline - time.monotonic()
         if remaining <= 0:
             _reject('OPENCODE_TIMEOUT')
         for key, _ in selector.select(min(remaining, 0.1)):
             if key.data == 'stdin':
                 try:
                     written = os.write(key.fileobj.fileno(), payload[offset:offset + 8192])
                 except (BlockingIOError, InterruptedError):
                     continue
                 except OSError:
                     _reject('OPENCODE_PROMPT_DELIVERY_INCOMPLETE')
                 if type(written) is not int or written < 0:
                     _reject('OPENCODE_PROMPT_DELIVERY_INCOMPLETE')
                 offset += written
                 if offset >= len(payload):
                     selector.unregister(key.fileobj)
                     try:
                         key.fileobj.close()
                     except OSError:
                         _reject('OPENCODE_PROMPT_DELIVERY_INCOMPLETE')
                     delivered = True
                     _fire_delivered(delivered_callback)
                 continue
             try:
                 chunk = os.read(key.fileobj.fileno(), 8192)
             except (BlockingIOError, InterruptedError):
                 continue
             if not chunk:
                 selector.unregister(key.fileobj)
                 continue
             destination = stdout if key.data == 'stdout' else stderr
             cap = MAX_OUTPUT_BYTES if key.data == 'stdout' else MAX_STDERR_BYTES
             chunk_start = len(stdout) if key.data == 'stdout' else 0
             if len(destination) + len(chunk) > cap:
                 _reject('OPENCODE_OUTPUT_TOO_LARGE' if key.data == 'stdout'
                         else 'OPENCODE_STDERR_TOO_LARGE')
             destination.extend(chunk)
             if key.data == 'stdout':
                 scan_from = 0
                 while True:
                     end = chunk.find(b'\n', scan_from)
                     if end < 0:
                         break
                     line_end = chunk_start + end + 1
                     complete = bytes(stdout[denial_line_start:line_end])
                     denial_line_start = line_end
                     scan_from = end + 1
                     if _has_structured_tool_denial(complete):
                         _reject('TOOL_DENIED')
     ```
   - **Post-Selector Exit Wait Without Reap (lines 1021–1023)**:
     ```python
     if not delivered:
         _reject('OPENCODE_PROMPT_DELIVERY_INCOMPLETE')
     _await_exit_without_reap(process, deadline=work_deadline)
     ```
     where `_await_exit_without_reap` (lines 626–634) calls `_observe_exit_without_reap(process, remaining, deadline)`:
     ```python
     def _await_exit_without_reap(process, *, deadline):
         remaining = max(0, deadline - time.monotonic())
         try:
             exited = _observe_exit_without_reap(process, remaining, deadline)
         except Exception:
             _reject('OPENCODE_CLEANUP_UNKNOWN')
         if not exited:
             _reject('OPENCODE_TIMEOUT')
     ```

---

### C. Structural Differences: `opencode` vs Provider-Neutral Routes (`agy`, `claude`, `gw-agent`)

The wait implementation in `opencode_direct_developer.py` differs from `_default_runner` in four critical architectural aspects:

| Dimension | Provider-Neutral Routes (`_default_runner`) | OpenCode Developer (`opencode_direct_developer.run`) |
| :--- | :--- | :--- |
| **`stdin` Lifecycle & Registration** | `stdin=subprocess.DEVNULL`. Stdin is never registered in the selector; prompt is supplied via CLI arguments or files. | `stdin=subprocess.PIPE`. `stdin` is registered in `selector` with `EVENT_WRITE` until the prompt is fully written, then closed and unregistered. |
| **In-Loop Stream Processing** | Raw chunk accumulation into bytearrays with cap enforcement (`MAX_STDOUT_BYTES` = 1MB, `MAX_STDERR_BYTES` = 64KB). | Raw chunk accumulation with higher caps (`MAX_OUTPUT_BYTES` = 16MB) **plus** incremental line-by-line scanning of `stdout` for `_has_structured_tool_denial` (`TOOL_DENIED`). |
| **Post-Stream Exit Waiting** | Calls `process.wait(timeout=remaining)` (base.py line 609), directly reaping the child process leader. | Calls `_await_exit_without_reap` (opencode_direct_developer.py line 1023), observing exit via non-reaping wait checks (`_observe_exit_without_reap`) so PID/PGID tracking remains valid for `CLIProcessLifecycle`. |
| **Error & Exception Types** | Raises `RouteError` (`ROUTE_OUTPUT_TOO_LARGE`, `TIMEOUT`, `RUNNER_FAILED`). | Raises `DirectOpenCodeError` (`OPENCODE_TIMEOUT`, `OPENCODE_OUTPUT_TOO_LARGE`, `TOOL_DENIED`, `OPENCODE_PROMPT_DELIVERY_INCOMPLETE`). |

---

## 2. Why That Wait Uses CPU

### A. Confirmed Source Facts

The following facts are established directly by inspection of the source code:

1. **Fixed 100 ms Polling Slices**:
   Both `_default_runner` (base.py line 596) and `opencode_direct_developer.run` (opencode_direct_developer.py line 969) invoke `selector.select(min(remaining, 0.1))`. The timeout argument passed to the kernel polling mechanism is capped at 0.1 seconds.
2. **Loop Iteration Work**:
   Every iteration of the `while selector.get_map():` loop executes:
   - `lifecycle.check()`: reads signal state (`self.signum is not None`).
   - `time.monotonic()` clock calculation.
   - Selector map verification (`selector.get_map()`).
   - The OS selector system call (`kevent` on macOS/Darwin via `KqueueSelector`, `epoll_pwait` on Linux via `EpollSelector`).
3. **Idle Loop Math**:
   If `selector.select(0.1)` passively blocks in the kernel for the full 100 ms when no I/O is ready, the loop runs at a maximum frequency of 10 iterations per second. Executing 10 iterations per second of Python control code uses negligible CPU (< 0.1% of a single core).
4. **Necessary Condition for High CPU**:
   High CPU utilization within `llmtool task delegate` can only occur if `selector.select()` is returning immediately (or after a near-zero interval) rather than blocking for 100 ms, or if a high volume of events continuously triggers chunk reads and loop processing.
5. **OpenCode Stdin Writable State**:
   In `opencode_direct_developer.py` (lines 959–960), `stdin` is registered with `selectors.EVENT_WRITE`. Under POSIX semantics, an OS pipe buffer with available space is continuously ready for writing. As long as `stdin` remains registered, `selector.select()` will report `stdin` ready immediately with 0 ms sleep.

---

### B. Hypotheses for Elevated CPU Utilization

The following mechanisms are plausible explanations for elevated CPU consumption, but remain hypotheses pending empirical profiling evidence:

- **Hypothesis 1: Continuous Writable Readiness on `stdin` in OpenCode**:
  During the prompt transmission phase, `stdin` is registered for `EVENT_WRITE`. If the pipe buffer does not fill up immediately, `selector.select()` returns without delay on every call. If prompt delivery spans multiple iterations or encounters partial writes, the selector spins in a 0 ms loop until delivery completes and `stdin` is unregistered.
- **Hypothesis 2: Spurious Readiness or Level-Triggered Spinning on Pipe EOF**:
  On macOS (`selectors.KqueueSelector`), pipes monitored with `KQ_FILTER_READ` enter a ready state when the peer closes the write end or reaches EOF. If there is any lag, unhandled state, or race condition between a stream reporting EOF and being unregistered via `selector.unregister(key.fileobj)`, or if one stream remains open while the other spins on EOF, `select()` returns immediately on every call.
- **Hypothesis 3: High-Frequency Sub-Process Stream Chunks**:
  If the child developer process (`agy`, `claude`, or `opencode`) writes small chunks of output, ANSI progress indicators, or unbuffered debug logs to `stdout`/`stderr`, `selector.select()` wakes upon every chunk. In OpenCode, every newline in `stdout` triggers string slicing and `_has_structured_tool_denial` regex evaluation (lines 1009–1020), amplifying user-space CPU usage.
- **Hypothesis 4: Short Remaining Time or Clock Precision Artifacts**:
  If `remaining` approaches zero or clock queries produce negative or zero values, `min(remaining, 0.1)` collapses to `0`, converting the selector into a non-blocking poll.
- **Hypothesis 5: Process Attribution Misidentification**:
  Informal reports of high CPU during delegate runs may reflect CPU consumption by child processes (e.g. Node/Python/Go provider processes) or sibling helper processes, rather than the parent `llmtool task delegate` process itself [UNVERIFIED].

---

### C. Diagnostic Evidence Required to Establish Root Cause

To confirm which hypothesis is the active cause and rule out others, the following empirical evidence must be collected:

1. **Sampling Profiler Traces**:
   Run `py-spy dump --pid <delegate_pid>` or macOS `sample <delegate_pid> 5` while `llmtool task delegate` is running. This will isolate the exact Python frame (`selectors.py:select`, `os.read`, `_has_structured_tool_denial`, or `lifecycle.check`) accumulating CPU time.
2. **System Call Trace & Rate Measurement**:
   Run `dtruss -p <delegate_pid>` (macOS) or `strace -c -p <delegate_pid>` (Linux) to measure the call frequency and elapsed latency of `kevent`, `poll`, `read`, and `write`. A rate of thousands of `kevent` calls per second with 0 ms latency confirms spin on immediate selector returns.
3. **Selector Instrumentation**:
   Temporarily instrument `_default_runner` and `opencode_direct_developer.run` to log:
   - The elapsed wall-clock duration of each `selector.select()` call.
   - The ready keys returned (`key.data`, `key.events`).
   - The number of loop iterations per second.
4. **PID-Level Resource Accounting**:
   Measure `ps -o pid,ppid,%cpu,command` across the parent `llmtool task delegate` process and all spawned child/grandchild processes to prove whether the parent process or the child CLI is consuming CPU.

---

## 3. Candidate Fixes, Process Cleanup, and Risk Analysis

### A. Process Ownership and Descendant Cleanup Architecture

Process lifecycle management is governed by `CLIProcessLifecycle` (`tools/llmtool/llmtool/providers/cli_lifecycle.py`):
1. **Ownership & Process Groups**:
   Processes are spawned with `start_new_session=True` and owned via `lifecycle.own(process)` (lines 304–330). This pins the process leader and its process group ID (`pgid == process.pid`).
2. **Signal Handling & Deferred Cancellation**:
   The lifecycle installs signal handlers for `SIGINT`, `SIGTERM`, and `SIGHUP` (lines 265–271). In `_record(signum, frame)` (lines 235–240), the handler explicitly **does not raise an exception**:
   ```python
   def _record(self, signum, frame):
       if self.signum is None:
           self.signum = signum
   ```
   This prevents unwinding between process creation and ownership assignment. Cancellation is detected only when user-space code calls `lifecycle.check()` (line 332), which raises `_Cancelled()` if `self.signum is not None`.
3. **Descendant Teardown**:
   When exiting the `with lifecycle:` context or upon `lifecycle.stop()` (lines 336–342), `bounded_terminate(process, deadline)` is executed:
   - Delivers `SIGKILL` to the entire process group: `os.killpg(process.pid, signal.SIGKILL)`.
   - Reaps the process leader: `process.wait()`.
   - Verifies the absence of all remaining group descendants via `_group_absent_until(process.pid, deadline)`.
   - Confirms cleanup status (`CONFIRMED` vs `UNKNOWN`).

**Critical Design Constraint**: Any candidate fix must not alter or bypass `CLIProcessLifecycle`, must preserve synchronous ownership on the main thread, and must ensure prompt entry into `lifecycle.__exit__` so that `bounded_terminate` is executed before cleanup deadlines expire.

---

### B. Evaluation of Candidate Fixes

#### Candidate Fix 1: Event-Driven Passive Wait with Increased/Adaptive Selector Timeout Slice

**Description**:
Retain the single-threaded `DefaultSelector` loop, but increase the timeout slice passed to `selector.select(slice_timeout)`.
- **Variant 1A**: Full remaining timeout: `selector.select(timeout=max(0.0, remaining))`.
- **Variant 1B**: Moderate bounded timeout slice: `selector.select(timeout=min(remaining, 1.0))` (1.0 second).

**Risk Assessment**:
- **Results**: *Risk: None.* Whenever `stdout` or `stderr` receives data from the child process, the operating system kernel wakes `selector.select()` immediately. Data draining, chunk limits, and buffer accumulation are identical.
- **Timeouts**: *Risk: None.* Overall task timeout remains bounded by `remaining = deadline - time.monotonic()`.
- **Process Ownership & Descendant Cleanup (F2)**: *Risk: None.* The execution remains single-threaded. Normal context manager exit (`finally` / `__exit__`) invokes `bounded_terminate`, preserving full process group teardown and descendant verification.
- **Cancellation-Delay Risk (F4)**:
  - *Under PEP 475*, Python automatically retries interrupted system calls (such as `kevent`/`select`) on `EINTR` if the signal handler does not raise an exception.
  - Because `CLIProcessLifecycle._record` only records `self.signum = signum` without raising, `select()` will *not* return to Python when a signal arrives; it will continue sleeping in the kernel until the timeout expires or stream data arrives!
  - **In Variant 1A (Full Timeout, up to 3600s)**: If the child process is quiet and a `SIGINT` (Ctrl-C) arrives, `selector.select()` will block for the entire remaining task duration (minutes or hours). `lifecycle.check()` will not execute, and the process will not respond to cancellation. **Variant 1A presents an unacceptable cancellation-delay risk.**
  - **In Variant 1B (Bounded 1.0s Slice)**: The maximum delay between signal arrival and cancellation detection via `lifecycle.check()` is strictly bounded to 1.0 second. This provides acceptable cancellation responsiveness while reducing idle iteration frequency by 90% (from 10 Hz to 1 Hz) if `select` is blocking.

---

#### Candidate Fix 2: Self-Pipe / Wakeup Pipe Event Loop

**Description**:
Create a non-blocking internal pipe (`wake_r, wake_w = os.pipe()`) and register `wake_r` in the selector with `EVENT_READ`.
When `CLIProcessLifecycle` receives a signal, or via a signal callback, write a single byte to `wake_w`.
This allows using the full remaining timeout:
```python
ready = selector.select(timeout=max(0.0, remaining))
```
If a signal arrives, the write to `wake_w` wakes `selector.select()` immediately (0 ms latency). When `wake_r` is ready, the loop calls `lifecycle.check()` and raises `_Cancelled()`.

**Risk Assessment**:
- **Results**: *Risk: None.* Stream collection for child pipes is unchanged.
- **Timeouts**: *Risk: None.* Bounded by `remaining`.
- **Process Ownership & Descendant Cleanup (F2)**: *Risk: Low.* Single-threaded execution model is preserved. Upon `_Cancelled`, control flow exits the loop and reaches `lifecycle.__exit__`, triggering `bounded_terminate` on the child process group. The only cleanup requirement is closing `wake_r` and `wake_w` in a `finally` block to prevent file descriptor leaks.
- **Cancellation-Delay Risk (F4)**: *Risk: None.* Signals wake `selector.select()` with 0 ms delay.

---

#### Candidate Fix 3: Spin Prevention with Backoff and State-Driven Stdin Registration

**Description**:
Address the specific conditions that cause `selector.select()` to return immediately without blocking:
1. **OpenCode Stdin Management**: In `opencode_direct_developer.py`, do not register `stdin` with `EVENT_WRITE` continuously. Only register `stdin` when there are pending prompt bytes to write. Once `offset >= len(payload)`, unregister and close `stdin` immediately. If a write returns `BlockingIOError`, leave it registered; otherwise unregister until the next write slice.
2. **Cooperative Spin Backoff**: If `selector.select()` returns an empty list or returns with elapsed time < 1 ms without transferring any stream bytes, execute an explicit cooperative yield (`time.sleep(0.01)` or `0.02`).

**Risk Assessment**:
- **Results**: *Risk: None.* Preserves identical payload delivery, buffer bounds, and hash verification.
- **Timeouts**: *Risk: None.* Backoff sleep (10–20 ms) does not affect multi-minute timeouts.
- **Process Ownership & Descendant Cleanup (F2)**: *Risk: None.* Retains the proven single-threaded lifecycle model; `bounded_terminate` and group cleanup operate without modification.
- **Cancellation-Delay Risk (F4)**: *Risk: Low.* Cancellation delay is bounded by the 100 ms selector slice plus backoff (at most ~120 ms), maintaining high responsiveness to `SIGINT` and `SIGTERM`.

---

### C. Analysis of Threaded `communicate()` Models and Cleanup Risks (F2)

Prior discussions considered adopting standard library `subprocess.Popen.communicate()` or background reader threads. This recommendation is rejected because it introduces significant risks to process ownership and descendant cleanup:

1. **Thread Blocking on Orphaned Pipes**:
   Worker threads blocked on synchronous `stream.read()` cannot be interrupted by Python signals. If a child process hangs or spawns descendants that inherit open pipe descriptors, reader threads hang indefinitely, preventing the main thread from completing cleanup or exiting.
2. **Violation of Main-Thread Signal Contracts**:
   `CLIProcessLifecycle` requires execution on the main thread to install and restore signal handlers (cli_lifecycle.py lines 40–42, 247–250). Delegating process waiting to background threads breaks signal ownership invariants.
3. **Deadlock in Teardown**:
   If the main thread attempts to terminate the process group while reader threads are actively blocked on file descriptors, deadlocks or race conditions on pipe closure can occur, preventing `bounded_terminate` from confirming group absence (`_group_absent_until`).

---

## 4. Summary Matrix

| Metric / Dimension | Candidate Fix 1A (Full Timeout) | Candidate Fix 1B (Bounded 1s Slice) | Candidate Fix 2 (Self-Pipe Wakeup) | Candidate Fix 3 (Spin Backoff & Stdin State) | Threaded `communicate()` (Rejected) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **CPU Reduction** | Eliminates idle spin | Eliminates idle spin | Eliminates idle spin | Eliminates idle spin | Eliminates idle spin |
| **Architectural Complexity** | Trivial (1 line) | Trivial (1 line) | Low (self-pipe added) | Low (backoff + stdin gating) | High (threading model) |
| **Cancellation-Delay Risk (F4)** | **HIGH** (blocks up to 3600s on signal due to PEP 475) | **LOW** (max 1.0s delay to detect cancellation) | **NONE** (instant wakeup via pipe) | **LOW** (max ~120ms delay) | **HIGH** (threads block on pipe) |
| **Descendant Cleanup Risk (F2)** | **NONE** (single-threaded `bounded_terminate`) | **NONE** (single-threaded `bounded_terminate`) | **LOW** (close pipe fds in `finally`) | **NONE** (single-threaded `bounded_terminate`) | **HIGH** (thread hangs, orphaned descendants) |
| **Result Integrity Risk** | None | None | None | None | Low |
| **Applicability to OpenCode** | Requires stdin fix | Requires stdin fix | Requires stdin fix | Directly addresses stdin spin | High rewrite cost |

### Recommendation
The optimal path is a combination of **Candidate Fix 3** (resolving the OpenCode `stdin` continuous writable state and adding spin backoff) and **Candidate Fix 1B** (raising the polling slice to a bounded 1.0 second). This combination:
- Eliminates CPU spinning during both prompt delivery and idle execution.
- Bounds cancellation delay to at most 1.0 second under PEP 475 without requiring complex self-pipe machinery.
- Retains single-threaded `CLIProcessLifecycle` ownership, ensuring reliable `bounded_terminate` PGID teardown and descendant cleanup.
