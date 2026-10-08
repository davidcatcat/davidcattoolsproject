# llmtool repo review — PR #49 remediation 的獨立 re-review，以及 Mac acceptance 的第二次重新驗證（2026-10-09）

## Repo 狀態

- `main @ ceae425`，**沒有改變**。
- open draft 仍然是 PR #49 和 PR #45。PR #45 這一輪沒有新的 commit。
- **PR #49 有一個新的 commit：`6874410`**（`fix(llmtool): retain self-group cleanup and unresolved ownership`），對應我上一輪 review（`111f160`）的 P49-M1 和 P49-M2，以及 coordinator 提出的 S49-2。

## 審查範圍和方法

- **審查對象：** PR #49，head **`68744100c2890d05ca0e565f4d246ae1c773e951`**（`6874410`；原本誤植為 `68744107`，依照 QA49-N3 更正）。
- **閱讀 `111f160..6874410` 的完整 diff：**
  - production：`_git_supervisor.py` 的 `_last_act`；
  - test host：`containment_probe_parent.py`；
  - test：`test_git_containment.py`（+73／−15）；
  - 文件：`WORKFLOW.md`、新增的 spec amendment `llmtool_pr49_b5_self_anchor_amendment_20261008.md`；
  - evidence 目錄：`reports/pr49_security_remediation_20261008/`。
- **獨立驗證（在真正的 Mac 上，sandbox 以外）：**
  - `test_git_containment`：venv 的 Python 3.13 和 `/usr/bin/python3` 3.9.6 都是 **OK**；
  - **重新執行完整的 14 個 native acceptance 情境**（見下方）；
  - 驗證 committed 的 `mac-native.json` 和目前的檔案 hash 是否一致，並掃描 evidence log 中是否有 secret。
- **CI：** PR checks 的 3.9 和 3.14（run 37805296744 和 37805302648）**全部 pass**。
- **沒有做的事：** 沒有修改 production 或 test；沒有執行真正的 `git add`；沒有 merge；沒有開始 A11b.2.2.2。

## P49-M1：CLOSED

修正後的 `_last_act`：

```python
pid = os.getpid()
if (pid != own.pid or own.pid != own.pgid or own.pid != own.sid
        or os.getpgrp() != pid):
    os._exit(79)
os.killpg(0, signal.SIGKILL)  # Current group, never a remembered numeric ID.
os._exit(79)
```

- **完全依照建議修正：**
  - 移除了 libproc 的 `identity()` 和 `replace(ppid)` 的比對，所以暫時性的 libproc 失敗或 PPID 的 race **不會再讓 supervisor 不殺 group 就直接結束**；
  - 判斷條件只剩下 `getpid`、`getpgrp` 和啟動時已經驗證過的 pid、pgid、sid，**都是不會失敗的 syscall**；
  - signal 送給的是**目前所在的 group**（`killpg(0)`），不是記住的數字 ID。
- **剩下的 `os._exit(79)` 分支：** 只有在 `getpgrp() != getpid()` 時才會走到。但 supervisor 從來不呼叫 `setsid` 或 `setpgid`，所以這個條件實際上不可能成立。走到這個分支時，它**不會對任何其他 group 送出 signal**，這是正確的保守處理。
- **新增的 test：**
  - `identity` 被 patch 成丟出例外時，`killpg(0)` **仍然會被執行**；
  - `getpgrp` 傳回其他的 group 時，**不會**送出任何 kill；
  - 兩次讀取到不同的 PPID（模擬 parent 正在死亡）時，不會影響清除。
- 在 native 的 `parent_sigkill_plain` 和 `parent_sigkill_popen` 中，這個路徑**實際被執行了 40 次，全部 containment confirmed**（見下方）。

## P49-M2：CLOSED

- 新增了 `reports/llmtool_pr49_b5_self_anchor_amendment_20261008.md`，**明確寫出它取代 B5 §§3.2、6、11.1–11.3 的拓撲要求**（只適用於這個已經授權的 synthetic containment slice）。內容包括：
  - parent → 直接的 supervisor（PID == PGID == SID）→ 繼承 group 的 synthetic leader 和 descendant；
  - parent 只對**自己擁有、尚未 reap 的直接 supervisor** 送出 signal，而且要先驗證 PPID、UID、start time 和 PID／PGID／SID 的連續性；**先送出 signal 再 reap**；reap 之後的數字查詢只是被動的觀察；
  - 原本「依照 Git 孫 process 的身分做 fallback」的設計被取代，並寫明了原因：孫 process 可能被其他 process reap；
  - supervisor 的最後動作是 `killpg(0)`；
  - unresolved 時的回報是 best effort，但 ownership 不能因為回報失敗而被釋放。
- `WORKFLOW.md` 加入了連結，並重申 **real Git ADD 和 A11b.2.2.2 仍然沒有被授權**。
- **授權的依據現在可以在 repo 中追溯**（不再只存在於 ai-loop 的 `.ai-loop/` handoff）。

## S49-2（unresolved 時回報失敗的處理）：CLOSED

- `containment_probe_parent.py` 中，unresolved 的 `_event` 現在被 `try/except Exception: pass` 包住。即使出現 EPIPE、EBADF 或其他回報上的例外，**仍然會進入保留 ownership 的 operator-wait 迴圈**，不會正常回傳，也不會釋放 handler、fd 或 process。
- 有一個新的 test：`test_report_loss_retains_operator_wait_with_unavailable_supervisor`。
- amendment 也誠實地寫出：如果 supervisor 已經無法使用，終止 host **不是** containment 的保證，必須由外部處理 descendant。

## Mac acceptance：兩份 evidence 都是 PASS

| 來源 | 結果 |
|---|---|
| **committed 的 `mac-native.json`**（remediation 作者在 Mac 上產生） | macOS 15.7.9／Python 3.13.3，14／14 PASS，沒有 failure；**9 個 tested 檔案的 hash 和目前的 head 完全一致** |
| **我獨立重新執行**（canonical repo、sandbox 以外、`.venv` 的 Python 3.13.3、使用 committed 的 `task.json`） | **exit 0，14／14 PASS**；evidence 的 SHA 前綴是 `14a2cafeee10671f`（只保存在 scratchpad） |

我的重新執行中的各個情境：

- fd_inheritance、sigint、sigterm、sighup、hung_supervisor（13.2 秒）、leader_exit_descendant、identity_reuse、macos_start_time、kqueue_non_reaping、protocol_framing、code_runtime_binding、public_import_boundary：**全部 PASS**；
- **parent_sigkill_plain：20 次，全部 confirmed，最大延遲 0.0122 秒**；
- **parent_sigkill_popen：20 次，全部 confirmed，最大延遲 0.0124 秒**。

執行之後沒有留下任何 supervisor、child 或 host process（`pgrep` 確認），tracked tree 是 clean 的。

## Evidence 目錄：PASS（附 NOTE）

- `reports/pr49_security_remediation_20261008/` 新增了大約 6,300 行的 log 和 JSON。用常見的 secret pattern（API key、GitHub token、AWS key、private key、Authorization header）掃描，結果是 **0 筆**。
- `mac-py313-sandbox-failed.log` 中有 201 處使用者家目錄的絕對路徑，但**都只是 repo 內 test 檔案的路徑**。repo 是 **PRIVATE**，沒有實際的風險。
- **NOTE：** 這份 log 記錄的是在 Codex sandbox 中預期會失敗的一次執行（`ps` 和 loopback 被拒絕），保留它可以作為「sandbox 的結果不能當成 Mac PASS」的證據。但它有 3,055 行，對 repo 的大小並不友善。將來可以考慮只保留摘要和 digest。

## 仍然延後的 finding（沒有改變，也沒有被默默關閉）

amendment 中明確列出了以下 finding，**都不阻擋 PR #49 的 merge**：

- S49-3：LOW，計算目錄 hash 時的 raw fd 洩漏；
- P49-L1：LOW，fixture 的 fault hook 在 product execution 之前必須分離；
- P49-L2：runtime 相容性，ctypes 的 fd 會讓 supervisor fail closed；
- P49-L3：LOW，reap 之後用數字 ID 確認時，可能出現保守的誤判。

**P49-L1 必須在 A11b.2.2.2 的設計中處理。**

## 建議

1. **PR #49：** 我這一輪的獨立 security re-review 是 **PASS**。amendment 中列出的其他 gate 包括「independent functional QA」和 coordinator acceptance，請依照流程完成。完成之後可以 **squash merge**（commit 很多，而且有 merge commit），並在 squash message 中保留 tested head `6874410` 和 Mac evidence 的 digest。
2. **A11b.2.2.2：** 在 PR #49 merge 之後，**先寫 2.2.2 的設計 spec**（真正的 Git ADD，加上 post-state 的完整分類，B5 §14.1）。這份 spec 必須處理 P49-L1（把 fault hook 從 product engine 中分離）、S49-3，以及 product host 的單一 thread 前提（B5 F4）。spec 經過審查之後才實作。
3. **PR #45（ai-loop 自動化）：** 我仍然建議在 merge 之前做一次獨立的 security review，重點是 workflow 的權限、自動 push 或 merge 的能力、Mac gate evidence 的真實性（例如 `task.json` 和 evidence 的來源是否可以被偽造），以及 secret 的處理。

## 判定

```text
PR #49 INDEPENDENT SECURITY RE-REVIEW (remediation 6874410):
PASS

Reviewed head:
68744100c2890d05ca0e565f4d246ae1c773e951 (6874410; corrected from mistyped 68744107 per QA49-N3)

P49-M1 supervisor last-act self-group cleanup:
CLOSED — getpid/getpgrp leadership check + killpg(0, SIGKILL); libproc/PPID no longer gate the final act; wrong-group
  never signaled; new tests for identity failure, PPID race and wrong group; exercised natively 40/40

P49-M2 spec/implementation topology:
CLOSED — versioned self-anchor amendment supersedes B5 §§3.2/6/11.1–11.3 for this slice; linked from WORKFLOW.md;
  authorization now traceable in-repo

S49-2 unresolved report loss:
CLOSED — best-effort reporting, retained operator-wait ownership; test added

Native Mac acceptance:
PASS — committed evidence hash-bound to current files (14/14); independent rerun 14/14, exit 0, SIGKILL 20+20,
  max latency 0.0124 s, no residual processes

Unit tests / CI:
PASS — test_git_containment OK on 3.13 and 3.9.6; PR CI 3.9/3.14 pass

Evidence directory:
PASS — no secret patterns; repo-local absolute paths only in a private repo (NOTE: large sandbox-failure log)

Deferred (unchanged, non-blocking for PR #49):
S49-3, P49-L1 (must be handled in 2.2.2 design), P49-L2, P49-L3

Ready to merge PR #49:
YES from this security re-review — subject to the remaining independent functional QA / coordinator acceptance gates

May implement real git add (A11b.2.2.2):
NO — design spec + review first, after PR #49 merges

PR #45:
NOT REVIEWED (no new commits) — independent security review still recommended before merge
```

- 本報告是這一輪唯一 commit 的檔案，commit 到 PR #49 的 branch，**只新增了 `reports/` 底下的檔案**，不影響 tested hash。沒有修改 production 或 test，沒有 merge。
- acceptance 的 evidence 只保存在 session 的 scratchpad 中。
