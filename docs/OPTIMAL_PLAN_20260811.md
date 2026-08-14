# 最优计划：v9.x 工业级达标与 12-31 上实盘

> 生成日期：2026-08-11  
> 当前基线：assert_data_validity 12P/0F、industrial_grade_check 11P/1W/0F、engineering_debt_gate GREEN、代码审查积压 0  
> 硬 deadline：2026-12-31 上实盘  
> 计划周期：08-11 ~ 12-31（20 周）

> **🔧 08-12 二次更正（v3）**：G15 事件驱动回测的"完全不存在"判断已**证伪**。
> 经实际核查 [utils/backtest/](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/backtest) 目录，11 个模块 + 7 个测试文件已全部落地（W6.3 Sprint 3 于 2026-08-12 提前完成，超前 65+ 天）。
> 核心组件清单：`event_driven_engine.py`(663行) + `matching_engine.py`(423行) + `order_queue.py`(241行) + `adapters.py`(270行) + `latency_model.py`(150行) + `constraints.py`(130行) + `result_converter.py`(406行) + `a_share_rules.py`(W6.4.2 T+1规则) + `vectorbt_bridge.py`(W6.4.1 偏差0.000%) + `honest_validation.py`(W6.6.2 三件套) + `deflated_sharpe.py`(W6.6.2 DSR)。
> 证据指针：[ROADMAP.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/ROADMAP.md) W6.3 条目（backtest 219→264 全绿）+ [tests/unit/backtest/](file:///e:/各种PY程序/28-终极量化交易系统8.4/tests/unit/backtest) 7 个测试文件。
> 影响：① 阶段 A 工期从 2 周压缩为 ~1 周（仅余 G6+/G7/AutoResearch 三项）；② AutoResearch 前置依赖已就绪，可立即推进；③ 关键路径图与下一步立即执行列表同步更新。

> **🔧 08-11 核查更新（v2）**：经实跑核查，原计划存在以下偏差，已就地修正：
> - **G5 研究/生产隔离**：✅ **已完成**（原计划第五节误列为待办）。`engineering_debt_gate.py` 实跑 T4 [OK]，utils/ 下零 `import research.*`，债务等级 GREEN。
> - **G6 mypy CI 模式**：✅ **CI 已接入**（原计划标记"待接入"过时）。`.github/workflows/ci.yml` L281-286 已有 `MyPy Typecheck (utils/* — 阻断式)` 步骤，`continue-on-error` 已移除；`docs/mypy_baseline_v9.2.txt` (58KB) 已存在。剩余工作仅是 `mypy.ini` → `--strict` 收紧。
> - ~~**G15 事件驱动回测**：❌ **完全不存在**（原计划表述"骨架待建"模糊）。`utils/hedge_rebalance_backtest.py` 是纯向量化回测（1412 行），无 EventDrivenBacktest 类、无订单队列、无撮合引擎、无事件循环。需从零搭建，工期从 5 天调整为 **7-10 天**。~~ **🚨 08-12 二次更正：上述判断已证伪**。详见顶部 v3 注记。`utils/backtest/event_driven_engine.py` 等模块均存在且完整，W6.3 Sprint 3 已于 2026-08-12 提前完成。原 v2 判断误将 `hedge_rebalance_backtest.py` 当作全项目唯一回测路径，未核查 `utils/backtest/` 子包。
> - **AutoResearch Skill**：❌ **完全不存在**。全项目扫描 `auto_research` 关键字零匹配，需从零创建 `ai_decision/auto_research_skill.py`。**（08-12 注：前置依赖 G15 已就绪，本项可立即推进）**

---

## 一、现状诊断（08-11 快照）

| 维度 | 状态 | 说明 |
|------|------|------|
| 数据完整性 | ✅ 12/12 PASS | D1 压力测试 actual_pnl 已恢复非零 |
| 工程地基 | ✅ 11P/1W/0F | C1 WARN 反映 broker.enable=false 预期状态 |
| 工程债务 | ✅ GREEN | T1-T5 全部绿灯，T4 环境隔离已修复 |
| 代码审查 | ✅ 0 待办 | M-1~M-8 及 B1-B4、G-1~G-4 全部闭环 |
| 执行闭环 | ⚠️ 骨架完成 | G1 QMT 主链路已接线，待 xtquant 安装；G2/G4 撮合+落盘完成 |
| 研究隔离 | ✅ 已修复 | 生产代码无 research.* import，T4 GREEN |
| 测试覆盖 | ⚠️ 产物已生成 | coverage.xml 存在，4301 passed / 118 failed / 17 errors |
| mypy 基线 | ✅ baseline + CI 已接入 | docs/mypy_baseline_v9.2.txt (58KB) 已生成；ci.yml L281-286 阻断式接入（mypy.ini 配置，非 --strict） |
| 回测引擎 | ✅ 事件驱动已落地（08-12 更正）| `utils/backtest/event_driven_engine.py` 663行 + 撮合/队列/延迟/适配器/转换器全套；W6.4.1 vectorbt 对照偏差 0.000%；W6.4.2 T+1 6/6 全绿。原 v2 判断"纯向量化"过时 |
| 核心热路径 | ❌ 全 Python | 无 C++/Rust 加速，延迟分层未落实 |
| CVaR 计算 | ⚠️ 解析近似 | 非蒙特卡洛，尾部风险估计偏乐观 |
| Feature Store | ❌ 缺失 | 数据管道缺物理分层，特征复用率低 |
| 多源校验 | ⚠️ 弱 | 单一数据源故障时 fail-open 降级，缺交叉验证 |
| AutoResearch | ❌ 未建立 | 因子/策略自动迭代仍手工，缺版本化与回滚机制 |
| 测试健康度 | ⚠️ 118 failures | 主要因缺失依赖(deflated_sharpe/torch)、配置变更、编码问题 |

---

## 二、最优策略：三阶段冲刺

### 阶段 A：可信度修复（08-11 ~ 08-22，2 周）

**目标**：把当前“能跑”的状态固化为“可审计、可重复、可回滚”。

| 优先级 | 任务 | 交付物 | 验收标准 | 预估工期 |
|--------|------|--------|----------|----------|
| ✅ 已完成 | G5 研究/生产隔离 | （已交付）utils/ 下零 `import research.*` | engineering_debt_gate T4 GREEN（实跑通过） | 0 天 |
| ✅ 已完成 | G6 mypy CI 接入 | （已交付）ci.yml L281-286 阻断式 mypy | PR 含类型错误时 CI 红（已生效） | 0 天 |
| P0 | G6+ mypy --strict 收紧 | mypy.ini → --strict 模式 | mypy --strict 0 错误 | 1 天 |
| 🔧 W6.3.3 进展 | type:ignore 合约盲区清零 | directional_futures_trader(12) + wt_spread_strategy(28) + wt_backtest_engine(9) + automated_execution_system(15) + data_provider(29) = **93 处 → 0** | 全文件 0 type:ignore + mypy 0 错误 + 106 测试全绿 | 已完成 |
| P0 | G7 覆盖率提升 | 覆盖率从当前 ~50% → 80% | htmlcov 显示 ≥80% | 3 天 |
| P0 | ~~G15 事件驱动回测（从零）~~ | ✅ **已完成（W6.3 Sprint 3，2026-08-12 提前 65+ 天）**：`EventDrivenEngine` + `MatchingEngine`(TICK/BAR/HYBRID) + `OrderQueue` + `LatencyModel`(Fixed/Random/Queue) + `StrategyAdapter` + `ResultConverter` + 双模式时钟(MONOTONIC_INDEX/WALL_CLOCK_NS, NonMonotonicTimestampError 防前视硬门禁) + A股T+1规则 + vectorbt对照(偏差0.000%) | 与向量化回测偏差 0.000%（W6.4.1 验证）；264 测试全绿 | 0 天（原估 7-10 天已撤销） |
| P1 | AutoResearch Skill | ai_decision/auto_research_skill.py（从零创建） | 因子迭代闭环可运行（依赖 G15 完成） | 4 天 |
| P2 | G14 多源交叉校验 | 数据源健康检查 + 交叉比对 | 单源故障时告警而非静默降级 | 3 天 |

**阶段 A 门禁**：
- assert_data_validity 12/12 PASS
- industrial_grade_check 0 WARN
- engineering_debt_gate GREEN
- 测试 collection 0 errors

---

### 阶段 B：执行闭环补齐（08-23 ~ 09-30，5 周）

**目标**：从“信号生成”到“成交回报”全链路无断点，且可追溯。

| 优先级 | 任务 | 交付物 | 验收标准 | 预估工期 |
|--------|------|--------|----------|----------|
| P0 | G1 QMT 真实下单 | xtquant 安装 + 影子账户验证 | dry_run 影子期 2 周达标 | 7 天 |
| P0 | G2 再平衡撮合 | rebalance_order_executor 已存在，补 positions.json 更新 | 再平衡订单 FILLED 后 positions 同步 | 2 天 |
| P0 | G4 成交回报驱动 PnL | daily_pnl 完全由 fills 驱动 | 无成交时 PnL=0，有成交时精确匹配 | 3 天 |
| P1 | G11 CVaR 蒙特卡洛 | 替换解析公式为蒙特卡洛模拟 | 2000 路径 99% CVaR 可复现 | 4 天 |
| P1 | G9 Feature Store | 特征物理分层 + 缓存层 | 特征复用率 ≥60% | 5 天 |
| P2 | G10 核心热路径 Rust | 行情解码/订单生成 Rust 原型 | 延迟降低 50% 证明 | 10 天 |

**阶段 B 门禁**：
- 影子账户运行 2 周，PnL 偏离 < 2σ
- 成交回报 100% 落盘，fills 与 positions 一致
- CVaR 蒙特卡洛与解析公式偏差 < 10%

---

### 阶段 C：架构升级与实盘准备（10-01 ~ 12-31，13 周）

**目标**：达到 12-31 上实盘的 12 项准入标准。

| 优先级 | 任务 | 交付物 | 验收标准 | 预估工期 |
|--------|------|--------|----------|----------|
| P0 | 实盘灰度发布 | 10% 资金 → 50% → 100% | 每阶段回滚触发条件明确 | 持续 |
| P0 | 监控覆盖三大维度 | 系统/业务/数据监控大盘 | 告警 5 秒触达 On-Call | 5 天 |
| P1 | 策略容量评估 | 各策略容量天花板报告 | 单策略容量 ≤ 经验上限 | 3 天 |
| P1 | 交易成本模型 | Almgren-Chriss + 分层滑点 | 回测扣除真实冲击成本 | 4 天 |
| P1 | 因子生命周期管理 | 自动退役 + 季度审查 | 连续 6 个月 ICIR<0.2 自动标记 | 3 天 |
| P2 | 多策略组合优化 | 相关性 < 0.3 约束 | 期望 diversification 达标 | 5 天 |
| P2 | 极端情景风控 | 历史十大行情 + 蒙特卡洛 | 99% CVaR 覆盖 | 4 天 |

**阶段 C 门禁**：
- live-trading-admission-criteria 12/12 通过
- 影子账户与回测预期偏差 < 30%
- 极端情景最大损失 < 净值 20%

---

## 三、关键路径与依赖

```
[✅ G5 已完成] → G6+ --strict / G7 覆盖率 → [✅ G15 事件驱动回测 已完成 08-12] → AutoResearch Skill
                                                                                    ↓
G1 QMT 安装 → 影子账户 → 灰度发布 ← 成交回报驱动 PnL ← G4 修复
                                                                                    ↓
G11 CVaR → G9 Feature Store → G10 Rust 原型 → 核心热路径加速
```

**关键依赖**：
1. **xtquant 安装** 是 G1 的唯一外部阻塞，需立即启动环境申请（08-11 已确认未安装）
2. ~~**G5 隔离** 是 G6/G7 的前置~~ — ✅ 已完成，CI 不会误报
3. ~~**AutoResearch Skill** 严格依赖 G15 回测引擎事件化，否则自动迭代会污染向量化回测（`utils/hedge_rebalance_backtest.py` 当前是纯向量化）~~ **（08-12 更正：G15 已就绪，AutoResearch 可立即推进；纯向量化回测 `hedge_rebalance_backtest.py` 仍保留，事件驱动引擎在 `utils/backtest/` 子包内独立实现，两者通过 `ResultConverter` 同构 BacktestResult 对齐口径）**

---

## 四、资源与时间预算

| 阶段 | 工期 | 关键产出 | 风险 |
|------|------|----------|------|
| A | ~~2 周~~ **1 周（08-12 更正：G15 已完成，仅余 G6+/G7/AutoResearch）** | G6+ --strict / G7 80% / ~~G15 事件驱动骨架~~ / AutoResearch 原型 | ~~G15 从零搭建工期可能超 7 天~~（已撤销） |
| B | 5 周 | 执行闭环全通、影子账户就绪 | xtquant 安装延迟 |
| C | 13 周 | 实盘灰度、监控就绪 | 策略容量超预期需减仓 |

**总工时预估**：~~约 50 人天（按 1 人全职计，20 周内完成；G15 从零搭建增加 5 天）~~ **约 45 人天（08-12 更正：G15 已完成撤销 5 天）**

---

## 五、下一步立即执行（08-12 修订，v3）

> **修订说明**：原 v2 第 1 项 G5 隔离修复经实跑核查已完成，移除；G6 mypy CI 接入已生效，调整为"收紧到 --strict"。~~G15 与 AutoResearch 均需从零搭建，且存在严格依赖关系。~~ **（08-12 v3 更正：G15 已完成，AutoResearch 前置依赖已就绪，两项可并行推进）**

1. ~~**启动 G5 隔离修复**~~：✅ 已完成（utils/ 下零 `import research.*`，T4 GREEN 实跑通过）
2. **申请 xtquant 环境**：联系券商开通 QMT 权限，安装 xtquant 包（G1 唯一外部阻塞，越早启动越好）
3. **冻结 G7 覆盖率基线**：当前 ~50%，制定逐周提升计划（每周 +5%）
4. ~~**G15 事件驱动回测骨架**：在 `utils/backtest/event_driven.py`（新文件）创建 `EventDrivenBacktest` 类，含订单队列、撮合引擎、延迟模型、事件循环四要素（**7-10 天工期，AutoResearch 的前置**）~~ **✅ 已完成（W6.3 Sprint 3，2026-08-12）**：实际落地于 `utils/backtest/` 子包内 11 个模块，主引擎类为 `EventDrivenEngine`（非 `EventDrivenBacktest`），含订单队列/撮合/延迟/事件循环四要素 + 双模式时钟 + A股T+1规则 + vectorbt对照。详见顶部 v3 注记
5. **AutoResearch Skill 骨架**：在 `ai_decision/auto_research_skill.py` 新建（4 天工期，G15 已就绪可立即启动）
6. **G6+ mypy --strict 收紧**：从 `mypy.ini` 渐进模式 → `--strict`（1 天工期，可穿插）

---

## 六、验收标准（12-31 上实盘 checklist）

- [ ] assert_data_validity 12/12 PASS（连续 7 天）
- [ ] industrial_grade_check 0 FAIL / 0 WARN
- [ ] engineering_debt_gate GREEN（连续 7 天）
- [ ] 测试覆盖率 ≥ 80%
- [ ] mypy --strict 0 错误
- [ ] 影子账户运行 ≥ 2 周，PnL 偏离 < 2σ
- [ ] 成交回报 100% 落盘，fills 与 positions 一致
- [ ] 监控告警 5 秒触达
- [ ] 极端情景最大损失 < 净值 20%
- [ ] 策略容量评估报告完成
- [ ] 交易成本模型上线
- [ ] 灰度发布流程文档化

---

## 七、铁律重申

1. **研究/生产隔离**：任何自动迭代不得直接修改生产代码，必须通过 Skill 协议
2. **失败友好**：决策路径 fail-closed，观测路径 fail-open，均留日志
3. **可追溯**：每个订单/信号/成交全生命周期可审计
4. **灰度铁律**：实盘前必须经过影子账户 + 灰度发布，不得跳过
5. **门禁不后门**：任何指标不达标时，CI 必须阻断，不得为 deadline 放水
