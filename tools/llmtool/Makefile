PYTHON ?= python3

.PHONY: check test test-impact syntax env-bootstrap

check: syntax test

syntax:
	$(PYTHON) -m compileall -q llmtool llmtool.py tests scripts

test:
	$(PYTHON) -m unittest discover -s tests -v

# Explicit developer iteration route. Required exact-head/CI/Mac gates continue
# to use `make check`; this target is not a release acceptance substitute.
test-impact:
	$(PYTHON) -m llmtool.test_runner --base "$(BASE)"

# Invoke after the Integrator has created, registered and locked a D6 worktree.
# The script verifies/reuses that worktree's own venv and checked wheel cache.
WORKTREE_ENV_PYTHON ?= python3
WORKTREE_ENV_ROOT ?= ../..
WORKTREE_ENV_CACHE ?=
WORKTREE_ENV_RECREATE ?= 0

env-bootstrap:
	$(WORKTREE_ENV_PYTHON) scripts/worktree_env.py bootstrap --worktree "$(WORKTREE_ENV_ROOT)" --extras tui $(if $(strip $(WORKTREE_ENV_CACHE)),--cache-root "$(WORKTREE_ENV_CACHE)",) $(if $(filter 1,$(WORKTREE_ENV_RECREATE)),--recreate,) --json
