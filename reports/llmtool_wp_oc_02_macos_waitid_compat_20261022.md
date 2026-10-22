# WP-OC-02：macOS OpenCode Worker / Direct Developer process lifecycle 相容性修復報告

- 任務：WP-OC-02（Owner 指派，OpenCode Developer 執行）
- 日期：2026-10-22（Asia/Taipei）
- 分支：`fix/llmtool-opencode-macos-wait-compat-20261022`
- Base：`0cabd79874c6803196f65c94e5ec1e62932f01ff`（= origin/main HEAD 時點；`git merge-base HEAD origin/main` 驗證一致）
- Commit：`23f48dd68ce3fec456611e25e926f8e3ddb9fe4e`
- Draft PR：https://github.com/davidcatcat/davidcattoolsproject/pull/270（`state=draft`、`base=main`、變更 2 檔）
- 前序證據：WP-OC-01 報告（PR #266，Mac 本地全量統計 23F+14E 的來源；該 PR 未合併、本報告不修改它）

## 1. 摘要

Mac 本地（macOS 15.7.9 / Apple CLT Python 3.9.6 / x86_64）全量套件 1877 tests 有 23 failures + 14 errors，全部集中於 `test_opencode_worker.py`（19F+8E）與 `test_opencode_direct_developer.py`（4F+6E）。

逐項驗證後確認單一根因：**Apple CLT CPython 3.9.6 沒有暴露 `os.waitid` binding，但 macOS 本身有 `waitid(2)` syscall**。生產 `_observe_exit_without_reap` 對缺 primitive 一律 fail-closed `OPENCODE_CLEANUP_UNKNOWN`，造成 37 項級聯失敗（direct developer 的 10 項亦全部經此路徑級聯，該檔本身無需修改）。

修復採最小 platform dispatch：`os.waitid` 仍是所有平台的主呼叫縫；僅 `sys.platform == 'darwin'` 且缺 binding 時，改用 libc `waitid` 的 ctypes binding（維持完全相同的 WNOWAIT 不消費語意、失敗一律 fail-closed、無 reaping 替代路徑）。Linux 與任何有原生 binding 的 Python 行為 byte-identical。

修復後同一台 Mac：**worker 47/47 OK、direct developer 18/18 OK、foreground host 23/23 OK、完整套件 1877 tests OK（skipped=3、exit 0）**。期間另發現並修掉一個由初版 ctypes 映射（64 bytes siginfo_t，實際 kernel 需 104 bytes）造成的 heap overflow / 延遲 SIGSEGV，修復後 Mac 再無新 crash report。

## 2. 修復前基線（同一台 Mac、task-local venv）

| 套件 | 修復前 |
|---|---|
| `test_opencode_worker.py` | 19 failures + 8 errors（47 tests） |
| `test_opencode_direct_developer.py` | 4 failures + 6 errors（18 tests） |
| 完整套件（`make test`） | 23 failures + 14 errors（1877 tests） |

原始 traceback 保存於工作目錄外之證據區：`T/opencode/wp-oc-02/worker-repro.log`、`dev-repro.log`。

## 3. 根因逐項驗證（不採信報告推測）

37 項失敗的唯一共同路徑為 `_observe_exit_without_reap`：

- 10 項為 `AttributeError`（`patch.object(w.os, 'waitid', ...)` 在 macOS 上因屬性不存在而失敗）；
- 其餘 27 項為觀察函式 fail-closed 回 `OPENCODE_CLEANUP_UNKNOWN` 後的上層 assert 級聯；
- `test_opencode_direct_developer.py` 的 10 項失敗全部由同一路徑級聯產生，該生產檔不需修改（僅間接受益）。

真機實證（未修改任何生產碼前）：

1. `getattr(os, 'waitid')` 在 CLT 3.9.6 為 `None`；同機 Python 3.13.3 有 `os.waitid` → 屬 **CPython binding 缺失**，非平台無 syscall。
2. waitid(2) 語意以 ctypes 直接呼叫驗證：`P_PID=1`、`WEXITED=4`、`WNOHANG=1`、`WNOWAIT=32`；`WNOWAIT` 觀察後子進程仍可被 `waitpid` 收割、`Popen.returncode` 保持 `None` → 不消費 zombie 狀態。
3. SDK 驗證 `sizeof(siginfo_t) = 104` bytes，欄位 offsets：`si_signo@0 / si_errno@4 / si_code@8 / si_pid@12 / si_uid@16 / si_status@20`；`P_ALL=0 / P_PID=1 / P_PGID=2`。

## 4. ctypes 映射事故與修復（重要教訓）

初版 binding 將 Darwin `siginfo_t` 映射為 64 bytes。kernel 對 `waitid(2)` 會寫滿 104 bytes → 越界寫入相鄰 heap → 延遲 SIGSEGV（crash report 徵狀：`EXC_BAD_ACCESS at 0x…aa`、`_PyType_Lookup` 解引用 malloc poison `0xaa`，崩潰點與肇因無關）。時間軸證據：最後一次 crash 為 14:25:23（`dev-after.log` 寫入時間吻合，屬修復前 run）；heap 修復後的 `worker-after2.log`（14:28）、`worker-after3.log`（14:30）與其後所有 run 期間 **再無任何新 crash report**。

修復方式：

- `_SigInfo` 加 `_pad = c_int*24`（`sizeof = 128 ≥ 104`），並於 binding 建立時自檢 `ctypes.sizeof(_SigInfo) < 104 → return None`（fail-closed，不猜測）；
- 回傳 plain Python `_InfoView(si_pid, si_uid, si_status)`，任何 ctypes 物件不逸出呼叫邊界；
- 錯誤語意：`result != 0` 時取 `errno`；`ECHILD → ChildProcessError`（與 `os.waitid` 一致）、其他 → `OSError`；
- `si_pid == 0` 視為「暫無可觀察事件」→ `None`（與 `WNOHANG` 契約一致）；
- 呼叫端任何異常仍 fail-closed `OPENCODE_CLEANUP_UNKNOWN`，**永不** 退回 `poll/wait`（會提前 reap）。

## 5. 生產修復（`llmtool/opencode_worker.py`，+80 行）

- 新增 `import errno`、`import sys`；`_libc_waitid_binding()` 與模組常量 `_WAITID_FALLBACK`。
- `_libc_waitid_binding()` 僅在 `os.name == 'posix' and sys.platform == 'darwin'` 建立，其餘平台直接 `None`（不 import ctypes、無額外成本）。
- `_observe_exit_without_reap` 內 platform dispatch：

```python
waitid = getattr(os, 'waitid', None)
if not callable(waitid) and sys.platform == 'darwin':
    waitid = _WAITID_FALLBACK
```

- 旗標安全驗證不變：`P_PID`/`WEXITED`/`WNOHANG`/`WNOWAIT` 必須為非零 int，否則 UNKNOWN；`pid` 必須為正 int。
- 無第二套 Supervisor、無 daemon/controller、無新抽象層；沿用既有 process lifecycle 觀察點。
- Linux 路徑：`os.waitid` callable → 直接使用；`sys.platform != 'darwin'` 使 fallback 恆為 `None`（雙重 gate），行為 byte-identical。

## 6. 測試修復（`tests/test_opencode_worker.py`，+122 行）

1. `patch.object(w.os, 'waitid', ..., create=True)`：三處 seam（wait 錯誤模擬）在 macOS 缺原生屬性時可建立，在 3.13 等有屬性平台正常覆蓋/還原。
2. missing-primitive 測試（原 fail-closed 驗證）追加 `patch.object(w, '_WAITID_FALLBACK', None, create=True)`，在 macOS 仍可重現「原語缺失 → UNKNOWN」。
3. 新增測試端 `_test_libc_waitid()` / `_waitid_nowait(pid)`：獨立於生產 seam 的 libc waitid（同步套用 104B screen），用於驗證 leader 在觀察後仍 waitable；`_still_waitable` 改用該 helper。
4. 兩處固定 1200ms 預算改為 `tight_budget()`，詳見第 7 節。

未使用 skip、未 mock 掉被測語意、未放寬任何 assertion；`state/home` 必須為空的生產不變量未被觸碰（曾嘗試 pyc symlink 進 home，被 foreground host `admit` 的 empty-home 檢查擋下並回退，該不變量維持 23/23 OK）。

## 7. 殘餘 3 項 timing 失敗：fixture platform floor（與 waitid 無關）

修復 waitid 後仍有 3 項失敗（`test_after_source_reconciliation…` 的 auth/unknown、`test_timeout_and_output_limit…` 的 overflow）。逐項追查結果：

1. 失敗模式一致：`OPENCODE_TIMEOUT`（stage=CLEANUP）或 `('check','after')` 計數 0，且實測 wall 恰為 ~970ms ≈ work deadline（1200ms 預算扣 20% 準備區間後的 960ms）→ 不是 racing，是**合法 pipeline 無法在預算內完成**。
2. cProfile：parent 端 spawn 僅 2ms，950ms 全在 `selector.select()` 等事件——瓶頸在 fixture 子進程啟動，非生產邏輯。
3. 子進程成本實測：Apple CLT Python 的 pyc 快取固定在 `$HOME/Library/Caches/com.apple.python`（依 HOME 解析）；fixture 於「必空」sandbox HOME 下無快取可用，每次重編譯標準庫 http 鏈（~350ms/child）。server+client 串行 → 合法 pipeline 0.97–1.57s（隨機器負載變動），壓過固定 1200ms。
4. 已排除但無效的方向（留檔以免重複）：`-E`/`-I` 移除、`-S`、`PYTHONPYCACHEPREFIX`（被 `-I` 忽略）、`__PYVENV_LAUNCHER__`、把 pyc symlink 進 sandbox home（破壞 empty-home 生產不變量，已回退）。

採用的修法是**不改 assertion 的預算派生**：`tight_budget()` 在進程內以兩次有界（10s 上限）全 pipeline 量測取 max 為 floor，`budget = max(1200, floor*1.5)`。當 floor ≤ 800ms（Linux CI 的常態）時數值即為原本的 1200ms，行為不變；macOS 慢機器才放大。被測語意（timeout 於 deadline 觸發、overflow 先於 deadline 觸發、no-resend、after-reconciliation 必跑）完全不變。

## 8. 驗證矩陣（targeted → regression → 完整）

| 階段 | 結果 |
|---|---|
| Targeted：上述兩個 timing 方法（兩輪） | OK ×2 |
| `test_opencode_worker.py` | **OK 47/47** |
| `test_opencode_direct_developer.py` | **OK 18/18** |
| `test_opencode_foreground_host.py`（home 不變量） | **OK 23/23** |
| `test_cli.py` / `test_validator.py` | OK / OK |
| `test_fd_lifetime` / `test_run_budget` / `test_cli_lifecycle_state` / `test_cli_provider_lifecycle` | OK / OK / OK(skipped=1) / OK |
| 完整套件 `make test` | **Ran 1877 tests — OK (skipped=3), exit 0** |

修復前 23F+14E → 修復後 0。全程未出現 SIGSEGV；所有結果 `cleanup_confirmed=True`、process `reaped=True`、`group_absent=True`。

## 9. 獨立自審

- 模型：`opencode/glm-5.3-flash-free`（與開發模型 `deepseek-v4.1-flash-free` 不同），獨立 read-only session。
- Gate：Security / Functional / Mac Native / Regression / Resource Cleanup → **VERDICT: PASS**。
- 已修正：MEDIUM（docstring 預算閾值口徑 860 → 實際 800）、`_errno` import 移至檔頭。
- 留檔 NIT：EINTR 無 PEP-475 自動重試（呼叫為非阻塞 WNOHANG|WNOWAIT，且任何錯誤 fail-closed，無安全影響）；`ChildProcessError(number)` 不設 `.errno` 屬性（isinstance 與通捕行為正常）。

## 10. A–G 驗收

- **A. 37 項全消除或逐項證據**：成立（worker 19F+8E→0、dev 4F+6E→0；根因鏈逐項實證）。
- **B. Targeted PASS**：成立（47/47、18/18、23/23 及相鄰模組）。
- **C. 完整 PASS**：成立（1877 OK、skipped=3）。
- **D. 無新增殘留 process/FD**：成立（reaped/group_absent/cleanup_confirmed；修復後無新 crash report）。
- **E. Linux 相容不破壞**：成立（平台雙 gate；Linux 預算維持 1200ms 逐 byte 等值）。
- **F. Self-Review PASS**：成立（不同免費模型、五 gate）。
- **G. Draft PR 等正式 Reviewer**：成立（#270，draft、base=main、2 files、HEAD 與 GitHub oid 一致）。

## 11. UNKNOWN / 待正式 Reviewer

- **UNKNOWN**：CLT Python 在 sandbox HOME 下單 child ~350ms 的 C 層機制未再深入（已實證 pyc 快取依 HOME 解析；此成本僅影響測試 fixture latency，不影響生產語意）。
- **UNKNOWN**：Linux CI 尚未在本工作包內執行（本機為 macOS）；需 Reviewer/CI 於 Linux 驗證 1877 tests 與 1200ms 預算等值行為。
- 待 Reviewer 驗證：真機複驗 WNOWAIT 不 reaping 與 siginfo offsets；`tight_budget()` 派生設計是否接受（量測上限 10s×2、非任意放寬）。
- 本工作包未合併 main、未修改 PR #266、未觸及 Issue #267、無付費 Provider、未建立 daemon/controller。

## 12. 追加修正（Claude Developer 接手，2026-10-22）

- **CI 事實**：HEAD `7ba248c` 的 CI run 39741616251：check (3.9) SUCCESS、check (3.14) FAILURE——`test_orchestration.OrchestrationCLITests.test_explicit_text_and_file_never_read_stdin` 預期 stderr `''`，實得兩次 `ResourceWarning: Implicitly cleaning up <TemporaryDirectory '/tmp/opencode-worker-…'>`（Ran 1877, failures=1, skipped=6）。
- **根因**：§7 的 `_pipeline_floor_ms()` 建立一個不會被 test runner 執行的 TestCase 實例 `case`，並兩次呼叫 `case.job()`；`job()` 以 `self.addCleanup(temp.cleanup)` 登記 fixture `TemporaryDirectory`，但該實例的 cleanups 從未執行，兩個目錄只能在之後的 GC 時被 finalizer 隱式清理，警告因而落入之後某個擷取 stderr 的測試。
- **修法（修洩漏，不抑制警告）**：量測迴圈包在 `try/finally: case.doCleanups()`，兩個 fixture 目錄在量測結束即明確清理。未改任何 assertion、預算公式或生產程式碼。
- **本機驗證（macOS，本 worktree）**：
  - `/usr/local/bin/python3.13 -B -W always::ResourceWarning -m unittest tests.test_opencode_worker` → Ran 47, OK，ResourceWarning 0 次。
  - `/usr/bin/python3`（Apple CLT 3.9.6）`-B -W always::ResourceWarning -m unittest discover -s tests -p test_opencode_worker.py` → Ran 47, OK，ResourceWarning 0 次。
  - `make check PYTHON=<scratch Python 3.13.3 venv：requirements-ci.txt + editable install>` → Ran 1877 tests, OK (skipped=2), exit 0；log 中 ResourceWarning 0 次。
  - 本機無 Python 3.14；3.14 結果以本次 push 後的 exact-head CI 為準（見 PR 留言）。
