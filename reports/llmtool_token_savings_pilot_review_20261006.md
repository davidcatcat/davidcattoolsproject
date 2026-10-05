# llmtool PR #36 first paired token pilot — review 與 blind grading（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #36 `bench(llmtool): record first paired token dogfood pilot`，branch `bench/llmtool-token-savings-pilot-20261006`，**head `3cf138228f5dc62cd977315b8bfa06cb87b55e55`**。
- **Base：** `main @ f4d1019`；ahead 8。
- **本輪新增的 commit：**
  - `1024ebf`：pin accounting review execution rules
  - `f85413b`：**pre-registration**（fixture／gold／schema／task／helper，2026-10-06T03:06:11+08:00）
  - `3cf1382`：pilot 結果
- **變更：** 只有 reports（pilot report、`reports/token_benchmark/20261006/` 底下的 fixture、gold、schema、task、`evidence_reader.py`、`validation.py`、`preregistration.json`、`results.json`、blind grading package）。沒有 production、tests 或 CI 的變更。
- **方式：**
  1. 閱讀 pilot report、`results.json`、helper，以及 pre-registration 的時間順序。
  2. **重跑 deterministic gold checks**：用 `validation.grade()` 檢查 6 個匿名輸出。
  3. **Blind grading：** 我已經讀過 PR 描述（含 token 數字），自己不再 blind，所以交給**獨立的 subagent**。它**只能讀** grading package 和 3 組 fixture／gold／schema／task，禁止讀取 results、report、PR、git。它聲明：「Blinding: I read only the permitted files」。A／B 的對應只存在 PR 作者本機的暫存目錄，grader 和我都沒有接觸。
  4. **確認一個混淆變因：** 在公司 gateway 上，用**同一個 synthetic B01 fixture** 做 2 次診斷呼叫（thinking 關閉 vs provider 預設）。這不是 benchmark session，也不影響 Codex 的測量。

## 結論

**PR #36（pilot 記錄）：APPROVE，同時補上 blind grading 的結果和 3 個解讀上的 finding。**

```text
Blind grading: 6/6 outputs PASS; B01/B02/B03 pair parity EQUIVALENT
Quality-approved pairs: 3/3 (B03 literal miss judged semantically equivalent; gold unchanged)
Primary KPI (input+output), all 3 quality-approved pairs:
  control 54,849  vs  dogfood 112,144  ->  codex_tokens_saved = -57,295 (-104.46%)
Can claim Codex token savings: NO — this n=1 pilot shows dogfood INCREASED Codex tokens
```

這是一個**有效的負面描述性結果**（n=1），不能推廣，也不能建立 routing 政策。

## 1. 流程和契約的確認（PASS）

| 項目 | 結果 |
|------|------|
| pre-registration 在第一個 session 之前 | `f85413b` 在 03:06 commit；所有 session 都綁定這個 SHA；fixture 和 gold 的 digest 都已列出 |
| 6 個 session 的設定相同 | 同一個 Codex 0.159.2（每次都確認）、`gpt-6.1-sol`／high／default、read-only、`--ephemeral --ignore-user-config --ignore-rules`、相同的 `--output-schema` |
| 單一 turn 和 accounting | 每個 session 都只有一次 `thread.started`、`turn.started`、`turn.completed`，exit 0；欄位集合不變；`cache_write = 0`；使用已接受的 `input+output` mapping。符合 R1–R3 |
| raw evidence 的驗證（M1） | dogfood 的初始 prompt 沒有放入完整的 fixture；每個 dogfood 都只用一次 `evidence_reader.py` 讀取，並記錄 byte range（3 個都讀了整份 fixture，`[0, N)`）。證據來自實際的 `command_execution` 輸出，不是 model 的自我回報 |
| 公司呼叫（S1） | 5 次 = 3 次成功 + 2 次失敗（length），2 次手動重試；失敗的那 2 次 usage 是 unknown（不填 0），所以 company 的 total 和 mixed delta 都是 unknown。如實記錄 |
| 執行順序（S2） | B01 C→D、B02 D→C、B03 C→D，事先固定 |
| deterministic grading（M3） | 我重跑 `validation.grade()`：B01 A／B 和 B02 A／B 全部 PASS；B03 有一邊 `required_facts_terms_literal=false`，另一邊全部 PASS。**和 report 完全一致** |
| 範圍 | 沒有 router、budget、A11b.2、A11c，沒有第二次重複，沒有 merge |

## 2. Blind grading 的結果（獨立 grader，只讀允許的檔案）

| Output | Verdict | 重點 |
|--------|---------|------|
| B01-A／B01-B | PASS／PASS | 3 個群組（timeout、assertion、unknown）、4 個 test 各出現一次、unknowns 保留、沒有捏造 identifier、root cause 只以否定的方式出現 |
| B02-A／B02-B | PASS／PASS | `UPSTREAM_TIMEOUT` × 3、9 個事件都依序列出、`RECOVERY_OBSERVED` 不被當成修復的證據、沒有 command |
| B03-A／B03-B | PASS／PASS | 10 個 entry 的 category、source、locator 都正確；B03-A 的「Queue depth was 12…」「Consumer count was 2」**在語意上等同**於 gold 的 `queue_depth`／`consumer_count`（gold 沒有修改，literal 的結果照實保留） |

**pair parity：** B01 EQUIVALENT；B02 EQUIVALENT；B03 EQUIVALENT（B 的措辭稍微更明確，但不影響 PASS）。

**結果：** B03 的 literal miss 在語意上被判定為等同，所以 **3 對都是 quality-approved**。

| Fixture | Control | Dogfood | Codex saved | % |
|---------|--------:|--------:|------------:|--:|
| B01 | 18,452 | 37,517 | −19,065 | −103.32% |
| B02 | 17,582 | 39,294 | −21,712 | −123.49% |
| B03 | 18,815 | 35,333 | −16,518 | −87.79% |
| **全部 3 對** | **54,849** | **112,144** | **−57,295** | **−104.46%** |

Company 的 total 和 mixed delta 仍然是 **unknown**（失敗的嘗試沒有 usage）。

**blind grading 的限制：** 只有一位 grader（Claude subagent，和被測的 Codex 是不同的 model 家族）。grader 指出 B02 的 literal check「3」不具鑑別力（`req-03`、`prior_timeouts=3` 這些子字串就能滿足），屬於 gold 的設計弱點（P4）。

## 3. Findings（解讀和下一輪的設計）

### P1（Major，解讀）：這個 pilot 量到的主要是固定開銷，不是 delegation 的效益

- 每個 session 的 base context 大約 **17–18k input tokens**（system 加上工具描述；control 只有一次 request）。
- dogfood 依照 M1 的規則**必須讀取原始 evidence** → 多了一次 tool call → **多一次 Responses request，又重送一次大約 18k 的 base context**（dogfood 的 `cached_input_tokens` 是 18–29k，正是這個重送的部分）。
- fixture 只有 1.2–1.4 KiB（大約幾百 tokens），**遠小於這個固定開銷**；而且公司 JSON 加上驗證結果之後，dogfood 的初始 prompt 反而比較大（例如 B02 是 4,573 bytes 對 2,043 bytes）。
- **結論：** 「小型 evidence 加上必須回查 raw」的情境下，dogfood 對 Codex 是**結構性的淨增加**。這個結論是有效的，但**完全不能推論到大型 log 或測試輸出**，而那才是 dogfood 本來想省的情境。

### P2（Major，混淆變因）：公司呼叫沒有關閉 thinking

- run 使用的是 `default` profile 加上 CLI 的 `--base-url`／`--model`，**沒有** `provider_options.enable_thinking: false`（公司的 config 範例和 dogfood 文件都使用 false）。
- **我的診斷（同一個 B01 fixture、`test-triage`、`max_tokens` 8192）：**

  | 設定 | completion tokens | 時間 | 回應 |
  |------|------------------:|-----:|------|
  | `enable_thinking: false` | **385** | 1.9 秒 | 合法 JSON |
  | provider 預設 | **1,999** | 8.4 秒 | 合法 JSON |

- 這解釋了 pilot 中 2 次 `MODEL_OUTPUT_INCOMPLETE`（預設 `max_tokens` 2000 被耗盡）、手動重試、company completion 高達 2.9k–4.8k，以及 company 的延遲 15–38 秒。
- **影響：** 不影響 Codex 的主要 KPI（Codex 收到的是同樣的 JSON），但 **company 的 token、延遲、重試次數都不具代表性**，mixed volume 也因此失真。
- **下一輪：** pre-registration 必須固定公司 profile 的 `enable_thinking: false`（或明確記錄設定），並把它列為 company 設定的一部分。

### P3（Medium，解讀）：cache 和順序的影響很大

- **不是 pre-registered 的指標，只作為描述**：用 Codex 自己的 `blended_total = (input − cached) + output` 計算：B01 −1,017、B02 −3,152、**B03 +12,666**，三對合計 +8,497。B03 的 dogfood 有 29,184 cached tokens（跑在 control 之後，cache 是熱的）。
- 這表示主要 KPI 的負值有很大一部分是被 cache 的重送 prefix，而且**執行順序會大幅改變非 cached 的部分**。
- **這不能當成節省的宣稱**（不是事先登記的 KPI，n=1，而且受順序影響）。但下一輪如果要比較 cost-relevant 的指標，必須**事先登記**一個 secondary metric，並使用 counterbalanced 的重複。

### P4（Low）：gold 的 literal check 不具鑑別力

B02 的「3」會被 `req-03`、`prior_timeouts=3` 這類子字串滿足。建議用結構化的比對（例如 repeated_patterns 中 UPSTREAM_TIMEOUT 的 request 數等於 3）。

### P5（Low）：建議 PR 作者把 blind grading 的結果併入 pilot report

在 report 補上一段，引用本報告和 grader 的「Blinding: I read only the permitted files」聲明，把 B03 從「INVALID pending blind」更新為「quality-approved（語意等同）」，並更新 quality-approved aggregate。原始的 literal 結果照實保留。

## 4. 下一輪建議（不在本 PR 執行）

1. **fixture 的大小要接近實際使用**：例如數十到數百 KB 的 CI log 或 test 輸出，讓 raw evidence 遠大於大約 18k 的固定開銷。並事先登記「只讀取 company 指出的範圍」與「讀取全部」兩種 M1 的做法。
2. 固定公司 profile 的 `enable_thinking: false`（P2）。
3. 事先登記 secondary metric（例如 non-cached input + output），並使用 counterbalanced 的多次重複（P3）。
4. 修正 gold 的鑑別力（P4）。如果可以，使用 2 位 grader 並記錄彼此的一致度。

## 判定

```text
PR #36 REVIEW: APPROVE (accurate, contract-compliant pilot record)
Blind grading: 6/6 PASS, 3/3 pairs EQUIVALENT (grader read only permitted files)
Quality-approved Codex delta: -57,295 tokens (-104.46%), n=1 per fixture, descriptive only
Can claim Codex token savings: NO (observed increase)
Major interpretation findings: P1 fixed-overhead dominated; P2 company thinking not disabled
New blockers for this PR: 0 (recommend P5 report update before merge)
```

- 本報告 commit 到 PR #36 的 branch（只新增 `reports/` 底下的檔案）。
- 這一輪沒有執行 benchmark session，只有 2 次 company 診斷呼叫，使用 synthetic fixture，而且沒有 Codex 的測量。
- 沒有 merge，也沒有開始 A11b.2 或 A11c。
