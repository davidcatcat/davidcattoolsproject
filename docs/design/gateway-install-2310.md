# Gateway install drift: how a gw-agent run starts the installed gateway (#2310)

This is a read-only investigation note for #2310. It is not a review and not
acceptance. It records only what the listed repository files say at the main
SHA recorded as this Task's `source_sha`,
`a4268e1f900fe1350b037e562ec736e8bcc6a591`, and nothing was run against a live
host. No code or configuration was changed.

## 1. How a run locates and starts the installed gateway

### 1.1 The location is configured, not discovered

- `tools/llmtool/developer-routes.json:29` sets
  `"package_path": "~/.local/lib/api-agent-gateway"` for the `gw-agent` route.
  The same route block (lines 28-56) names `profile_name` `llmtool-direct`, the
  model `gw`, the data classes and the budgets.
- `GwAgentRoute.__init__` reads `package_path` at
  `tools/llmtool/llmtool/developer_routes/gw_agent.py:168`, requires a string
  that is absolute or starts with `~/` (lines 174-176), expands `~`, resolves
  the path with `strict=True`, and requires an `api_agent_gateway` child
  directory owned by the current user and not writable by group or others
  (lines 205-222). The resolved path is stored in `self.package_path`
  (line 223).
- There is no search path, no virtualenv lookup, no console-script lookup and no
  `pip` metadata lookup in that constructor (`gw_agent.py:166-223`). The
  configured directory is the only candidate.

### 1.2 The start command

- `build_env` copies a small allowlist of environment variables and sets
  `PYTHONPATH` to `self.package_path` (`gw_agent.py:479-487`; `PYTHONPATH` at
  line 485). The profile API key, when present, is placed in
  `GW_AGENT_API_KEY` (lines 486-487); credential handling is not examined
  further here.
- `build_argv` starts the package as a module with the current interpreter:
  `[sys.executable, '-B', '-P', '-m', 'api_agent_gateway']`, followed by `run`
  or `resume` and the explicit file flags (`gw_agent.py:438-453`; module form at
  line 452). On Python older than 3.11 it uses a `-c` launcher that deletes
  `sys.path[0]` and runs the module (`gw_agent.py:57-58`, line 453).
- `tools/api-agent-gateway/api_agent_gateway/__main__.py:1-5` imports `main`
  from `.cli` and exits with its return code. That `main` is at
  `tools/api-agent-gateway/api_agent_gateway/cli.py:350-359`.
- The package also declares a console script, `gw-agent =
  api_agent_gateway.cli:main` (`tools/api-agent-gateway/pyproject.toml:12-13`),
  and the README documents running from the repo with
  `PYTHONPATH=tools/api-agent-gateway python -m api_agent_gateway`
  (`tools/api-agent-gateway/README.md:13-16`). llmtool uses neither of those
  entry forms; it uses `-m` against the configured `package_path`.
- llmtool passes `--state-dir`, `--profiles-file`, `--workdir`, `--profile`,
  `--task-file`, `--result-file`, `--audit-file` and `--authorization-file`
  explicitly (`gw_agent.py:442-450`). The gateway defaults in `cli.py:33-36`
  (state directory) and `cli.py:250` (profiles file) are therefore not used on
  this path.

### 1.3 Session selection and the second launcher

- `tools/llmtool/llmtool/gw_agent_callsite.py:19-55` opens a session home and a
  lock through `open_locked_home`, and `route_fields` (lines 103-110) supplies
  `session_home` and `session_id` to the route. Those select the session
  directory passed to the gateway as `--state-dir` (`gw_agent.py:449`); they do
  not choose the package.
- `tools/llmtool/llmtool/gw_agent_sessions.py:308` defines
  `sweep(store, now=None, *, runner=None, package_path=None)`. For an ENDED
  session it runs `end ... --reason task_terminal` with the same module form
  (lines 399-407) and sets `PYTHONPATH` to `package_path` when one is supplied
  (lines 406-407). Which caller supplies that value is outside the listed files
  (see section 4).

### 1.4 The installed copy is also the verifier

- llmtool does not normally import the installed package in-process, but it does
  load one module from it. `_verified_audit_digest` (`gw_agent.py:555-579`)
  builds an isolated module from
  `self.package_path / 'api_agent_gateway' / '__init__.py'` and calls
  `audit.verify` on the audit file (lines 560-573). The audit verifier therefore
  also comes from the installed copy.

## 2. Version and install-origin information

Version string: it exists, but it cannot show drift.

- `tools/api-agent-gateway/api_agent_gateway/__init__.py:7` sets
  `__version__ = '0.1.0'`; `tools/api-agent-gateway/pyproject.toml:7` declares
  `version = "0.1.0"`. Both are repository constants, so a checkout of main
  carries the same value until someone edits the files.
- The gateway exposes the value only through `--version` (`cli.py:41`).
  llmtool's route never passes `--version` and never reads `__version__`: the
  constructor (`gw_agent.py:166-223`) checks path shape and permissions only,
  and `parse_output` (`gw_agent.py:493-541`) validates the result schema,
  status, served model and audit-digest format, not a gateway version. The
  digest itself is re-checked in `collect` (`gw_agent.py:643-661`).
- `tools/llmtool/developer-routes.json:28-56` has no version or source-commit
  field for the route; the block carries the package path, profile,
  authorization reference, data classes, budgets and timeouts. The adapter
  reads the path, profile, authorization reference and default model at
  `gw_agent.py:168-180` and validates the allowed data classes at lines 240-245.

Install origin: none recorded.

- Nothing in the listed files writes or reads a source commit, tag, wheel name,
  install timestamp or package metadata for `~/.local/lib/api-agent-gateway`.
  The only checks are that the resolved directory and its `api_agent_gateway`
  child exist, are owned by the current user and are not group- or
  other-writable (`gw_agent.py:205-222`). A stale but correctly permissioned
  install passes.
- The adapter refuses a package path that equals, contains or sits inside the
  task workdir (`gw_agent.py:353-359`), so the installed copy is expected to
  live outside the D6 rather than be the checkout under test.

Plain answer: a version string exists (`0.1.0`), but it is a static constant that
nothing compares, and no install-origin information exists. On the listed
evidence, llmtool cannot tell whether the installed gateway is older than main.

## 3. Candidate fixes and their limits

None of the three requires sudo or root, handles credentials, or changes
gw-agent data classes, the egress basis or the budgets. They are options, not
work done by this note.

### Candidate 1: Declared expected version plus a preflight check

- Change: add a non-secret expected-version field to the `gw-agent` route in
  `tools/llmtool/developer-routes.json` (the block at lines 28-56), validate it
  in `GwAgentRoute.__init__` (`gw_agent.py:168-223`), read the installed
  `api_agent_gateway.__version__` from `self.package_path`, and refuse with a
  config error on mismatch. Bump the constant (`__init__.py:7`,
  `pyproject.toml:7`) at each release.
- Limits: the constant is edited by hand, so this catches only a forgotten
  reinstall after a bump; two different trees can share `0.1.0` and pass;
  importing installed code adds an in-process import step to a route that today
  loads installed code only for `audit.verify` (`gw_agent.py:555-579`).

### Candidate 2: Install-time provenance marker compared with the expected commit

- Change: have the install step write a small non-secret marker, such as the
  source commit, into the installed directory, and have `GwAgentRoute.__init__`
  read it and compare it with an expected commit recorded in route config or
  with the D6 HEAD.
- Limits: no install or release script appears in the listed files, so where the
  marker would be written is UNVERIFIED; the check still trusts whoever writes
  the marker; comparing with the D6 HEAD conflates "gateway version" with "task
  checkout"; it needs a release-process change, not only a docs change.

### Candidate 3: Run the repository copy

- Change: point `package_path` at the in-repo `tools/api-agent-gateway`, or
  copy it into a private location at run start, so the code that runs matches
  the checkout.
- Limits: `GwAgentRoute` already refuses a package path inside or containing the
  workdir (`gw_agent.py:353-359`), and the README states that the package and
  llmtool do not import each other (`README.md:3-11`). This is a design change
  that needs a different confinement story; a copy step adds write and
  permission surface; and it would make every run depend on a D6 checkout.

## 4. UNVERIFIED

- Which caller passes `package_path` to `sweep`
  (`gw_agent_sessions.py:308`, lines 406-407); the caller is outside the listed
  files.
- Whether an install script, release script, CI job or package manager updates
  `~/.local/lib/api-agent-gateway`, and when; no such file is in the read set.
- Whether the installed copy on any host currently matches
  `a4268e1f900fe1350b037e562ec736e8bcc6a591`; no host filesystem was inspected.
- Whether `tools/api-agent-gateway/tests/` covers version or install checks; it
  was not read.
- Gateway internals beyond the cited lines, such as `Store`, `parse_task` and
  adapter behaviour; they were not read.
- Whether the `docs` check passes; it was not run in this Task.

## 5. Scope

Only this file was written. No code, configuration, route data, data class,
egress basis or budget was changed. No installer, sudo/root or credential
operation was performed or is claimed.
