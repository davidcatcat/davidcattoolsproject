# A11a.7 — Final acceptance validation — 2026-10-04

Status: acceptance and documentation in progress. Overall **A11a NOT DONE**;
external review and the A11a.7 gate's merge remain required. No automatic merge.

## Pinned Task Contract

Coordinator pinned this user-authorized contract before documentation changes or
verification handoff. Payload encoding: UTF-8/LF with one final newline.

```text
Task ID: A11a7-final-acceptance
Version: v1
Base: 6b7809ace69cdcbe6030e94cd9ee816b6d22710e
Goal: final acceptance of implementation spec section17 A11a.7, auditing A11a.1-6 delivery/evidence and executing fresh canonical/installed CLI verification before documentation publication and external review.
Authorized scope: existing canonical Mac checkout; new A11a7 feature branch from fetched latest main; root/tool README and ROADMAP current-status/usage/limitations updates; dated final validation report; isolated test/runtime/wheel artifacts; normal commit/push and draft PR. Reproduce an acceptance blocker and propose/implement only the minimal scope-preserving fix if needed, after coordinator assessment.
Forbidden scope: alternate project clone/worktree/checkout; reset/discard/stash unrelated work; force push/history rewrite/main merge; A11b/A11c/executor/hooks/router/retry/fallback; new diagnostics content retention; automatic cleanup/repair; doctor damaged-count contract change; runs ordering or --json implementation; edits to historical spec/review/validation evidence; falsely treating historical/Claude live results as fresh verification; marking overall A11a DONE before external review and gate merge.
Required audit: map spec A11a.1-6 contracts to delivered modules/tests, merged PR/commit ancestry and historical reports; verify latest main, PR24 merge6b7809a and actual postmerge3.9/3.14 CI; identify milestone-boundary clarifications without changing historical spec or concealing deferred findings.
Required fresh execution: canonical make check on actual Python3.9 and current CI Python3.14 from canonical cwd; record interpreter/version/SHA/commands/exits/counts/skips. Fresh installed CLI smoke with artifact/checkout provenance, version/help, isolated offline doctor/runs/trace, synthetic ask normal/verbose/debug default-off and opt-in metadata persistence, safe failure path, exactly one synthetic mock provider request per ask and no retry/fallback; user config/credential/auth/session files untouched. Installed wheel smoke does not become company live inference evidence.
Deferred limits: preserve Claude M2/M4/M5, P1-P5 doctor behavior, N1 unknown staging byte/scan accumulation and no staging reporting, legacy malformed reservations fail-closed, native publication/platform limits and grace, runs incomplete ordering/nulls and unsupported runs --json. No deferred item may be silently claimed fixed.
Evidence gates: contract digest; exact implementation/base SHA and runtime/config/test byte manifest; fresh canonical and installation smoke artifacts/results; docs links/examples/status consistency and git diff --check; independent read-only frozen-diff/evidence/conclusion review with at most two correction rounds; final published-head Python3.9/3.14 CI with offline canonical/clean-tree steps; draft PR and remote SHA/ahead-behind; external review remains pending and no self-merge.
Roles: Developer gpt-6.1-sol/high sole documentation/source/test writer for accepted changes; separate independent read-only Reviewer gpt-6.1-sol/high; requested Git gpt-5.6-luna/low unavailable in native catalog so coordinator is sole Git writer. Accepted requested selection is separate from UNVERIFIED runtime attestation; no unmeasured cost/token savings claim.
Environment: actual Mac /Users/davidsun/git/davidcataiporject/davidcattoolsproject only; ordinary temporary runtime/install artifacts allowed, never repository copies/checkouts. No fresh live company/external provider request is required or inferred for this acceptance scope; any such unperformed checks are explicitly NOT-TESTED.
Completion wording: A11a.1-6 merged; A11a.7 verification/documentation delivered on feature branch pending external review/merge; overall A11a NOT DONE until this gate is merged. A11b and later execution milestones NOT STARTED.
```

Contract SHA-256: `5f921f82388bb4c3a8d84453c5f46c992a158d5519f9216d05f47ed37af985e7`.

## Preflight

- Canonical root/cwd verified:
  `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Original tracked working tree clean; unrelated benchmark/reasoning untracked
  artifacts retained. No stash/reset/cleanup or alternate checkout used.
- Fresh fetch confirms origin/main `6b7809ace69cdcbe6030e94cd9ee816b6d22710e`.
- PR #24 merged at that exact SHA; mergedAt 2026-10-04T15:33:24Z, final feature
  head `cc6327787bd4a49baabbded377a1366b30f109a6` includes Claude re-review.
- [Post-merge CI 37213447952](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37213447952)
  independently checked completed/SUCCESS, exact merge head, Python 3.9 and 3.14
  jobs and Offline canonical checks/Working tree is clean steps all SUCCESS.
  This is prerequisite baseline CI, not this round's fresh canonical evidence.
- Created only branch `feat/a11a7-final-acceptance-20261004` from origin/main in
  this same canonical checkout; initial feature/main divergence 0/0.
- Read current AGENTS.md, tools/llmtool/WORKFLOW.md, safe-git/inspect-repo/
  review-change/implement-feature/validate-runtime skills and implementation spec
  sections 17/18 plus Claude persistence hardening re-review.
- Native Python3.9.6 and checkout Python3.13.3 available. No installed Python3.14
  found in PATH/common Homebrew/framework paths; isolated temporary runtime
  provisioning is required for fresh local CI-version verification.

## Evidence and status

Original-source delivery audit, fresh canonical and installed CLI execution,
provenance and retained limits are recorded below. At this pre-publication cutoff,
independent frozen-diff review, normal publication, draft PR and final exact-head CI
remain PENDING. External review and gate merge remain required; overall **A11a NOT DONE**.


## Delivery audit — original source and milestone boundaries

Audit environment: actual Mac, canonical cwd, branch
`feat/a11a7-final-acceptance-20261004`, implementation/base HEAD
`6b7809ace69cdcbe6030e94cd9ee816b6d22710e`, remote identity
`git@github.com:davidcatcat/davidcattoolsproject.git`. This round changes only
current documentation. Source/test/package/config bytes remain those of PR #24's
merge. The initial untracked benchmark/reasoning artifacts are unrelated and retained.

| Spec §17 slice | Delivered source and concrete test seam | Merged ancestry and historical evidence |
| --- | --- | --- |
| A11a.1 | `diagnostics.py`, `diagnostics_summary.py`, `errors.py`; `test_diagnostics.py`, `test_diagnostics_summary.py` verify typed closed schema/codes, bounds, safe serialization and legacy errors | PR #15 `98f9d48`; [core report](llmtool_a11a1_core_diagnostics_validation_20261002.md) |
| A11a.2 | `diagnostics_runtime.py`, `cli.py`, HTTP/Claude/Codex provider adapters; `test_diagnostics_runtime.py` verifies ephemeral lifecycle, safe stderr, unchanged stdout/exits and one provider call | PR #16 `0d32403`; [ephemeral report](llmtool_a11a2_ephemeral_ask_diagnostics_validation_20261002.md) |
| A11a.3 | `diagnostics_settings.py`, `config.py`, `configure.py`, `diagnostics_store.py`; settings/store tests verify user-only precedence, default-off, ownership/locks/reservations/publication/retention | PR #18 `b9ac538`; [store report](llmtool_a11a3_secure_diagnostics_store_validation_20261004.md) |
| A11a.4 | `cli.py`/runtime/store connection; `test_diagnostics_ask_persistence.py` exercises public ask flags, safe saved metadata and secondary store failure containment | PR #19 `2bade6a`; [persisted ask report](llmtool_a11a4_persisted_ask_validation_20261003.md) |
| A11a.5 | `diagnostics_reader.py`, `diagnostics_cli.py`; `test_diagnostics_reader.py` covers bounded anchored offline readers, unverified listing, completion verification, projections and races | PR #22 `da027f0`; [reader report](llmtool_a11a5_runs_trace_validation_20261004.md) |
| A11a.6 | `doctor.py` and CLI/store integration; `test_doctor.py`, `test_diagnostics_failure_matrix.py`, hardening store tests cover offline inspection, credential key-only checks, provider/store failure crossproducts, crash-safe initialization and bounded candidate fds | PR #24 `6b7809a`; [doctor report](llmtool_a11a6_offline_doctor_validation_20261004.md), [hardening report](llmtool_a11a6_persistence_hardening_validation_20261004.md), [external re-review](llmtool_a11a6_persistence_hardening_rereview_20261004.md) |

All six full merge SHAs were checked with
`git merge-base --is-ancestor <merge_sha> HEAD` (exit 0 each); original command/result
records are `/private/tmp/a11a7-developer-evidence-20261004/ancestry.json`.
Remote PR/CI identity is independently checked by the coordinator; local ancestry
alone is not a remote freshness claim. Historical report PASS/live results describe
their original tested revisions and are not this round's fresh verification.

Boundary clarifications preserve the original spec: only `ask` has a diagnostics
lifecycle, provider requests are sequential, and event IDs are ordered inside that
flow; concurrent tests prove the existing store serialization, not a general
concurrent event/span scheduler. Debug provides bounded safe event summaries;
Python traceback/paths/locals/repr are never collected. Reader JSON is a projected
stable schema, while `runs` has only human output. Persistence is explicit metadata
opt-in; `LLMTOOL_HOME` and debug never enable it. Provider != Role != Agent.
No A11b executor/hooks/routing/retry/fallback implementation is introduced.

## Spec §18 evidence coverage matrix

The concrete test references below were read against current source and validated
by AST existence checks (177 references: 165 main matrix plus 12 hardening
references). This is a source coverage
audit, not independent execution evidence. The coordinator's fresh full canonical
logs identify the interpreter, counts/skips and executed results for this same
byte manifest. Installed CLI evidence separately tests packaged public behavior.
Mock/fault injection and platform-branch tests do not prove live authentication,
native Windows execution, every race, arbitrary same-UID isolation or power-loss
durability. Every original §18 group is represented; no deferred finding is
silently converted to PASS by this mapping.

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

### Narrow PR #24 hardening evidence added after the original matrix

| Accepted boundary | Current concrete tests |
| --- | --- |
| M1 unpublished initialization and conservative rollback; no directory rollback or unknown deletion | `test_diagnostics_store.py:test_start_initialization_faults_rollback_then_save_recovers`; `test_diagnostics_store.py:test_mkdir_replacement_before_initial_stat_is_preserved`; `test_diagnostics_store.py:test_start_rollback_preserves_unprovable_inventory` |
| Atomic native no-replace publication and unavailable-backend failure | `test_diagnostics_store.py:test_native_directory_publication_never_overwrites_existing_targets`; `test_diagnostics_store.py:test_unavailable_native_directory_publication_has_no_rename_fallback`; `test_diagnostics_store.py:test_publication_namespace_races_preserve_unknown_data` |
| Real SIGKILL interruption windows and unchanged published-run grace | `test_diagnostics_store.py:test_initialization_crash_interruption_windows_remain_conservative`; `test_diagnostics_store.py:test_postpublication_fsync_failure_preserves_valid_run_until_grace` |
| Unknown staging capacity preserved and reservation short writes completed | `test_diagnostics_store.py:test_unknown_staging_bytes_remain_capacity_accounted`; `test_diagnostics_store.py:test_initialization_reservation_short_writes_complete` |
| M3 candidate descriptor bounds with revalidation and active/changed-data preservation | `test_diagnostics_store.py:test_low_nofile_capacity_cleanup_and_fd_lifetime_in_subprocess`; `test_diagnostics_store.py:test_capacity_reopen_preserves_mutated_replaced_and_active_candidates` |

## Retained limits and unperformed checks

| Item | Current acceptance boundary |
| --- | --- |
| M2 | DEFERRED / NOT FIXED: late partial trace and summary-publication remnants can retain capacity; no damaged-content recovery or automatic repair added |
| M4 | DEFERRED / NOT FIXED: full completed trace/reservation/summary validation runs under the root lock; no caching/performance redesign |
| M5 | DEFERRED / NOT FIXED: reader/store scan limits differ; no settings-bound reduction or reader scan-policy change |
| P1 | DEFERRED / NOT FIXED: damaged run artifacts can fail the whole doctor inspection, exit 1/empty stdout; no damaged-count or body-reading contract |
| P2 | DEFERRED / NOT FIXED: doctor structural presence does not verify content completion, write access or available capacity |
| P3 | DEFERRED / NOT FIXED: configured credential environment presence is key-only, even when its value is empty |
| P4 | DEFERRED / NOT FIXED: safe fixed broad doctor error classifications remain |
| P5 | Historical finding preserved; current README/ROADMAP merge-status wording refreshed as authorized documentation. No doctor behavior fix claimed |
| N1 | DEFERRED / NOT FIXED: unknown unpublished `.run-init-*` stages remain preserved, consume root byte/scan budgets and can accumulate; no staging reporting/cleanup |
| Legacy malformed reservations | Fail closed and preserved; names/owner/mode/grace do not authorize deletion |
| Native publication/platform | Darwin `renameatx_np(RENAME_EXCL)` or Linux `renameat2(RENAME_NOREPLACE)` support required; unsupported/cross-filesystem rejection stays secondary to ask. Fresh Mac runtime evidence and Linux CI are separate |
| Crash/grace/same-UID | SIGKILL tests cover specified interruption windows, not power-loss durability; published abandoned runs keep one-hour grace; unmanaged same-UID source swaps can be preserved/rejected after publication without claiming namespace isolation |
| Runs | Summary recorded status remains unverified, no-summary/incomplete rows retain null times/last ordering; no `runs --json` |
| Live/native checks | Company VPN/gateway inference, external live providers, existing CLI auth/session and native Windows execution NOT-TESTED; outside required synthetic acceptance scope. Historical live results are not fresh PASS |

## Artifact identity and pending gates

Runtime/config/test/package source SHA-256 manifest:
`/private/tmp/a11a7-developer-evidence-20261004/runtime-test-byte-manifest.json`,
79 files, manifest SHA-256 `1d985ea6446fc676923b7093940ce15d722db5d3391d36529c5cf6c55f9642aa`.
These original artifacts remain available for independent read-only review;
report summaries do not replace the logs/scripts/manifests.

Fresh canonical Python 3.9/3.14 results, wheel/installed smoke and documentation
validation are recorded below. Independent frozen-diff review,
publication/draft PR and final published-head Python 3.9/3.14 offline canonical
and clean-tree CI remain PENDING. External review and this A11a.7 gate's merge
remain required. A11a.1–6 merged; A11a.7 acceptance in progress; overall
**A11a NOT DONE** until gate merge. A11b and later execution milestones NOT STARTED.

Requested Developer/Reviewer `gpt-6.1-sol` / high selection was accepted by the
native launch interface; actual runtime attestation is **UNVERIFIED**. Requested
Git `gpt-5.6-luna` / low is **UNAVAILABLE**; coordinator is sole Git writer.
No measured token/cost/savings claim is made.


## Fresh canonical execution — actual Mac

Tested implementation: `6b7809ace69cdcbe6030e94cd9ee816b6d22710e`; source/test/config
bytes match the 79-file manifest above. Both full checks were launched by the
coordinator from the canonical root, sequentially; Developer inspected original
logs and independently hashed them. Execution crossed midnight in Asia/Taipei:
fresh smoke completion checkpoint 2026-10-04T16:00:10Z / 2026-10-05 00:00:10 +08:00.
The dated report/task identifier remains 20261004.

| Interpreter / dependency | Exact canonical command from root | Result / original log |
| --- | --- | --- |
| `/usr/bin/python3`, actual Python 3.9.6, PyYAML 6.0 | `make -C tools/llmtool check PYTHON=/usr/bin/python3` | PASS exit 0; compileall then 867 tests in 109.950s, `OK (skipped=1)`; `/private/tmp/llmtool-a11a7-py39.log` |
| `/private/tmp/llmtool-a11a7-py314-venv/bin/python`, actual Python 3.14.8, PyYAML 6.0.3 | `make -C tools/llmtool check PYTHON=/private/tmp/llmtool-a11a7-py314-venv/bin/python` | PASS exit 0; compileall then 867 tests in 71.153s, `OK (skipped=1)`; `/private/tmp/llmtool-a11a7-py314.log` |

Both runs skipped only
`test_git_intel.GitFixture.test_unusual_filename_bytes_roundtrip` because the host
filesystem/sandbox rejects non-UTF-8 filename bytes. This inherited skip is not a
PASS for that case. Mac Python 3.9's PyYAML 6.0 is within the supported dependency
range; it is not identical to CI's pinned 6.0.3. Python 3.14.8 was provisioned into
ordinary temporary runtime/venv directories, with no project clone/copy/checkout.
No fresh live company/external inference or existing CLI session was used.

Original log SHA-256:

- Python 3.9: `c3e26d5d2369727364f1d8c3e2a72234e05818329da904bfd4cf52c84369fe7b`.
- Python 3.14: `ab624a692a34744015a4bd4c795554b277237c3932481ca5784a52bce0cdd56d`.

## Fresh wheel and installed CLI execution

Build launched from canonical root against the checkout, using Python 3.14.8,
setuptools 84.0.0, wheel 0.48.0, packaging 26.3 and PyYAML 6.0.3. Metadata/build/
wheel output paths are temporary artifacts. Existing ignored repository `build/`
and `llmtool.egg-info/` directories were detected before the build and left in place;
the build explicitly used separate temporary directories. Build reports the
existing setuptools `llmtool.skills` package-discovery warning; every Python and
skill resource is present and independently byte-verified, so no package omission
is claimed or hidden.

Exact build/install orchestration:

```sh
/private/tmp/llmtool-a11a7-py314-venv/bin/python /private/tmp/a11a7-build-install.py
```

`/private/tmp/a11a7-wheel-build-20261004/build-install-result.json` contains exact
argv, cwd, dependency versions, exits and full-log hashes. It invokes setuptools
`setup()` from `tools/llmtool` with `egg_info --egg-base` and `build --build-base`
temporary paths, then `bdist_wheel --dist-dir --bdist-dir` temporary paths; build
exit 0. The fresh wheel is installed with pip `--no-deps --force-reinstall` into
separate `/private/tmp/a11a7-installed-venv-20261004`, exit 0, after provisioning
PyYAML 6.0.3. Initial dependency download failed under sandbox DNS restrictions;
the approved dependency-only installation succeeded. No user config/credentials,
auth/session files, Keychain or environment dump was used.

Wheel: `/private/tmp/a11a7-wheel-build-20261004/wheels/llmtool-0.1.0-py3-none-any.whl`,
SHA-256 `f23c529d0c1e91510171260845528347d237b9fa42f766639c55f8dfc8b87d62`.
Build/install helper SHA-256 `b367363db59d421ad9033d03538628bca7c10a8ea374ff640fb0460b64367ab6`.
Full logs: `/private/tmp/a11a7-wheel-build-20261004/build.log` and `install.log`.
Their SHA-256 values are `f79c2694b8336415bfde08df214dc6adee3c0479311287a91b1ec159d83e2cad`
and `d73c5f84670647c1c7b2ba17b65e4a9eba4d0761c38d190fea53c62db1fde442`.

The installed console executable is
`/private/tmp/a11a7-installed-venv-20261004/bin/llmtool`, `llmtool 0.1.0`, using
actual Python 3.14.8. The console shebang/import and wheel/installed
`entry_points.txt` verify `llmtool.cli:main`; an isolated `python -I` import resolves
`llmtool.__file__` to that venv's site-packages. Before provider smoke and again
afterward, all 43 packaged Python and SKILL.md files match exact checkout, wheel
and installed bytes; the complete sorted name set matches, detecting extras or
omissions as well as changed content. Console SHA-256
`21db9dc0bb6e88307359fd406cd44824470fe3f9abf8d8dc7398c927fdd2fd74`; entry point metadata SHA-256
`ed007e91fb5e486b70573d0c311ed37bec131c23aa8e020eda0fcf3286d3cdc9`.

Exact final smoke command, launched from canonical root (approved localhost bind):

```sh
/private/tmp/llmtool-a11a7-py314-venv/bin/python /private/tmp/a11a7-installed-smoke.py --python /private/tmp/a11a7-installed-venv-20261004/bin/python --wheel /private/tmp/a11a7-wheel-build-20261004/wheels/llmtool-0.1.0-py3-none-any.whl --evidence /private/tmp/a11a7-installed-smoke-evidence-final-20261004
```

Final result PASS / exit 0: **42 command invocations, 10 synthetic asks, exactly
10 HTTP POST requests, six persisted runs, no smoke skips**. HTTP server bound
only `127.0.0.1` and returned synthetic markers/metadata; no external/company
request or real credential was configured. Each subprocess receives only an
explicit minimal environment and private synthetic HOME/LLMTOOL_HOME. Read commands
have zero local mock requests and compare file hashes/inodes/modes/mtime snapshots
before/after; these are nonmutation checks, not write probes. Synthetic config
hash remains unchanged.

| Installed public behavior | Executed assertions |
| --- | --- |
| Identity/help | Version, root/ask/doctor/trace help; isolated import/entry-point and package byte identity |
| Offline absent readers | doctor absent observation, runs exact no-saved message, missing trace NOT_FOUND; no store creation and unchanged synthetic HOME snapshot |
| Invalid usage/ID | Traversal-like trace ID fails ID_INVALID with exit 1; runs --json, doctor --json and conflicting ask save flags fail usage 2 with no request/store |
| Default-off ask | Normal/verbose/debug success stdout exact marker/newline, normal stderr empty, one request each, no state directory; verbose/debug expose valid 32-hex run_id, provider type and diagnostics_persisted=false |
| Opt-in ask | Normal/verbose/debug save one request each; stdout unchanged; verbose/debug report matching saved run ID/type/true; private directory/file modes and no prompt/response/raw body/reasoning/Authorization persisted |
| Safe provider failure | Persisted synthetic HTTP 429 in all three modes: one request each, exit 1/empty stdout, unchanged fixed first error line, PROVIDER_RATE_LIMITED with saved run ID; no raw body or response marker in stderr or artifacts |
| Secondary store failure | Unsafe 0755 synthetic store: one request, success stdout/exit 0 retained, exactly one DIAGNOSTICS_STORE_UNSAFE warning, diagnostics_persisted=false, original mode preserved/no store mutation |
| Trace projection/completion | All six runs through JSON/errors-JSON/human-errors; exact schema/version, complete state, directory/summary/trace IDs match, event count/bytes/sequence and one provider.request, success/error status and structured filter verified; no retry/fallback event |
| Read-only saved inspection | doctor/runs/trace leave content/inode/mode/mtime snapshot unchanged; listing reports trace evidence unverified |

Attempt accounting: first launch was blocked at sandbox socket bind before any
CLI/provider call; approved launch passed the original fixture. The coordinator
then requested stricter response-leakage/run-ID/provider-type assertions; final
fixture was rerun in a fresh private state directory and passed. These are fixture/
environment refinements, not product regressions or weakened criteria. No full
canonical rerun was needed because only the temporary smoke script changed.
There were two completed successful executions, each with 10 synthetic asks and
10 local HTTP requests: **20 total synthetic requests** across those executions.
The final run's 10 requests are the authoritative acceptance result. Earlier
successful evidence remains at
`/private/tmp/a11a7-installed-smoke-evidence-approved-20261004/result.json`, with
recorded earlier script SHA-256
`2d0dac3fa6aaa91aa5485daef1c48942567ed4d78dabd57e46fec6526720793f`;
that earlier fixture is superseded by the stronger final script/result above.

Final original artifacts (the stronger final run is authoritative):

- `/private/tmp/a11a7-installed-smoke.py`, SHA-256 `50cb9bef4f6545d240a9038e826e8b28b78427a5f6b766ae8e67ee8239c47270`.
- `/private/tmp/a11a7-installed-smoke-evidence-final-20261004/result.json`, SHA-256 `1fb7d67a151bc98ea662373aa06267c76114183fa487272e934d557497936860`.
- `/private/tmp/a11a7-installed-smoke-evidence-final-20261004/commands.json`, every command/exit/stdout/stderr/request count, SHA-256 `f10a914e89c490ecf4c2cf4e4cee49d9821cf2f6571fd783bd9b9a8003925d61`.
- `/private/tmp/a11a7-installed-smoke-evidence-final-20261004/package-manifest.json`, all 43 packaged file hashes, SHA-256 `69cb0f4c8f2e8d36b634978fca00758593cfb0b9fee29cbeb84aeb61d1a2bd5f`.

## Documentation checks and pre-publication cutoff

`/private/tmp/a11a7-validate-docs.py` validates local Markdown targets, current
status wording, all concrete test references by AST, unchanged runtime manifest,
pinned contract digest and task file allowlist, plus `git diff --check`.
The final validator output and frozen file/patch/artifact manifest are retained
under `/private/tmp/a11a7-developer-evidence-20261004` for independent read-only
review. Report-only/documentation commits can cite the implementation SHA because
the byte manifest proves runtime/config/test bytes unchanged.

At this pre-publication cutoff, fresh required local execution is PASS within
the explicitly stated skip/coverage limits. Independent frozen-diff acceptance
review remains PENDING until its result is appended and verified. Final published
commit/draft PR identity and exact-head Python 3.9/3.14 canonical/clean-tree CI
remain PENDING publication and will be recorded once in the PR/final handoff;
no recursive report-only commit is required merely to embed the report's own SHA.
External review and gate merge remain required and pending. Overall **A11a NOT DONE**.

## Independent review — correction round 1

The separate read-only Reviewer inspected the frozen v1 four-document diff,
pinned contract, source/tests, original logs and installation/evidence manifests.
No runtime, security or scope acceptance blocker was found. Two low report
corrections were requested: clarify 177 total section 18 matrix/hardening test
references and explicitly account for both completed smoke executions/20 total
synthetic requests. Both documentation corrections are addressed here; no
runtime/config/test or fixture bytes changed and no execution rerun was required.
Final independent confirmation of frozen v2 remains PENDING at this checkpoint.
External review and gate merge remain pending; overall **A11a NOT DONE**.
