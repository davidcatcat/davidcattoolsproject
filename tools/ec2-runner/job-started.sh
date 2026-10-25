#!/bin/bash
# Runner hooks must end in .sh; the checks live in job-started.py.
exec /usr/bin/python3.9 -I -B /opt/llmtool-ci/job-started.py
