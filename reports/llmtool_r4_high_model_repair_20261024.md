# R4 high-model offline repair — 2026-10-24

Status: implementation and focused offline checks PASS; independent high-model rereview, Gate 8,
formal Claude review, exact-head CI and native acceptance remain pending. No G1–G4
milestone acceptance or merge readiness is claimed.

## Identity and authority

- Contract: [#301 Task Contract v3](https://github.com/davidcatcat/davidcattoolsproject/issues/301#issuecomment-6380698920), plus PM's exact report-path restriction.
- Repository: `davidcatcat/davidcattoolsproject`.
- D6 root: registered private D6 root; local path withheld.
- Task: `a11c-r4-codex-high-20261024`; Developer/session: `/root/r4_high_developer`;
  assigned model: `gpt-6.1-sol`, High. Sole writer under PM-established ACTIVE/LOCKED D6 ownership.
- Branch: `feat/a11c-r4-codex-high-20261024`.
- Approved base and unchanged HEAD: `cf6eb6d92100dc684d49f1f28434d4fd053f3eb6`.
  Candidate is uncommitted; HEAD alone does not identify its implementation. Use the
  ten-file manifest below until the Integrator publishes an exact candidate SHA.
- Verified origin: `git@github.com:davidcatcat/davidcattoolsproject.git`.
- Reviewed B-min implementation SHA-256:
  `8ba9acbdaa42b2bf96de8c43af25c5572532267e919edab712ac8769a81f14ab`.
  Re-ran PM's exact private-baseline invocation before mutation: exit 0,
  `PASS: read-only preflight`. Private inventory evidence remains local.

Read-only source reference was the original locked D6 tree
`a11c-r4-appserver-claude-20261024`, anchored at HEAD
`cdbe12a02f0650de228edafdfa313e14b0cd7c87`. Its adapter candidate was uncommitted,
so these ten files were read from that working tree, not attributed to HEAD blobs.
The source manifest SHA-256 was
`7f80fa20849b579f6c2644052fb20eb1378dc319588e58221541011ff9d0081a`.
No writes, tests, ownership changes or Git mutations were performed there.

## Delivered repairs

1. **Shell snapshot propagation:** add `shell_snapshot` to the disabled launch
   features and derive the isolated feature config from the same tuple. Thus argv
   contains `--disable shell_snapshot`, and `[features]` explicitly disables it,
   alongside `shell_tool` and `unified_exec`. A fake-child regression checks the
   actual launched argv, isolated config, clean shutdown and empty process group.
2. **Budget widening:** the ported `RunLedger` accepted a broader `RunBudget`.
   An offline reproducer admitted three requests/two retries. Reject caller budgets
   exceeding any route hard limit before filesystem side effects; respect tighter
   wall limits for remaining runtime and launch admission as well as requests.
3. **Terminal event versus complete delivery:** the proxy marked terminal usage
   followed by upstream truncation or timeout `SUCCEEDED`. Success now requires a
   clean upstream end and a successfully flushed downstream final frame. Terminal
   usage without that completion remains `UNKNOWN`; follow-up forwarding is denied.
   Added malformed-terminal and unterminated-terminal SSE cases alongside the two
   failure reproductions. Existing healthy-stream and downstream-disconnect
   assertions remain intact.

4. **Independent-review framing repair:** independent high-model review found
   that `HTTPResponse.read1()` returns empty bytes on premature fixed-length FIN
   while its remaining `length` stays positive. Review also confirmed the stdlib
   accepts chunked EOF before the required trailer-section terminator. Both cases
   reproduced `SUCCEEDED` despite incomplete upstream framing. A per-connection
   `_StrictHTTPResponse` now records the chunked trailer CRLF terminator and
   requires completed chunk state; fixed-length completion requires `length == 0`.
   Unknown/close-delimited framing is refused. Each failure becomes `UNKNOWN` and
   denies replay. Added short-fixed-body, missing-chunk-trailer and unknown-framing
   regressions plus healthy fixed-length success; existing healthy chunked,
   disconnect and truncation tests remain unchanged.

The other candidate files were ported within the ten-file allowlist. The
App-server injection seam remains off by default and cannot be enabled through
provider fields/options. No production path was enabled.

## Commands and observed results

Commands below use the D6 root unless a different cwd is named. No full suite or
GitHub Actions was run.

| Check | Exact command | Result |
| --- | --- | --- |
| Shell regression, before repair | `./tools/llmtool/.venv/bin/python -B -m unittest discover -s tools/llmtool/tests -p test_codex_app_server_runtime.py -k test_shell_snapshot_is_disabled_in_config_and_launched_argv -v` | exit 1; 1 test, 0.634s; missing config setting |
| Shell regression, repaired | same command | exit 0; 1 test, 0.389s |
| Intermediate combined baseline, before final budget/proxy repairs | `./.venv/bin/python -B -m unittest discover -s tests -p 'test_codex_*.py' -v`, cwd `tools/llmtool` | exit 0; 187 tests, 108.675s; superseded |
| Budget regressions, before repair | `./tools/llmtool/.venv/bin/python -B -m unittest discover -s tools/llmtool/tests -p test_codex_app_server_budget.py -k custom_budget -v` | exit 1; 2 tests, 0.007s; 6 failing subcases |
| Budget module, repaired | `./tools/llmtool/.venv/bin/python -B -m unittest discover -s tools/llmtool/tests -p test_codex_app_server_budget.py -v` | exit 0; 25 tests, 0.080s |
| Terminal timeout/truncation, before repair | `./tools/llmtool/.venv/bin/python -B -m unittest discover -s tools/llmtool/tests -p test_codex_responses_proxy.py -k test_terminal_usage_then -v` | exit 1; 2 tests, 1.549s; actual state SUCCEEDED instead of UNKNOWN |
| Terminal cases, repaired | `./tools/llmtool/.venv/bin/python -B -m unittest discover -s tools/llmtool/tests -p test_codex_responses_proxy.py -k terminal -v` | exit 0; 8 tests, 6.598s |
| Earlier combined candidate, superseded after independent-review FAIL | `./.venv/bin/python -B -m unittest discover -s tests -p 'test_codex_*.py' -v`, cwd `tools/llmtool` | exit 0; 193 tests, 114.354s; superseded |
| Unknown-framing regression, before review repair | `./tools/llmtool/.venv/bin/python -B -m unittest discover -s tools/llmtool/tests -p test_codex_responses_proxy.py -k framing -v` | exit 1; 6 tests, 12.677s; unknown framing incorrectly SUCCEEDED |
| Short fixed-length body, RED | `./tools/llmtool/.venv/bin/python -B -m unittest discover -s tools/llmtool/tests -p test_codex_responses_proxy.py -k premature_fin -v` | exit 1; 1 test, 0.516s; SUCCEEDED instead of UNKNOWN |
| Missing chunked trailer terminator, RED | `./tools/llmtool/.venv/bin/python -B -m unittest discover -s tools/llmtool/tests -p test_codex_responses_proxy.py -k trailer_terminator -v` | exit 1; 1 test, 0.510s; SUCCEEDED instead of UNKNOWN |
| Proxy module after review repair | `./tools/llmtool/.venv/bin/python -B -m unittest discover -s tools/llmtool/tests -p test_codex_responses_proxy.py -v` | exit 0; 62 tests, 61.314s; PASS |
| **Final combined focused set after review repair** | `./.venv/bin/python -B -m unittest discover -s tests -p 'test_codex_*.py' -v`, cwd `tools/llmtool` | **exit 0; 197 tests, 118.611s; PASS** |
| Initial tracked diff whitespace, before review repair | `git diff --check` | exit 0; historical check |
| All ten source/test files | Python AST parse, trailing-whitespace scan, exact allowlist/status comparison and SHA-256 manifest verification | PASS |

The combined pattern selects exactly the five authorized Codex test modules.
Tests use synthetic credentials, loopback servers, pipe peers and temporary fake
Python executables; no installed Codex/App-server was invoked. Fake test ledgers
are disposable fixtures and do not consume the actual launch allowance.

## Self-review and remaining gates

Re-read the ported runtime/client/ledger/proxy/provider paths, focused tests, the
base provider diff and the complete repair diff against the locked candidate in a
separate read-only self-review pass. Confirmed default-off injection, fixed
loopback destination/path, redirect/upgrade denial, synthetic environment,
per-request cap, request/retry/launch admission, UNKNOWN replay denial,
deadline-bound pipes/network, process-group cleanup and unchanged assertions.
The initial self-review missed the permissive fixed-length/chunked EOF behavior;
independent high-model review returned a blocking FAIL. The framing repair above
addresses those reproduced findings. A fresh read-only self-review checked the
strict adapter, EOF guard, per-connection scope and all four new tests. No further
reproducible blocker was found within this repair pass. Self-review is not
independent acceptance; the repaired candidate awaits independent rereview.

This follow-up changed only the proxy, its test module and this report. Compared
all ten source/test hashes against the earlier frozen candidate: the other eight
are byte-identical. AST and whitespace validation passed, and the new source/test
manifest is recorded below. No Git commands were run for this follow-up. Local
paths remain withheld from this publication artifact.

Launch 8 remains blocked on the PM's separate Gate 8 review and task-required
independent high-model rereview. The contract reports seven of eight actual
App-server launches already used; this task added zero. No runtime claim is made
for the repaired installed CLI: real-shell suppression and full native behavior
remain untested here. Formal separate-context Claude review, exact-head CI and
applicable native gates still apply before merge. No commit, stage, push,
publication, ownership transfer, paid fallback, external Provider call, real
credential access or production enablement occurred.

Developer token usage/cost: **UNKNOWN**. No provider usage or savings claim.

## Changed files and frozen candidate hashes

The only additional changed file is this report. Source/test SHA-256 values:

| Path | SHA-256 |
| --- | --- |
| `tools/llmtool/llmtool/codex_app_server.py` | `9da73ab3daacfa81fed8b8eb75b2f3dcb8a7d3310a11bc460b418587efa046be` |
| `tools/llmtool/llmtool/codex_app_server_budget.py` | `4af9a91142d0c6fd0ce5688477872750d7f5566276870af2b496ba8fa8ad97d6` |
| `tools/llmtool/llmtool/codex_app_server_runtime.py` | `0af3e88f3af82f98971ee17fbc89bb33584a664ea39a2c3503b7dedd8078d585` |
| `tools/llmtool/llmtool/codex_responses_proxy.py` | `72a8e6c5d98e9f16ac2e1d54ca2dabef757a3ce908a96abfcf95269e00ffaaeb` |
| `tools/llmtool/llmtool/providers/codex_cli.py` | `b5575f23e23740d87a601430d6ff7f1ed1fa4bbbd15eb9cf7b8e40ad08075e32` |
| `tools/llmtool/tests/test_codex_app_server.py` | `b0306b89ead05823fc0deba0f1e191900fb2e2974b9bb5d7854cb17b75b2d30c` |
| `tools/llmtool/tests/test_codex_app_server_budget.py` | `2a8fc729d91f2429feb1bedf8151a411217511d2a2b35e97db92d3e4e3868af9` |
| `tools/llmtool/tests/test_codex_app_server_runtime.py` | `2176447d54babf28ccbedb19bcf200aa86dd08b5c2dc296b010f7ee542353f96` |
| `tools/llmtool/tests/test_codex_responses_proxy.py` | `ece22a3b3e18f4b460db335dac033129e1430246e5beaeb990cd88a75f82449d` |
| `tools/llmtool/tests/test_codex_cli_provider.py` | `1b3bb5efce8209f30d0635bf3f5d1a29662bbbd5e7b113a10f55476ebda39e81` |

Candidate source/test manifest SHA-256:
`adc703feec336df0b8cddc8ab62970a1b4b02c01508092d724f77d9d8a6aedc9`.
Manifest encoding is UTF-8 concatenation of lexicographically sorted records:
relative path, NUL, lowercase file SHA-256, newline. The report is excluded to
avoid a recursive hash.

你需要做：無
