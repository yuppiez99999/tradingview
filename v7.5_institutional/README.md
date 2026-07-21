# v8.2 Institutional — 机构级全自动量化交易系统

> v8.2 在 v8.1 全自动交易链路基础上，引入双 LLM 架构 (Volcengine 豆包 + DeepSeek) 与深度思考模块，增强盘中决策质量。系统覆盖从宏观分析 → 信号生成 → 仓位管理 → 对冲执行 → 实盘下单 → 盘后归因的完整闭环。

| 字段 | 内容 |
|------|------|
| 版本 | 8.2-rebuild-500w-2030-exit |
| 基础规模 | 500 万 RMB |
| 账户结构 | 股票ETF账户 300万 + 对冲保护账户 200万 |
| 目标年化 | ≥ 10.72% (五路径交叉验证预测) |
| 最大回撤 | < 15% |
| 单笔风险 | ≤ 1.5% |
| 清仓目标 | 2030-12-31 |
| Python | 3.8+ (兼容至 3.14) |
| 核心依赖 | numpy, pandas, scipy, scikit-learn, pyyaml |
| 可选依赖 | ntplib (NTP同步), lightgbm (ML增强) |

---

## 1. 版本演进

| 版本 | 日期 | 核心新增 |
|------|------|---------|
| v7.5 | 2026-07-06 | 机构级重建: Risk Parity + 三联对冲 + SOR + Walk-Forward |
| v7.6 | 2026-07-18 | 六模块日度增强: Vol Targeting / Cash Yield / Hedge Commander / Crowding Detection / Black-Litterman / PnL Attribution |
| v8.0 | 2026-07-19 | 顶级对冲基金优化: 风险预算建仓 + Greeks动态对冲 + 交易成本模型 + 智能执行算法 |
| v8.1 | 2026-07-20 | 全自动交易链路: LLM盘中决策 + 同花顺券商接入 + 自动审批 + 执行复核 |
| v8.2 | 2026-07-21 | 双LLM架构 (豆包+DeepSeek) + 深度思考 + 年化预测 10.72% |

---

## 2. 目录结构

```
v7.5_institutional/
├── config/
│   ├── settings.yaml                # 全局配置 (风险参数/对冲/执行/回测)
│   ├── portfolio.yaml               # 20 标的组合配置 (四大板块权重)
│   └── positions.json               # 实时持仓状态
├── src/
│   ├── risk/
│   │   ├── risk_manager.py          # RiskManager + RiskBudgeter
│   │   ├── circuit_breaker.py       # 四级熔断引擎
│   │   ├── stress_tester.py         # 三段压力测试
│   │   └── vol_targeting.py         # ★v7.6 波动率目标定仓 (EWMA+GARCH)
│   ├── hedging/
│   │   ├── beta_hedger.py           # EWMA Beta + 期货空头
│   │   ├── vol_hedger.py            # VIX 分级 + 期权保护
│   │   ├── correlation_hedger.py    # 相关性 + 避险资产
│   │   ├── hedge_coordinator.py     # 三联对冲协调器
│   │   └── hedge_commander.py       # ★v7.6 对冲执行指挥官
│   ├── execution/
│   │   ├── smart_order_router.py    # SOR + Iceberg + 滑点熔断
│   │   ├── algo_engine.py           # TWAP/VWAP/POV/Iceberg
│   │   ├── broker_api.py            # 模拟券商 API
│   │   └── ntp_sync.py              # NTP 时间同步
│   ├── alpha/
│   │   ├── factor_library.py        # 因子库 (技术/基本面/宏观)
│   │   ├── signal_generator.py      # 信号生成器
│   │   └── signal_fusion.py         # 多源信号融合
│   ├── backtest/
│   │   ├── metrics.py               # Sortino/Calmar/DSR
│   │   ├── cost_model.py            # 佣金+滑点+融资成本
│   │   ├── walk_forward.py          # 滚动样本外回测
│   │   └── scenario_lib.py          # 三段压力测试
│   ├── pnl/                         # ★v7.6 PnL 归因
│   │   └── pnl_attribution.py       # 七成分日度收益分解
│   ├── portfolio/                   # ★v7.6 投资组合优化
│   │   └── black_litterman.py       # Black-Litterman 主观观点融合
│   ├── signals/                     # ★v7.6 信号分析
│   │   └── crowding_detector.py     # 信号拥挤度检测 (ETF资金流)
│   ├── treasury/                    # ★v7.6 现金管理
│   │   └── cash_yield.py            # 闲置现金逆回购自动部署
│   └── v76_integration.py           # ★v7.6 六模块统一桥接编排器
├── hexin_broker/                    # ★v8.1 同花顺券商集成
│   └── hexin_broker/
│       ├── hexin_broker.py          # 主券商接口 (登录/持仓/委托)
│       ├── stock_trader.py          # 股票交易执行
│       ├── futures_trader.py        # 期货交易执行
│       └── hexin_config.py          # 券商配置
├── ths_real_broker.py               # ★v8.1 同花顺实盘券商
├── ths_sim_broker.py               # ★v8.1 同花顺模拟盘券商 (734行)
├── llm_intraday_decision_engine.py  # ★v8.1 LLM 盘中决策引擎 (514行)
├── intraday_monitor.py              # ★v8.1 盘中实时监控 (516行)
├── ai_auto_approver.py              # ★v8.1 AI 自动审批 (213行)
├── ai_decision_sandbox.py           # ★v8.1 AI 决策沙箱 (344行)
├── dynamic_risk_adjuster.py         # ★v8.1 动态风险调整器 (294行)
├── execution_reviewer.py            # ★v8.1 执行复核 (188行)
├── etf_flow_monitor.py              # ★v8.1 ETF 资金流监控 (936行)
├── weekly_trade_executor.py         # ★v8.1 本周交易计划执行器 (725行)
├── auto_v76_runner.py               # ★v7.6 自动执行引擎 CLI
├── daily_workflow.py                # 日度工作流 (Phase 1-7)
├── execute_trade_plan.py            # 交易计划执行
├── main.py                          # 系统主入口
├── trade_plans/                     # 交易计划目录
│   ├── auto_trade_plan_500w_2026-2030.json  # 主交易计划
│   ├── trade_plan_YYYYMMDD.json     # 每日自动生成计划
│   └── weekly_plan_YYYYMMDD_YYYYMMDD.json   # 周度汇总
├── reports/                         # 报告归档
│   ├── annual_return_prediction_20260721.md  # 年化预测报告
│   └── v76_enhanced_report_*.md     # v7.6 增强报告
├── logs/                            # 系统日志
├── tests/
│   └── test_v75.py                  # 单元测试
└── register_weekly_trade_task.ps1   # ★v8.1 Windows 任务计划注册
```

---

## 3. 快速开始

### 3.1 环境准备

```bash
pip install numpy pandas scipy scikit-learn pyyaml lightgbm
# 可选
pip install ntplib    # NTP 时间同步
```

### 3.2 系统自检

```bash
cd v7.5_institutional

# 全套自检
python main.py --full-test

# 分模块测试
python main.py --risk-test       # 风控模块
python main.py --hedge-test      # 对冲模块
python main.py --exec-test       # 执行模块
python main.py --backtest-test   # 回测模块
```

### 3.3 交易计划管理

```bash
# 查看本周计划概览
python weekly_trade_executor.py --week

# 执行今日计划 (全天)
python weekly_trade_executor.py

# 上午/下午分批次
python weekly_trade_executor.py --session morning
python weekly_trade_executor.py --session afternoon

# 干跑模式 (不实际下单)
python weekly_trade_executor.py --dry-run

# 指定日期
python weekly_trade_executor.py --date 2026-07-22
```

### 3.4 v7.6 日度增强决策

```bash
# 一键执行 (需先有当日 PnL 报告)
python auto_v76_runner.py --date 2026-07-21

# 产出: reports/v76_enhanced_report_{date}.md + .json
```

### 3.5 LLM 盘中决策 (v8.1+)

```bash
# 盘中实时决策 (读取持仓+资金流+风险状态 → LLM分析 → 输出操作建议)
python llm_intraday_decision_engine.py

# 盘中监控 (实时价格+盈亏+熔断状态)
python intraday_monitor.py
```

### 3.6 Windows 自动任务

```powershell
# 注册每周交易任务 (管理员 PowerShell)
cd v7.5_institutional
.\register_weekly_trade_task.ps1

# 卸载
.\register_weekly_trade_task.ps1 -Uninstall
```

---

## 4. v7.6 六模块日度增强架构

v7.6 在 v7.5 风控/对冲/执行/回测四层之上新增六个日度增强模块，通过 `src/v76_integration.py` 五阶段流水线编排：

```
Stage A (Vol Targeting) → Stage B (Cash Yield) → Stage C (Hedge Commander)
→ Stage D (Crowding Detection) → Stage E (PnL Attribution)
```

### 4.1 Vol Targeting — 波动率目标定仓

Bridgewater All-Weather / AQR 标准。双模型融合 (EWMA 60% + GARCH(1,1) 40%)，仓位缩放至目标年化波动率 12%。回撤 > 10% 时自动减半缩放因子。

- 文件：`src/risk/vol_targeting.py`
- 核心类：`VolTargetingEngine`
- 返回：缩放因子 (0.25-2.00)

### 4.2 Cash Yield — 闲置现金逆回购

Bridgewater 风格。超额现金的 85% 自动部署至 GC001 逆回购，产生约 1.55% 年化收益。周五效应增强 (×2.5 倍)。

- 文件：`src/treasury/cash_yield.py`
- 核心类：`CashYieldManager`

### 4.3 Hedge Commander — 对冲执行指挥官

Bridgewater 风格每日 Beta 对齐强制执行。分四级紧急度 (ROUTINE / ELEVATED / URGENT / CRITICAL)，含 2 天超时升级机制。

- 文件：`src/hedging/hedge_commander.py`
- 核心类：`HedgeExecutionCommander`

### 4.4 Crowding Detection — 信号拥挤度检测

RenTech / AQR 标准。ETF 资金流集中度监控，拥挤时信号乘数递减 (MILD: 0.80x → MODERATE: 0.60x → EXTREME: 0.30x)。8 天半衰期衰减。

- 文件：`src/signals/crowding_detector.py`
- 核心类：`SignalCrowdingDetector`

### 4.5 Black-Litterman — 主观观点融合优化

Goldman Sachs 标准。动态均衡先验 + 主观观点融合 (P/Q/Omega) → 后验收益 → 均值方差权重优化。

- 文件：`src/portfolio/black_litterman.py`
- 核心类：`BlackLittermanEngine`

### 4.6 PnL Attribution — 收益归因分解

Citadel 标准。每日收益分解为七成分：Beta + Alpha + 风格因子 + 行业因子 + 特质收益 + 对冲贡献 + 交易成本。含 TCA 滑点分析。

- 文件：`src/pnl/pnl_attribution.py`
- 核心类：`PnLAttributionEngine`

### 4.7 六模块桥接 — 一行集成

```python
from v76_integration import v76IntegrationBridge

bridge = v76IntegrationBridge(total_capital=5_000_000)
bridge.initialize()
result = bridge.run_daily_enhanced(context)
# result 包含: vol_scale, cash_income, hedge_action, crowding, alpha_bps, actions[]
```

---

## 5. v8.0 顶级对冲基金优化

v8.0 在前序版本基础上新增四个优化维度：

**风险预算驱动建仓**: Risk Parity 权重替代等权，20 标的按波动率贡献分配每日建仓金额。

**Greeks 动态对冲**: 接入执行单的 Delta/Gamma/Theta/Vega 监控，自动调整期货合约手数和期权行权价。

**交易成本模型**: 佣金 + 印花税 + 滑点 + 冲击成本四层建模，成本扣减后自动调整交易量。

**智能执行算法选择器**: 根据标的流动性自动选择 TWAP (高流动性) / VWAP (中等) / Iceberg (低流动性)。

---

## 6. v8.1 全自动交易链路

### 6.1 LLM 盘中决策引擎

盘中实时采集持仓/盈亏/ETF资金流/风险状态，调用 LLM (豆包/DeepSeek) 生成操作建议，经 AI 自动审批后推送至交易执行模块。

- 文件：`llm_intraday_decision_engine.py` (514 行)
- 输入：PnL 报告 + ETF 资金流 + 风险状态
- 输出：操作建议清单 (加仓/减仓/对冲调整/不动)

### 6.2 同花顺券商集成

- `ths_sim_broker.py` (734 行): 模拟盘 - 股票/ETF/期货/期权全品种下单
- `ths_real_broker.py` (279 行): 实盘券商接口
- `hexin_broker/`: 同花顺内核封装 (登录/持仓/委托/查询)

### 6.3 AI 自动审批与执行复核

- `ai_auto_approver.py` (213 行): LLM 建议 → 规则校验 → 自动批准/标记人工复核
- `ai_decision_sandbox.py` (344 行): AI 决策沙箱 - 模拟执行 + 回滚验证
- `execution_reviewer.py` (188 行): 成交后复核 - 滑点/偏离/超时检查

### 6.4 盘中监控

- `intraday_monitor.py` (516 行): 实时价格 + 盈亏 + 熔断状态 + 偏离告警
- `dynamic_risk_adjuster.py` (294 行): 动态风险调整 - 根据盘中波动实时调整仓位上限

### 6.5 ETF 资金流监控

`etf_flow_monitor.py` (936 行): 24 只核心 ETF 日度资金流向跟踪，含风格轮动信号 + 拥挤度预警 + 国家队信号识别。

### 6.6 周度交易执行器

`weekly_trade_executor.py` (725 行): 从周计划文件读取本周全部交易日计划，支持上午/下午分批次执行，自动生成每日执行报告并更新周计划进度。

---

## 7. v8.2 双 LLM 架构与深度思考

v8.2 核心升级：将 LLM 决策从单模型升级为双模型并行推理 + 对抗验证。

**双 LLM 并行**: Volcengine 豆包 Speed (主力) + DeepSeek V3 (复核)，两模型独立生成决策后交叉验证，不一致时触发人工审核。

**深度思考模块**: 在交易信号生成前注入宏观分析层 (康波周期 + 十五五规划 + 社保基金)，使 LLM 决策具备中长期政策视角。

**年化收益五路径预测**: 自下而上 (12.93%) / 自上而下 (11.20%) / 风险预算 (10.80%) / 系统内置 (10.72%) / 历史回测 (10.10%)，概率加权后采纳基准 10.72%。

---

## 8. 数据源优先级

| 优先级 | 数据源 | 说明 |
|--------|--------|------|
| P0 | Wind 数据终端 | 主数据源，优先使用 WindPy 原生客户端 |
| P1 | Wind MCP | 强制回退，通过 analytics_data / stock_data 获取 |
| P2 | iFinD MCP | 强制回退，同花顺金融终端 |
| P3 | AKShare / AnySearch | 免费回退，A股/期货/指数公开数据 |
| P4 | 新浪财经 API | 免费实时行情兜底 |
| P5 | 本地缓存 | Parquet/JSON 缓存 |
| P6 | 预定义价格 | 保证系统永不崩溃的兜底价格 |

强制规则：Wind → Wind MCP → iFinD → 免费源，不可跳级。需 `WIND_API_KEY` 和 `IFIND_TOKEN` 环境变量。

---

## 9. 核心风控体系

### 9.1 风险预算 (Risk Budgeting)

改进型 Kelly (James-Stein 收缩): `f = min(0.5 × (μ_shrink - rf) / σ², 0.015 × C / (σ × |β|))`

三级回撤防御：

| 回撤区间 | 模式 | 仓位系数 | 操作 |
|---------|------|---------|------|
| DD < 10% | NORMAL | 1.0 | 正常交易 |
| 10% ≤ DD < 14% | DEFENSE | 0.5 | 只减不加 |
| DD ≥ 14% | CIRCUIT_BREAKER | 0.0 | 强制清仓 + 停止 24h |

### 9.2 三联对冲 (Auto-Hedging)

| 对冲类型 | 触发条件 | 工具 |
|---------|---------|------|
| Beta Hedge | β > 0.7 | IF/IC/IM 期货空头 (当前: 5手IF) |
| Vol Hedge | VIX > 30 | Put Spread / 裸 Put / 紧急 Put |
| Correlation Hedge | ρ̄ > 0.85 | 黄金 ETF + 国债逆回购 |

### 9.3 智能执行 (SOR)

- Iceberg: 单笔委托 ≤ 盘口深度 10%
- 滑点三级熔断: 单笔 > 0.5% (撤单) → 累计 > 1.0% (暂停30分钟) → 全局中位数 > 0.3% (降速50%)
- NTP 时间戳: 漂移 > 50ms 自动校准

### 9.4 组合安全边距

- 建仓期: 20 交易日 × 20 万/日 (8/7 前完成)
- KillSwitch: L0 正常 / L1 暂停买入 / L2 只平仓 / L3 强制清仓
- 对冲账户 200 万独立运作，不与现货账户混用

---

## 10. 回测协议

目标函数: `J = Sortino + 0.5 × Calmar - λ‖w‖²`

Walk-Forward: 训练 24 月 / 测试 3 月 / 步长 3 月

三段压力测试 (必过):
- 2020-02-19 ~ 2020-03-23 (COVID 闪崩)
- 2022-05-01 ~ 2022-05-12 (Luna 崩盘)
- 2024-08-01 ~ 2024-08-05 (日元 Carry Trade 平仓)

Deflated Sharpe Ratio: DSR ≥ 0.95 方可通过 CRO Gate

Monte Carlo 模拟预测: P50 年化 20.2% / 波动 14.1% / Sharpe 1.294 / 最大回撤 P50 -10.1%

---

## 11. 配置文件

主配置: `config/settings.yaml`

```yaml
risk:
  single_trade_risk: 0.015      # 单笔风险 1.5%
  defense_dd_threshold: 0.10
  circuit_break_dd: 0.14
hedging:
  beta_target: 0.3
  beta_trigger: 0.7
  vix_trigger: 30
  corr_trigger: 0.85
execution:
  slippage_break: 0.005
  ntp_max_drift_ms: 50
backtest:
  walk_forward_train: 24
  walk_forward_test: 3
```

---

## 12. CRO Gate (上线前必过)

- [ ] Walk-Forward 5 窗口拼接 Sortino ≥ 1.0
- [ ] 三段压力测试 Max DD < 15%
- [ ] Deflated Sharpe Ratio ≥ 0.95
- [ ] 无未来函数 (PIT 检查通过)
- [ ] NTP 漂移 < 50ms 持续 7 个交易日
- [ ] 滑点熔断在历史回放中正确触发
- [ ] CRO 签字

---

## 13. 关键文档

- **年化收益预测报告**: [reports/annual_return_prediction_20260721.md](file:///e:/各种PY程序/28-终极量化交易系统7.1/v7.5_institutional/reports/annual_return_prediction_20260721.md)
- **设计 Memo**: [../QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL.md](file:///e:/各种PY程序/28-终极量化交易系统7.1/QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL.md)
- **部署清单**: [DEPLOYMENT_CHECKLIST.md](file:///e:/各种PY程序/28-终极量化交易系统7.1/v7.5_institutional/DEPLOYMENT_CHECKLIST.md)
- **每周交易执行器**: `python weekly_trade_executor.py --week`
- **主入口**: [main.py](file:///e:/各种PY程序/28-终极量化交易系统7.1/v7.5_institutional/main.py)
- **单元测试**: [tests/test_v75.py](file:///e:/各种PY程序/28-终极量化交易系统7.1/v7.5_institutional/tests/test_v75.py)

---

**作者**: yuppiez99999  
**日期**: 2026-07-21  
**版本**: v8.2.0-institutional  
