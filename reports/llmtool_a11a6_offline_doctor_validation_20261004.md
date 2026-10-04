# A11a.6 — Offline doctor and failure matrix — 2026-10-04

Status: implementation and required Cloud local validation PASS; independent
frozen-revision review, exact-head CI, external review and publication gates pending.
Overall A11a remains incomplete. A11a.7 and A11b/A11c/A12 remain unstarted.

## Pinned Task Contract

Task ID: `A11a6-offline-doctor`; version: **v1**. Coordinator accepted this
contract before any repository write. The contract payload below is hashed as
UTF-8, with LF newlines and one final newline; digest is recorded before source
implementation. This development contract is not a new runtime task/schema.

```text
Task ID: A11a6-offline-doctor
Version: v1
Base: da027f0d728e3b3ff605a6d404c49c8beb08ec28
Goal: implement spec section 17 A11a.6 offline doctor and named complete section 18 store/provider/error failure matrix.
Authorized scope: bare human doctor command; minimal strict bounded credential-free config view using existing layering/selection; trusted user diagnostics policy; existing safe provider configuration readiness and which plus absolute executable availability; anchored bounded read-only existing store structure/permissions; meaningful source/tests/docs.
Forbidden scope: network/model/login/probe/subprocess execution by doctor; credential value resolution or credential-env get; auth/session file reads; store creation/write/repair/cleanup/chmod/probe; runs --json/null ordering changes; changed ask readiness/stdout/exits/default-off/one-call contracts; retry/fallback/escalation/executor/router/hooks engines; A11a.7/A11b/A11c/A12 implementation; edits to historical reports/spec; Developer Git mutations.
Doctor output: fully buffered fixed labels/numeric policy/supported provider enum only; no profile/model/origin/path/envname/foreign error text; credential membership is presence only, never validity/authentication; store structure is not completed metadata or task health.
Doctor exits: successful factual inspection 0 including absent store, unconfigured readiness, missing executable or unsupported persistence platform; invalid config/home, unsafe/unavailable store, bounds/races fixed safe error 1; usage 2; interrupt 130; broken pipe 0.
Stack policy: retain fixed INTERNAL_UNEXPECTED and safe debug event summaries; collect no Python traceback, stack paths, locals, exception repr or raw messages.
Required evidence: doctor no-network/client/process/auth/secret-resolution/value-access tripwires; policy/layering/provider-readiness tests; nonmutation/absence/private owner/modes/symlink/hardlink/nonregular/active/locks/race/bounds/platform/output tests; named section 18 existing/new matrix; persisted debug typed provider/store failure combinations proving primary result, stdout, exits, privacy, one call and no retry/fallback.
Validation: fresh focused regressions, canonical make check including compileall, editable and fresh wheel CLI smoke/provenance, docs/diff checks, exact patch digest; independent frozen revision review with at most two correction rounds; final-head Python 3.9/3.14 CI, draft PR and external review gates remain pending until supplied.
Delivery: configured existing checkout only; Root sole Git mutator; Developer freezes exact file allowlist and digest before Root commit; no merge or next slice.
Environment limits: Cloud Linux offline/mock evidence only; actual runtime model/reasoning UNVERIFIED; Mac/live/auth/inference optional NOT-TESTED, never inferred from presence or mocks.
```

Contract SHA-256: `a6c3bed07312c955b00a506f61984140e1a559de5dff09cec6855004f4e6b4d5`.

## Checkout and authorization

- Existing configured checkout: `/workspace/davidcattoolsproject`.
- Repository: `davidcatcat/davidcattoolsproject`.
- Branch: `feat/a11a6-offline-doctor-cloud-20261004`.
- Clean base HEAD: `da027f0d728e3b3ff605a6d404c49c8beb08ec28`.
- Coordinator independently confirmed PR #22 merged at that exact SHA and
  post-merge main CI `37193806074` SUCCESS, Python 3.9/3.14, each 824 tests.
  These are historical baseline facts, not validation of this patch.
- Read AGENTS/WORKFLOW, root/tool README, ROADMAP, spec sections 1–20,
  earlier diagnostics validation reports, and inspect-repo/implement-feature/
  validate-runtime/review-change/cloud-environment-runtime skills.
- Requested Developer: `gpt-6.1-sol` / high. Actual runtime attestation:
  **UNVERIFIED**; no authoritative runtime metadata is available.
- Root remains the sole Git writer. No new clone/worktree/checkout is used.
- Inherited runtime guards and existing `/tmp/.git` marker remain intact.
  Prior user authorization permits empty fake-runtime fixtures in `/var/tmp`
  and loopback mocks with explicit additional permissions; no live inference.


## Final implementation and inspection limits

`doctor` is a bare human-output command only. It uses the shared strict layered
configuration view without calling normal request loaders, `api_key`, client
constructors or completion. All configured profiles still undergo existing
validation; provider selection/environment/project precedence remains unchanged.
User diagnostics settings are projected independently of legacy config-show.
Credential presence uses key iteration, because default Mapping and os._Environ
membership can call `__getitem__`. Inspection comparisons canonicalize Windows
environment names to uppercase and preserve POSIX case sensitivity. Empty values are present,
not authenticated. Literal credentials are only labeled configured.

Doctor-only preflight rejects credential bindings to environment names consumed
by configuration overrides/location/executable discovery. It checks all profiles,
including inactive profiles, before provider/environment overrides, diagnostics
home resolution and `which`. Normal ask/config behavior is unchanged. The exact
reserved set lives in `config.INSPECTION_ENV_NAMES` and is tested by value-access
tripwires. Locating the trusted user config necessarily retains existing
HOME/default-location bootstrap **before** bindings are known. That is location
selection, not credential resolution; no alternate auth/session files are read.
Tests with explicit/mocked home verify no credential-value fetch after bindings
are known. This does not claim that process startup/config-path bootstrap never
reads ordinary home-location variables.

Store inspection reuses anchored reader descriptors and shared root/run locks.
It checks private owner/modes, regular single-link files, identities, names and
bounded sizes. It reads no trace/summary/reservation/lock bodies, so a present store
is not a validated metadata/health/completion claim. Busy runs skip mutable
artifacts; unknown names are counted without following or printing. Missing store
is factual absence, with no creation/repair/cleanup/write probe. Unknown data is
preserved. Unsupported persistence skips store/location I/O. Output is fully
buffered and consists only of fixed labels/provider enums and bounded integers.

The inherited ask debug contract is retained: fixed `INTERNAL_UNEXPECTED`, safe
metadata/event summaries, no Python traceback/stack paths/locals/exception repr.
Spec §6 permits optional stack metadata; none is collected here. README/ROADMAP's
obsolete deferred-to-A11a.6 stack wording is replaced with this explicit policy.
`runs`/`trace` implementation/output/sorting remain unchanged. One old reader usage
case asserting bare doctor did not exist is replaced with forbidden `doctor
--json`; every other traversal/usage/security assertion is preserved.

## Executed validation — historical initial candidate c09e832

All commands ran from `/workspace/davidcattoolsproject`, branch
`feat/a11a6-offline-doctor-cloud-20261004`, HEAD
`da027f0d728e3b3ff605a6d404c49c8beb08ec28` plus this identified patch. Python
3.12.14, Linux 6.18.44 x86_64/glibc 2.41, PyYAML 6.0.3. Existing editable CLI
`tools/llmtool/.venv/bin/llmtool` reports `llmtool 0.1.0`; module provenance is this
checkout's `tools/llmtool/llmtool/__init__.py`. Fresh wheel CLI lives at
`/tmp/a11a6-wheel-venv/bin/llmtool`; module provenance is that environment's
`lib/python3.12/site-packages/llmtool/__init__.py`. Build version remains 0.1.0.

Required runtime tests used prior-authorized `with_additional_permissions` with
write `/var/tmp` and network enabled for loopback mocks. Inherited guard variables
and `/tmp/.git` marker stayed intact. Generated temporary fake executable/state
fixtures contain no project checkout. No live model, auth/session, VPN, external
inference or repository Git mutation was performed by the Developer.

| Check | Exact command / evidence | Result |
| --- | --- | --- |
| Final doctor public/adversarial suite | `tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_doctor.py -v` | PASS, exit 0, 23/23, 0.467 s |
| Provider/store/concurrent failure matrix | `tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_diagnostics_failure_matrix.py -v` | PASS, exit 0, 5 methods, 232 public ask fault cases plus 2 controlled same-run concurrency cases, 4.557 s; final canonical executes these again |
| Diagnostics regression before collision repair | `tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p 'test_diagnostics*.py'` | Historical patch PASS, exit 0, 222/222, 6.847 s; final canonical re-executes all on final bytes |
| Final canonical syntax/full unittest | `make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"` | PASS, exit 0, compileall + 852/852, no skips, 22.104 s |
| Offline wheel build | `tools/llmtool/.venv/bin/python -m pip wheel --no-deps --no-build-isolation --no-index --wheel-dir /tmp/a11a6-wheel tools/llmtool` | PASS, exit 0 |
| Fresh wheel environment/install | `tools/llmtool/.venv/bin/python -m venv --system-site-packages /tmp/a11a6-wheel-venv`; `/tmp/a11a6-wheel-venv/bin/python -m pip install --no-index --no-deps --force-reinstall /tmp/a11a6-wheel/llmtool-0.1.0-py3-none-any.whl` | PASS, exit 0; only existing PyYAML dependency inherited, llmtool installed from fresh wheel |
| Editable and fresh installed CLI smoke | `tools/llmtool/.venv/bin/python /tmp/a11a6-smoke.py` | PASS, exit 0: version/provenance, absent/populated/unsafe store, user policy, executable availability without invocation, literal credential presence, invalid config, forbidden doctor JSON, runs/trace human/JSON/errors and bytes/modes/names/inode/mtime/ctime/link-count nonmutation |
| Wheel source/resource/entry-point provenance | Same smoke script: ZIP byte comparison of all packaged Python/skill files and `llmtool.cli:main` console entry point | PASS, 43 files |
| Diff/links/status/matrix references | `git diff --check`; `tools/llmtool/.venv/bin/python /tmp/a11a6-doc-check.py` | PASS, exit 0; 63 local links, 167 concrete matrix method references, pinned contract digest and historical file preservation |

Final wheel SHA-256:
`e2dcd22534069f0d36a9e0976745c2eff8710531bf860ee248cd20d8fad9e6d4`.
The first wheel's digest and 851-test PASS preceded the collision safety repair
and are superseded for final source evidence. Those source/test bytes did not change between the successful 852-test execution
and initial review commit `c09e83274b970c769a21cab666e1b9c1ba0c3669`. They are
historical initial-candidate evidence after the Round 1 repair below.

Initial non-PASS attempts are retained honestly: matrix's first default-sandbox
execution failed before fake CLI spawn because the existing isolation selected
`/var/tmp`, unavailable without the already-authorized explicit permission; the
permissioned run passed. Initial focused/canonical execution failed only on the
historical A11a.5 assertion rejecting bare doctor (851 tests, sole failure); the
narrow obsolete-case update and fresh reruns passed. Installed smoke fixture
initially used the wrong internal RunContext.error signature and failed during
fixture preparation; corrected fixture and fresh wheel/editable runs passed. None
of these initial results is reported as PASS or live validation.

## Complete spec §18 acceptance and failure coverage

Each row names concrete executed tests. References below are test method names
within `tools/llmtool/tests/`; final canonical executes all rows. This is a bounded
case matrix, not a guarantee of every OS/interleaving/semantic failure. Diagnostics
remain observational; no recovery event/call is invented. Current ask is sequential;
there is no new general concurrent event-ID allocator or runtime nested-span flow.
The concurrency row checks the store's existing serialization and fail-closed
sequence contract; the ID/span rows check the pure schema/generators.

### Core/schema

| Requirement / failure | Concrete test evidence |
| --- | --- |
| Run ID exact syntax and bounded collision retry | `test_diagnostics.py:test_invalid_ids_rejected_without_echo`; `test_diagnostics_store.py:test_run_collision_regenerates_and_three_attempts_stop` |
| Unique run/span IDs and exact entropy sizes | `test_diagnostics.py:test_run_id_generation_shape_and_uniqueness`; `test_diagnostics.py:test_span_id_generation_shape_and_uniqueness`; `test_diagnostics.py:test_generators_request_exact_random_byte_counts` |
| Ordered accepted event IDs; concurrent serialized writes and invalid sequence rejection | `test_diagnostics_runtime.py:test_drop_count_warning_once_and_no_sequence_gaps`; `test_diagnostics_failure_matrix.py:test_concurrent_same_run_writes_serialize_and_invalid_sequence_fails_closed` |
| UTC RFC3339 timestamp format and calendar validation | `test_diagnostics.py:test_timestamp_utc_milliseconds_and_timezone_normalization`; `test_diagnostics.py:test_timestamp_requires_exact_format_and_real_date` |
| Exact schema/version and deterministic JSON | `test_diagnostics.py:test_event_schema_and_version_exact`; `test_diagnostics.py:test_event_exact_schema_and_stable_detached_json` |
| Duplicate/unknown/unsupported-version reader behavior | `test_diagnostics_reader.py:test_supported_extensions_unknown_events_and_free_text_omitted`; `test_diagnostics_reader.py:test_invalid_json_duplicates_nonfinite_utf8_and_line_bound`; `test_diagnostics_reader.py:test_invalid_event_schema_types_ids_sequence_controls_and_bounds` |
| NaN/Infinity rejected including deeply nested values | `test_diagnostics.py:test_json_rejects_nonfinite_even_deeply`; `test_diagnostics_summary.py:test_strict_json_bounds_duplicates_constants_utf8_depth` |
| Event/text/cause/metadata structural/size bounds | `test_diagnostics.py:test_safe_text_utf8_bound`; `test_diagnostics.py:test_safe_cause_closed_scalars_ranges_and_bounds`; `test_diagnostics.py:test_json_structural_bounds_and_cycles`; `test_diagnostics_runtime.py:test_memory_budgets_drop_without_unbounded_accumulation` |
| Oversize event rejected/drop accounting, never unsafe truncation | `test_diagnostics.py:test_event_size_enforced_without_truncating`; `test_diagnostics_runtime.py:test_drop_count_warning_once_and_no_sequence_gaps`; `test_diagnostics_ask_persistence.py:test_invalid_metadata_drop_is_counted_and_safe_trace_can_finish` |

### Error/output compatibility

| Requirement / failure | Concrete test evidence |
| --- | --- |
| Legacy LLMToolError str/args unchanged | `test_diagnostics.py:test_legacy_constructor_string_args_and_catching` |
| Successful stdout unchanged and quiet normal stderr | `test_diagnostics_runtime.py:test_normal_success_byte_compatible_and_quiet`; `test_diagnostics_ask_persistence.py:test_saved_modes_preserve_output_and_allowlist_only` |
| Original normal error first line and stable second code/run-ID line | `test_diagnostics_runtime.py:test_expected_provider_failure_first_line_and_code`; `test_diagnostics_failure_matrix.py:test_http_status_failures_persisted_and_secondary_store_faults` |
| Exit 0/1/2/130 and BrokenPipe preserved | `test_diagnostics_runtime.py:test_interrupt_keeps_exit_130_without_retry`; `test_diagnostics_runtime.py:test_broken_pipe_keeps_exit_zero`; `test_diagnostics_runtime.py:test_usage_failure_creates_no_observer`; `test_diagnostics_ask_persistence.py:test_saved_interrupt_and_output_closed_keep_original_exits`; `test_doctor.py:test_broken_pipe_zero_and_interrupt_130` |
| Existing verbose metadata order preserved | `test_diagnostics_runtime.py:test_verbose_preserves_metadata_order_and_appends_safe_fields`; `test_diagnostics_runtime.py:test_verbose_metadata_survives_stricter_event_model_validation` |
| Debug adds stderr metadata only; no global JSON envelope | `test_diagnostics_runtime.py:test_debug_safe_summaries_and_one_request`; `test_diagnostics_runtime.py:test_non_ask_expected_error_and_success_unchanged`; `test_diagnostics_failure_matrix.py:test_http_transport_failures_all_output_modes_without_retry` |
| Usage errors precede run creation and reject doctor extensions | `test_diagnostics_runtime.py:test_usage_failure_creates_no_observer`; `test_diagnostics_reader.py:test_run_id_and_unrequested_flags_rejected_without_echo`; `test_doctor.py:test_bad_home_and_unrequested_options_keep_safe_error_contract` |

### Privacy/redaction

| Requirement / failure | Concrete test evidence |
| --- | --- |
| API-key patterns/Authorization/configured credential values excluded from JSON | `test_diagnostics.py:test_credentials_rejected_before_serialization`; `test_diagnostics_ask_persistence.py:test_saved_modes_preserve_output_and_allowlist_only`; `test_diagnostics_failure_matrix.py:test_http_status_failures_persisted_and_secondary_store_faults` |
| Prompt/response absent from persisted bytes | `test_diagnostics_store.py:test_persisted_observer_metadata_excludes_private_markers`; `test_diagnostics_ask_persistence.py:test_saved_modes_preserve_output_and_allowlist_only`; `test_diagnostics_failure_matrix.py:test_http_model_failures_persisted_and_store_fault_crossproducts` |
| Raw HTTP bodies and Claude/Codex stderr absent | `test_diagnostics_failure_matrix.py:test_http_status_failures_persisted_and_secondary_store_faults`; `test_diagnostics_failure_matrix.py:test_cli_real_fixture_failures_persisted_and_store_fault_crossproducts` |
| Unsafe terminal controls/surrogates rejected | `test_diagnostics.py:test_safe_text_rejects_controls_surrogates_and_empty_text`; `test_diagnostics_reader.py:test_invalid_event_schema_types_ids_sequence_controls_and_bounds` |
| Debug stack paths/locals/repr not collected; unexpected exception never stringified | `test_diagnostics_runtime.py:test_unexpected_internal_exception_is_never_stringified`; `test_diagnostics_ask_persistence.py:test_unexpected_provider_exception_not_stringified_or_saved` |
| Credential-looking model rejected/redacted before metadata display | `test_diagnostics_runtime.py:test_common_token_shapes_rejected_with_fixed_safe_error`; `test_diagnostics_ask_persistence.py:test_invalid_metadata_drop_is_counted_and_safe_trace_can_finish` |
| Doctor does not echo paths/profiles/models/origins/envnames/secrets | `test_doctor.py:test_literal_presence_without_display_or_resolution`; `test_doctor.py:test_all_provider_readiness_and_executable_absolute_contract`; `test_doctor.py:test_invalid_config_fixed_error_before_store_or_provider_access` |

### Provider behavior

| Requirement / failure | Concrete test evidence |
| --- | --- |
| HTTP timeout/connect/runtime error mapping | `test_diagnostics_runtime.py:test_timeout_connection_and_transport_sites`; `test_diagnostics_failure_matrix.py:test_http_transport_failures_all_output_modes_without_retry` |
| 401/403/429/500/503 typed status mapping and numeric causes | `test_diagnostics_runtime.py:test_http_status_codes_and_numeric_causes_without_retry`; `test_diagnostics_failure_matrix.py:test_http_status_failures_persisted_and_secondary_store_faults` |
| Malformed/tool-call/empty/oversize response and incomplete length mapping | `test_diagnostics_runtime.py:test_invalid_oversize_empty_and_incomplete_response_sites`; `test_diagnostics_failure_matrix.py:test_http_model_failures_persisted_and_store_fault_crossproducts` |
| CLI missing executable, timeout, nonzero, invalid/empty/overflow mapping | `test_claude_cli_provider.py:test_coded_runtime_sites_use_one_fake_process_and_no_private_data`; `test_codex_cli_provider.py:test_coded_runtime_sites_use_one_fake_process_and_no_private_data`; `test_diagnostics_failure_matrix.py:test_cli_real_fixture_failures_persisted_and_store_fault_crossproducts` |
| Adapter allowlists Reply.metadata; unknown objects not stringified | `test_diagnostics_runtime.py:test_adapter_unknown_values_never_stringified`; `test_diagnostics_ask_persistence.py:test_saved_modes_preserve_output_and_allowlist_only` |
| Exactly one call in normal/verbose/debug/save; no retry/fallback request/event | `test_diagnostics_runtime.py:test_normal_success_byte_compatible_and_quiet`; `test_diagnostics_runtime.py:test_debug_safe_summaries_and_one_request`; `test_diagnostics_ask_persistence.py:test_saved_modes_preserve_output_and_allowlist_only`; `test_diagnostics_failure_matrix.py:test_http_transport_failures_all_output_modes_without_retry`; `test_diagnostics_failure_matrix.py:test_cli_real_fixture_failures_persisted_and_store_fault_crossproducts` |
| Offline doctor only existing pure readiness/which absolute contract | `test_doctor.py:test_all_provider_readiness_and_executable_absolute_contract`; `test_doctor.py:test_offline_no_client_network_process_auth_or_secret_loader` |

### Config

| Requirement / failure | Concrete test evidence |
| --- | --- |
| Absent mapping default false and trusted user bounds | `test_diagnostics_settings.py:test_default_off_and_precedence`; `test_diagnostics_settings.py:test_strict_types_ranges_unknown_and_cross_limit`; `test_doctor.py:test_policy_bounds_and_user_only_layering` |
| CLI save/no-save precedence | `test_diagnostics_ask_persistence.py:test_cli_and_trusted_user_precedence`; `test_diagnostics_settings.py:test_internal_option_precedence_and_unspecified_public_default` |
| Project diagnostics forbidden | `test_diagnostics_settings.py:test_project_diagnostics_rejected`; `test_diagnostics_ask_persistence.py:test_project_diagnostics_cannot_enable_or_override_store`; `test_doctor.py:test_policy_bounds_and_user_only_layering` |
| LLMTOOL_HOME location never enables persistence | `test_diagnostics_ask_persistence.py:test_default_debug_and_location_only_do_not_access_store`; `test_doctor.py:test_default_missing_store_factual_output_and_no_mutation` |
| Legacy config-show unchanged; configure preserves exact diagnostics mapping | `test_diagnostics_settings.py:test_diagnostics_do_not_change_resolved_or_show_contract`; `test_diagnostics_settings.py:test_configure_preserves_exact_partial_mapping` |
| Malformed diagnostics config fails before provider I/O | `test_diagnostics_settings.py:test_invalid_config_fails_before_provider_io`; `test_diagnostics_ask_persistence.py:test_malformed_diagnostics_config_precedes_store_and_provider`; `test_doctor.py:test_invalid_config_fixed_error_before_store_or_provider_access` |
| Doctor existing provider/environment/project selection retained | `test_doctor.py:test_existing_config_selection_overrides_without_profile_value_output` |
| Configured credential presence by keys only, including default Mapping/os._Environ and empty value | `test_doctor.py:test_credential_key_iteration_only_not_values_or_default_contains`; `test_doctor.py:test_real_os_environ_never_fetches_configured_credential_value`; `test_doctor.py:test_windows_credential_presence_lower_mixed_case_empty_absent_no_value_fetch`; `test_doctor.py:test_posix_credential_and_reserved_names_remain_case_sensitive` |
| Reserved credential-binding collisions fail closed before known secret fetch; normal config unchanged | `test_doctor.py:test_credential_binding_collisions_fail_before_known_env_value_fetch`; `test_doctor.py:test_windows_reserved_bindings_lower_mixed_case_active_inactive_no_value_fetch` |

### Store/security

| Requirement / failure | Concrete test evidence |
| --- | --- |
| New directories 0700/files 0600 and atomic complete trace/summary publication | `test_diagnostics_store.py:test_private_layout_atomic_completion_and_schema` |
| Wrong owner/private modes fail safely without chmod | `test_diagnostics_store.py:test_existing_unsafe_permissions_not_chmodded`; `test_diagnostics_store.py:test_wrong_owner_rejected`; `test_doctor.py:test_unsafe_modes_wrong_owner_and_nonregular_components` |
| Symlink home/ancestor/runs/run/file rejected; targets untouched | `test_diagnostics_store.py:test_symlink_components_runs_and_lock_rejected_without_target_mutation`; `test_diagnostics_reader.py:test_symlink_at_every_boundary_never_reads_target`; `test_doctor.py:test_symlinks_at_home_runs_run_lock_artifact_and_ancestor_boundaries` |
| Non-directory/nonregular components fail safely | `test_diagnostics_store.py:test_non_directory_component_and_invalid_path_safe`; `test_doctor.py:test_unsafe_modes_wrong_owner_and_nonregular_components` |
| No-clobber run directories and bounded collision recovery | `test_diagnostics_store.py:test_run_collision_regenerates_and_three_attempts_stop`; `test_diagnostics_ask_persistence.py:test_three_collisions_fail_safely_and_keep_ephemeral_id` |
| Concurrent independent run IDs/reservations | `test_diagnostics_store.py:test_two_processes_reserve_distinct_ids_and_protect_capacity`; `test_diagnostics_store.py:test_reused_store_and_parallel_writers_have_independent_warnings` |
| Root/run locks; busy bounded root and nonblocking active run | `test_diagnostics_store.py:test_held_store_lock_bounded_unavailable_no_extra_run`; `test_diagnostics_reader.py:test_busy_run_lock_never_retries_or_inspects_mutable_artifacts`; `test_doctor.py:test_permission_and_lock_failure_generic_bounded_no_partial_stdout`; `test_doctor.py:test_busy_run_artifacts_not_inspected_or_repaired` |
| Active run never cleaned; abandoned unlocked grace/recovery | `test_diagnostics_store.py:test_active_run_kept_and_count_capacity_failure`; `test_diagnostics_store.py:test_abandoned_grace_active_lock_then_recovery` |
| Age/count cleanup oldest first | `test_diagnostics_store.py:test_age_and_count_cleanup_oldest_first`; `test_diagnostics_store.py:test_equal_completion_times_evict_by_run_id_under_count_and_byte_pressure` |
| Byte cleanup; unknown/unrelated data never deleted | `test_diagnostics_store.py:test_byte_cleanup_preserves_unrelated_data`; `test_diagnostics_store.py:test_unknown_files_and_invalid_completed_runs_never_deleted`; `test_diagnostics_store.py:test_unrelated_large_data_disables_save_no_deletion` |
| Active reservations counted against total capacity | `test_diagnostics_store.py:test_active_reservations_enforce_total_capacity`; `test_diagnostics_store.py:test_stale_reservation_reconciliation_preserves_ineligible_runs` |
| Max-run and total-byte limits/full capacity simulation | `test_diagnostics_store.py:test_max_run_bytes_disables_writer_before_overflow`; `test_diagnostics_ask_persistence.py:test_capacity_failure_continues_once_without_deleting_active_run` |
| Permission-denied simulation and public provider/store crossproducts | `test_diagnostics_store.py:test_permission_failure_safe_without_raw_exception`; `test_diagnostics_failure_matrix.py:test_http_status_failures_persisted_and_secondary_store_faults` |
| Partial/crashed trace classified incomplete | `test_diagnostics_reader.py:test_partial_and_published_without_summary_remain_incomplete`; `test_diagnostics_failure_matrix.py:test_cli_real_fixture_failures_persisted_and_store_fault_crossproducts` |
| Write warning bounded/once; provider success/output/result preserved | `test_diagnostics_store.py:test_write_failure_warning_once_partial_retained`; `test_diagnostics_ask_persistence.py:test_append_false_exception_write_and_fsync_failures_are_secondary`; `test_diagnostics_ask_persistence.py:test_completion_exception_false_and_publication_failure_are_secondary` |
| Provider failure primary under start/late append/publication failures | `test_diagnostics_ask_persistence.py:test_provider_failure_precedes_one_secondary_store_warning`; `test_diagnostics_failure_matrix.py:test_http_status_failures_persisted_and_secondary_store_faults`; `test_diagnostics_failure_matrix.py:test_cli_real_fixture_failures_persisted_and_store_fault_crossproducts` |
| Hardlinks/missing root/run locks fail closed without repair | `test_diagnostics_reader.py:test_hardlinked_file_and_missing_locks_fail_without_repair`; `test_doctor.py:test_hardlinked_lock_and_artifact_missing_lock_no_repair`; `test_doctor.py:test_populated_missing_and_unsafe_root_lock_never_repaired` |
| Reader races/real publication windows preserve stable/active semantics | `test_diagnostics_reader.py:test_actual_publication_temp_and_hardlink_windows_are_incomplete`; `test_diagnostics_reader.py:test_real_cleanup_waits_for_reader_and_snapshot_remains_valid`; `test_doctor.py:test_run_and_home_replacement_races_fail_before_output`; `test_doctor.py:test_artifact_replacement_during_stat_fails_before_output` |

### Platform

| Requirement / failure | Concrete test evidence |
| --- | --- |
| macOS resolver, Linux/XDG and relative XDG fallback | `test_diagnostics_settings.py:test_platform_defaults_and_xdg` |
| Explicit absolute LLMTOOL_HOME and invalid relative/control home | `test_diagnostics_settings.py:test_explicit_location_pure_no_expansion_or_home_lookup`; `test_diagnostics_settings.py:test_invalid_locations_fail_safely`; `test_doctor.py:test_bad_home_and_unrequested_options_keep_safe_error_contract` |
| Unsupported Windows persistence warns once before provider, one unsaved call | `test_diagnostics_store.py:test_unsupported_backend_safe_bounded_warning_no_io`; `test_diagnostics_ask_persistence.py:test_create_failures_and_unsupported_backend_continue_once_unsaved` |
| Ephemeral diagnostics remain usable without supported persistence | `test_diagnostics_runtime.py:test_direct_ask_no_diagnostics_io_network_shell_or_home_lookup`; `test_diagnostics_ask_persistence.py:test_create_failures_and_unsupported_backend_continue_once_unsaved` |
| Doctor unsupported platform factual output without store/location I/O | `test_doctor.py:test_unsupported_platform_reports_without_store_or_location_io` |

### Diagnostics CLI

| Requirement / failure | Concrete test evidence |
| --- | --- |
| Runs no creation/cleanup/credential/provider/auth reads | `test_diagnostics_reader.py:test_missing_store_and_runs_do_not_create_anything`; `test_diagnostics_reader.py:test_reader_never_resolves_config_credentials_provider_or_auth`; `test_diagnostics_reader.py:test_no_mutation_calls_and_all_open_flags_readonly` |
| Trace exact ID/path traversal impossible | `test_diagnostics_reader.py:test_run_id_and_unrequested_flags_rejected_without_echo` |
| Trace bounds, errors filter, stable JSON and incomplete labeling | `test_diagnostics_reader.py:test_file_event_structure_aggregate_and_output_limits`; `test_diagnostics_reader.py:test_saved_run_json_human_and_structured_filter`; `test_diagnostics_reader.py:test_partial_and_published_without_summary_remain_incomplete` |
| Doctor zero network/request/process/auth/session/secret resolution | `test_doctor.py:test_offline_no_client_network_process_auth_or_secret_loader`; `test_doctor.py:test_credential_key_iteration_only_not_values_or_default_contains`; `test_doctor.py:test_real_os_environ_never_fetches_configured_credential_value` |
| Doctor creates/writes/repairs/probes nothing, no artifact-body reads | `test_doctor.py:test_default_missing_store_factual_output_and_no_mutation`; `test_doctor.py:test_existing_store_never_reads_artifact_bodies_or_writes`; `test_doctor.py:test_busy_run_artifacts_not_inspected_or_repaired` |
| Doctor honest offline/readiness/structure labels, policy and bounds/races | `test_doctor.py:test_default_missing_store_factual_output_and_no_mutation`; `test_doctor.py:test_all_provider_readiness_and_executable_absolute_contract`; `test_doctor.py:test_policy_bounds_and_user_only_layering`; `test_doctor.py:test_scan_home_and_file_size_bounds`; `test_doctor.py:test_run_and_home_replacement_races_fail_before_output` |

## Required gates and evidence limits

Cloud local checks above are PASS only on the bound source/test patch. Independent
Reviewer verdict/frozen SHA, PR publication and exact-head Python 3.9/3.14 CI are
pending coordinator delivery. External/human review remains a separate gate; this
Developer report does not replace it. Do not merge or mark A11a.6/A11a DONE from
local checks alone. A11a.7 final A11a validation remains unstarted.

Mac live CLI authentication/session, company VPN/gateway inference, native macOS
filesystem behavior and native Windows backend execution: **NOT-TESTED**, optional
for this offline-only slice. Pure platform resolver/unsupported-backend injected
tests are PASS only for those branches; Linux POSIX filesystem tests do not prove
native Mac/Windows behavior or authenticated inference. No company summary was
sent to an external provider, and no inference call was made.

Existing A11a.1–5 validation/review reports and original spec historical semantics
remain unchanged. README/ROADMAP now describe independently confirmed A11a.5 merge
and historical post-merge CI, while A11a.6 remains a feature-branch implementation.
No model/runtime identity or savings/complete semantic correctness claim is made.

## Initial frozen source/test artifact binding — historical c09e832

Source/test manifest SHA-256: `2e7e20bbe6a0d7d26a5abb7a025fbf97872a22be9c34a45114e8441af774f764`.
Source/test patch SHA-256 against base: `0e60ab16932fa4b1e19d51966846553290b5bfe86ca9a8d631332377907a248c`.

Manifest encoding: sorted repository-relative paths; concatenate each path UTF-8
+ NUL + exact file bytes + NUL, then SHA-256. Patch encoding uses the same sorted
paths and delimiters, replacing file bytes with `git diff --binary --no-ext-diff
<base> -- <path>` output; for a file absent at base, use `git diff --no-index
--binary --no-ext-diff -- /dev/null <path>` output. Base is
`da027f0d728e3b3ff605a6d404c49c8beb08ec28`. Only the eight runtime/test files
below are included, so documentation changes do not alter tested runtime binding.
Initial digests bind file bytes at `c09e832`; reconstruct those bytes with
read-only Git inspection to reproduce that historical digest. The working-tree
helper below now computes the replacement Round 1 digest.

- `tools/llmtool/llmtool/cli.py`
- `tools/llmtool/llmtool/config.py`
- `tools/llmtool/llmtool/diagnostics_cli.py`
- `tools/llmtool/llmtool/diagnostics_reader.py`
- `tools/llmtool/llmtool/doctor.py`
- `tools/llmtool/tests/test_diagnostics_failure_matrix.py`
- `tools/llmtool/tests/test_diagnostics_reader.py`
- `tools/llmtool/tests/test_doctor.py`

Exact final all-file manifest (including this report) and the 12-file publication
allowlist are supplied separately in `/tmp/a11a6-freeze-evidence.json` and the
coordinator handoff, avoiding a self-referential report digest. Root will commit
only that allowlist. This initial candidate was frozen during review; the coordinator authorized
Round 1 repair only after the Reviewer stopped with REQUEST CHANGES. The next
freeze is recorded below. Developer performs no Git mutations. Review/publication/CI SHA and results must
be supplied by the coordinator, not inferred from this uncommitted working tree.

## Independent review and correction Round 1 of at most 2

Reviewer independently read requirements/source on exact clean commit
`c09e83274b970c769a21cab666e1b9c1ba0c3669` before Developer report evidence.
Verdict: **REQUEST CHANGES**, R1 blocker; R2 documentation finding accepted for
routine correction. Its independent doctor 23/23 (0.464 s), canonical 852/852
(no skips, 22.236 s) and wheel 43-file provenance PASS are historical evidence
for that initial candidate, not acceptance of the repaired source. The coordinator
authorized this bounded repair after review stopped; Task Contract v1 and its
`a6c3bed07312c955b00a506f61984140e1a559de5dff09cec6855004f4e6b4d5`
digest remain unchanged. No scope or acceptance condition was weakened.

- **R1 — BLOCKER, config.py initial lines 264/314:** exact-case comparisons did
  not match Windows os._Environ uppercase key semantics. A binding such as
  `api_key_env: llmtool_model` bypassed reserved-variable preflight before the
  later `LLMTOOL_MODEL` override lookup could fetch its value. A normal binding
  such as `doctor_secret` falsely appeared absent when `DOCTOR_SECRET` existed.
  Reviewer reproduced this with a case-insensitive Mapping and actual synthetic
  `os._Environ` using `str.upper` as its key codec; the credential-getter tripwire
  observed the forbidden fetch. Disposition: repaired by one pure platform-aware
  inspection-name normalizer, applied only to reserved-binding and key-presence
  comparisons. Windows uses uppercase canonical names; POSIX keeps exact case.
  Ordinary ask/config layering and bootstrap location precedence are unchanged.
  New tests cover lower/mixed-case active/inactive reserved bindings, ordinary
  credentials present/absent/empty, both Mapping and synthetic actual os._Environ
  codecs with credential get/getitem forbidden, POSIX case separation and ordinary
  Windows-config behavior preservation. These are injected Linux-host tests,
  **not native Windows validation**.
- **R2 — documentation, README initial lines 73/86:** two active checkpoint
  paragraphs still described PR #22 draft/pending, doctor deferred and A11a.5
  resumed with later slices unstarted. Disposition: corrected to A11a.5 merged
  PR #22/`da027f0`/historical post-merge CI and current A11a.6 feature-branch
  implementation with review/delivery pending. Historical reports remain intact.

Round 1 validation and replacement artifact binding are recorded below.
Initial 852-test PASS does not establish repaired-byte PASS.

### Fresh Round 1 validation on repaired bytes

Checks ran from the same configured checkout, HEAD
`c09e83274b970c769a21cab666e1b9c1ba0c3669` plus the repaired patch. Environment,
Python/package provenance, permission authorization and offline/live limits are
unchanged. Inherited guards and `/tmp/.git` marker remain intact.

| Check | Exact command / evidence | Result |
| --- | --- | --- |
| Repaired doctor and Windows-codec regressions | `tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_doctor.py -v` | PASS, exit 0, 26/26, 0.804 s |
| Repaired canonical compileall/full unittest | `make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"` | PASS, exit 0, 855/855, no skips, 23.309 s; includes all 232 provider/store fault scenarios and 2 concurrency cases |
| Rebuilt wheel | `tools/llmtool/.venv/bin/python -m pip wheel --no-deps --no-build-isolation --no-index --wheel-dir /tmp/a11a6-wheel tools/llmtool` | PASS, exit 0 |
| Fresh replacement install | `/tmp/a11a6-wheel-venv/bin/python -m pip install --no-index --no-deps --force-reinstall /tmp/a11a6-wheel/llmtool-0.1.0-py3-none-any.whl` | PASS, exit 0 |
| Editable/reinstalled wheel CLI smoke/provenance | `tools/llmtool/.venv/bin/python /tmp/a11a6-smoke.py` | PASS, exit 0, same full console smoke/nonmutation cases and all 43 packaged Python/skill files byte-equal to repaired source |
| Installed codec-specific repair smoke | `tools/llmtool/.venv/bin/python /tmp/a11a6-round1-installed-casefold.py` | PASS, exit 0, editable and wheel: lower/mixed reserved collision rejected and empty lower/mixed ordinary credential present with actual synthetic os._Environ codec and forbidden credential getters; injected Linux evidence only |
| Round 1 documentation/history/contract/whitespace | `tools/llmtool/.venv/bin/python /tmp/a11a6-doc-check.py`; `git diff --check` | PASS, exit 0; 63 local links, 170 named matrix method references, unchanged pinned v1 digest and historical reports/spec |

Round 1 rebuilt wheel SHA-256:
`91c50b90d5142424ab8a40c2c39b94a6e9aafb5356a0794f7cdb86b05fc04f71`.
No runtime/test/package source bytes changed after the successful 855-test run.
Only the review/validation report was finalized afterward.

### Replacement frozen binding and review gate

Full eight-file source/test manifest SHA-256:
`4dc617498879ddff2d29b777de1b9172fe845319c85d15ca088ad7e6bcf15054`.
Full source/test patch SHA-256 against original main base `da027f0`:
`1fbd9e92e66ff781118f0fdc619f6d2d0ff554571d14e705db1a6b5119cc144a`.
Round 1 source/test repair patch SHA-256 against `c09e832`:
`1ee57c9fb10604262d078ceef3b5f1bf956d40451fa64807162bef31657dd7c2`.
The manifest/patch encodings are unchanged from the initial binding section.
The repair patch includes only `tools/llmtool/llmtool/config.py` and
`tools/llmtool/tests/test_doctor.py`.

Four-file repair publication allowlist:

- `reports/llmtool_a11a6_offline_doctor_validation_20261004.md`
- `tools/llmtool/README.md`
- `tools/llmtool/llmtool/config.py`
- `tools/llmtool/tests/test_doctor.py`

Final four-file and full 12-file feature manifests are supplied separately in
`/tmp/a11a6-round1-freeze-evidence.json` to avoid self-reference. Reproduce with
`tools/llmtool/.venv/bin/python /tmp/a11a6-evidence.py` followed by
`tools/llmtool/.venv/bin/python /tmp/a11a6-round1-evidence.py`. Root alone commits
the four-file repair. Developer is frozen without Git mutation for independent
review on the next exact committed SHA. R1/R2 repairs are Developer dispositions;
Reviewer acceptance remains **PENDING**, as do publication, exact-head CI and
external gates. This does not mark A11a.6 or overall A11a DONE.
