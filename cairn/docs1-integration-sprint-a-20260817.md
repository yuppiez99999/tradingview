---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-17
updated: 2026-08-17
related:
  - docs/1设计计划集成到系统内并能完整运行_20260817.md
  - docs/高价值项目接入落地指南_20260809.md
  - docs/高价值项目集成排期计划_20260811.md
---

# docs/1 八项目集成 — Sprint A 知识专题

> 以 `docs/1`（8 个高价值 GitHub 项目接入建议）为输入的集成实施记录。
> 本批次定位为 **AI 工具链与开发者体验层**，与 08-09 基础设施层、08-11 模块增强层互补不重复。

## 1. 项目定位与批次划分

`docs/1` 含 8 个项目，分两组：
- **组 1（路由/微调/记忆/采集）**：Switchyard、unsloth、TencentDB-Agent-Memory、OpenBiliClaw
- **组 2（RAG/可视化/CLI/插件化）**：code-graph-rag、diagram-design/archify、EchoBird、deepseek-harness

三个批次（见 `docs/1设计计划集成到系统内并能完整运行_20260817.md` §3）：
- **Sprint A（低风险高 ROI）**：OpenBiliClaw + code-graph-rag + diagram-design ✅ DONE 2026-08-17
- **Sprint B（AI 路由与记忆层）**：Switchyard SKIP + TencentDB-Agent-Memory ✅ 完成
- **Sprint C（高门槛层）**：unsloth ❌ 阻塞(GPU) + EchoBird ✅ + deepseek-harness ✅

## 2. docs/1 原描述与代码现状的关键修正

接入前探索发现 `docs/1` 多处接入点描述与真实代码不符，已修正：

| docs/1 原描述 | 真实现状 | 影响 |
|---|---|---|
| `ml_enhanced_trainer.py` | 不存在；实际 `lgb_enhanced_trainer.py` + `lgb_trainer/` 包 | unsloth 接入点改为 `lgb_trainer/llm_finetune/` |
| `utils/ai_hedge_fund/` | 实际在 `quant_modules/ai_hedge_fund/` | TencentDB-Agent-Memory 接入点修正 |
| `utils/` 136 py | 实际 155 py | code-graph-rag 规模修正 |
| LiteLLM 未提及 | 已深度接入（`utils/llm_gateway/litellm_router.py`，W7.4.3 重构 2026-08-12）+ 5-provider fallback + 成本统计全套 | Switchyard 与 LiteLLM 功能重叠，Sprint B 需先评估是否 SKIP |
| 舆情采集层未提及 | `utils/media_crawler_adapter.py`（7 平台）+ `last30days_adapter.py`（海外）已成熟，只是未接入 `signal_fusion.py` | OpenBiliClaw 接入难度从"高"降为"中"（只差接入融合） |

## 3. Sprint A 交付物

### 3.1 W.A.1 OpenBiliClaw 舆情接入信号融合

**接入链路**：
```
MediaCrawlerAdapter (7 平台: 小红书/B站/微博/知乎/抖音/快手/贴吧)
    → NewsSentimentEngine (中文金融情感词典打分)
    → SentimentSignalSource.get_signal(code) -> SignalResult
    → SignalFusionEngine.register_source("sentiment", ..., initial_weight=0.05)
```

**关键设计**：
- `composite_sentiment ∈ [-1,1]` → `score ∈ [0,1]` 映射：`score = (sentiment + 1) / 2`
- `confidence < min_confidence (0.3)` 时自动降权（confidence=0, action=HOLD）
- 采集失败/无数据返回中性 SignalResult（score=0.5, confidence=0），不污染融合
- feature-flag `USE_SENTIMENT_SIGNAL_SOURCE` 默认 false，灰度开启
- 不引入 finbert/snownlp（留给 08-09 §3.5 打分层），本任务只做采集→融合

**交付物**：
- `utils/signal_sources/__init__.py`（新建）
- `utils/signal_sources/sentiment_signal_source.py`（新建 365 行）
- `utils/signal_fusion.py` 末尾追加 `_get_sentiment_signal_source` / `register_sentiment_signal_source` / `is_sentiment_signal_enabled`
- `configs/feature_flags.yaml` 新增 `USE_SENTIMENT_SIGNAL_SOURCE`
- `tests/unit/test_sentiment_signal_source.py`（17 测试）

### 3.2 W.A.2 code-graph-rag Python 封装

**接入链路**：
```
.code-review-graph/graph.db (MCP 已生成, 126MB, 8819 nodes / 83531 edges)
    → CodeGraphRAG Python 封装 (sqlite3 标准库, 无新依赖)
    → ai_coordinator.record_decision_with_impact (opt-in)
```

**关键设计**：
- 不引入新工具（code-review-graph MCP 已就绪），只在 Python 层封装 RAG 检索 API
- `impact_analysis(change_path, max_depth=2)` 递归查 CALLS/IMPORTS_FROM/REFERENCES/INHERITS 边
- 不改 `record_decision` 高频路径签名，新增 `record_decision_with_impact` opt-in 方法
- graph.db schema：nodes(kind/name/qualified_name/file_path/line_start) + edges(kind/source_qualified/target_qualified)

**交付物**：
- `utils/ai_tools/__init__.py`（新建）
- `utils/ai_tools/code_graph_rag.py`（新建 365 行）
- `utils/ai_coordinator.py` 新增 `record_decision_with_impact` 方法
- `tests/unit/test_code_graph_rag.py`（25 测试）

### 3.3 W.A.3 diagram-design HTML 图表生成器

**接入链路**：
```
reporting/html_chart_generator.py (mermaid + echarts CDN)
    → generate_flowchart / generate_architecture / generate_gantt
    → 4 项目特定图表: 对冲五阶段 / 数据源降级 / AI 路由 / 信号融合
    → (后续) generate_daily_report.py --with-html-charts
```

**关键设计**：
- 自包含 HTML（内联 CSS，引用 CDN JS），可独立打开
- mermaid.js（流程图/甘特图）+ echarts（架构图）双引擎
- HTML 转义安全（`html.escape` 防 XSS）
- 4 项目特定图表预置：`generate_hedge_phases_chart` / `generate_data_source_degradation_chart` / `generate_ai_decision_routing_chart` / `generate_signal_fusion_chart`
- 接入 `generate_daily_report.py`（55KB 大文件）留作后续，避免本批次风险

**交付物**：
- `reporting/html_chart_generator.py`（新建 352 行）
- `tests/unit/test_html_chart_generator.py`（20 测试，含 HTML well-formed 校验）

## 4. 质量门禁

| 检查项 | 结果 |
|---|---|
| ruff | All checks passed（W.A.1/2/3 全部） |
| mypy | 无报错（自己文件，预先存在的其他文件报错不动） |
| pytest | 62 passed (17 + 25 + 20) |
| feature-flag | `USE_SENTIMENT_SIGNAL_SOURCE` 默认 false，双签启用 |
| 向后兼容 | `record_decision` 签名不变；新方法 opt-in |

## 5. Sprint B/C 完成状态（2026-08-17 更新）

### Sprint B（AI 路由与记忆层）— ✅ 全部完成
- **W.B.1 Switchyard 与 LiteLLM 协调评估**: ⚠️ SKIP。LiteLLM 已覆盖 Switchyard 全部核心能力，NVIDIA NIMs 非当前需求
- **W.B.2 TencentDB-Agent-Memory 团队级共享记忆中枢**: ✅ 完成。`utils/ai_memory/team_memory_hub.py`（434 行）+ state 注入零侵入 23 分析师 + 23 测试

### Sprint C（高门槛层）— 2/3 完成 + 1 阻塞
- **W.C.1 unsloth 本地微调**: ❌ 阻塞（需 GPU，M5Max 可能不支持 unsloth；备选 `docs/云端训练方案_华为云ModelArts_20260815.md`，等 GPU 就绪再做）
- **W.C.2 EchoBird 多 CLI 模型切换**: ✅ 完成。`scripts/cli_model_switcher.py`（250 行）+ `configs/cli_profiles/`（4 profile）+ 23 测试
- **W.C.3 deepseek-harness 插件化重构**: ✅ 完成。`utils/ai_coordinator_plugins/`（5 文件）+ `ai_coordinator.py` 改造 route()/resolve_conflicts() 委托 PluginRegistry + shadow 比对 + 50 测试

### 总计
- **完成**: 6/8 任务（W.A.1~3 + W.B.2 + W.C.2 + W.C.3）
- **SKIP**: 1/8（W.B.1，LiteLLM 已覆盖）
- **阻塞**: 1/8（W.C.1，需 GPU）
- **测试**: 158 passed（17 + 25 + 20 + 23 + 23 + 50）
- **质量门禁**: ruff All checks passed / 全部 feature-flag 默认 false / 向后兼容

## 6. 与现有 Wave 的协调

- **Wave 7 Phase 0-3**（uv/dotenv/Prefect/DuckDB/LiteLLM）：Sprint A 全是新增文件/文档，不冲突；Sprint B W.B.1 需与 LiteLLM 协调；Sprint C W.C.3 需在 Phase 0-3 完成后
- **08-09 落地指南**：OpenBiliClaw 采集层与 08-09 §3.5 finbert/snownlp 打分层互补
- **08-11 排期计划**：本批次属第三层（AI 工具链），与前两批互补不重复

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [GitHub 高价值项目集成策略（Wave 6）](github-integration-wave6.md) (相似度 16%)
- [GitHub 周热门项目集成 Wave10 (2026-08-21)](github-integration-wave10-20260821.md) (相似度 15%)
- [Wave 9：GitHub 热榜项目集成决策沉淀](github-trending-wave9-20260819.md) (相似度 14%)
- [第三方项目批量集成专题 — 2026-08-22](third-party-integration-batch-20260822.md) (相似度 10%)
- [Code-Review-Graph MCP 工具使用指南](code-review-graph-guide.md) (相似度 8%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
