# llmtool PR #37 Round 2 fixture/gold preregistration — review（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #37，branch `bench/llmtool-token-savings-large-evidence-20261006`，**head `4f7531e`**（base `main @ 65df409`）。
- **本輪新增的 commit：**
  - `41f267c`：preregistration handoff
  - `4f7531e`：preregister round 2 fixtures and gold
- **變更（只有 reports，19 個檔案）：**
  - preregistration report 和 handoff；
  - `token_benchmark/20261006_round2/stage_a/`：L01、L02、L03 的 fixture、gold、output_schema、task；
  - `generator.py`、`coverage.py`、`selector.py`、`self_check.py`、`preregistration.json`。
- **方式：**
  1. 先靜態檢查各個 script 的 import 和 IO，確認沒有 network、subprocess 或 LLM 呼叫，才執行。
  2. **重跑 `self_check.py`**（在 `/private/tmp` 執行，`-B`，不寫入 repo）：
     - Python **3.9.6**（有 jsonschema）：**20／20 OK**；
     - venv Python **3.13.3**：19／19 OK，加上 1 個因為 venv 沒有 `jsonschema` 而無法執行的 test（和 report 的說明一致）。
  3. **獨立重算** `preregistration.json` 中全部 16 個 artifact 的 SHA-256：**0 個不一致**。
  4. 確認 fixture 的大小和內容樣式。
- 這一輪沒有呼叫 Codex 或 company，也沒有執行 Stage A。執行之後 repo 的被追蹤檔案沒有改變。

## 結論：APPROVE（preregistration 完整，Stage A 在取得明確執行授權之後可以開始）

## 驗證結果

| 項目 | 結果 |
|------|------|
| fixture 大小 | L01 **49,152**、L02 **65,536**、L03 **131,072** bytes，等於 48／64／128 KiB 的 tier |
| 能否重現 | `self_check` 在暫存目錄重新產生，**byte-identical**；換 seed 之後內容會改變；generator 使用固定的 LCG 和 seed，不依賴時間或亂數 |
| artifact 雜湊 | 16／16 和 `preregistration.json` 一致 |
| gold 隔離 | generator、coverage、selector 的 signature 都沒有 gold 參數；AST 中沒有 `gold.json` 字串；runtime 會攔截 `open`、`Path.read_bytes`、`Path.read_text` 對 gold 的讀取，測試通過 |
| coverage（M2） | L01：anchored 的 FAILURE 全集，加上 missing、duplicate、unexpected、file membership。L02：EVENT tuple、從實際 event 解析出的次數（拒絕 prose 中的數字）、時間順序。L03：strict A4 parser，加上引用存在且唯一、在 selection 之後檢查是否被包含，**不宣稱語意上的完整** |
| selector | ±2 行、gap ≤ 2 合併（gap 3 不合併）、依來源排序、32 個接受、33 個拒絕、上限邊界剛好接受、多 1 byte 拒絕、超過上限或等於整份 fixture **一律拒絕，不會截斷**、未知的 anchor 拒絕 |
| 上限 | `min(32768, floor(bytes/5))`：9,830／13,107／26,214 bytes |
| 提供方式（M1） | 凍結的 selected raw bytes 原文直接放進初始 prompt；預期 0 次 tool call；違反時標成 `DELIVERY_CONTRACT_VIOLATION` |
| 預算 | 每一對的 allowance 固定為 S_i × 3：97,593／113,976／179,514；每一對完成之後才扣除實際用量；剩餘不足就標成 `BUDGET_CEILING_NOT_STARTED`；不因結果是否有利而停止 |
| company | 獨立的 HOME，`enable_thinking=false`、temperature 0、max_tokens 4096；實際送出的 Request 必須再次證明 thinking 是關閉的；固定的 company instructions 不含 gold 或範例答案；不會中途調高上限 |
| 評分 | 兩位 blind grader，只能讀取匿名輸出、fixture、gold、schema 和 task；意見不一致時不計入；A／B 的對應在執行時私下隨機產生 |

## 我上一輪建議的處理情況

| 建議 | 處理 |
|------|------|
| 1 cache 背景 | 選擇**只記錄、不操控**：每個 session 記錄前一個 session 的 ID、fixture、variant、間隔時間和 `cached_input_tokens`，不使用 nonce。可以接受，並且明確寫出「fresh session 不等於 cold cache」 |
| 3 每一對的 allowance | 已固定為 deterministic 的規則 |
| 4 fixture 的真實性 | 雜訊行使用了 timestamp、service、shard、op_id、duration、DeprecationWarning 等接近真實的格式，目標行是 canonical 格式。N1 的限制也寫進了 report |
| 5 L03 的長度 | 必要的語意內容限定為 7 個目標，不需要輸出 661 筆 inventory。但 company 在 4096 tokens 內能否完成仍是 **NOT TESTED**（見 R1） |
| 6 兩位 grader | 已採用 |

## 剩下的風險（non-blocking，執行時注意）

- **R1（L03 最可能失敗）：** L03 的 company 輸入有 128 KiB，其中有 661 筆 inventory 記錄。`context-pack` 有兩個風險：
  - (a) 輸出超過 4096 tokens → `finish_reason=length`；
  - (b) 引用太多 inventory 記錄 → selector 合併前超過 32 個範圍，或者超過上限 → 被拒絕。

  依照凍結的規則，這兩種情況都只能標成 company triage 失敗或 `SELECTIVE_READ_CAP_INSUFFICIENT`，不能調整。這是誠實的設計，但建議在報告中**預先說明 L03 失敗的可能性**，避免結果出來之後被解讀成方法本身失效。
- **R2：** venv 沒有 `jsonschema`，所以 schema 的 metaschema test 只能在 `/usr/bin/python3` 執行。建議執行 Stage A 前，在同一台 Mac 上再用 3.9 執行一次 `self_check`，作為執行前的 gate。
- **R3：** company 會讀取完整的 fixture（17–46k prompt tokens），mixed volume 很可能是負的。這已經在 preflight review 的 O3 指出，報告時請和 Codex 的節省並列呈現。

## 判定

```text
PR #37 PREREGISTRATION REVIEW: APPROVE
Byte-identical regeneration: VERIFIED (self_check 20/20 on Python 3.9; 19/19 + 1 env-limited on venv)
Artifact digests: 16/16 match
Gold isolation (generator/coverage/selector): VERIFIED (static + runtime guard)
Selector/coverage/caps/budget/delivery contracts: frozen and deterministic
Stage A may proceed: YES — only after explicit execution authorization (per the stop gate)
New blockers: 0 (R1 L03 length/over-citation risk to state up front)
```

- 這是只有 report 的 PR（`reports/` 不會觸發 CI，是預期的行為）。
- 本報告 commit 到 PR #37 的 branch。這一輪沒有呼叫 Codex 或 company，沒有執行 Stage A，沒有 merge，也沒有開始 A11b.2 或 A11c。
