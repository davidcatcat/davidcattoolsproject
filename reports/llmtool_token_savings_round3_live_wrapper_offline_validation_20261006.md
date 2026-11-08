# Round 3 C3-A — live wrapper offline validation (2026-10-06)

C3-A implementation and offline verification are complete. Independent C3-B review
is pending; **live Company Phase 0 is NOT AUTHORIZED by this step**. No live results
or semantic benchmark outputs were created.

## Task Contract and provenance

- Contract: [C3-A handoff](llmtool_token_savings_round3_live_wrapper_freeze_handoff_20261006.md),
  frozen as present at starting head `d1b755f3641479af3413d0e84d1e12b150f4a8d9`.
- Canonical actual Mac repository:
  `/Users/davidsun/git/davidcataiporject/davidcattoolsproject` (Darwin).
- Repository: `davidcatcat/davidcattoolsproject`; existing Draft PR #38.
- Branch: `bench/llmtool-token-savings-round3-format-compliance-20261006`.
- Exact tested head: `3b44341cfc2cca8d902f92681cda593fb09dec7d`.
- Python: `/usr/bin/python3`, **Python 3.9.6**.
- The publication following this implementation is report-only. It changes none
  of the tested wrapper, instructions, manifest, helper, fixtures, gold or runtime
  bytes. A report-only head does not establish live verification or C3-B approval.

Authorized scope: separate experiment wrapper/tests, two frozen instruction files,
Request preregistration and this report. Acceptance: actual CLI/client Request
construction with zero network, strict before-network admission, gold isolation,
complete usage accounting, replay reconciliation, existing 88-test regression and
Python 3.9 wrapper tests. Company/Codex calls, production or runtime skill changes,
fixture/gold changes, retries/fallback and merge remain prohibited.

## Changed artifacts and implementation

Only these six new files belong to C3-A:

- `reports/token_benchmark/20261006_round3/live_wrapper.py`
- `reports/token_benchmark/20261006_round3/test_live_wrapper.py`
- `reports/token_benchmark/20261006_round3/instructions/L02.txt`
- `reports/token_benchmark/20261006_round3/instructions/L03.txt`
- `reports/token_benchmark/20261006_round3/phase0_preregistration.json`
- This validation report.

The wrapper invokes the real `llmtool.entrypoint.main` and unchanged real
`OpenAICompatibleClient`. A scoped instance `opener.open` observer captures the
constructed `urllib.request.Request` and raises `DryRunCaptured`, a dedicated
`BaseException`. The original opener is never called in the freeze path. Socket
construction, connection, DNS, subprocess creation and original urllib opener are
blocked during offline tests/dry runs. The zero-network spy test independently
counts each of those entry points and asserts **0** calls. This is actual Request
construction evidence, not live inference evidence.

An ordinary temporary runtime HOME holds only synthetic config/input and is
removed after each invocation. The environment is cleared within the scope,
credentials are null, and the fixed config is restored by context-manager exit.
The wrapper rejects a project `.llmtool.yaml` override rather than changing it.
Real user config is not read or mutated by the invocation. No repository copy or
alternate checkout is created.

Instruction files deliberately have no terminal LF. User-message bytes are exactly
`instruction_bytes + b"\n\n" + fixture_bytes`, without strip or normalization.
Each Request has exactly two messages, ordered `system`, `user`, and exactly the
top-level fields `model`, `temperature`, `max_tokens`, `messages`,
`chat_template_kwargs`. POST endpoint is
`http://home-llm.example:8000/v1/chat/completions`; **the endpoint was not contacted**.
Actual constructed bodies prove `model=gw`, numeric non-bool `temperature=0`,
integer non-bool `max_tokens=4096`, exactly `enable_thinking=false`, and no
Authorization. Config timeout is 120 seconds.

The future observer verifies all frozen artifact hashes, full Request-body hash,
exact user-message hash and strict Request shape before the reviewed
`observe_request_without_mutation()` and one possible original-opener delegation.
No retry API exists. The executable script exposes **only `--freeze`**, not live
execution. Future `Phase0Wrapper.run_next` is a library path requiring separate
C3-B review and live authorization; it was exercised solely with in-memory fake
responses under network guards. The future handoff can pin the manifest digest
using `load_preregistration(expected_sha256)` before decoding its contents.

## Frozen SHA-256 values

| Artifact | SHA-256 |
| --- | --- |
| `live_wrapper.py` | `9a8db0770fff71417ae9ad2bf324d657803964f764273ae2eabf51a83c2cc94b` |
| `test_live_wrapper.py` | `ca0975a08df18de11b8f7bc7f59ba8eccd7c8e0455e9c3f1a97d5b8c4b765864` |
| `format_probe.py` (unchanged) | `500fb8bd990d3a5bdb6e18779619ca87a21af964a84682f88a10741b4fee87a3` |
| `test_format_probe.py` (unchanged) | `80e62df3140e10b34cfe6cf22188e219e53ca0aac0568d08602e0e4169cefd79` |
| Config bytes | `2482b927cc91ebcbde20325bda4574db48ed5987fa7a1e8ff7c7079405bfd790` |
| `phase0_preregistration.json` | `81ad71b05c31c2cf858f29ec87027818252a40041edf4f0b363f133d22c09ece` |

| Lane artifact | L02 SHA-256 | L03 SHA-256 |
| --- | --- | --- |
| Instruction | `690e22577b3f4c4d8b39dee5a9bd68fa713ec3d9c7736182a7d73d3b5573168e` | `f59de65889933c342cd0c9d30cb29c314a7c56fb25f5b75d1c049dc49a513d12` |
| Fixture | `859effa1e4d6e978372f4914aca8c53e4af2953f3850ceaad2a68732060892da` | `44caecfb4fc77e639d471710b6fc190ca2e1145ae94a22396c5cdcc487dd88ce` |
| User message | `e0194460028b4d7a08b6607141285da5352f0c71a4cff0d8a4f57fe2686a22ac` | `3b94f0d8ba02b4e1d206beeb184a2d50553d2e1d48efcac87a069030513645e2` |
| Full Request body | `cc3c79f8e9a55356b1f8c1a464ecef5dcf4a45646a1eb7c07cf59315ec4e360f` | `6c1f7b77db218ae9962a78bae46190cab38c0c5277a4b952a967bc8eaac7cdf0` |
| Runtime skill | `92881dde1c5aed76d0d189902fc523e129c9069ed633048493e5a92063b880b0` | `a53a6e48373cfd0a1b688b17aaad4644e550c8b52a4b27e8320ecd77c60a8809` |

L03 gold/evaluation metadata SHA-256:
`0e54eb372345ba64105512744c09a7cb60027fbebeebd04fd7834f51bbf2e8d8`.
The manifest also freezes the relevant CLI, input, config, prompts, skill loader,
A4 parser and OpenAI-compatible provider source hashes. Freeze reads no gold; its
known evaluation digest is copied from the authoritative handoff.

## Python 3.9 execution evidence

Executed from the canonical repository on the exact tested implementation commit:

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_live_wrapper.py
git diff --check
```

| Check | PASS | FAIL | ERROR | SKIP | Exit | Observed suite seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Existing format-probe regression | 88 / 88 | 0 | 0 | 0 | 0 | 0.173 |
| Wrapper offline suite | 70 / 70 | 0 | 0 | 0 | 0 | 3.269 |
| `git diff --check` | PASS | 0 | 0 | 0 | 0 | n/a |

The initial zero-network freeze command also exited 0:

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/live_wrapper.py --freeze
```

After commit, the pinned manifest was loaded and each lane's dry run repeated;
the complete returned freeze facts matched preregistration exactly. Repeated hash
stability is also covered by `test_l02_repeated_freeze_stable` and
`test_l03_repeated_freeze_stable`. Freeze captures one real Request per lane and
sends zero. Other suite Request constructions and fake transport attempts are
offline test activity, not Company attempts.

## Failure paths, replay and isolation

- Request: extra/stream/tools/tool_choice/response_format fields, duplicate JSON
  keys, NaN/Infinity, Authorization (including unredirected headers), URL/method,
  model, numeric values/bool coercion, thinking drift, message shape, user bytes,
  system text and serialized-body whitespace drift all reject before delegation.
- Gold: freeze and actual Request construction run with gold-file access denied.
  The valid anchor adapter runs with all file I/O denied. Only after frozen L03
  anchors exist does the target loader verify gold bytes' digest, decode them and
  return only `target_ids`. A mismatched digest rejects before decoding.
- Usage: all three integers must be present, nonnegative, and sum correctly.
  Missing/incomplete/bool/negative/inconsistent usage becomes unknown. Tests show
  an emitted success with unknown usage cannot PASS and stops later requests.
- Safe production transport and length errors are captured as typed error codes
  from the real client boundary. Synthetic real-provider error tests confirm
  transport mapping and `MODEL_OUTPUT_INCOMPLETE`, with no raw stderr published,
  no retries and no raised token limit.
- Finalization: L02 uses fixture-only canonical coverage. L03 freezes anchors
  first and then performs verified post-freeze target recall. Anchor-stage
  evaluations cannot complete accounting; 3/7 cannot PASS. Per-lane early stop,
  six-trial order, 300k ceiling and 35k/59k admission bounds are unchanged and
  covered by the existing reviewed 88-test helper suite.
- Replay: preserved raw stdout is reconstructed and evaluated again before
  accounting. Final UTF-8 `result_json` bytes, evaluation hash and raw stdout hash
  must match. Honest L02/L03 replays PASS; internally consistent forged evaluations,
  changed raw output, changed fixture and changed grading metadata are rejected.
  Original evaluation/accounting state remains unchanged on rejection. Wrapper
  integrity failure halts the future runner without retry or semantic PASS.
- Publication: `RawStdout` computes the original-byte SHA before secret scan and
  adapter parsing. A synthetic secret marker blocks publication without redaction
  or alteration of the bytes. Future raw artifacts use exclusive private-file
  creation; replay reads those preserved bytes before accounting completion.
- Instructions: verbatim EVENT/prose-placement and one-record-one-evidence/no
  inventory rules are checked. Neither instruction contains any frozen gold
  target ID; the prohibition is intentionally not applied to the full fixture.

L03 full-fixture multimap remains **668 records / 534 distinct pairs / 120 ambiguous
pairs / 254 records in ambiguous pairs**. No fixture or helper modification was
needed.

## Scope and remaining gates

Production/runtime/skill changes: **NONE**. `git diff` against starting head for
`tools/llmtool`, `skills`, both reviewed helper files and frozen Round-2 artifacts
is empty. Existing nine unrelated untracked benchmark/reasoning/case artifacts were
preserved and not staged or cleaned. Tracked changes belong only to C3-A.

NOT-TESTED: live Company inference, live Phase-0 six-trial behavior/usage, semantic
format compliance, Codex benchmark, production full canonical suite and independent
C3-B review. The task requires the two offline suites, not production modifications
or live validation. Current PASS evidence does not establish future inference
reliability, quality parity, token savings, merge readiness or live authorization.
Scoped in-process patches require serial main-thread invocation; this wrapper is
an experiment artifact, not a generic executor or runtime signal framework.

```text
Exact tested head: 3b44341cfc2cca8d902f92681cda593fb09dec7d
Python: 3.9.6
format_probe regression: 88 / 88 PASS
live_wrapper tests: 70 / 70 PASS
Zero-network dry runs: L02 PASS / L03 PASS
Network calls: 0 (provider HTTP)
Socket opens: 0 (offline execution)
Replay reconciliation: PASS
Gold isolation: PASS
Company live calls: 0
Codex calls: 0
Production/runtime/skill changes: NONE
Ready for independent C3 review: YES
Live Company Phase 0: NOT STARTED
```
