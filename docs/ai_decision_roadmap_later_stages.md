# 多 AI 模型综合自动交易决策系统 — 后期计划（Roadmap）

> 本文档定义 `ai_decision` 包（v1，Shadow 优先实现）完成之后的后续阶段路线图。
> 当前状态：`ai_decision/` 10 个模块已实现，默认 Shadow 模式全链路跑通，无 Key 优雅降级；
> 缺口：`auto` 模式尚未对接实盘执行引擎，真实模型校准/回测验证/生产治理待做。

## 总目标

把"能跑通的 Shadow 决策中台"演进为"可灰度上线的可信自动执行系统"，遵循三条硬约束：
1. 真实模型缺失时一律降级，绝不因缺 Key 崩溃或误下单；
2. 硬风控独立于 AI，兜底优先级高于任何 AI 输出；
3. 任何上线必须走"影子 → 模拟 → 自动灰度"三阶段，且每阶段有回滚触发条件。

---

## 阶段一：真实 API 接入与模型校准（连接期）

**目标**：用户填入 Key 后，对应真实模型自动启用并能被量化评估。

- 在 `.env` 填入 `DEEPSEEK_API_KEY` / `GLM_API_KEY` / `MOONSHOOT_API_KEY` / `CLAUDE_API_KEY` / `OPENAI_API_KEY`，`get_active_provider()` 已支持探测，本阶段只做验证与基准。
- 建立**模型健康检查**：liveness probe（单次 ping + 延迟分布），失败自动熔断并回退 Mock（复用 `v8.3_institutional/src/ai/model_router.py` 的熔断器）。
- **延迟/成本基准**：逐模型记录 P50/P99 延迟、单次 token 消耗、月度预算上限；超预算自动降级 Mock（成本熔断）。
- **Brier 动态权重校准**：用 `AICoordinator` 已有 `ai_decision_accuracy` 表，叠加实盘/回测标签，滚动 30 日重估各角色权重，写回 `ai_decision.yaml` 的 `weight` 段。
- **角色-模型映射优化**：基于成本/延迟/质量实测，调整 DeepSeek=信号、GLM-5.2=合规、Kimi3=多模态研报、Claude/GPT=可扩展占位的分配，输出"最优角色映射表"。
- 验收：填 Key 后对应模型 REAL 启用，无 Key 仍 Mock 跑通；延迟/成本看板有数据。

## 阶段二：实盘执行链路对接（auto 模式落地）

**目标**：`decision_gate` 在 `auto` 放行后，把 `TradingDecision` 转化为可执行的交易指令，并经风控进入真实 broker。

- 新增 `ai_decision/execution_bridge.py`：将 `TradingDecision` 映射为 `utils/execution` 的 `ExecutionStrategy` / `OrderRouter.route_order()` 可消费的 `execution_plan`（含 side、symbol、qty、price_type、limit_price、slice 信息）。
- 对齐**硬风控**：单笔 ≤ 净值 2%、日内累计 ≤ 10%、涨跌停/停牌过滤、黑名单 —— 复用 `decision_gate.run_hard_risk` 与现有硬风控，**不重复造轮子**。
- 接入 `SmartOrderRouter` + broker（已有 `_use_live = smart_router and broker` 开关）；`shadow` 仅写审计、`paper` 产出模拟指令、`auto` 经阈值 + Judge 裁决类型放行。
- 灰度发布：自动资金 10% 运行 3 天 → 50% 运行 1 周 → 全量；每阶段回滚触发条件（PnL 偏离 > 2σ 立即回滚）。
- 执行结果回写审计：订单 id、成交价、滑点、TCA（`Implementation Shortfall`），落 `reports/ai_decision/`。
- 验收：`auto` 模式对通过阈值的决策真正下单，未通过则升级人工确认；硬风控拦截不受 AI 影响。

## 阶段三：样本外验证与回测（可信度）

**目标**：用历史数据证明 AI 决策相对基线的增量价值，且未被过拟合欺骗。

- **历史回放**：用逐日行情/新闻/研报快照回放 `run_decision` 全链路，评估命中率、Brier、夏普、最大回撤。
- **基线对比**：纯规则信号 vs 五 Agent 投票 vs AI 辩论聚合，看增量 IC 与边际夏普（要求边际夏普改善 > 0.05 才值得上线）。
- **偏差防控（严守记忆规则）**：用实际财报披露日而非报告期截止日、逐日成分股快照防幸存者偏差、停牌冻结不可交易、涨跌停不可成交、调仓日信号至少滞后一期。
- **辩论有效性分析**：辩论触发率、辩论后决策准确率提升（vs 快速聚合）、False Positive 率，确认辩论不是昂贵噪声。
- 验收：输出回测报告，后半段 CAGR ≥ 前半段 60%，每月 IC 显著为正；辩论增量可量化。

## 阶段四：性能 / 成本 / 可靠性优化

**目标**：在不触外部 API 的前提下保证 CI 与盘中实时性。

- Provider 层**响应缓存**（相同 prompt 命中）+ 批量并发；盘中辩论总超时 60s、盘后 300s 硬约束，超时降级"快速投票模式"。
- **Token 成本看板** + 预算熔断（超预算降级 Mock）。
- 多层防御对齐风控记忆：事前 30μs 检查（单笔金额/Delta/Gamma/保证金/撤单率）、事中毫秒监控（PnL 偏离 3σ、5 分钟涨跌 2% 削仓、盘口深度骤降 70% 撤单）、事后 Brinson 归因。
- 单元测试全量 Mock，断言"无 Key 也跑通 + 不真实下单"，CI 零外部 API 触碰。

## 阶段五：可观测性、监控与告警

**目标**：让决策系统可监控、可复盘、可告警。

- 决策仪表盘：模型命中率、Brier、辩论触发率、各模型延迟/成本、模式分布（shadow/paper/auto）。
- 审计日志分析：`reports/ai_decision/*.jsonl` → 每日自动复盘摘要。
- 告警规则：模型连续失败、Brier 跌破阈值、auto 模式异常放量、硬风控拦截突增；与 `realtime_monitor/` 集成，5 秒内触达 On-Call。
- 验收：仪表盘可看，告警可触发，复盘可输出。

## 阶段六：模型治理、A/B 与迭代

**目标**：让系统可持续演进而不退化。

- 多模型组合 A/B：对比不同角色分配方案的实盘表现。
- 模型退役/升级审查：连续 6 月 ICIR<0.2 或连续 3 月多空夏普<0 触发退役审查；因子/模型拥挤度监控。
- Prompt 版本管理 + 辩论框架迭代（复用 `bull_bear_case_builder_skill` 持续打磨多空论证模板）。
- 用户文档：如何填 Key、如何切模式、如何解读审计、如何配置灰度。

---

## 依赖与风险

- 阶段二依赖 `utils/execution` 的 `SmartOrderRouter`/`broker` 接口稳定；若其接口变化，需在 `execution_bridge.py` 隔离适配。
- 阶段三回测质量依赖历史数据快照完整性（行情/新闻/研报），数据缺口会引入前视偏差。
- 真实模型成本可能超出预算，阶段一的预算熔断是必要安全阀。
- 所有外部 API 调用在测试环境必须 Mock，避免 CI 误触与费用泄漏。

## 优先级建议

阶段一、二为"从 Shadow 到真实执行"的必经之路，优先级最高；
阶段三决定系统是否可信，应在任一 `auto` 放量前完成；
阶段四、五、六为生产级打磨，可随使用规模渐进推进。
