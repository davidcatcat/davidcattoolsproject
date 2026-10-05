# Codex CLI 0.159.2 token accounting mapping — independent review（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #36，branch `bench/llmtool-token-savings-pilot-20261006`，**head `3f1270b2ea66ccc7d75a46d21e4effafdd8f5679`**（ahead 5、behind 0，相對 `main @ f4d1019`）。
- **本輪新增的 commit：**
  - `df01fed`：accept Codex 0.159.2 token accounting mapping
  - `05026cc`：record accepted Codex usage mapping
  - `3f1270b`：add review handoff
- **重點檔案：**
  - [`llmtool_codex_01592_usage_mapping_20261006.md`](llmtool_codex_01592_usage_mapping_20261006.md)（mapping decision）
  - `llmtool_codex_01592_usage_mapping_review_handoff_20261006.md`（handoff）
  - pilot report 的狀態更新
- **獨立驗證的方式：** **不依賴 PR 的說法**，自己用 GitHub API 讀取 upstream `openai/codex` 在 tag **`rust-v0.159.2`**（tag object `8b9fa496…`）的原始碼：
  - `codex-rs/exec/src/exec_events.rs`
  - `codex-rs/exec/src/event_processor_with_jsonl_output.rs`
  - `codex-rs/protocol/src/protocol.rs`
  - `codex-rs/codex-api/src/sse/responses.rs`
  - `codex-rs/app-server-protocol/src/protocol/v2/thread.rs`
- 這一輪沒有執行 Codex、company 呼叫或 benchmark。

## 結論

**CODEX 0.159.2 TOKEN ACCOUNTING MAPPING: PASS**

```text
mapping valid: YES
double-counting rule valid: YES
fresh single-turn restriction sufficient: YES (with notes R1–R3)
new blocker: 0
B01–B03 pilot may proceed: YES — after the remaining preconditions listed in the mapping
  report (fixture/gold pre-registration, fixed run order, identical settings for all six
  sessions, company/VPN preflight, deterministic validation, raw-evidence verification,
  blind grading). No token-savings claim exists until pairs are actually measured.
```

## 原始碼證據（rust-v0.159.2）

### 1. 公開的 JSONL usage 沒有 `total_tokens`

`exec_events.rs` 的 `pub struct Usage`（第 61–73 行）只有 `input_tokens`、`cached_input_tokens`、`cache_write_input_tokens`、`output_tokens`、`reasoning_output_tokens`。這和 preflight 觀察到的結果一致。

### 2. 各欄位和 upstream Responses usage 的對應

`codex-api/src/sse/responses.rs` 第 118–151 行：

```rust
struct ResponseCompletedUsage { input_tokens, input_tokens_details, output_tokens,
                                output_tokens_details, total_tokens, ... }
impl From<ResponseCompletedUsage> for TokenUsage {
    input_tokens:             val.input_tokens,
    cached_input_tokens:      input_tokens_details.cached_tokens,
    cache_write_input_tokens: input_tokens_details.cache_write_tokens,
    output_tokens:            val.output_tokens,
    reasoning_output_tokens:  output_tokens_details.reasoning_tokens,
    total_tokens:             val.total_tokens,
}
```

- `cached_input_tokens` 和 `cache_write_input_tokens` 都來自 **`input_tokens_details`**，也就是 `input_tokens` 的 breakdown。
- `reasoning_output_tokens` 來自 **`output_tokens_details`**，也就是 `output_tokens` 的 breakdown。
- `total_tokens` 是 upstream 回報的值，被 Codex 保留在內部，但沒有放進公開的 JSONL。

### 3. Codex 自己也把 cached 當成 input 的子集

`protocol.rs` 第 2430–2437 行：

- `non_cached_input() = input_tokens − cached_input()`
- `blended_total() = non_cached_input + output_tokens`

只有在 cached 是 input 的子集時，這樣的計算才成立。

### 4. 累加的方式一致

`TokenUsage::add_assign`（第 2467–2474 行）會**同樣地**累加 input、cached、cache_write、output、reasoning 和 total。如果每次回應的 `total = input + output`，累加之後仍然成立。

### 5. `turn.completed.usage` 是 thread 的累計值

- `event_processor_with_jsonl_output.rs`：`usage_from_last_total()` 讀取 `ThreadTokenUsage.total.*`（第 118–127 行）；收到 `ThreadTokenUsageUpdated` 時更新（第 509–510 行）；在 `TurnStatus::Completed` 時輸出 `TurnCompleted { usage }`，然後 `InitiateShutdown`（第 525–536 行）。
- `thread.rs` 第 1918–1920 行：`ThreadTokenUsage { total: TokenUsageBreakdown, last: TokenUsageBreakdown, … }`。
- 也就是說，公開的 usage 是**整個 thread 的累計值**，不是單一 turn 的差額。在全新、單一 turn、ephemeral、不 resume 的 `codex exec` 中，thread 的累計值就等於那一個 turn（包括這個 turn 內所有的 Responses 呼叫，例如使用工具時的多次 request），所以可以直接比較。
- 在 resume 或多個 turn 的情況下，它是累計值，**不能**當成 per-turn 的值。這證實 mapping report 的範圍限制是必要的，而且有原始碼依據。

### 6. OpenAI Responses 的語意

`input_tokens_details.cached_tokens` 是 input 的子集，`output_tokens_details.reasoning_tokens` 是 output 的子集，`total_tokens = input_tokens + output_tokens`。

## 逐項確認

| 要求 | 判定 | 依據 |
|------|------|------|
| `codex_comparable_total = input_tokens + output_tokens` | **valid** | 用同一個 usage 物件裡兩個公開的頂層欄位，重建被省略的 upstream `total_tokens`（§2、§4、§6） |
| cached 已經包含在 input，不能再加 | **valid** | cached 來自 `input_tokens_details`；`non_cached_input = input − cached`（§2、§3） |
| reasoning 已經包含在 output，不能再加 | **valid** | reasoning 來自 `output_tokens_details`（§2） |
| cache-write 不加入 total | **valid** | cache-write 來自 `input_tokens_details`，是 input 的 breakdown（§2）；見 R2 |
| 不能延伸到 resume 或多個 turn | **valid 而且必要** | 公開的 usage 是 thread 的累計 `total`（§5） |
| preflight 的例子 | 17473 + 9 = **17482**，計算正確；preflight 不計入 B01–B03 | — |
| B01–B03 還沒執行，不能宣稱節省 | **正確** | pilot report 仍然是 NOT EXECUTED，「Can claim token savings: NO」 |

## Notes（non-blocking，建議寫進 pilot 的執行規則）

- **R1（失敗的 turn）：** `TurnStatus::Failed` 的路徑不會輸出 `TurnCompleted { usage }`（§5）。失敗或中斷的 session **拿不到 usage**，必須記成 unknown，而且那一對不能用來宣稱節省。不能用 0 或部分值代替。
- **R2（cache-write 的假設）：** cache-write 是 input 子集的結論，依據是它位在 `input_tokens_details` 這個 schema 位置，以及 OpenAI 的語意。本次 preflight 的值是 0。如果 pilot 中出現 `cache_write_input_tokens > 0`，建議在記錄中註明，並確認同一個 session 的 input 有沒有異常；如果將來換成其他 provider 或語意不同的後端，必須重新驗證。
- **R3（版本和 schema 的固定）：** 每個 session 都要記錄 `codex --version`，以及 usage 欄位的集合。版本或欄位改變時就停止，重新驗證（mapping report 已有這條規則）。另外建議在記錄中保留 thread 和 turn 的數量，作為「單一 turn」的證據（例如 JSONL 只有一個 `turn.completed`）。
- **R4（同一個 turn 內的多次 request）：** 使用工具時，一個 turn 會有多次 Responses 呼叫，每次都會重新送出 context，所以 input 的累計會比 context 大小高很多。這是**實際消耗的 token**，對 control 和 dogfood 一視同仁，可以比較；但不要把它解讀成 context 大小。

## 判定

```text
CODEX 0.159.2 TOKEN ACCOUNTING MAPPING: PASS
mapping valid: YES
double-counting rule valid: YES
fresh single-turn restriction sufficient: YES (add R1 failed-turn=unknown rule)
new blocker: 0
B01–B03 pilot may proceed: YES (only after the remaining listed preconditions; no savings claim yet)
```

- 本報告 commit 到 PR #36 的 branch（只新增 `reports/` 底下的檔案）。
- 這一輪沒有執行 benchmark，沒有 merge PR #36，也沒有開始 A11b.2 或 A11c。
