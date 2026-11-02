# AI-loop helper

This is an imported, transitional helper retained for reference and local
maintenance. It is unmaintained and is **not an llmtool dispatch route**. Do
not use it to dispatch AI work or publish refs.

The launcher is intended to run checked-out source only. It requires a clean
`main` checkout with the expected `origin`; it does not fetch code, bootstrap a
cache, or run a remote ref. Pass paths explicitly with `--repo` and
`--task-dir`, or set `AI_LOOP_REPO` and `AI_LOOP_TASK_DIR`.

Direct publication to `automation/ai-loop` and diagnostics refs is disabled.
Frozen QA and maintenance material in this directory is historical evidence,
not an active dispatch or publication contract.

The historical implementation flow can push a feature branch and run
`gh pr create`; those actions are not part of this standalone helper contract.
Do not treat this transitional tool as an approved publication route.

Run its local tests with:

```sh
python -B -m unittest discover -s tools/ai-loop
```
