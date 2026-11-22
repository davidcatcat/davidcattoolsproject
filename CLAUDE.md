# Repository Guidance (Claude)

@AGENTS.md

Claude's entrypoint. The line above imports [AGENTS.md](AGENTS.md), the single shared source of rules and skill routing for every AI (Owner decision E6, [#1502](https://github.com/davidcatcat/davidcattoolsproject/issues/1502)). Do not copy shared rules here; change AGENTS.md instead. This file holds only rules that apply to Claude alone.

## Claude only

- Claude work defaults to the `claude2` account; the Owner may name another account by explicit command. No automatic rotation. A one-shot CLI flag or env override does not change the stored default. Switching accounts never bypasses worker dispatch.
- Claude Code hooks (for example the skill-read hook) are defence in depth; the real gates run at the point of action: the dispatch gate, `task complete` and merge checks.
