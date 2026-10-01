# Versioned A7 feedback and regression data

These are curated data fixtures, separate from packaged runtime skills and root
repository engineering skills. No CLI command discovers or writes this directory
implicitly. All feedback CLI inputs/outputs must be explicitly selected.

`sensitive-read.feedback.json` records the historical A1.1 configuration-dump
suggestion, which was never executed. Its proposal is an illustrative review
artifact, **not an applied skill change**. The regression evaluates A6's risk
classification for three exact candidate lines: cat = SENSITIVE_READ; ls -l and
stat = SAFE_READ. Passing does not endorse executing any command or mean a model
stopped suggesting cat.

`context-quality.feedback.json` is a human-curated A4 quality scenario, not a new
live-provider observation. Its candidate separates the observed HTTP 500, possible
configuration mismatch, missing exception, and next evidence. Assertions check
exact elements plus A4/A6 structural support; they do not prove factual truth.

From the canonical repository:

```sh
tools/llmtool/.venv/bin/llmtool feedback regression \
  --case tools/llmtool/feedback/sensitive-read.regression.json \
  --output-file tools/llmtool/feedback/sensitive-read.candidate.txt --json
tools/llmtool/.venv/bin/llmtool feedback regression \
  --case tools/llmtool/feedback/context-quality.regression.json \
  --output-file tools/llmtool/feedback/context-quality.candidate.json --json
```

The fixtures remain repository-local test/review data and are not shipped inside
the wheel. Three runtime skills still ship unchanged. Curate and redact any new
case before recording it; do not include credentials or hidden reasoning.
