# llmtool PR #38 Round 3 — C3-B B1 targeted re-review（2026-10-06）

## 範圍與方法

- **審查對象：**
  - Draft PR #38，review head `0b53da329632e62e0d02d6128c52f53bf136c88f`；
  - exact tested B1 implementation head `3325f75e954a4099e31c5936412307320974cb4f`；
  - 新的 pin `37f48c17…9404`。
- **閱讀：**
  - B1 re-review handoff；
  - B1 remediation validation；
  - 我上一輪的 C3-B review；
  - `live_wrapper.py` 的完整 diff（`94dd567..3325f75`）；
  - `test_live_wrapper.py` 新增的 17 個 test；
  - `phase0_preregistration.json`。
- **執行：**
  - 在**整個 process 層級封鎖網路**的條件下（scratchpad 中的 `sitecustomize`，讓所有 socket 連線直接 raise），重跑兩個 suite；
  - 用一個**獨立的 probe script**（只放在 scratchpad），重現 B1 原本的繞過方式，並對照新的 sealed path。
- **沒有做的事：**
  - 沒有連線到 `192.0.2.10:4000`；
  - 沒有呼叫 company 或 Codex；
  - 沒有修改 wrapper、test、manifest 或 production；
  - 沒有 merge。

## 結論：PASS，B1 已關閉

## 1. Revision integrity：PASS

- `3325f75..0b53da3` 之間只有兩個 docs 檔案：B1 validation 和 re-review handoff。`git diff 3325f75 HEAD -- reports/token_benchmark/` 是空的，所以 wrapper、test、manifest 和 instructions 都沒有改變。
- B1 本身（`94dd567..3325f75`）只修改了三個檔案：
  - `live_wrapper.py`：+28／−8；
  - manifest：+8／−3；
  - test：+198／−10。
- PR 相對於 `fecbdba` 的所有變更都在 `reports/` 底下。production、runtime 和 skill 都沒有修改。
- manifest 的 SHA-256 = **`37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404`**，等於 pin。

## 2. Sealed constructor：CLOSED

- `Phase0Wrapper.__init__(self, expected_manifest_sha256)` 會直接呼叫 `load_preregistration(expected_manifest_sha256)`。
- **我實際測試：** 下列四種輸入**都產生 `WrapperIntegrityFailure`，而且 `_json` 被呼叫的次數都是 0**：
  - 傳入 registration dict；
  - 大寫的 pin；
  - `None`；
  - `'0'*64`。

  原因是 `load_preregistration` 先用 `re.fullmatch('[0-9a-f]{64}')` 檢查型別和格式。
- `Phase0Wrapper` 公開的屬性只有 `expected_manifest_sha256`（唯讀 property）、`registration`（唯讀 property）和 `run_next`。沒有 `from_dict`、classmethod 或其他可以繞過的建構方式。
- `admitted_open(..., expected_manifest_sha256, ...)` 也已經改為接收 pin。傳入 dict 會在 `load_preregistration` 的格式檢查中產生 WIF，不會送出 request，也不會被 delegate（`test_admitted_open_rejects_dict_authority`）。

## 3. 先比對 pin，再 decode：PASS

`load_preregistration` 的順序：

1. 檢查 pin 的格式；
2. `read_bytes()`；
3. **比對 `sha(raw) == pin`**；
4. 之後才呼叫 `probe._json`（嚴格的 decoder）；
5. 呼叫 `verify_registration`。

我在建構時、以及在偽造 manifest 的情況下，都用 `_json` 的 side_effect 確認 decode 的次數是 0。對應的 test 是 `test_wrong_constructor_pin_before_decode` 和 `test_self_consistent_forged_manifest_blocked`。

## 4. registration 不能再成為執行的依據：PASS

- `runner.registration` 回傳一個 JSON round-trip 的**detached copy**。我把回傳值中的 body SHA 改成 0，內部的值沒有改變。
- `runner.registration = …` 會產生 AttributeError，因為 property 沒有 setter；`expected_manifest_sha256` 也不能重新指定。
- `run_next` 和 `admitted_open` 每次都會**重新從檔案載入**，`self._registration` 只是保存最後一次載入的結果，不會被當成執行的依據。

## 5. 每一次 run 都重新比對 pin：PASS

- `run_next` 在 `require(not self.halted)` 之後，**第一步**就是 `load_preregistration(pin)`，接著才 `begin_next()`。所以連「這個 trial 要不要標成 NOT_STARTED」的判斷，也都在重新比對 pin 之後才進行。
- `test_every_run_next_reloads_pinned_manifest` 證明：即使已經因為 transport 失敗而變成 unknown，下一次 `run_next` 如果遇到 manifest drift，仍然會產生 WIF，而且 records 沒有增加。

## 6. Artifact、instruction 和 manifest 的 drift：PASS

- 有對應的 test：
  - `test_mid_run_manifest_drift`；
  - `test_mid_run_instruction_drift`（L02 和 L03）；
  - `test_mid_run_artifact_drift`（`errors.py`）。

  它們都斷言：產生 WIF、`invoke_cli` **沒有被呼叫**、records 是空的、`halted=True`、沒有產生任何輸出檔案。
- 我獨立重現了一次：建構完成之後，模擬 L03 instruction 被修改，再呼叫 `run_next`。結果是 WIF，`invoke_cli` 被呼叫 0 次，`halted=True`，records 是 `()`。

## 7. Pre-opener 和 observer 期間的 TOCTOU：PASS

**`admitted_open` 的順序：**

1. `load_preregistration(pin)`，包含完整的 verify：所有 artifact 和兩個 lane 的 instruction、user message；
2. 檢查 timeout 和 lane；
3. `user_message(lane, contract)`；
4. `validate_request`：包含 body 和 user 的 bytes 比對，以及不修改 request 的 observer；
5. **再一次** `require(load_preregistration(pin) == registration)`；
6. `mark_request_emitted`；
7. `original_open`。

**test 涵蓋的情況：**

- `test_toctou_{manifest,instruction,artifact}_drift_before_opener`：drift 發生在 `run_next` 驗證之後、opener 被呼叫的那一刻；
- `test_drift_during_request_observation_before_emission`：drift 發生在 observer 執行**之後**，涵蓋 manifest、L03 instruction 和 `terminal.py`。

這些 test 都斷言：產生 WIF、delegate 的次數是 0、`request_emitted=False`、`halted=True`、沒有輸出。

**剩下的時間窗：** 最後一次 re-pin 和 `original_open` 之間只隔了一行 `mark_request_emitted`。而且送出去的 Request bytes **已經用 pinned contract 驗證過**，所以即使在這個極短的時間窗內修改了檔案，也不會改變實際送出的內容。可以接受。

## 8. 重現原本的 B1 繞過方式：CLOSED

**permanent test `test_self_consistent_forged_manifest_blocked`：**

1. 在記憶體中修改 L03 instruction；
2. 用 `dry_run('L03')` 重新計算 instruction、user message 和 body 的 SHA。這三個值都和原本不同，test 有斷言；
3. 用這些值建立一個自洽的偽造 dict；
4. 確認 `verify_registration(forged)` **仍然接受**；
5. 用偽造的 bytes 取代 manifest，再用 `Phase0Wrapper(expected_manifest_sha256=pin)` 建構，結果是 **WIF，而且 decode 被呼叫 0 次**。

**我獨立重現：** 用我上一輪的 drift 方法（在 L03 instruction 後面加一句話）。

- 偽造的 dict 仍然可以通過 `verify_registration`。這是預期的行為，也就是 B1 的成因。
- **sealed constructor 使用 pin 時，會拒絕偽造的 manifest，decode 被呼叫 0 次。**
- 把偽造的 dict 直接傳給 constructor，也會被拒絕。

## 9. 擴大 runtime artifact 的 pin：PASS

- manifest 現在有 **17 個 artifact**：4 個 round3 的 helper／test，加上 13 個 runtime 檔案。
- 新增的 runtime 檔案：`errors.py`、`diagnostics.py`、`terminal.py`、`providers/base.py`、`providers/__init__.py`。
- 原本的 runtime 檔案都保留：`cli.py`、`entrypoint.py`、`inputs.py`、`prompts.py`、`config.py`、`skills.py`、`context.py`、`providers/openai_compatible.py`。
- 全部 17 個 digest 都和磁碟上的檔案相符。
- **committed manifest 和這次重新執行的 `build_preregistration()` 完全相等。** 也就是說，manifest 確實是從真正的 freeze 產生的，不是手寫的。
- production 檔案沒有改變（PR 中沒有任何 `reports/` 以外的檔案）。

## 10. 凍結的 Request 和 user message：PASS

| lane | manifest 的值 | 這次重新 `dry_run`（禁止讀取 gold、封鎖網路） |
|---|---|---|
| L02 user | `e0194460…22ac` ✓ | ✓ |
| L02 body | `cc3c79f8…360f` ✓ | ✓ |
| L03 user | `3b94f0d8…45e2` ✓ | ✓ |
| L03 body | `6c1f7b77…cdf0` ✓ | ✓ |

四個值都和 B1 之前相同。

## 11. Regression：PASS

| suite | 結果（封鎖網路） |
|---|---|
| `test_format_probe.py` | **88／88 ok**，exit 0 |
| `test_live_wrapper.py` | **94／94 ok**，exit 0（`def test_` 的數量也是 94） |
| `git diff --check` | PASS |

沒有任何 FAIL、ERROR 或 skipped。在 process 層級封鎖網路的情況下全部通過，所以**沒有呼叫 company，也沒有任何網路連線**。tracked 檔案也沒有被修改。

## 12. Live handoff 的條件：仍然保留（不是 blocker）

B1 validation（第 160–169 行）和 handoff（第 122–124 行）都已經寫明：

- **raw output 的位置：** `output_directory` 必須在 tracked repo 以外；只有在 secret scan PASS 之後，才能複製到 `reports/`。未經 scan 的 company 輸出不能直接寫進 tracked 的 `reports/`。
- **送出 request 之後的 integrity halt：** 如果 trial 停在 STARTED 加上已送出的狀態，必須回報 **ACCOUNTING INCOMPLETE / UNKNOWN** 並停止。不能宣稱 company 的 token 總數是完整的。

## 附註（non-blocking）

1. **test 中的 pin 是從磁碟上的檔案計算的**（`cls.manifest_pin = sha(manifest_bytes)`），所以 test 本身不會綁定到 review 過的值 `37f48c17…`。和 review 過的值之間的綁定，來自本次 review 確認「檔案的 SHA 等於 pin」，以及「committed manifest 等於真正的 freeze」。
   - **live handoff 必須把 `37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404` 直接寫成字面值**，不能從檔案計算。
2. `runner._expected_manifest_sha256` 是 Python 的 private 屬性，在程式碼層級仍然可以被重新指定。效果等同於「傳入另一個 pin」，所以控制的方式和第 1 點相同：handoff 只能使用 review 過的那個字面值。

## 判定

```text
PR #38 ROUND-3 C3-B B1 TARGETED RE-REVIEW:
PASS

Exact review head:
0b53da329632e62e0d02d6128c52f53bf136c88f

Exact tested implementation head:
3325f75e954a4099e31c5936412307320974cb4f

Pinned preregistration SHA256:
37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404

Revision integrity:
PASS — docs-only after 3325f75; manifest SHA equals pin; all PR files under reports/; no production change

Arbitrary registration authority:
CLOSED — dict/None/uppercase/zero pin rejected with zero decodes; no alternate constructor; read-only detached
  registration view; admitted_open takes the pin, not a dict

Constructor pin-before-decode:
PASS — format check → raw bytes → SHA vs pin → strict decode → verify_registration

Every-run re-pin:
PASS — run_next reloads/re-verifies the pinned manifest before begin_next (incl. NOT_STARTED decisions);
  post-construction manifest/instruction/artifact drift → WIF, invoke_cli never called, halted, no output

Pre-opener / observer TOCTOU drift:
PASS — re-pin before validation and again after observation; drift at opener time or during observation →
  WIF, zero delegations, request_emitted=False

Self-consistent forged manifest bypass:
CLOSED — permanent test and independent reproduction: forged dict still passes verify_registration, but the
  sealed constructor with the reviewed pin rejects the forged manifest before decode

Expanded runtime artifact pins:
PASS — 17 artifacts incl. errors/diagnostics/terminal/providers base/__init__; prior pins retained;
  all digests match disk; committed manifest equals fresh real freeze

Frozen Request/user-message identity:
PASS — L02 e0194460…/cc3c79f8…, L03 3b94f0d8…/6c1f7b77… unchanged (manifest and fresh dry_run)

Offline regressions:
88/88 helper
94/94 wrapper

New before-live blockers:
NONE — handoff must hard-code the reviewed pin literal (not compute it from the file); raw output outside tracked
  repo until scan PASS; post-emission integrity halt reported as ACCOUNTING INCOMPLETE / UNKNOWN and STOP

May prepare exact live Phase-0 execution handoff:
YES

May run live Company Phase 0 now:
NO — exact live execution handoff still must pin
37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404
and explicitly authorize the live run
```

- 本報告是這一輪唯一 commit 的檔案。沒有修改實作，沒有連線到 company endpoint，沒有呼叫 Codex，沒有 merge，也沒有開始 A11b.2 或 A11c。
