# CliNest

CliNest 是獨立的本機 AI CLI 多帳號管理工具（**規劃階段／尚未實作**）。

首版目標：使用 CLI / TUI 管理 Codex CLI 與 Claude Code 的多個獨立登入帳號；每個帳號保持可獨立呼叫，新的 Terminal 可透過原生 `codex`、`claude` 命令使用所選預設帳號，腳本與本機程式也能明確指定任一 Profile。

**完整初版規劃：** [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)

- 先實測原生帳號隔離與平行呼叫，再進入正式開發。
- 首版只做 CLI、TUI、Profile 登入與指定帳號啟動。
- 不做 GUI、Web、額度監控、Agent／任務調度或背景 Daemon。
- 不應覆蓋或破壞現有 Codex / Claude 的原生登入與設定。

此目錄獨立運作，不依附 Repo 裡的其他 AI 工具。
