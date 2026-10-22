# llmtool 歷史架構圖與流程圖索引

> 盤點日期：2026-10-22。此資料夾保存歷史討論的**可編輯重建稿**，不宣稱是先前產生之原始圖片。若後續找到原始圖，應另存、建立來源對照，不可直接覆蓋。

## 已重建（Mermaid，可由 GitHub 預覽）

| 檔案 | 對應歷史討論 | 類型 | 狀態 |
| --- | --- | --- | --- |
| [multi-agent-roles.mmd](source/multi-agent-roles.mmd) | 2026-10-16～10-18 多 AI 協作、PM/Integrator 與 Worker | 架構 | RECONSTRUCTED / TARGET |
| [development-review-loop.mmd](source/development-review-loop.mmd) | 2026-10-17～10-22 Developer→Reviewer→Integrator→PM | 流程 | RECONSTRUCTED / TARGET |
| [chatgpt-github-bridge.mmd](source/chatgpt-github-bridge.mmd) | 2026-10-21 ChatGPT↔GitHub↔llmtool 討論 | 架構 | RECONSTRUCTED / PROPOSED |
| [company-llm-routing.mmd](source/company-llm-routing.mmd) | 2026-09-28～10-19 公司 LLM 與工具、其他 AI 求援 | 流程 | RECONSTRUCTED / PROPOSED |

上述圖均為**歷史設計概念重建**，不能以此認定現有版本已具備相同能力。決策與真正 runtime/QA 狀態以 [ROADMAP](../../tools/llmtool/ROADMAP.md)、[WORKFLOW](../../tools/llmtool/WORKFLOW.md)、[2026-10-21 產品需求會議](../meetings/2026-10-21-llmtool-product-requirements.md) 及對應 PR 的驗收紀錄為準。

## 待取回或待確認的舊圖

| 歷史討論 | 原始資產情況 |
| --- | --- |
| 2026-10-19 Web Dashboard、資料流未來科技風格與動態設計 | 已找到設計內容；舊 PNG/動畫未取得 |
| 2026-10-20～21 軟體專案規劃書 v1.0/v1.1 中的系統架構與操作流程 | 已找到歷史文件產出紀錄；需比對原文件是否有可抽取圖檔 |
| 2026-10-21 ChatGPT 橋接的四張 Mermaid | 已知先前 PR #261 文件內包含四張 Mermaid；優先從既有 PR 擷取，避免重複重畫 |
| 2026-10-22 TUI/GUI/CLI/Web UI Agent 跨介面設計 | 已找到需求對話，未取得原圖 |
| 2026-08-27～09-01 監控 RCA 架構、16:9 系統與服務圖 | 不屬於 llmtool 核心；原圖未取得，待分專案歸檔 |
| 2026-05-05 Jira/Jenkins/ELK 混合架構 | 不屬於 llmtool 核心；原圖未取得 |
| 2025-12-05 Nomad/Consul/Prometheus 監控架構 | 不屬於 llmtool 核心；曾有文字架構，原圖未取得 |
| 2024-10-08 Jenkins CI/CD 部署架構 | 不屬於 llmtool 核心；原圖未取得 |

## 保存規範

1. 原始 PNG/SVG/HTML（若找回）與重建稿分別標記 **ORIGINAL** 或 **RECONSTRUCTED**。
2. 修改設計圖要保留 `.mmd` 可編輯來源；可另產出 `.svg`，但禁止將新產圖冒充舊原圖。
3. 每張圖標記歷史日期、相關會議/PR、PROPOSED/TARGET/IMPLEMENTED 狀態；`IMPLEMENTED` 必須有現行程式與驗收證據。
4. 目前為文件圖稿歸檔，不授權任何 runtime 功能開發或部署。
