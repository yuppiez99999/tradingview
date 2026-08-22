# ai_decision 全链路集成计划 — C 轨

> 将 `ai_decision/` 17 个孤立模块串成 EOD 管道内的决策链。本文件为集成设计文档，实现排期 W35-W37（8/25-9/12）。
> LOG 指针: `cairn/LOG.md` 2026-08-21 C 轨准备条目。
> 上游方案: `v86集成升级最优方案_20260821.md` §五 C 轨。

## 1. 现状扫描（2026-08-21）

### 1.1 17 模块清单与行数

| 文件 | 行数 | 职责 | C 轨挂接点 | 行数合规 |
|---|---|---|---|---|
| `__init__.py` | 50 | 包初始化 | — | ✅ |
| `cli.py` | 119 | CLI 入口 | CLI 统一（§3.5） | ✅ |
| `config.py` | 116 | 配置 | — | ✅ |
| `rag_context.py` | 122 | RAG 上下文 | **signals hook**（§3.1） | ✅ |
| `models.py` | 221 | 数据模型 | — | ✅ |
| `orchestrator.py` | 228 | 任务编排 | CLI 统一（§3.5） | ✅ |
| `decision_gate.py` | 185 | 决策门控 | review hook（§3.2） | ✅ |
| `consensus_aggregator.py` | 163 | 共识聚合 | review hook（§3.2） | ✅ |
| `debate_engine.py` | 280 | 多模型辩论 | review hook（§3.2） | ✅ |
| `providers.py` | 356 | LLM provider | provider 统一（§3.6） | ✅ |
| `auto_research_defaults.py` | 368 | 默认配置 | — | ✅ |
| `auto_research_skill.py` | 446 | 自动研究 | — | ✅ |
| `health.py` | 425 | 健康检查 | UI 接入（§3.7） | ✅ |
| `dashboard.py` | 705 | 仪表盘 | **report hook**（§3.4） | ✅ |
| `eod_review.py` | 795 | EOD 复盘 | review hook（§3.2） | ⚠️ 接近上限 |
| `backtest_replay.py` | 860 | 回测回放 | — (独立) | ❌ **超 800** |
| `execution_bridge.py` | 437 | 执行桥接（核心） | **execute hook**（§3.3） | ✅ 已拆分 |
| `grayscale_state.py` | 362 | 灰度状态机 | — (从 bridge 拆出) | ✅ 新建 |
| `execution_risk.py` | 226 | L1/L2 风控 | — (从 bridge 拆出) | ✅ 新建 |
| `execution_tca.py` | 225 | TCA pre/post trade | — (从 bridge 拆出) | ✅ 新建 |
| `execution_audit.py` | 109 | 审计记录 | — (从 bridge 拆出) | ✅ 新建 |

### 1.2 行数超标预警（C 轨集成前必须处理）

> **[2026-08-21 更新]** `execution_bridge.py` 拆分已完成 ✅（1198→5 文件，89 单测全绿，ruff 全绿，接口不变）。

| 文件 | 行数 | 超标 | 处理方案 | 优先级 | 状态 |
|---|---|---|---|---|---|
| `execution_bridge.py` | ~~1198~~ → 437 | ~~+398~~ | 拆为 5 文件：`execution_bridge.py`(437 核心) + `grayscale_state.py`(362) + `execution_risk.py`(226) + `execution_tca.py`(225) + `execution_audit.py`(109) | 🔴 P0 | ✅ 已完成（89 单测全绿） |
| `backtest_replay.py` | ~~860~~ → 766 | ~~+60~~ | 拆为 3 文件：`backtest_replay.py`(766 核心) + `backtest_replay_types.py`(220 类型/协议/常量) + `backtest_replay_mocks.py`(125 Mock) | 🟡 P2 | ✅ 已完成（32 单测全绿） |
| `eod_review.py` | 795 | 临界 | **监控**：接近 800，C 轨 review hook 不改其内部，暂不拆；若 hook 注入后超 800 再拆 | ⚪ P3 | ⚪ 监控 |

**拆分原则**（遵循 AGENTS.md §5.3）：
- 多小文件 > 少大文件，典型 200-400 行
- 拆分不改接口，只移动代码到新文件 + re-export
- 不可变性保持，单测同步迁移

## 2. 集成数据流（5 挂接点）

```
institutional_pipeline_runner.py
  ├─ phase: data      → DataConnectorManager（不变）
  ├─ phase: factors   → AlphaFactor（不变）
  ├─ phase: signals   → SignalFusionEngine（不变）
  │    └─ [HOOK 1] rag_context.build_context() 注入 cairn 知识上下文
  ├─ phase: review    → [HOOK 2] eod_review + debate + consensus + decision_gate
  ├─ phase: execute   → DailyTradeExecutor（不变）
  │    └─ [HOOK 3] execution_bridge.execute_decision() 桥接执行
  └─ phase: report    → Markdown 报告
       └─ [HOOK 4] dashboard.DashboardGenerator 仪表盘嵌入
  └─ [HOOK 5] health.get_default_monitor() 健康检查（01_系统概览）
```

## 3. 挂接点设计

### 3.1 HOOK 1 — signals 阶段：RAG 上下文注入

**模块**: `rag_context.py`
**入口**: `build_context(symbol, ...) -> DecisionContext` (line 77) + `context_to_prompt(ctx) -> str` (line 111)
**精确注入点**: `institutional_pipeline_runner.py` `run()` 方法 **line 296 之后**（Step 3 `fusion_signals = self._step_signal_fusion(alpha_report)` 之后，`result["steps"]["signal_fusion"]` 赋值之前）
**设计**:
```python
# W35 实现 — 插入在 line 296 之后
# Step 3.5: AI RAG 上下文注入 (feature flag 控制, 默认关闭)
if os.environ.get("AI_DECISION_INTEGRATED", "0") == "1":
    try:
        from ai_decision.rag_context import build_context, context_to_prompt
        for sig in fusion_signals:
            ctx = build_context(symbol=sig.symbol, date=self.ctx.report_date)
            sig.meta = {**(sig.meta or {}), "rag_prompt": context_to_prompt(ctx)}
        logger.info("[Pipeline] RAG 上下文注入完成, %d signals", len(fusion_signals))
    except (ValueError, TypeError, KeyError, ImportError, OSError, AttributeError) as e:
        logger.warning("[Pipeline] RAG context 注入失败，降级: %s", e)
```
**验收**: `AI_DECISION_INTEGRATED=1` 时 fusion_signals 含 `meta.rag_prompt`；` =0` 或失败时 signals 不变

### 3.2 HOOK 2 — review 阶段：EOD 复盘 + 辩论 + 共识 + 门控

**模块**: `eod_review.py` (EODReviewGenerator) + `debate_engine.py` (run_debate) + `consensus_aggregator.py` (aggregate) + `decision_gate.py` (run_hard_risk + apply_mode)
**精确注入点**: `institutional_pipeline_runner.py` `run()` 方法 **line 403 之后**（Step 6 `execution_plans = self._step_execution_routing(...)` 之后，回测完整性守卫之前）
**设计**:
```python
# W35 实现 — 插入在 line 403 之后
# Step 6.5: AI EOD 复盘 (feature flag 控制, 默认关闭)
if os.environ.get("AI_DECISION_INTEGRATED", "0") == "1":
    try:
        from ai_decision.eod_review import EODReviewGenerator
        review = EODReviewGenerator().generate(
            date=self.ctx.report_date,
            portfolio_decision=portfolio_decision,
            execution_plans=execution_plans,
        )
        result["steps"]["ai_eod_review"] = (
            review.to_dict() if hasattr(review, "to_dict") else review
        )
        logger.info("[Pipeline] AI EOD 复盘完成")
    except (ValueError, TypeError, KeyError, ImportError, OSError, AttributeError) as e:
        logger.warning("[Pipeline] AI EOD 复盘失败，降级到规则复盘: %s", e)
```
**验收**: `AI_DECISION_INTEGRATED=1` 时 result 含 `steps.ai_eod_review`；失败时降级，不阻塞管道

> **注**: 辩论/共识/门控（debate_engine/consensus_aggregator/decision_gate）在 EOD 复盘内部按需调用，不单独挂接管道。`EODReviewGenerator.generate()` 内部编排复盘→辩论→共识→门控链。

### 3.3 HOOK 3 — execute 阶段：执行桥接

**模块**: `execution_bridge.py` (execute_decision + get_grayscale_summary + advance_grayscale)
**精确注入点**: `institutional_pipeline_runner.py` `run()` 方法 **line 403 之后**（与 HOOK 2 同位置，HOOK 2 之后）
**前置**: ✅ `execution_bridge.py` 拆分已完成（§5.2，5 文件 ≤500 行，89 单测全绿）
**设计**:
```python
# W35 实现 — 插入在 HOOK 2 之后
# Step 6.6: AI 执行桥接 (feature flag 控制, 默认关闭)
if os.environ.get("AI_DECISION_INTEGRATED", "0") == "1":
    try:
        from ai_decision.execution_bridge import execute_decision, get_grayscale_summary
        ai_exec_summary = get_grayscale_summary()
        result["steps"]["ai_grayscale_state"] = ai_exec_summary
        logger.info("[Pipeline] AI 灰度状态: stage=%s pnl=%.4f",
                     ai_exec_summary.get("stage"), ai_exec_summary.get("cumulative_pnl"))
    except (ValueError, TypeError, KeyError, ImportError, OSError, AttributeError) as e:
        logger.warning("[Pipeline] AI 执行桥接失败，降级: %s", e)
```
**注**: `execute_decision()` 的逐标的桥接在 W35 后半按 `execution_plans` 逐 plan 调用；W35 前半先接 `get_grayscale_summary()` 读取灰度状态（只读，零风险）
**验收**: `AI_DECISION_INTEGRATED=1` 时 result 含 `steps.ai_grayscale_state`；失败时降级，不阻塞管道

### 3.4 HOOK 4 — report 阶段：仪表盘嵌入

**模块**: `dashboard.py` (DashboardGenerator line 144)
**精确注入点**: `institutional_pipeline_runner.py` `run()` 方法 **line 428 之前**（`result["status"] = "ok"` 之前）
**排期**: W37（非 W35）
**设计**:
```python
# W37 实现 — 插入在 line 428 之前
if os.environ.get("AI_DECISION_INTEGRATED", "0") == "1":
    try:
        from ai_decision.dashboard import DashboardGenerator
        dashboard = DashboardGenerator().generate(
            date=self.ctx.report_date, result=result,
        )
        result["steps"]["ai_dashboard"] = (
            dashboard.to_dict() if hasattr(dashboard, "to_dict") else dashboard
        )
    except (ValueError, TypeError, KeyError, ImportError, OSError, AttributeError) as e:
        logger.warning("[Pipeline] 仪表盘嵌入失败: %s", e)
```
**验收**: 报告含 `steps.ai_dashboard`，失败时不阻塞报告生成

### 3.5 HOOK 5 — CLI 统一 + UI 健康检查

**CLI**: `orchestrator.py` (run_decision line 95 + run_batch line 244) → `量化策略系统_统一入口_v8.6.py` 新增 `--ai-decision-full`
**UI**: `health.py` (get_default_monitor line 487) → `ui/pages/01_🏠_系统概览.py` 健康卡片
**排期**: W36 CLI + W37 UI

### 3.6 provider 统一

**模块**: `providers.py` (get_active_provider line 375)
**目标**: 与 `utils/alpha/llm/router.py` LLMRouter 统一 provider 注册（含 ds4）
**排期**: W36（与 CLI 统一同步）

## 4. 集成顺序与排期

> **[2026-08-21 状态]** W35 前置拆分 ✅ 完成 + W35 HOOK 1-3 精确注入设计 ✅ 就绪（含行号 + feature flag + 优雅降级）。W35 实现可直接按 §3.1-§3.3 代码插入。

| 周次 | 任务 | 前置 | 验收 | 状态 |
|---|---|---|---|---|
| **W35 前** ✅ | `execution_bridge.py` 拆分（1198→5 文件 ≤500 行） | 无 | ✅ 89 单测全绿，接口不变 | ✅ 完成 |
| **W35 设计** ✅ | HOOK 1-3 精确注入设计（行号 + 伪代码 + feature flag） | 拆分完成 | §3.1-§3.3 含精确注入点 | ✅ 就绪 |
| **W35 实现** | HOOK 1 signals + HOOK 2 review + HOOK 3 execute 注入 | 设计就绪 | flag off 管道不变；on 时 3 hook 优雅降级 | ⏳ 待 8/25 |
| **W36** | HOOK 5 CLI 统一 + provider 统一 | W35 hook 就绪 | `--ai-decision-full` 可运行 | ❌ |
| **W37** | HOOK 4 report 仪表盘 + UI 健康卡片 | W36 CLI 就绪 | UI 渲染正常 | ❌ |

## 5. 阻塞项

### 5.1 ds4 硬件 POC 阻塞（B 轨，影响 provider 统一）— ✅ 已评估

- **状态**: ✅ 已评估（2026-08-21）— **本机不可行**（6GB 显存远不够 + 无 Windows 编译支持）
- **详见**: `cairn/ds4-integration.md` §九
- **影响**: B 轨 ds4 shadow 无限期推迟 → C 轨 provider 统一（§3.6）的 ds4 部分阻塞
- **缓解**: C 轨 provider 统一先不接 ds4（`GLM5_DS4_ENABLED=0` 永久保持），ds4 代码保留，待远程硬件触发
- **行动**: ~~W35 需先评估 ds4 安装可行性~~ ✅ 已完成，结论：本机不可行，不阻塞 C 轨

### 5.2 execution_bridge.py 拆分（C 轨前置，P0）✅ 已完成

- **状态**: ✅ 已拆分（2026-08-21）— 1198 行 → 5 文件（437+362+226+225+109），全部 ≤500 行
- **验证**: 89 单测全绿（64 unit + 6 integration + 19 modules）+ ruff 全绿
- **接口**: 完全不变（re-export + `__all__` 声明 23 个符号），所有 `from ai_decision.execution_bridge import X` 仍可用
- **关键设计**: monkey patch 兼容（延迟 import）+ 循环 import 规避（TYPE_CHECKING guard）+ logger 名对应模块

## 6. 验收门禁（遵循 v86 方案 §八）

- [ ] `AI_DECISION_INTEGRATED=1` 时 EOD 管道全流程通过
- [ ] shadow 运行 7 天，集成前后交易指令一致率 ≥ 99%
- [ ] `--ai-decision-full` CLI 模式可独立运行
- [ ] UI 仪表盘 + 健康检查卡片渲染正常
- [ ] 单文件 <800 行（execution_bridge 拆分后）
- [ ] pre-commit 门禁全绿（T201 / pyflakes / ruff）
- [ ] feature flag off 时管道行为零变化

## 7. 回滚

`AI_DECISION_INTEGRATED=0` 即切回原管道，零影响。每个 hook 独立 try/except，单 hook 失败不阻塞其他。

## 8. 后续

- 本文件为 C 轨设计文档，W35 实现时按 §4 排期推进
- `execution_bridge.py` 拆分 ✅ 已完成（§5.2）
- ~~ds4 安装可行性评估为 W35 第一项任务~~ ✅ 已完成（本机不可行，不阻塞 C 轨）