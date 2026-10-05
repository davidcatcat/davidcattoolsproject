# llmtool QG-QA1 Targeted Re-QA round 2 — PR #31 R-1（2026-10-05）

## 範圍與方法

- **審查對象：** PR #31 `fix/llmtool-qg-qa1-required-bugs-20261005`，HEAD `1409eac`，以 `origin/main` `fe169af` 為基礎（ahead 7、behind 0，沒有 base drift）。
- **本輪新增的 commit：**
  - `c2662ad`：fix(llmtool): preserve file error domain context
  - `1409eac`：R-1 validation report
- **變更：** `feedback_files.py`、`feedback.py`、`benchmark.py`、`benchmark_cli.py`、`orchestration_cli.py`，以及新增的 `tests/test_qg_qa1_file_context.py`。
- **對照：** 上一輪的 [targeted re-QA](llmtool_qg_qa1_targeted_rereview_20261005.md)：Q-BUG-1 FAIL，原因是 R-1（檔案層級的錯誤仍顯示 `feedback file …`）。
- **方式：**
  - 在 canonical repo 進行，沒有使用 worktree 或 clone。
  - 逐行閱讀 `8fb501f..1409eac` 的 diff，並用 `git grep` 列出 `read_feedback_text`、`read_feedback_file`、`evaluate_regression` 的**所有呼叫位置**。
  - 用 public CLI 重跑上一輪 §5 標成 FAIL 的每一列，另外補了 `benchmark live` 和 suite entry 的 case。
  - probe 都在 scratchpad 內、以 scratchpad 為工作目錄執行（`$Q/reqa/R1fix.*`），使用 `env -i` 和獨立的 HOME，provider 是會計算 request 數的 local mock。
  - **沒有把 Codex 的 validation report 當成證據。**

## 結論：R-1 確認修好 → Q-BUG-1 PASS → TARGETED RE-QA PASS，APPROVE

## 修正方式（從 source 確認）

- `read_feedback_text`、`_parent`、`_read` 都新增了 keyword-only 的 `context` 參數，預設是 `'feedback'`。所有檔案層級的訊息都改成用 `context +` 開頭，包括 traversal、explicit file、operation failed、regular file、exceeds 1 MiB、must be UTF-8。
- `evaluate_regression` 新增 `context` 參數，`evaluate_benchmark` 傳入 `'benchmark output'`。
- **所有不屬於 feedback 的呼叫位置都傳入了固定的標籤：**

  | 呼叫位置 | 標籤 |
  |---------|------|
  | orchestrate 的 `--plan` | `orchestration plan` |
  | benchmark 的 `--spec` | `benchmark spec` |
  | benchmark 的 `--case`、suite entry 的 case | `benchmark case` |
  | benchmark 的 `--manifest` | `benchmark suite` |
  | benchmark 的 `--output-file`、suite entry 的 output、live 的輸出 | `benchmark output` |
  | benchmark 的 `--runtime-file` | `benchmark runtime` |

- feedback 指令（capture、regression、review、show、validate）的呼叫沒有傳入參數，維持 `feedback`。
- 標籤是 caller 寫死的常數，不會包含檔名或輸入資料，所以不會產生 echo。
- 檢查的條件、上限和流程都沒有改變。

## 重跑結果（public CLI）

| Case | 結果 | rc | 判定 |
|------|------|----|------|
| orchestrate：plan 超過 1 MiB | `orchestration plan file exceeds 1 MiB` | 1 | PASS |
| orchestrate：不是 UTF-8 | `orchestration plan file must be UTF-8` | 1 | PASS |
| orchestrate：找不到檔案、是目錄 | `orchestration plan file operation failed; details omitted` | 1 | PASS |
| orchestrate：traversal | `orchestration plan file paths cannot contain parent traversal` | 1 | PASS |
| benchmark `--spec`：超過上限、不是 UTF-8 | `benchmark spec file exceeds 1 MiB`／`… must be UTF-8` | 1 | PASS |
| benchmark `--case`：找不到檔案 | `benchmark case file operation failed; details omitted` | 1 | PASS |
| benchmark `--manifest`：找不到檔案、不是 UTF-8 | `benchmark suite file …` | 1 | PASS |
| benchmark `--output-file`：含有 ESC、找不到檔案、超過上限 | `benchmark output text contains unsupported controls`／`benchmark output file …` | 1 | PASS |
| benchmark `--runtime-file`：不是 UTF-8、找不到檔案 | `benchmark runtime file …` | 1 | PASS |
| **benchmark live**：mock 回傳含有 ESC 的輸出 | `benchmark output text contains unsupported controls`（**剛好 1 次 request**） | 1 | PASS |
| suite entry：output 含有 ESC、case 找不到 | `benchmark output text …`／`benchmark case file …` | 1 | PASS |
| 對照組：合法的 spec 加上 candidate | `PASS` | 0 | PASS |
| **對照組：feedback** 的 validate（超過上限、不是 UTF-8）、regression（輸出含有 ESC） | `feedback file exceeds 1 MiB`／`feedback file must be UTF-8`／`feedback text contains unsupported controls` | 1 | PASS（A7 的措辭沒有被破壞） |

- 所有輸出中，marker `MARKERZQ` 都**沒有**被 echo，也沒有出現 traceback 或 `/Users/`。
- 錯誤的 case 都沒有發出 request；唯一的 1 次 request 來自 `benchmark live`。

## Regression 與 CI

| 項目 | 結果 |
|------|------|
| Python 3.13.3 | **981 tests OK**（1 skipped），比上一輪多 16 個 |
| Python 3.9.6 | **981 tests OK**（1 skipped） |
| GitHub Actions（`1409eac`） | push `37297331907` success、PR `37297336386` success，3.9 和 3.14 都是 pass |
| F1、D-34b、DG-1、launcher、journeys、並發 | 本輪沒有修改到相關的程式碼（只改了 feedback、benchmark、orchestration 的標籤）。上一輪的 PASS 結果仍然適用 |
| working tree | 被追蹤的檔案是乾淨的，只有原本就有的未追蹤檔案 |

## Final gate

```text
Q-BUG-1: PASS
F1: PASS
D-34b: PASS
D-34a: PLATFORM LIMITATION
DG-1 ADJACENT REGRESSION: PASS
LAUNCHER REGRESSION: PASS
KEY USER JOURNEYS: PASS
NEW CONFIRMED FUNCTIONAL BUGS: 0
NEW SECURITY REVIEW CANDIDATES: 0
TARGETED RE-QA RESULT: PASS
PR #31 MERGE RECOMMENDATION: APPROVE
READY FOR COORDINATOR FINAL REVIEW: YES
```

**剩下的非 blocking 項目**（見上一輪報告）：
- Y-F1：launcher 自己的 import 那一行；
- E-1：orchestration 每一步執行前才 resolve profile；
- N-1：部分 benchmark 的 schema 錯誤只寫 "benchmark object"；
- N-2：TERM／HUP 結束時，沒有 `--verbose` 就看不到 run_id；
- D-34a 是 platform limitation；公司 gateway 的 live smoke 在上一輪是 BLOCKED。

APPROVE 代表 Q-BUG-1、F1、D-34b 都已關閉，沒有新的 blocking regression，exact-head CI 通過。A11b 仍然未開始；這一輪沒有 merge，PR #31 保持 Draft。
