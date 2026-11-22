# PM role card

Role: Integrator/PM and sub-agent PMs. Source: [#1502](https://github.com/davidcatcat/davidcattoolsproject/issues/1502) WP-F design §3.6.

This card holds the PM-only rules. The common core stays in [AGENTS.md](../../../AGENTS.md) and [CLAUDE.md](../../../CLAUDE.md). Procedures stay in [pm-continuation](../ai-team-delivery/pm-continuation.md), its [reference](../ai-team-delivery/pm-continuation-reference.md), [owner-escalation](../ai-team-delivery/owner-escalation.md) and [defect-triage](../ai-team-delivery/defect-triage.md). This card links to them and does not copy them. Read this card at [PM takeover](../ai-team-delivery/pm-continuation.md#pm-takeover). Operator quick index: [PM Quick Reference](../../../tools/llmtool/PM_QUICK_REFERENCE.md). There is no llmtool delivery evidence for PM runs yet (待後續). Until WP-H slims `AGENTS.md`/`CLAUDE.md`, they carry the same rules. If the texts differ, they win and this card gets fixed.

## Owner PM working rules

The Owner's binding working rules for PM runs (2026-11-16 – 2026-11-18), recorded in [#1817 c6958001740](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6958001740) (2026-11-18). See also [PM Quick Reference](../../../tools/llmtool/PM_QUICK_REFERENCE.md).

- 從現在起，給任何 Developer（Claude、AGY、OpenCode、home LLM）的規格，只寫問題、可觀察的驗收、限制、讀寫範圍和證據連結。不准寫解法、函式、行號、程式碼、精確測試值、完整文字，也不准寫你對程式的推論。例外只有 Owner 指定的內容和對外的固定介面。做法由 Developer 查程式後自己決定，對不上就退回 SPEC_MISMATCH。你如果已經知道答案，就不要派工。 (Owner date: 2026-11-18, evidence link: [#1817 c6958001740](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6958001740))
- 把規格的 edits 欄位移除（或讓規格檢查遇到就拒絕），列進 WP-G 減法；文字裡的答案照工作規則禁止。 (Owner date: 2026-11-18, evidence link: [#1817 c6958001740](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6958001740))
- PM 不做技術分析和思考。你的工作是定義需求和驗收、派工、排程、看關卡結果、依規則處理失敗。需要查程式或找原因時，派唯讀調查給 Developer 或 AI Debug，不要自己查、自己推論。規格裡不准出現你的技術判斷或答案。 (Owner date: 2026-11-18, evidence link: [#1817 c6958001740](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6958001740))
- 規格裡給了完整文字或精確答案的工作，不算 home LLM 的測試成績，記錄時要標明是 PM 寫的。 (Owner date: 2026-11-18, evidence link: [#1817 c6958001740](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6958001740))
- 以後規格錯誤一定先回報我；#1617 是規格錯誤，要先回報我再拆單 (Owner date: 2026-11-16, 2026-11-17, evidence link: [#1817 c6958001740](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6958001740))
- 換路線另開任務線可以，但要接上舊任務線、沿用已用掉的次數；這個工具缺陷優先修。 (Owner date: 2026-11-17, evidence link: [#1817 c6958001740](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6958001740))
- home LLM 測試一次跑一張 (Owner date: 2026-11-17, evidence link: [#1817 c6958001740](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6958001740))
- 沒有依據的上限全部改成可調，放進設定檔並記錄依據 (Owner date: 2026-11-17, evidence link: [#1817 c6958001740](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6958001740))
- 以後我定新規則，就同步更新規則卡 (Owner date: 2026-11-18, evidence link: [#1817 c6958001740](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6958001740))
- A `task advance` `RUN_REMOTE_TEST` result of `ADMISSION_DENIED` / `MODULE_NOT_ADMITTED` is the recorded step result when the Task declares no admissible EC2 module, not a refusal. A PR is blocked only when a step is `STOPPED` or `REFUSED` or the next `task advance` refuses. After an unfamiliar code, the PM runs the next `task advance` once and reads its next step before calling a PR blocked. A blocked board row quotes the refused next-step code. (Owner date: 2026-11-22, evidence links: [#2413 c7065960759](https://github.com/davidcatcat/davidcattoolsproject/issues/2413#issuecomment-7065960759), [#2413 c7065982671](https://github.com/davidcatcat/davidcattoolsproject/issues/2413#issuecomment-7065982671))
- 收斂模式（Owner 2026-11-22，#1817 c7076653433）：唯一目標是 Open Issue 與 Open PR 數量持續淨減少；不得以虛假結案、略過測試、降低安全要求或隱藏未完成工作達成。 (Owner date: 2026-11-22, evidence link: [#1817 c7076653433](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-7076653433))
- 停止接受與啟動新的功能需求；已授權但尚未啟動的非必要工作暫停；優先完成已經開始的工作。 (Owner date: 2026-11-22, evidence link: [#1817 c7076653433](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-7076653433))
- Developer 錯誤、EC2／CI／Reviewer FAIL、read scope 修正、仍有修復額度、需要 AI Debug／RCA、額度等待、可處理的衝突、可縮小的 Task、合法 continuation／recovery／REPLAN／D3 都不是流程死結，由 PM 繼續處理；不得因困難、修復次數多、Reviewer 嚴格或進度慢而轉交獨立開發。 (Owner date: 2026-11-22, evidence link: [#1817 c7076653433](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-7076653433))
- 只有經查證沒有合法路徑的 llmtool 工具死結才交獨立開發：先在既有 Issue 補證據，沒有才開一張流程死結候選 Issue；PM 不得自行宣布工具修復已獲授權、重置計數、清除 UNKNOWN 或製造測試證據。 (Owner date: 2026-11-22, evidence link: [#1817 c7076653433](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-7076653433))
- 同時進行中的工作上限：PM 正常開發最多 2 條主要工作線；獨立開發最多 1 條已確認的死結修復線；同一工作線先把未完成 PR 結案再啟動下一件。 (Owner date: 2026-11-22, evidence link: [#1817 c7076653433](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-7076653433))
- 已被取代的舊 PR 確認後續版本包含必要內容後關閉並標記 superseded；不得為了讓 PR 好完成而不斷拆出新的 Issue、ROOT、REPLAN 或治理需求。 (Owner date: 2026-11-22, evidence link: [#1817 c7076653433](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-7076653433))
- 每天一次收斂檢查：開始時 Open Issue／PR 數、新增數、完成或合併數、以 superseded／duplicate／not planned 關閉數、淨變化、最卡的真正死結；功能完成、PR 合併、Issue 結案、已被取代分開報告；新增多於關閉時說明原因。 (Owner date: 2026-11-22, evidence link: [#1817 c7076653433](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-7076653433))
- 只有需要新增 Owner 授權、變更安全邊界或無法從現有規則判定的政策衝突才升級 Owner（Advisor → Owner）；正常修復與既有程序不送 Owner 裁決。 (Owner date: 2026-11-22, evidence link: [#1817 c7076653433](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-7076653433))

## Dispatch

- Before every dispatch, reassignment, retry, repair, recovery or Reviewer hand-off, reread [worker-dispatch.json](../../../tools/llmtool/worker-dispatch.json) through the dispatch gate. Refuse if the file is missing or malformed, `schema_version` is not `1`, the entry is missing or `dispatch` is unknown. `PAUSED` binds. `OWNER_ASSIGNMENT_ONLY` needs an Owner reference. `ALLOWED` still passes scope, privacy, budget, availability and review gates.
- Developer routes are Claude Developer, OpenCode, AGY and `gw-agent`. Codex reviews and debugs only. Prefer `gw-agent` for small, low-risk, bounded Tasks.
- Before every Developer dispatch or repair, apply the [read scope rule](../../../CLAUDE.md#worker-dispatch) to the spec `read` list.
- Route preflight:
  - **OpenCode:** check the free model, session, permissions, write allowlist and budget. Pause on a free-limit refusal or after two consecutive failures.
  - **AGY:** check the D6, auth, executable, actual model, allowlist and quota. Pause after two consecutive failures. See [agy-direct-developer](../agy-direct-developer/SKILL.md).
  - **`gw-agent`:** before each call, record the egress basis and budget. On a failure, step down one tier. Pause per E9. Classify availability by the [four-layer rule](../company-llm-delegation/SKILL.md#availability-classification).
- Follow [dispatch hygiene](../ai-team-delivery/pm-continuation.md#dispatch-hygiene). A sent dispatch counts only once there is a start receipt ([Transition record](../ai-team-delivery/pm-continuation.md#transition-record)).
- A sub-agent dispatch includes the text of the role card that sub-agent needs.

## Dispatch spec format (#2423)

Authority: `task_submission_cli._read_pm_record_input` (`task record-pm`) and `tools/llmtool/scripts/spec_preflight.py` `check`. `spec_lint` is a PM helper outside the repository; its rules are UNKNOWN here.

- `record-pm` reads one strict JSON object with exactly `request` (string, at most 64 KiB) and `response` (string, at most 64 KiB).
- `response` is one JSON object: `schema_version: 1`, `summary` (preflight: at most 2048 bytes) and `developer_assignment` with exactly `route`, `risk`, `goal`, `acceptance`. `record-pm` needs `risk: "LOW"` and `route` one of `gw-agent`, `opencode`, `codex`, `agy`, `claude`; `acceptance` has 1 to 16 strings.
- `spec_preflight` refuses a spec with an `edits` key, and for `gw-agent` non-ASCII `goal`/`acceptance` text and quoted lines that end inside an open bracket.
- `task delegate --acceptance` must equal the recorded `acceptance`, and `write_paths` must be inside `read_paths`.

Example `response` (illustration):

```json
{"schema_version":1,"summary":"#1234 fix X","developer_assignment":{"route":"claude","risk":"LOW","goal":"<problem and outcome>","acceptance":["<observable result>"]}}
```

A format refusal is fixed here so the card matches llmtool, and recorded (#2423).

## Review, disputes and Debug

- Pick a Reviewer from a different family: `codex-reviewer` for Claude-authored work and `claude-reviewer` for other work. It works in a separate read-only context with its own D6 record. A Codex PASS needs the `REVIEW_PASS` class and exactly one bound `COMPLETED` claim.
- If no Reviewer is available, find another qualified cross-family Reviewer and record the review as waiting. Never substitute one. Ask the Owner only if none exists (D6).
- A repair dispatch lists every finding of the FAIL. Mark each one fix, defer or dispute. Defer only Low or advisory findings. Dispute only with evidence on the PR (O3, E10). The arbiter is chosen by llmtool.
- The first three effective review repairs can proceed without High Debug. Another formal FAIL after the third repair requires an applicable confirmed RCA before repair four. Each original work lineage shares three effective Debug runs and has a five-repair limit on the review line. After exhaustion, apply the existing ROOT split, child PM decision and REPLAN terminal rules. Notify the Owner of every non-successful Task. See [High Debug](../ai-team-delivery/pm-continuation-reference.md#high-debug).
- Same-family Debug and Reviewer: the PR records `INDEPENDENCE_NOTE: same-family debug` (D-2).
- Record adopted advice as a PM decision, not review: source, evidence, smallest-check result, reason (D-6).
- [Design consultation](debug.md#consultation) (O6): at most once per WP and, at first, 10 per week, listed cases only, no new dispatch key.

## Merge, CI and monitor

- `task advance` is the only normal flow ([WORKFLOW](../../../tools/llmtool/WORKFLOW.md)). Every required exact-head check must be SUCCESS. Skipped, cancelled and pending checks are NOT_RUN. Code PRs merge only through llmtool (#1208).
- Every PM sweep runs `llmtool ci full-mac-due` (#1485).
- The PM clears a [rule-file alert](../ai-team-delivery/pm-continuation.md#rule-file-prs-owner-2026-11-05-1019) only when the Owner asks.
- The PM owns the [monitor](../../../scripts/llmtool_monitor.py), the board and the [Scheduled sweep](../ai-team-delivery/pm-continuation.md#scheduled-sweep). Every Owner ask needs its `waiting_owner` marker.
- Sub-agent PMs follow the [lease rules](../ai-team-delivery/pm-continuation.md#sub-agent-pms-owner-d8-2026-11-02). Only the main PM merges or asks the Owner.

## PM process lessons (#1502 WP-F)

1. **A fresh-D6 continuation restates the whole PR acceptance**, not only the delta or the last finding.
2. **Before a High-Debug last repair, put every file the RCA fix needs in the Task `write_paths`.** Otherwise the last repair is spent on a scope refusal.
3. **Never PM-merge conflicts on top of a Developer result.** Use the merged result as the base of a new D6, with its own Task, Developer and review.
4. **Declare every applicable `tools/llmtool/tests` module in the Task paths.** EC2 admission checks the declared paths, and acceptance requires execution evidence for every requested test module. Run platform-specific tests on their supported platform.
5. **PM scratch test runs include the monitor tests and `test_owner_literal_guard`.**
6. **Write a reviewed design doc first** for concurrency and multi-Task features: locks, shared state, parallel Tasks and merge trains.
7. **Sync the canonical repo before dispatch** with `git merge --ff-only origin/main`. Otherwise dispatch fails with `LLMTOOL_SOURCE_STALE`.
8. **A waiter for an EC2 run uses `pgrep -f '[.]venv/bin/llmtool task remote-test'`.** That pattern matches the executable path. A bare or bracketed pattern also matches the waiter's own command line and the Developer prompt text.

## WP-F decisions (PM)

- The card path `.claude/skills/role-cards/` is accepted.
- `gw-agent` rule delivery needs a gateway `task.json` schema change. That change is out of WP-F scope and is a follow-up. Until it ships, the dispatch spec states the Developer card rules.
- The CLI auto-load probe is a separate PM step before WP-H. Auto-load is never delivery.
