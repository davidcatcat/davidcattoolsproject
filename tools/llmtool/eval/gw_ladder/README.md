# Company gw capability ladder

This offline ladder measures company gw on six synthetic Python tasks, from a one-line correction through a behavior-preserving refactor. The fixtures use standard-library Python only and contain no private data. Each level's `TASK.md` is the complete prompt; the `hidden_test.py` and `reference.patch` files are evaluator material and must not be included in gw's input.

## Run a level

The PM creates a D6 task for one level and invokes llmtool with route `company-gw` and `--mode proposal`. Set the task's read paths to that level's `TASK.md` and `target.py` (plus `helper.py` for L4). Set write paths to `target.py` (plus `helper.py` for L4). The model receives only the task statement and permitted source files; it does not receive hidden tests or reference patches. The authorized execution environment applies an accepted proposal and runs the level scorer from the repository root:

```sh
python3 tools/llmtool/scripts/gw_ladder.py L1 --root .
```

The scorer prints one JSON object containing `level`, `result` (`PASS`, `FAIL`, or `ERROR`), `ran`, and `failures`. It runs the hidden unittest with isolated Python (`-I -B`) and a 60-second limit. It does not import proposed output in the scorer process or make network requests.

## Ladder stop rule

Allow one initial attempt and at most one evidence-guided repair for each level, within the task's existing budget. Stop the ladder at the first level that fails twice; do not start higher levels after that. Record the actual route, inputs, attempts, scorer JSON, usage, and outcome for each level, including `UNKNOWN` when evidence is incomplete. The ladder result measures this bounded synthetic task set and does not establish general coding capability.
