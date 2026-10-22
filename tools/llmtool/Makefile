PYTHON ?= python3

.PHONY: check test test-impact syntax

check: syntax test

syntax:
	$(PYTHON) -m compileall -q llmtool llmtool.py tests scripts

test:
	$(PYTHON) -m unittest discover -s tests -v

# Explicit developer iteration route. Required exact-head/CI/Mac gates continue
# to use `make check`; this target is not a release acceptance substitute.
test-impact:
	$(PYTHON) -m llmtool.test_runner --base "$(BASE)"
