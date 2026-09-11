# LLM 权限边界规范（AUTO-2 / Production Invariant I-01 成文，2026-09-05）

> **不变量 I-01**: 任何 LLM 不得直接产生 execution order。
> **适用范围**: 全仓所有 LLM 调用路径（现有三条 + 未来新增）。
> **配套检测**: `scripts/check_llm_boundary.py`（检测性 CI 门禁，**非阻断**——报告违例但不 fail build；转阻断需用户拍板）。

---

## 一、铁律

1. **LLM 输出永远是"建议/报告/复盘"性质**，产生执行动作必须经过确定性风控门（`ai_decision/decision_gate.py`）与执行编排层（`ai_decision/execution_bridge.py`）。
2. **LLM 不得持有资金密钥、不得直接调用 broker/下单 API、不得绕过 Feature Flag 与双签**。
3. **LLM 决策必须留痕**：所有 LLM 参与的决策写入审计（JSONL），含模型名、输入摘要、输出与最终是否采纳。
4. **新增 LLM 路径的准入**：须在本文件登记（§三 路径注册表）+ 通过 `check_llm_boundary.py` 检测 + 人工确认其输出终点是"报告/建议"而非执行。

## 二、三条既有 LLM 路径（已核验合规）

| 路径 | 模块 | 输出终点 | 合规机制 |
|------|------|---------|---------|
| AI Hedge Fund 20 分析师 | `quant_modules/ai_hedge_fund/`（LangGraph 编排） | 多空辩论结论 → signal_fusion 加权融合（信号建议） | 融合权重确定性计算；LLM 仅贡献信号分量 |
| GLM-5 盘中决策 | `utils/glm5_decision_engine.py` + `utils/glm5_client.py` | 决策建议 → **L1 decision_gate 硬风控门** → L2 execution_bridge（auto 模式仍被 veto/escalation 约束） | PreTradeGuard 六规则 + L2 风控 veto（测试 `test_execution_bridge` 覆盖 escalation/veto 链） |
| LLM 智能进化（Ideation） | `utils/llm_evolution/strategy_ideation.py`（D1-D4） | 因子/策略假设 → D2 假设验证（IC 显著性 + CRO Gate + 诚实三件套）→ 入库建议 | 入库需过 S1-S7 门禁；Kill Criteria 自动退役 |

**共同点**: 三条路径的输出都终止于"信号/假设/复盘报告"，执行动作均由确定性代码（风控六件套 T09-T18）独立决策。

## 三、路径注册表（新增 LLM 路径必须登记）

| # | 路径名 | 入口模块 | 输出性质 | 登记 |
|---|--------|---------|---------|------|
| 1 | ai_hedge_fund | `quant_modules/ai_hedge_fund/orchestrator.py` | 信号建议 | 既有 |
| 2 | glm5_intraday | `utils/glm5_decision_engine.py` | 决策建议（经 L1 门） | 既有 |
| 3 | llm_evolution | `utils/llm_evolution/strategy_ideation.py` | 因子假设 | 既有 |

## 四、检测性门禁（`scripts/check_llm_boundary.py`）

- **检测逻辑（AST 级）**: 扫描执行/下单模块（`automated_execution_system.py` / `ai_decision/execution_bridge.py` / `utils/execution/*` / broker 适配器 / T15 LiveOrderExecutor），若其 import 了 LLM 模块（`glm5*` / `ai_hedge_fund` / `llm_evolution` / `llm_gateway`）→ 报告违例（提示"LLM 模块被执行链直接引用，需人工确认是否绕过 decision_gate"）。
- **反向引用合法**: LLM 模块 import 风控/执行**接口类型**（如 RiskContext）不违例——检测按"执行链文件 → LLM 模块"单向判定。
- **退出码恒 0**（检测性非阻断）；CI 集成位置: `.github/workflows/ci.yml` 增量 job 可选步骤（转阻断须用户拍板）。

## 五、违例处置流程

1. 检测报告产出违例 → On-Call 人工核对该 import 是否产生执行语义。
2. 确认违例 → 当日移除直连（LLM 输出改走 decision_gate 输入通道）+ `cairn/LOG.md` 登记。
3. 误报 → 在检测脚本的合法映射表中登记豁免理由（含 LOG 指针）。

---
关联: Production Invariant I-01（ROADMAP）｜`ai_decision/decision_gate.py`（硬风控门）｜`scripts/check_llm_boundary.py`（检测器）
