PYTHON ?= python3

.PHONY: check test syntax

check: syntax test

syntax:
	$(PYTHON) -m compileall -q llmtool llmtool.py tests

test:
	$(PYTHON) -m unittest discover -s tests -v
