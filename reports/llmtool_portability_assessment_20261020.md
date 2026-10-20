# llmtool portability assessment — what happens on another computer (2026-10-20)

**Requested by:** Owner (「如果我的llmtool,放到別人的電腦,會怎樣?」 → 「寫成一個報告,上傳 git, 我找chatgpt討論」).
**Author:** Claude (independent Reviewer), read-only source inspection.
**Source:** main `d41600ff`. Where PR #235 (parked-code removal) changes the picture, this report says so.
**Status:** assessment for discussion. It is not an approved requirement or task, and nothing was changed in code.

## 1. Bottom line

- **The basic commands mostly work on another machine.** That means install, offline validation, chat, provider calls using the other person's own logins or API keys, and read-only Git snapshots.
- **The review/handoff/OpenCode machinery is deliberately bound** to this Mac, this account, this repository and exact CLI versions. On another machine it **refuses to run** (fail-closed) rather than misbehaving.
- **No credential of this Owner travels with the code.** Credentials live in each user's local CLI/OpenCode store or config, never in the repository.

## 2. What works out of the box

| Area | Requirement on the other machine |
|---|---|
| Install | Python ≥ 3.9, `PyYAML>=6,<7` (`tools/llmtool/pyproject.toml`); `pip install -e tools/llmtool` |
| Config | Their own `~/.config` file based on `config.example.yaml` / `config.example.company-qwen.yaml` |
| Offline commands | `validate`, `action`/`approval validate`, `benchmark`, `skills`, `diagnostics doctor/runs/trace`, `feedback`, `git` read-only snapshot |
| Provider calls | Their own installed and logged-in `claude` / `codex` CLI, or their own API key. llmtool launches the CLI per call (`claude --print …`, `codex exec …`); **no CLI window needs to stay open** |

## 3. What breaks or refuses (with evidence)

| # | Coupling | Evidence (at `d41600ff`) | Effect elsewhere |
|---|---|---|---|
| 1 | **Hard-coded Owner paths** | `review_bundle.py:25-26`, `review_exchange.py:22-23`, `test_triage_worker.py:173-175` (`/Users/davidsun/git/...`, `/Users/davidsun/.local/share/llmtool-dev-workspaces/...`). Also `rollout_census.py:65-66` and `_git_add_private.py:36`, both **removed by PR #235** | Review bundle/exchange and test-triage worker cannot find their roots, so they refuse |
| 2 | **Hard-coded GitHub repository** | `task_review_collector.py:202,221,391`, `review_authority.py:31,104,291`, `review_exchange.py:167`, `review_bundle.py:128`, `test_triage_worker.py:59` (`davidcatcat/davidcattoolsproject`, Issue #120 URL patterns) | The review/handoff pipeline only accepts this repo; a fork or another repo is rejected |
| 3 | **Exact CLI version pins** | `opencode_foreground_host.py:34` and `opencode_worker.py:228` (OpenCode **2.0.32** only); `review_authority.py:170` (Claude CLI **2.1.301/2.1.303** only) | Any other installed version is rejected until the pin is updated and re-reviewed |
| 4 | **Machine-bound Host registration** | Host pin files, executable SHA-256 pins, ledger device/inode, D6 workspace parent and registry under the canonical `.git` (see `skills/safe-git`, D6) | Must be re-created per machine; copied files are rejected (device/inode/hash mismatch) |
| 5 | **Company model route** | `company-gw` needs the company VPN and the company profile (`config.example.company-qwen.yaml`) | Unavailable outside the company network; this is a route status, not a crash |
| 6 | **OS assumptions** | `providers/cli_lifecycle.py` (Darwin process-group semantics), `diagnostics_store.py:67,236` (Darwin/Linux only; `renameatx_np`/`renameat2`), `diagnostics_settings.py:83` | macOS fully targeted; Linux partly (CI runs Linux tests); **Windows is not supported** |
| 7 | **The AI-team workflow** | PM/Developer/Reviewer roles, D6/D8/D8a decisions, Issue #120 as the coordination log, the monitor's default repo (`scripts/llmtool_monitor.py`) | Another person needs their own repo, issue, agents and Owner decisions; nothing transfers automatically |

## 4. Options for discussion

| Option | What it means | Cost / risk |
|---|---|---|
| **A. Keep personal (recommended now)** | Treat llmtool as this Owner's Mac tool; document the coupling (this report) | Zero cost; nothing is promised to others |
| B. Minimal portability | Move the hard-coded paths and repo name into the config (`workspace_parent`, `canonical_root`, `repository`, `coordination_issue`); keep exact version pins but make them a reviewed allowlist in config; add an install/first-run checklist | Small to medium: about 6 modules plus tests. Security review needed because these values feed the trust boundary: config must not let a caller widen authority |
| C. Full multi-user product | B, plus per-user Host registration bootstrap, Linux parity for process lifecycle, packaging, a docs site, and a separate multi-tenant threat model | Large; this is a new product direction and would compete with the current G3/M2 priority |

**Recommendation:** A for now. If someone else is meant to use it, do **B** as a bounded task after G3/D4 is live. B is a product requirement, so the Owner decides whether and when.

## 5. Related facts from the same discussion

- On this Mac the Owner does **not** need to keep the Claude/Codex/OpenCode CLI windows open for llmtool's own calls. llmtool starts and stops them per call, and OpenCode via its private `serve` + `run`, using the D8a credential path. The long-lived PM/Developer/Reviewer sessions stay open today only because G3 same-session wake/delivery is not live yet.
- PR #235, once merged, removes about 8,000 lines of parked code, including two of the hard-coded-path modules above.
