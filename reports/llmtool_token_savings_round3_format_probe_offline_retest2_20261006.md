# llmtool PR #38 Round 3 — C1／C2 修正後的第二次完整 offline retest（2026-10-06）

Status: **RETEST PASS / READY FOR TARGETED REMEDIATION REVIEW**

## 範圍與 provenance

- **Repo：** `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`（canonical）。沒有使用 worktree 或 clone；沒有 reset、clean 或 stash；9 個不相關的 untracked 路徑都保持原樣。
- **Branch：** `bench/llmtool-token-savings-round3-format-compliance-20261006`
- **同步：**
  - 開始時 local HEAD 是 `148d986`；
  - `git fetch origin` 之後，remote 是 `c1830ef2c60fbda61777461ffd20b3dd312a84ec`，等於 expected head；
  - 確認 local 是 remote 的 ancestor 之後，執行 `merge --ff-only`；
  - HEAD = `c1830ef`。
- **必讀文件（已讀）：**
  - 第一次的 retest（FAIL：83／88）；
  - failure remediation（root cause 有兩個：raw regex 的反斜線多寫了一次，以及 typed API 修改後一個 test 的預期例外沒有更新）；
  - retest2 的 handoff。
- **這一輪只做 retest。** 沒有修改 `format_probe.py`、`test_format_probe.py`、production、runtime、skill、fixture 或 gold，也沒有實作 live wrapper。

## 執行結果

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py -v
git diff --check
```

- 執行結果：`Ran 88 tests in 0.129s`，`OK`，**exit 0**。
- verbose log 中有 88 行 `... ok`，沒有任何 FAIL、ERROR 或 skipped 的紀錄。
- 用 `def test_` 靜態計算，test method 也是 **88** 個，和預期的數量一致。
- 執行之後 `git status` 沒有任何 tracked 檔案變更（使用了 `-B`，所以沒有產生 cache）。

## C1 修正的回歸檢查：PASS

**suite 中的 test（verbose log 中都是 `ok`）：**

| 要求 | test | 結果 |
|---|---|---|
| L03 的 anchor-stage evaluation → accounting 拒絕 | `test_accounting_rejects_anchor_stage_l03_evaluation` | ok |
| wrong-lane evaluation → 拒絕 | `test_accounting_rejects_wrong_lane_evaluation` | ok |
| failed final evaluation 不能被 caller 覆蓋成 PASS | `test_accounting_cannot_override_failed_evaluation_with_empty_reasons` | ok |
| plain dict 作為 request proof → TypeError | `test_request_proof_must_be_typed_observation_not_dict`，以及修正後的 `test_missing_request_proof_rejected` | ok |
| L03 只有 3／7 → `gate_passed` 不可能是 True | `test_three_of_seven_cannot_make_accounting_gate_pass` | ok |

**直接呼叫 module 的獨立確認（scratchpad script，不依賴 test 本身的 assertion）：**

- anchor-stage 的 L03 evaluation 傳給 `complete_evaluation`：**ValueError**。
- L02 的 evaluation 用在 L03 的 trial：**ValueError**。
- 舊的 `complete(trial, reasons=())` API 已經不存在（AttributeError），呼叫端無法自行提供 reasons。
- plain dict 作為 proof：**TypeError**。
- 3／7 的 final evaluation 被記錄為 `FAIL / TARGET_RECALL_BELOW_7_OF_7`。
- 6 個 trial 的完整流程中，L03-1 是 3／7：
  - L03-1 判為 FAIL；
  - L03-2 和 L03-3 判為 `NOT_STARTED_EARLY_FORMAT_GATE_FAIL`；
  - L02 繼續執行，3 次都 PASS；
  - **`gate_passed = False`**。

## C2 regex／classifier 的回歸檢查：PASS

**修正後的 regex：**

- `CANONICAL_LOOKING = (?i)(?:\bEVENT\s+event_id\b|\b(?:event_id|request_id|signature|ts)\s*[:=])`
- `NAMESPACE_REFERENCE = \b(?:EVT|REQ)-…\b`

raw string 中的 `\b` 和 `\s` 現在是真正的 regex operator，第一次 retest 中反斜線多寫一次的問題已經修正。

**8 個指定的輸入：** 把每一個輸入附加到一個完整、正確的 L02 output 的 evidence 中，直接呼叫 `evaluate_l02_without_gold`。

| 輸入 | 預期 | 實際 | 分類 |
|---|---|---|---|
| `event_id: EVT-T99 signature: UPSTREAM_TIMEOUT` | FAIL | **FAIL** | MALFORMED_OR_PARTIAL_EVENT |
| `EVENT event_id=EVT-FABRICATED` | FAIL | **FAIL** | MALFORMED_OR_PARTIAL_EVENT |
| `event_id=EVT-T01 was observed` | FAIL | **FAIL** | MALFORMED_OR_PARTIAL_EVENT |
| `Four EVENT lines were observed; no later EVENT lines.` | PASS（extra） | **PASS** | extra |
| fixture 自己的 `# Canonical EVENT records preserve timestamp evidence, …` | PASS（extra） | **PASS** | extra |
| `EVT-T09 UPSTREAM_TIMEOUT on REQ-99 at 08:02:20Z` | FAIL | **FAIL** | NAMESPACE_IDENTIFIER_REFERENCE_IN_EXTRA |
| `EVT-T01 REQ-17 timed out at 09:30:00Z` | FAIL | **FAIL** | NAMESPACE_IDENTIFIER_REFERENCE_IN_EXTRA |
| `event EVT-T01 repeated` | FAIL | **FAIL** | NAMESPACE_IDENTIFIER_REFERENCE_IN_EXTRA |

**8／8 都符合預期。** suite 中對應的 test 都是 ok：

- `test_colon_form_canonical_fields_are_not_harmless_extras`
- `test_malformed_event_does_not_hide_among_extras`
- `test_partial_or_embedded_event_cannot_repair_coverage`
- `test_benign_event_word_prose_is_extra`
- `test_fixture_event_comment_is_extra_not_canonical_claim`
- `test_namespace_identifier_reference_in_extra_fails`

## 觀察（不影響這次 retest 的判定，留給 targeted remediation review）

1. **namespace 規則比「只拒絕未知 ID」更嚴格：** evidence 和 timeline 的 extra 中，**只要出現 `EVT-` 或 `REQ-` 的 ID**，就會判 FAIL，即使是 fixture 中真實存在的 ID（例如 `event EVT-T01 repeated`）。`unknown_identifiers` 只作為 secondary 的資訊記錄。
   - 這符合本輪指定的預期，也比我在 implementation review 中提出的「至少產生 diagnostic」更嚴格。
   - 但這表示在 L02 中，**提到 event 或 request ID 的 prose 只能放在 evidence 和 timeline 以外的欄位**。凍結的 L02 instructions 必須明確告訴模型這一點，否則會因為 benchmark 的規則而 FAIL（C3）。
2. **CE10 這類偽造仍然可能：** 如果一個 `FrozenEvaluation` 是手動建構的，而且內部欄位彼此一致（`passed`、primary、secondary 都偽造成 PASS），它仍然會被 `complete_evaluation` 接受。`complete_evaluation` 會檢查一致性和 stdout digest，但不會從 raw stdout 重新計算一次。
   - 這和 implementation review 的評估一致（low，只有 wrapper 刻意繞過時才會發生）。
   - 仍然需要在 C3 中處理：最後的 reconciliation 必須從保存的 raw stdout 加上 fixture 重新執行 adapter，並確認結果完全相同。

## 必要欄位

```text
Exact tested head:
c1830ef2c60fbda61777461ffd20b3dd312a84ec
Python:
3.9.6 (/usr/bin/python3)

Tests:
PASS 88 / 88
FAIL: 0
ERROR: 0
SKIP: 0
Exit: 0

git diff --check:
PASS (exit 0, no output)

C1 remediation regression:
PASS

C2 regex/classifier regression:
PASS

format_probe.py SHA256:
500fb8bd990d3a5bdb6e18779619ca87a21af964a84682f88a10741b4fee87a3
test_format_probe.py SHA256:
80e62df3140e10b34cfe6cf22188e219e53ca0aac0568d08602e0e4169cefd79

L02 fixture SHA:
859effa1e4d6e978372f4914aca8c53e4af2953f3850ceaad2a68732060892da
L03 fixture SHA:
44caecfb4fc77e639d471710b6fc190ca2e1145ae94a22396c5cdcc487dd88ce

L03 multimap:
records: 668
distinct pairs: 534
ambiguous pairs: 120
records in ambiguous pairs: 254

Company live calls:
0

Codex calls:
0

Production/runtime/skill changes:
NONE

Ready for targeted remediation review:
YES
```

- 本報告是這一輪唯一 commit 的檔案。我在這裡停止：不會開始 live Company Phase 0，也不會實作或執行 live wrapper，不 merge。
