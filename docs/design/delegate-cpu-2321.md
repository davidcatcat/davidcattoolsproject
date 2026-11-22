# Investigation: Delegate CPU Utilization During Developer Runs (#2321)

## Executive Summary

When running `llmtool task delegate`, the process is observed to use substantial CPU (reported in #2321 as "most of a CPU core for minutes" [UNVERIFIED: informal observation; precise percentage unmeasured by baseline instrumentation]) while awaiting direct CLI Developer executions. This behavior has been reported across multiple provider routes, including `agy`, `claude`, and `opencode`.

This technical design note documents:
1. The exact locations (with file and line references) where `llmtool task delegate` waits for each provider's Developer run, including a dedicated analysis of how `opencode` differs from the provider-neutral routes.
2. The root cause analysis, strictly separating confirmed source code facts from hypotheses, and enumerating the empirical diagnostic evidence required to prove the CPU consumption mechanism.
3. Candidate architectural fixes, analyzing risks to result integrity, timeout accuracy, cancellation responsiveness (assessing cancellation-delay risk across timeout variants), and process group ownership/descendant cleanup under `CLIProcessLifecycle`.
4. A recommendation that is explicitly conditional on empirical profiling evidence, acknowledging that theoretical CPU reduction is unverified until the active mechanism is confirmed.

---

## 1. Where a Delegate Waits for Each Provider's Run

The execution flow for direct Developer dispatch originates in `tools/llmtool/llmtool/task_submission_cli.py` and branches based on the requested provider.

### A. Provider-Neutral Routes (`agy`, `claude`, `gw-agent`)

The routes `agy`, `claude`, and `gw-agent` share the unified execution contract implemented in `tools/llmtool/llmtool/developer_routes/base.py`.

1. **Dispatch Call Sites in `tools/llmtool/llmtool/task_submission_cli.py`**:
   - **`agy`**: `run_task_agy_delegate` (lines 5497–5814) invokes `execute(route, ...)` at line 5706.
   - **`claude`**: `run_task_claude_delegate` (lines 6207–6564) invokes `execute(route, ...)` at line 6431.
   - **`gw-agent`**: `run_task_gw_agent_delegate` (lines 6575–6994) invokes `execute(route, ...)` at line 6900.

2. **Route Runner Orchestration in `tools/llmtool/llmtool/developer_routes/base.py`**:
   - `run_route(...)` (lines 965–1269) prepares private temporary environments, verifies write boundaries, and invokes the runner at lines 1122–1128:
     ```python
     execute = runner or _default_runner
     runner_args = (argv, value['root'], env, value['timeout'])
     if on_child is None:
         code, stdout, stderr = execute(*runner_args)
     else:
         code, stdout, stderr = execute(*runner_args, on_child=on_child)
     ```
   - When no custom runner is injected, execution uses `_default_runner` (lines 576–621).

3. **Wait Loop in `_default_runner` (`tools/llmtool/llmtool/developer_routes/base.py` lines 584–613)**:
   - Spawns the CLI child process with `stdin=subprocess.DEVNULL` (lines 584–587):
     ```python
     process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                shell=False, close_fds=True, start_new_session=True)
     lifecycle.own(process)
     ```
   - Sets `stdout` and `stderr` to non-blocking mode and registers both with `selectors.DefaultSelector()` (lines 588, 592–594):
     ```python
     selector = selectors.DefaultSelector()
     ...
     for stream, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
         os.set_blocking(stream.fileno(), False)
         selector.register(stream, selectors.EVENT_READ, name)
     ```
   - **The Wait Loop (lines 595–609)**:
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
   - **Process Exit Reap (lines 610–613)**:
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
   - `run_task_opencode_delegate` (lines 4980–5339) delegates execution at lines 5175–5181:
     ```python
     result = opencode_direct_developer.run(
         root=registration.root, read_paths=read_paths,
         write_paths=write_paths, prompt=prompt,
         dispatch_store=store, dispatch_task_id=task['task_id'], steps=steps,
         prelaunch_validate=validate_prelaunch_d6_binding, model=model,
         launch_callback=record_opencode_launch,
         spawn_authorization=authorize_opencode_spawn, timeout_seconds=timeout)
     ```
   - In background execution mode (`--background`, `run_task_delegate` lines 8623–8688), `_run_background_delegate` (lines 233–308) performs a double fork; the parent process waits synchronously via `_background_read(read_fd)` (line 293), while the detached child executes `run_task_opencode_delegate` (lines 272–274) and enters `opencode_direct_developer.run`.

2. **Wait Loop in `opencode_direct_developer.py` (`run`, lines 979–1062)**:
   - Spawns the CLI child process with `stdin=subprocess.PIPE` (lines 979–984):
     ```python
     process = subprocess.Popen(argv, cwd=str(root), env=env,
         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
         stderr=subprocess.PIPE, bufsize=0, shell=False,
         close_fds=True, start_new_session=True)
     started_at = _utc_now()
     lifecycle.own(process)
     ```
   - **Stream Registration (lines 994–1000)**:
     Unlike `_default_runner`, `opencode_direct_developer.py` registers `stdin` in addition to `stdout` and `stderr`:
     ```python
     selector = selectors.DefaultSelector()
     for stream, name in ((process.stdout, 'stdout'),
                          (process.stderr, 'stderr')):
         os.set_blocking(stream.fileno(), False)
         selector.register(stream, selectors.EVENT_READ, name)
     os.set_blocking(process.stdin.fileno(), False)
     selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
     ```
   - **The Wait Loop (lines 1003–1059)**:
     ```python
     while selector.get_map():
         lifecycle.check()
         remaining = work_deadline - time.monotonic()
         if remaining <= 0:
             _reject('OPENCODE_TIMEOUT')
         for key, _ in selector.select(min(remaining, 0.1)):
             if key.data == 'stdin':
                 try:
                     written = os.write(key.fileobj.fileno(),
                                        payload[offset:offset + 8192])
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
   - **Post-Selector Exit Wait Without Reap (lines 1060–1062)**:
     ```python
     if not delivered:
         _reject('OPENCODE_PROMPT_DELIVERY_INCOMPLETE')
     _await_exit_without_reap(process, deadline=work_deadline)
     ```
     where `_await_exit_without_reap` (lines 660–667) calls `_observe_exit_without_reap(process, remaining, deadline)`:
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
| **Post-Stream Exit Waiting** | Calls `process.wait(timeout=remaining)` (`base.py` line 613), directly reaping the child process leader. | Calls `_await_exit_without_reap` (`opencode_direct_developer.py` line 1062), observing exit via non-reaping wait checks (`_observe_exit_without_reap`) so PID/PGID tracking remains valid for `CLIProcessLifecycle`. |
| **Error & Exception Types** | Raises `RouteError` (`ROUTE_OUTPUT_TOO_LARGE`, `TIMEOUT`, `RUNNER_FAILED`). | Raises `DirectOpenCodeError` (`OPENCODE_TIMEOUT`, `OPENCODE_OUTPUT_TOO_LARGE`, `TOOL_DENIED`, `OPENCODE_PROMPT_DELIVERY_INCOMPLETE`). |

---

## 2. Why That Wait Uses CPU

### A. Confirmed Source Facts

The following facts are established directly by inspection of the source code:

1. **Fixed 100 ms Polling Slices**:
   Both `_default_runner` (`base.py` line 600) and `opencode_direct_developer.run` (`opencode_direct_developer.py` line 1008) invoke `selector.select(min(remaining, 0.1))`. The timeout argument passed to the kernel polling mechanism is capped at 0.1 seconds.
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
   In `opencode_direct_developer.py` (lines 999–1000), `stdin` is registered with `selectors.EVENT_WRITE`. Under POSIX semantics, an OS pipe buffer with available space is continuously ready for writing. As long as `stdin` remains registered, `selector.select()` will report `stdin` ready immediately with 0 ms sleep until all bytes are delivered at lines 1023–1032.

---

### B. Hypotheses for Elevated CPU Utilization

The following mechanisms are plausible explanations for elevated CPU consumption, but remain hypotheses pending empirical profiling evidence:

- **Hypothesis 1: Continuous Writable Readiness on `stdin` in OpenCode [UNVERIFIED]**:
  During the prompt transmission phase, `stdin` is registered for `EVENT_WRITE`. If the pipe buffer does not fill up immediately, `selector.select()` returns without delay on every call. If prompt delivery spans multiple iterations or encounters partial writes, the selector spins in a 0 ms loop until delivery completes and `stdin` is unregistered.
- **Hypothesis 2: Spurious Readiness or Level-Triggered Spinning on Pipe EOF [UNVERIFIED]**:
  On macOS (`selectors.KqueueSelector`), pipes monitored with `KQ_FILTER_READ` enter a ready state when the peer closes the write end or reaches EOF. If there is any lag, unhandled state, or race condition between a stream reporting EOF and being unregistered via `selector.unregister(key.fileobj)`, or if one stream remains open while the other spins on EOF, `select()` returns immediately on every call.
- **Hypothesis 3: High-Frequency Sub-Process Stream Chunks [UNVERIFIED]**:
  If the child developer process (`agy`, `claude`, or `opencode`) writes small chunks of output, ANSI progress indicators, or unbuffered debug logs to `stdout`/`stderr`, `selector.select()` wakes upon every chunk. In OpenCode, every newline in `stdout` triggers string slicing and `_has_structured_tool_denial` regex evaluation (`opencode_direct_developer.py` lines 1048–1059), amplifying user-space CPU usage.
- **Hypothesis 4: Short Remaining Time or Clock Precision Artifacts [UNVERIFIED]**:
  If `remaining` approaches zero or clock queries produce negative or zero values, `min(remaining, 0.1)` collapses to `0`, converting the selector into a non-blocking poll.
- **Hypothesis 5: Process Attribution Misidentification [UNVERIFIED]**:
  Informal reports of high CPU during delegate runs may reflect CPU consumption by child processes (e.g. Node/Python/Go provider processes) or sibling helper processes, rather than the parent `llmtool task delegate` process itself.

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
   The lifecycle installs signal handlers for `SIGINT`, `SIGTERM`, and `SIGHUP` (lines 264–271). In `_record(signum, frame)` (lines 235–239), the handler explicitly **does not raise an exception**:
   ```python
   def _record(self, signum, frame):
       # No raising, killing, waiting, I/O or cleanup in a handler. In particular,
       # never unwind Popen between child creation and ownership assignment.
       if self.signum is None:
           self.signum = signum
   ```
   This prevents unwinding between process creation and ownership assignment. Cancellation is detected only when user-space code calls `lifecycle.check()` (lines 332–334), which raises `_Cancelled()` if `self.signum is not None`.
3. **Descendant Teardown**:
   When exiting the `with lifecycle:` context (`__exit__`, lines 343–360) or upon `lifecycle.stop()` (lines 336–341), `bounded_terminate(process, deadline)` (lines 119–184) is executed:
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
- **Cancellation-Delay Risk**:
  - *Under PEP 475*, Python automatically retries interrupted system calls (such as `kevent`/`select`) on `EINTR` if the signal handler does not raise an exception.
  - Because `CLIProcessLifecycle._record` only records `self.signum = signum` without raising, `select()` will *not* return to Python when a signal arrives; it will continue sleeping in the kernel until the timeout expires or stream data arrives.
  - **In Variant 1A (Full Timeout, up to `HARD_TIMEOUT_CAP_SECONDS` = 18000s)**: If the child process is quiet and a `SIGINT` (Ctrl-C) or `SIGTERM` arrives, `selector.select()` will block for the entire remaining task duration (minutes or hours). `lifecycle.check()` will not execute, and the process will not respond to cancellation. **Variant 1A presents an unacceptable cancellation-delay risk.**
  - **In Variant 1B (Bounded 1.0s Slice)**: The maximum delay between signal arrival and cancellation detection via `lifecycle.check()` is strictly bounded to 1.0 second. This provides acceptable cancellation responsiveness while reducing idle iteration frequency by 90% (from 10 Hz to 1 Hz) if `select` is blocking.
- **Process Ownership & Descendant Cleanup**:
  - **In Variant 1A**: *Risk: High.* When cancellation is delayed because `select()` blocks for minutes or hours, child and descendant processes continue executing un-terminated in the background. If an external orchestrator or CI supervisor escalates cancellation by sending `SIGKILL` to the unresponsive parent process (after a typical 5–10s grace period), `lifecycle.__exit__` is never executed; `bounded_terminate` is completely bypassed, leaving orphaned descendant processes running indefinitely in the D6 worktree. Furthermore, if the selector sleeps until the overall deadline expires, the finite cleanup reserve (`deadline + 5`) is exhausted before `bounded_terminate` is invoked, preventing confirmed verification and degrading cleanup status to `UNKNOWN`.
  - **In Variant 1B**: *Risk: Low.* The maximum cancellation delay is 1.0 second, which is well within standard cancellation grace periods. Normal context manager exit (`finally` / `__exit__`) invokes `bounded_terminate` promptly, preserving process group teardown and descendant absence verification before cleanup reserves expire.
- **CPU Reduction**:
  - *Outcome [UNVERIFIED]*: Increasing the selector slice only reduces CPU utilization if the high CPU is caused by the 100 ms polling frequency of an otherwise idle loop. If `select()` is returning immediately due to continuous stream readiness (e.g. `stdin` write readiness, EOF level triggering, or rapid chunk output), increasing the timeout parameter has zero effect on CPU utilization.

---

#### Candidate Fix 2: Self-Pipe / Wakeup Pipe Event Loop via `signal.set_wakeup_fd`

**Description**:
To allow the selector to block for the full remaining timeout (`selector.select(timeout=max(0.0, remaining))`) without suffering cancellation delays under PEP 475, an internal wakeup pipe can be used to notify the selector of signal delivery.

**Contractual Constraint in `CLIProcessLifecycle`**:
`cli_lifecycle.py` lines 235–239 explicitly mandate:
```python
def _record(self, signum, frame):
    # No raising, killing, waiting, I/O or cleanup in a handler. In particular,
    # never unwind Popen between child creation and ownership assignment.
    if self.signum is None:
        self.signum = signum
```
Because `_record` explicitly forbids performing I/O in a signal handler to protect process ownership, writing to a pipe from within `_record` (or from an ordinary Python signal callback) is prohibited. Furthermore, an ordinary Python callback cannot execute while `select()` is blocked inside the C-level system call under PEP 475 unless an event wakes the kernel call.

**Viable Architectural Implementation**:
Use Python's runtime C-level mechanism: `signal.set_wakeup_fd(wake_w, warn_on_full=False)`.
- When an OS signal arrives, the Python C runtime signal trampoline writes a byte directly to `wake_w` at the C/OS layer, without invoking Python bytecode or performing I/O in `_record`. `CLIProcessLifecycle._record` remains completely unchanged and compliant with the "no I/O in handler" invariant.
- `wake_r` is registered with `selector.register(wake_r, selectors.EVENT_READ, 'wakeup')`.
- The arrival of the wakeup byte causes the kernel `select()` call to return immediately (0 ms latency).
- Upon return, Python executes `_record(signum, frame)` (setting `self.signum`), drains `wake_r` via a non-blocking read, and invokes `lifecycle.check()`, which raises `_Cancelled()` and exits the loop.

**Risk Assessment**:
- **Results**: *Risk: None.* Stream collection for child pipes is unchanged.
- **Timeouts**: *Risk: None.* Bounded by `remaining`.
- **Cancellation-Delay Risk**: *Risk: None.* Signals wake `selector.select()` with 0 ms delay.
- **Process Ownership & Descendant Cleanup**: *Risk: Low (requires careful descriptor & global state management).*
  - `signal.set_wakeup_fd` is a process-global Python setting. The caller must save the previous wakeup fd and restore it via `signal.set_wakeup_fd(old_fd)` in a `finally` block to avoid clobbering other subsystems or leaking state.
  - Pipe file descriptors `wake_r` and `wake_w` must be created non-blocking (`os.set_blocking(fd, False)`) and closed in a `finally` block to prevent file descriptor leaks.
  - Draining `wake_r` must handle `BlockingIOError`.
  - Must run strictly on the main thread (consistent with `CLIProcessLifecycle.__enter__`).
  - Upon `_Cancelled`, control flow exits the loop and reaches `lifecycle.__exit__`, triggering `bounded_terminate` on the child process group without delay.
- **CPU Reduction**:
  - *Outcome [UNVERIFIED]*: Allows `select()` to block passively for the full duration during idle periods, but provides no CPU reduction if continuous stream readiness is causing immediate selector returns.

---

#### Candidate Fix 3: Cooperative Spin Backoff & OpenCode Stdin Invariant Preservation

**Description**:
Address the specific conditions that cause `selector.select()` to return immediately without blocking, while strictly maintaining prompt delivery and stream invariants:

1. **Preserving OpenCode Stdin Delivery Invariants**:
   - In `opencode_direct_developer.py` (lines 1023–1032), `stdin` must remain registered with `EVENT_WRITE` continuously while `offset < len(payload)` so that the selector wakes whenever the kernel pipe buffer has space.
   - Once `offset >= len(payload)`, `stdin` is unregistered from the selector and closed immediately (`key.fileobj.close()`).
   - *Rejection of Partial-Write Unregistration*: Unregistering `stdin` after a partial write without an external scheduling mechanism or timer would stall prompt delivery until timeout (`OPENCODE_TIMEOUT`), because no subsequent event would schedule the next write slice while the child process blocks waiting for prompt input. Therefore, `stdin` must remain registered until all prompt bytes are delivered.
2. **Cooperative Spin Backoff**:
   - If `selector.select()` returns immediately (elapsed wall-clock time < 1 ms) and transfers 0 bytes across all streams (e.g. due to persistent writable readiness on an un-drained pipe, level-triggered EOF readiness, or high-frequency polling), execute a brief cooperative yield via `time.sleep(0.01)` or `0.02` (10–20 ms).
   - This caps the maximum loop frequency at 50–100 iterations per second even during continuous readiness, preventing 100% CPU core spinning while preserving prompt delivery and stream draining.

**Risk Assessment**:
- **Results**: *Risk: None.* Preserves identical payload delivery, buffer bounds, stream parsing (`_has_structured_tool_denial`), and hash verification.
- **Timeouts**: *Risk: None.* Backoff sleep (10–20 ms) does not affect multi-minute timeouts.
- **Cancellation-Delay Risk**: *Risk: Low.* Cancellation delay is bounded by the selector slice plus backoff (at most ~110–120 ms), maintaining high responsiveness to `SIGINT` and `SIGTERM`.
- **Process Ownership & Descendant Cleanup**: *Risk: None.* Retains the single-threaded lifecycle model; `bounded_terminate` and group cleanup operate without modification.
- **CPU Reduction**:
  - *Outcome [UNVERIFIED]*: Throttles loop execution frequency and caps CPU utilization regardless of whether the spin is caused by idle timeout polling or continuous stream readiness, but actual CPU reduction remains unverified pending empirical measurement.

---

### C. Analysis of Threaded `communicate()` Models and Cleanup Risks

Prior discussions considered adopting standard library `subprocess.Popen.communicate()` or background reader threads. This recommendation is rejected because it introduces significant risks to process ownership and descendant cleanup:

1. **Thread Blocking on Orphaned Pipes**:
   Worker threads blocked on synchronous `stream.read()` cannot be interrupted by Python signals. If a child process hangs or spawns descendants that inherit open pipe descriptors, reader threads hang indefinitely, preventing the main thread from completing cleanup or exiting.
2. **Violation of Main-Thread Signal Contracts**:
   `CLIProcessLifecycle` requires execution on the main thread to install and restore signal handlers (`cli_lifecycle.py` lines 40–41, 247–250). Delegating process waiting to background threads breaks signal ownership invariants.
3. **Deadlock in Teardown**:
   If the main thread attempts to terminate the process group while reader threads are actively blocked on file descriptors, deadlocks or race conditions on pipe closure can occur, preventing `bounded_terminate` from confirming group absence (`_group_absent_until`).

---

## 4. Summary Matrix

| Metric / Dimension | Candidate Fix 1A (Full Timeout) | Candidate Fix 1B (Bounded 1s Slice) | Candidate Fix 2 (Self-Pipe Wakeup) | Candidate Fix 3 (Spin Backoff & Stdin State) | Threaded `communicate()` (Rejected) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **CPU Reduction** | [UNVERIFIED] Reduces CPU only if idle 100ms polling is the cause; ineffective if readiness triggers immediate return | [UNVERIFIED] Reduces CPU only if idle 100ms polling is the cause; ineffective if readiness triggers immediate return | [UNVERIFIED] Reduces CPU only if idle 100ms polling is the cause; ineffective if readiness triggers immediate return | [UNVERIFIED] Caps loop frequency to 50–100 Hz even under continuous readiness; unverified until profiled | [UNVERIFIED] Eliminates polling; unverified |
| **Architectural Complexity** | Trivial (1 line) | Trivial (1 line) | Moderate (`set_wakeup_fd` + fd lifecycle) | Low (cooperative sleep on 0-byte return) | High (threading model rewrite) |
| **Cancellation-Delay Risk** | **HIGH** (blocks up to 18000s on signal due to PEP 475) | **LOW** (max 1.0s delay to detect cancellation) | **NONE** (instant wakeup via pipe) | **LOW** (max ~120ms delay) | **HIGH** (threads block on pipe) |
| **Descendant Cleanup Risk** | **HIGH** (descendants run un-terminated during delay; external SIGKILL orphans group; deadline exhausted) | **LOW** (max 1.0s delay allows prompt `bounded_terminate`) | **LOW** (must clean up global fd and pipe descriptors in `finally`) | **NONE** (single-threaded `bounded_terminate` unaffected) | **HIGH** (thread hangs, orphaned descendants) |
| **Result Integrity Risk** | None | None | None | None | Low |
| **OpenCode Applicability** | Ineffective if stdin/EOF spins | Ineffective if stdin/EOF spins | Ineffective if stdin/EOF spins | Directly addresses spin without breaking stdin delivery | High rewrite cost |

---

### Recommendation

Any architectural fix must be **conditional on empirical profiling evidence** obtained through the diagnostic procedures in Section 2.C:

1. **Diagnostic Phase (Prerequisite)**:
   - Profile `llmtool task delegate` using `py-spy` and system call tracing (`dtruss`/`strace`) to establish the active CPU consumption mechanism.
   - Distinguish between:
     - Passive idle loop polling frequency (100 ms timeout slice).
     - Continuous stream readiness (e.g. `stdin` write readiness or pipe EOF level-triggering).
     - Sub-process CPU utilization (misattributed child process activity).

2. **Conditional Implementation Path**:
   - **If profiling confirms idle loop polling frequency is the primary cause**:
     Adopt **Candidate Fix 1B** (raising the selector slice to a bounded 1.0 second). This reduces loop iteration overhead by 90% during idle periods, strictly bounds cancellation delay to at most 1.0 second under PEP 475, and avoids the complexity of global wakeup pipes while preserving `CLIProcessLifecycle` descendant cleanup.
   - **If profiling confirms continuous stream readiness (e.g. pipe spin or OpenCode stdin readiness)**:
     Adopt **Candidate Fix 3** (cooperative spin backoff while maintaining continuous `stdin` registration until delivery completes at `opencode_direct_developer.py:1023–1032`). This caps CPU consumption to <= 50–100 iterations per second without stalling prompt delivery or altering process ownership contracts.
   - Candidate Fix 1A and Threaded `communicate()` are firmly rejected due to unacceptable descendant cleanup and cancellation-delay risks.
