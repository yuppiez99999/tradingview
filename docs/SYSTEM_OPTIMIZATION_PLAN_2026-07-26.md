# 系统下一步优化计划（2026-07-26）

> **视角**：顶级对冲基金 CTO / 量化系统架构师
> **当前版本**：v8.6.9（模拟盘模式 `TRADING_ENV=shadow`）
> **综合评分**：9.0/10（V3 审计），233 个测试全过，覆盖率 65.20%
> **核心瓶颈**：影子账户 Stage 1 仅运行 0/14 天，6 项 P1 风险缺口待补，finance_agent_orchestrator 未集成生产

---

## 一、现状诊断

### 1.1 已达成 ✅

| 维度 | 状态 | 数据 |
|------|------|------|
| 生产基线模型 | ✅ V9 Regime-Specific LGB | 年化 19.62% / 回撤 9.95% / Sharpe 1.315 / DSR=18 |
| 测试金字塔 | ✅ 233 个测试全过 | 单元 67% + 集成 25% + E2E 8%，覆盖率 65.20% |
| 风控守卫 | ✅ EOD 七 Guard 链 | KillSwitch 三级熔断 + 大盘熔断 + 流动性 + 隔夜跳空 + 回撤 + 波动率 + 对冲 |
| 信号融合 | ✅ 6 信号源 post-mix | alpha(0.70)+llm(0.10)+etf(0.12)+macro(0.08)+pipeline(0.05)+research(0.03) |
| GitHub 深度集成 | ✅ 3 项目落地 | code-review-graph + research_distiller + finance_agent_orchestrator |
| 环境隔离 | ✅ 三层保护 | `.env` + daily_workflow + signal_fusion |
| Windows 任务 | ✅ 4 任务 SYSTEM+HIGHEST | 06:00/07:00/09:30/14:00 |

### 1.2 核心瓶颈 ⚠️

| 编号 | 瓶颈 | 影响 | 紧迫度 |
|------|------|------|--------|
| **B1** | 影子账户 Stage 1 运行 0/14 天，`daily_nav=[]` 空数组 | 阻断 Stage 2 推进，无法验证 OOS 表现 | 🔴 高 |
| **B2** | finance_agent_orchestrator 未集成 daily_workflow Phase 7 | 多 Agent Shadow Mode 无法在生产对比验证 | 🟡 中 |
| **B3** | 6 项 P1 风险缺口（broker_callback / 熔断场景 / 相关性对冲 / risk_managed） | 黑天鹅极端市场对冲能力不完整 | 🟡 中 |
| **B4** | 测试覆盖率阈值 `.coveragerc fail_under=40`（实际 65.20%） | 阈值滞后，无法防止覆盖率退化 | 🟢 低 |
| **B5** | QuantPipelineFactor_06AM Last Result=1（SYSTEM 用户 PATH） | 离线因子生成在 SYSTEM 下失败（手动可跑） | 🟢 低 |
| **B6** | 数据源 3/5 健康（Wind MCP 未部署 / AKShare 代理问题） | 主数据源缺失，降级链兜底 | 🟢 低 |

---

## 二、优化计划（4 周路线图）

### 总体策略

```
第 1 周: 模拟盘稳定运行 + 集成补全 (B1+B2+B4)
第 2 周: P1 风险缺口修复 (B3: P1-L + P1-K + P1-G)
第 3 周: 黑天鹅场景 + 数据源优化 (B3: P1-H/I/J + B6)
第 4 周: Stage 2 评估准备 + 持续运行 (B1 验收)
```

---

### 第 1 周：模拟盘稳定运行 + 集成补全

> **目标**：让影子账户开始累积 NAV 数据，finance_agent_orchestrator 投入 Shadow Mode 运行

#### 任务 1.1: 影子账户 Stage 1 持续运行（B1）

| 项 | 内容 |
|----|------|
| **输入** | `output/shadow_account/shadow_state.json`（当前 `daily_nav=[]`） |
| **动作** | 确保 2026-07-27 起 daily_workflow Phase 10 `phase_shadow_monitor` 每日触发，写入 NAV 记录 |
| **验收** | 14 天后 `daily_nav` 长度 ≥ 10（允许 4 天非交易日），NAV 在 [0.97, 1.03] 区间 |
| **监控点** | 每日 16:00 检查 `shadow_state.json` 的 `last_updated` 和 `daily_nav` 长度 |
| **回滚** | 若 NAV 偏离 > 3%，触发 fail-fast 终止影子账户 |
| **工时** | 0h（已实现，仅需运行） |

#### 任务 1.2: finance_agent_orchestrator 集成 daily_workflow Phase 7（B2）

| 项 | 内容 |
|----|------|
| **输入** | `utils/finance_agent_orchestrator.py`（已就绪，16KB） |
| **动作** | 在 `daily_workflow.py` Phase 7 报告生成后追加 Shadow Mode 调用块（独立 try/except） |
| **注入点** | Phase 7 `phase_report` 末尾，Phase 8 之前 |
| **环境隔离** | 仅 `shadow/development` 模式激活，`production` 跳过（沿用 research_distiller 模式） |
| **验收** | 每日 16:00 后 `data/agent_orchestrator_audit/shadow_diffs_YYYYMMDD.jsonl` 存在且非空 |
| **测试** | 新增 2-3 个 E2E 测试验证 daily_workflow → orchestrator 完整链路 |
| **工时** | 2h |

**注入块设计**：
```python
# === v8.6.9 新增: 金融多Agent Shadow Mode (Phase 7) ===
try:
    from utils.trading_env import get_trading_env, TradingEnv
    if get_trading_env() != TradingEnv.PRODUCTION:
        from utils.finance_agent_orchestrator import FinanceAgentOrchestrator
        orchestrator = FinanceAgentOrchestrator()
        # 对持仓标的批量 orchestrate + shadow_compare
        for symbol in positions.keys():
            consensus = orchestrator.orchestrate(symbol, context)
            diff = orchestrator.shadow_compare(fusion_result, consensus)
            orchestrator.save_audit_log(consensus, diff, self.trade_date)
except Exception as e:
    logger.warning(f"金融多Agent Shadow Mode 失败 (不影响主流程): {e}")
```

#### 任务 1.3: 测试覆盖率阈值提升（B4）

| 项 | 内容 |
|----|------|
| **输入** | `.coveragerc`（当前 `fail_under = 40`） |
| **动作** | 提升到 `fail_under = 60`（实际 65.20%，留 5% 安全边际） |
| **验收** | `pytest --cov` 失败时退出码非 0（防止覆盖率退化） |
| **工时** | 5min |

#### 任务 1.4: Windows 任务 PATH 修复（B5）

| 项 | 内容 |
|----|------|
| **输入** | `QuantPipelineFactor_06AM` Last Result=1 |
| **动作** | 在 `setup_scheduled_tasks.bat` 中为 SYSTEM 任务配置 PATH，或改用 `py -3` launcher |
| **验收** | 2026-07-27 06:00 任务 Last Result=0 |
| **工时** | 30min |

---

### 第 2 周：P1 风险缺口修复（核心风控）

> **目标**：补全 3 项最关键的 P1 风险缺口，提升黑天鹅防御能力

#### 任务 2.1: P1-L shadow_account risk_managed 模式集成（B3-L）

| 项 | 内容 |
|----|------|
| **问题** | `shadow_account.py` 的 risk_managed 模式（波动率缩放 15% + 回撤去杠杆 50%）已实现但未集成生产 |
| **动作** | 在 `PortfolioOptimizer` 或 `daily_workflow Phase 10` 中激活 risk_managed=True |
| **设计** | 目标年化波动率 15%（对齐生产 MAX_DRAWDOWN_LIMIT=15%）+ 回撤>5% 时敞口降至 50%（基于昨日净值避免前视偏差） |
| **验收** | 影子账户 NAV 波动率 ≤ 15%，回撤>5% 时自动降杠杆 |
| **测试** | 新增 3-5 个单元测试验证 risk_managed 逻辑 |
| **工时** | 4h |

#### 任务 2.2: P1-K 相关性对冲集成 EOD Guard 链（B3-K）

| 项 | 内容 |
|----|------|
| **问题** | `correlation_monitor.py` + `correlation_hedger.py` 已实现但孤立，未集成 EOD Guard 链 |
| **动作** | 在 `risk_guard_integrator.py` 的 `run_all_guards()` 中追加第 8 个 Guard：相关性对冲 |
| **触发条件** | 持仓相关性 > 0.85（极端集中风险）时触发对冲订单 |
| **验收** | EOD 报告包含 `correlation_hedge` 字段，高相关时生成对冲订单 |
| **测试** | 新增 2-3 个集成测试验证 Guard 链 8 个环节 |
| **工时** | 2h |

#### 任务 2.3: P1-G broker_callback 注册（B3-G）

| 项 | 内容 |
|----|------|
| **问题** | KillSwitch L2/L3 强平动作无法真实执行（仅"标记"不"执行"），broker_callback 从未注册 |
| **动作** | 实现 `broker_callback` 对接 QMT/CTP 券商 API，注册到 KillSwitch |
| **设计** | L2 取消待执行订单 + 期权平仓；L3 变现 ETF + 停止交易 |
| **降级** | 模拟盘模式下 callback 仅记录日志（不真实下单），production 模式下真实执行 |
| **验收** | KillSwitch 触发 L2/L3 时 callback 被调用，日志记录执行动作 |
| **测试** | 新增 3-5 个单元测试 mock broker_callback |
| **工时** | 4h（需对接 QMT/CTP，模拟盘先 mock） |

---

### 第 3 周：黑天鹅场景 + 数据源优化

> **目标**：补全极端市场场景防护，提升数据源可靠性

#### 任务 3.1: P1-H 大盘熔断场景实现（B3-H）

| 项 | 内容 |
|----|------|
| **问题** | 沪深300 跌 5%/7% 触发全局平仓的场景未实现 |
| **动作** | 在 `market_circuit_breaker.py` 中新增沪深300 熔断检测 |
| **触发** | 沪深300 跌 5% → L2 禁止开仓；跌 7% → L3 全局平仓 |
| **验收** | 熔断触发时 Guard 链正确响应 |
| **工时** | 8h |

#### 任务 3.2: P1-I 隔夜跳空缺口控制（B3-I）

| 项 | 内容 |
|----|------|
| **问题** | 无 9:25 集合竞价前仓位调整 |
| **动作** | 在 `overnight_gap_monitor.py` 中新增 9:25 预检逻辑 |
| **验收** | 隔夜跳空 > 3% 时生成减仓建议 |
| **工时** | 4h |

#### 任务 3.3: P1-J 全局撤单场景（B3-J）

| 项 | 内容 |
|----|------|
| **问题** | >2000 家涨跌停时全局撤单未实现 |
| **动作** | 在 `risk_guard_integrator.py` 中新增全局撤单 Guard |
| **验收** | 极端场景触发时撤销所有未执行订单 |
| **工时** | 4h |

#### 任务 3.4: 数据源优化（B6）

| 项 | 内容 |
|----|------|
| **Wind MCP** | 部署 Wind MCP 主数据源（替换文件不存在的占位） |
| **AKShare** | 修复 curl_cffi 代理问题（升级 curl_cffi 版本或配置代理） |
| **情绪数据** | 接入真实情绪数据源（东方财富/雪球舆情），填补 `_fetch_sentiment_data()=None` 缺口 |
| **工时** | 6h |

---

### 第 4 周：Stage 2 评估准备 + 持续运行

> **目标**：影子账户运行满 14 天，评估 Stage 2 推进条件

#### 任务 4.1: Stage 2 验收评估（B1 验收）

| 验收条件 | 阈值 | 当前 |
|---------|------|------|
| 运行天数 | ≥ 14 天 | 0/14 ⏳ |
| PBO（回测过拟合概率） | < 0.5 | 待计算 |
| Sharpe | ≥ 0.5 | 待计算 |
| 回撤 | ≤ 2×CAGR | 待计算 |
| NAV 稳定性 | [0.97, 1.03] | 待观察 |

#### 任务 4.2: finance_agent_orchestrator Shadow Mode 评估

| 验收条件 | 阈值 |
|---------|------|
| 审计日志连续天数 | ≥ 10 天 |
| Agent 共识 vs SignalFusion 偏差 | < 15% |
| RiskAgent veto 触发频率 | < 5%（非异常频繁） |

#### 任务 4.3: research_distiller 信号源质量评估

| 验收条件 | 阈值 |
|---------|------|
| 蒸馏信号连续天数 | ≥ 10 天 |
| 信号非空率 | ≥ 80% |
| 信号方向准确率 | ≥ 55%（vs 后续收益方向） |

---

## 三、优先级矩阵

```
紧急度 ↑
  │
  │  B1(影子账户)     B3-G(broker_callback)
  │  B2(orchestrator)  B3-L(risk_managed)
  │
  │  B4(覆盖率阈值)   B3-K(相关性对冲)
  │  B5(PATH修复)     B3-H(大盘熔断)
  │
  │  B6(数据源)       B3-I/J(跳空/撤单)
  │
  └─────────────────────────────→ 影响范围
```

---

## 四、资源估算

| 周次 | 任务 | 工时 | 新增测试 |
|------|------|------|---------|
| 第 1 周 | B1 运行 + B2 集成 + B4 阈值 + B5 PATH | ~3h | +3 E2E |
| 第 2 周 | B3-L + B3-K + B3-G | ~10h | +11 单元/集成 |
| 第 3 周 | B3-H + B3-I + B3-J + B6 | ~22h | +9 单元/集成 |
| 第 4 周 | Stage 2 评估 + 持续运行 | ~4h | — |
| **合计** | — | **~39h** | **+23 测试** |

**预期成果**：
- 测试用例 233 → 256 个
- 覆盖率 65.20% → 70%+
- P1 风险缺口 6 → 0
- 影子账户 Stage 1 → Stage 2 就绪

---

## 五、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 影子账户 NAV 偏离 > 3% | 中 | Stage 2 推迟 | risk_managed 模式 + fail-fast 终止 |
| broker_callback 对接延迟 | 高 | L2/L3 无法真实执行 | 模拟盘先 mock，实盘前必须完成 |
| 大盘熔断场景测试困难 | 中 | 极端场景未验证 | 用历史熔断数据回测验证 |
| 数据源部署受阻 | 中 | 降级链兜底 | 保持 3/5 健康数据源，优先 iFinD |

---

## 六、成功标准

### 4 周后达成

1. ✅ 影子账户 Stage 1 运行满 14 天，NAV 稳定在 [0.97, 1.03]
2. ✅ finance_agent_orchestrator 每日产出 Shadow Mode 审计日志
3. ✅ 6 项 P1 风险缺口全部修复，EOD Guard 链从 7 → 8 个
4. ✅ 测试覆盖率 ≥ 70%，测试用例 ≥ 256 个
5. ✅ Stage 2 推进条件评估完成，决定是否进入 Stage 2（50% 资金）

### 关键约束（不可违反）

1. **模拟盘优先**：所有新功能先在 `TRADING_ENV=shadow` 验证，不接入实盘
2. **post-mix 模式**：新信号源不修改主融合公式
3. **fail-closed 原则**：异常时阻止交易，不降级放行（production 模式）
4. **测试先行**：每个 P1 修复必须配套回归测试
5. **14 天最小周期**：影子账户未满 14 天不推进 Stage 2

---

## 七、立即执行项（今天可做）

1. **任务 1.3**：提升 `.coveragerc fail_under` 从 40 → 60（5min）
2. **任务 1.2**：finance_agent_orchestrator 集成 daily_workflow Phase 7（2h）
3. **任务 1.4**：Windows 任务 PATH 修复（30min）

这三项今天即可完成，为后续 4 周优化计划奠定基础。
