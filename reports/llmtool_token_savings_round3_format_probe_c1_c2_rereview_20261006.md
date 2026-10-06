# llmtool PR #38 Round 3 — C1／C2 remediation re-review（2026-10-06）

## 範圍與方法

- **審查對象：**
  - Draft PR #38，branch head `d0ac1d1b14d93e047991021380de698b3b0b27bf`；
  - exact tested implementation head `c1830ef2c60fbda61777461ffd20b3dd312a84ec`。
- **閱讀：**
  - `format_probe.py` 和 `test_format_probe.py`。另外 diff 了 test 檔案，範圍是 `29bd0dd..c1830ef`，用來檢查有沒有為了通過測試而放寬既有的 test；
  - retest2 report；
  - C1／C2 re-review handoff；
  - 我先前的 implementation review。
- **執行：**
  - 在 scratchpad 用 Python 3.9.6 重跑 88 個 test；
  - 重跑我先前的 C1／C2 probe script；
  - 新增一個額外的 counterexample script，只使用記憶體中的資料，沒有寫入 repo。
- **沒有做的事：** 沒有呼叫 company 或 Codex，沒有修改 helper 或 test，沒有 merge。

## 結論：PASS（C1 和 C2 都已 CLOSED；C3 在 live 之前仍然是必要條件）

## 0. Revision integrity：PASS

- `c1830ef..d0ac1d1` 之間只有兩個 docs 檔案：retest2 report 和 re-review handoff。
- `git diff c1830ef HEAD -- reports/token_benchmark/` 是空的。
- SHA-256：
  - `format_probe.py` = `500fb8bd…87a3`
  - `test_format_probe.py` = `80e62df3…fd79`

  兩者都和 retest2 記錄的值相同。
- PR 相對於 `fecbdba` 的所有變更都在 `reports/` 底下。

## 1. C1 accounting／evaluation binding：CLOSED

| 要求 | 驗證（直接呼叫 module） |
|---|---|
| `RequestObservation` 是 frozen 且有型別 | `@dataclass(frozen=True)`。`obs.model = 'x'` 會產生 `FrozenInstanceError` |
| 任意的 dict 不能作為 proof | 傳入 `{}` 或 `dataclasses.asdict(PROOF)` 都產生 **TypeError**。型別正確但欄位錯誤（例如 `enable_thinking=0`）時，產生 ValueError |
| 由呼叫端提供 reasons 的 `complete()` 已經移除 | `hasattr(Phase0Accounting, 'complete')` 是 **False**。只剩下 `complete_evaluation` 和 `complete_pre_output_failure` |
| 完成 trial 必須提供 final `FrozenEvaluation` | 必須是 `type(...) is FrozenEvaluation`；而且 request 必須已經送出，否則 ValueError |
| lane 必須一致 | 把 L02 的 evaluation 用在 L03 的 trial：ValueError。把 L03 的 evaluation 重新標成 `L02`：stage 不符，ValueError |
| L02 必須是 `canonical_coverage`；L03 必須是 `phase0_l03_target_recall` | L03 的 anchor-stage 結果會被拒絕，錯誤訊息是 `final L03 post-freeze recall evaluation required` |
| reasons 來自 evaluation | `diagnostics = result['secondary_failure_reasons']`，primary 由 `primary_failure` 重新計算。並且檢查 `primary_failure_reason`、`passed` 和 `raw_stdout_sha256` 必須和 evaluation 的內容一致，不一致就拒絕 |
| 3／7 記錄為 FAIL | `FAIL / TARGET_RECALL_BELOW_7_OF_7` |
| 3／7 不可能讓 `gate_passed` 為 True | 完整執行 6 個 trial：L03-1 FAIL，L03-2 和 L03-3 是 `NOT_STARTED_EARLY_FORMAT_GATE_FAIL`，L02 三次 PASS，**`gate_passed=False`** |
| pre-output 失敗使用獨立且有限制的 API | `complete_pre_output_failure` 只接受 `TRANSPORT_OR_REQUEST_FAILURE` 和 `MODEL_OUTPUT_INCOMPLETE`，傳入 SCHEMA 或 PASS 都會被拒絕。request 尚未送出時，只能記錄 TRANSPORT，而且 usage 必須是 None；length 失敗必須在 request 已送出之後才能記錄。length 失敗之後，L02 停止（L02-2 標成 NOT_STARTED，usage 是 None），L03 繼續 |
| secondary 會被保留 | 一個同時有 unknown inventory 和 3／7 的 L03 evaluation，記錄為 primary `UNKNOWN_STRUCTURED_ANCHOR`、secondary `(UNKNOWN…, TARGET_RECALL…)`，而且 TrialRecord 記錄了 `raw_stdout_sha256` 和 `evaluation_sha256` |

**重現舊的 CE11 和 CE12：**

- **CE11：** 舊的寫法（把 anchor-stage 的空 reasons 傳給 `complete()`）現在無法執行。改用 `complete_evaluation` 傳入 anchor-stage 的結果，會產生 ValueError。完整走 6 個 trial 時，`gate_passed=False`。**已關閉。**
- **CE12：** 形狀正確的 dict 會產生 TypeError。**已關閉。**
- **CE10 這類偽造仍然存在（預期之內）：** 手動建構一個 `FrozenEvaluation`，並且讓 `passed`、primary 和 secondary **全部一致地偽造成 PASS**，仍然會被接受。這只有在 wrapper 刻意偽造時才會發生，不屬於 C1 的範圍。它必須靠 C3 的 raw-stdout replay reconciliation 處理。

## 2. C2 L02 classifier：CLOSED

- **regex 的原始碼**（`format_probe.py` 第 35–36 行）：

  ```python
  CANONICAL_LOOKING = re.compile(r'(?i)(?:\bEVENT\s+event_id\b|\b(?:event_id|request_id|signature|ts)\s*[:=])')
  NAMESPACE_REFERENCE = re.compile(r'\b(?:EVT|REQ)-[A-Za-z0-9_.:/-]+\b')
  ```

  我用 grep 搜尋整個檔案中的 `\\`（雙反斜線），結果是 **0 筆**。所以 `\b` 和 `\s` 是真正的 regex operator，第一次 retest 時反斜線多寫一次的問題已經修正。
- **6 個指定的類別，全部符合預期：**
  - `event_id: EVT-T99 signature: …` → FAIL（MALFORMED）；
  - `EVENT event_id=EVT-FABRICATED` → FAIL（MALFORMED）；
  - `event_id=EVT-T01 was observed` → FAIL（MALFORMED）；
  - `Four EVENT lines were observed…` → PASS（extra）；
  - fixture 的 `# Canonical EVENT records …` → PASS（extra）；
  - 3 個 EVT-／REQ- prose 的輸入 → FAIL（`NAMESPACE_IDENTIFIER_REFERENCE_IN_EXTRA`）。
- **fixture 的原始 log 行不會被誤判：** L02 fixture 中有 540 行不是 EVENT 的行。**即使逐字引用，也沒有任何一行會觸發** `CANONICAL_LOOKING` 或 `NAMESPACE_REFERENCE`。Round 2 的 24 個 extras 仍然 PASS（`test_round2_recorded_extra_shape_is_nonfatal` 是 ok）。
- **有沒有為了變綠而放寬 test：**
  - `test_malformed_event_does_not_hide_among_extras` 和 `test_partial_or_embedded_event_cannot_repair_coverage` **在 `29bd0dd..c1830ef` 之間完全沒有被修改**。它們在第一次 retest 時 FAIL 或 ERROR，修正 regex 之後才通過。
  - 只有 `test_missing_request_proof_rejected` 的預期例外從 ValueError 改為 TypeError。這是 typed API 的合理後果，而且新的 `test_request_proof_must_be_typed_observation_not_dict` 也覆蓋了同樣的行為。
  - I/O guard 從黑名單改為**白名單**（只允許 `hashlib`、`json`、`re`、`collections`、`dataclasses`、`datetime`、`llmtool`）。這處理了我先前提到的 `io.open` 繞過問題，**是加強**。
  - **一個輕微的範圍縮小（non-blocking）：** `test_failure_precedence_keeps_secondary` 原本檢查 accounting record 會保留全部的 secondary；現在只檢查 `primary_failure()`。我用上面的 multi-reason L03 counterexample 確認，**行為本身是正確的**（secondary 有保留）。但目前沒有 test 覆蓋「accounting record 保留多個 secondary」。建議在 wrapper 階段補上一個 test。

## 3. C3 的邊界：仍然是必要條件（YES）

`format_probe.py` 目前沒有實作下列任何一項，所以都必須在 live 之前凍結，並由 wrapper 實作：

1. **user message 的 digest：** observer 仍然不檢查 `messages`。必須和凍結的 instructions 加上 fixture 的 digest 完全相同。
2. **request body 允許的欄位：** observer 目前只檢查必要的欄位，不會拒絕多出來的欄位（例如 `stream: true`）。wrapper 必須凍結允許的欄位集合，並禁止 stream。
3. **transport 和 `finish_reason=length` 的判定：** helper 已經提供 `complete_pre_output_failure`，但判定本身要由 wrapper 根據 provider 的回應或錯誤碼完成。
4. **raw-stdout replay reconciliation：** 從保存的 stdout 加上 fixture 重新執行 adapter，確認 `result_json` 和 `evaluation_sha256` 完全相同。這是處理 CE10 的唯一方法。
5. **evaluation metadata 的 digest：** 在呼叫 loader 之前，先確認 gold 的 SHA。
6. **L02 的 instructions：** evidence 和 timeline 只能包含**逐字的 fixture 行**（EVENT 行，或原始的 log 行），不能有註解；**任何提到 EVT-、REQ- 的 ID 或 `signature:` 的敘述，都必須放在其他欄位**。我驗證過，`Error signature: UPSTREAM_TIMEOUT repeated three times` 放在 evidence 會 FAIL。這是 `(?i)` 加上 `[:=]` 的預期行為，但 instructions 必須明確告訴模型。
7. **L03 的 instructions：** 一筆 record 只能對應一個 evidence item；同一個 evidence item 可以支持多個類別；不要引用 inventory。

## 判定

```text
PR #38 ROUND-3 C1/C2 REMEDIATION RE-REVIEW:
PASS

C1 accounting/evaluation binding:
CLOSED — typed frozen RequestObservation; dict proof → TypeError; generic complete() removed;
  complete_evaluation requires final FrozenEvaluation with matching lane/stage and consistent
  primary/passed/stdout digest; reasons derived from evaluation; 3/7 → FAIL and gate_passed=False;
  bounded complete_pre_output_failure; CE11/CE12 no longer reproducible
  (CE10-class hand-forged consistent evaluation remains → C3 replay reconciliation)

C2 L02 classifier:
CLOSED — real \b/\s operators (0 double backslashes); all 6 specified classes behave as required;
  540 fixture non-EVENT lines remain harmless if quoted; prior malformed/partial tests unchanged and now pass

88-test regression:
PASS — 88/88 OK on Python 3.9.6 (reproduced); git diff --check PASS; I/O guard strengthened to allowlist;
  minor: accounting-level secondary-retention assertion narrowed (behaviour verified correct; restore a test later)

New helper-level blockers:
NONE

C3 live-wrapper requirements still pending:
YES — user-message digest, allowed-field body contract/no stream, transport vs finish_reason=length,
  raw-stdout replay reconciliation, evaluation-metadata digest, L02 verbatim-lines-only instructions
  (ID/`signature:` prose outside evidence/timeline), L03 one-record-one-evidence/no-inventory instructions

May design/freeze live Phase-0 wrapper:
YES

May run live Company Phase 0 now:
NO
```

- 本報告是這一輪唯一 commit 的檔案。沒有修改實作，沒有呼叫 company 或 Codex，沒有 merge，也沒有開始 A11b.2 或 A11c。
