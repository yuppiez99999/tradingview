# v7.6 Institutional — 机构级实盘交易系统

> v7.6 在 v7.5 机构级实盘基础上，新增六模块日度增强决策桥接（Vol Targeting / Cash Yield / Hedge Commander / Crowding Detection / Black-Litterman / PnL Attribution）+ 自动化执行引擎 `auto_v76_runner.py`，一键产出完整日度操作清单。

| 字段 | 内容 |
|------|------|
| 版本 | 7.6-rebuild-500w-2030-exit |
| 基础规模 | 500 万 RMB |
| 账户结构 | 股票ETF账户 300万 + 对冲保护账户 200万 |
| 目标年化 | ≥ 8% |
| 最大回撤 | < 15% |
| 单笔风险 | ≤ 1.5% |
| 清仓目标 | 2030-12-31 |
| Python | 3.8+ (兼容至 3.14) |
| 依赖 | numpy, pandas, scipy, scikit-learn, pyyaml, ntplib (可选) |

---

## 1. 目录结构

```
v7.5_institutional/
├── config/
│   ├── settings.yaml                # 全局配置
│   ├── portfolio.yaml               # 组合配置
│   └── trade_plans/
│       └── auto_trade_plan_500w_2026-2030.json  # 自动交易计划
├── data/
│   ├── raw/{stock,futures,options}/ # 原始数据
│   ├── processed/                   # 清洗后数据
│   └── cache/                       # 数据源缓存
├── src/
│   ├── risk/
│   │   ├── risk_manager.py          # RiskManager + RiskBudgeter
│   │   ├── circuit_breaker.py       # 四级熔断引擎
│   │   └── vol_targeting.py         # ★v7.6 波动率目标定仓 (EWMA+GARCH双模型)
│   ├── hedging/
│   │   ├── beta_hedger.py           # EWMA Beta + 期货空头
│   │   ├── vol_hedger.py            # VIX 分级 + 期权保护
│   │   ├── correlation_hedger.py    # 相关性 + 避险资产
│   │   ├── hedge_coordinator.py     # 三联对冲协调器
│   │   └── hedge_commander.py       # ★v7.6 对冲执行指挥官 (Beta偏离自动纠偏)
│   ├── execution/
│   │   ├── smart_order_router.py    # SOR + Iceberg + 滑点熔断
│   │   ├── algo_engine.py           # TWAP/VWAP/POV/Iceberg
│   │   └── ntp_sync.py              # NTP 时间同步
│   ├── backtest/
│   │   ├── metrics.py               # Sortino/Calmar/DSR
│   │   ├── cost_model.py            # 佣金+滑点+融资成本
│   │   ├── walk_forward.py          # 滚动样本外回测
│   │   └── scenario_lib.py          # 三段压力测试
│   ├── pnl/                         # ★v7.6 PnL归因模块
│   │   └── pnl_attribution.py       # 日度收益分解 (Beta/Alpha/风格/行业/成本)
│   ├── portfolio/                   # ★v7.6 投资组合优化
│   │   └── black_litterman.py       # Black-Litterman 主观观点融合+均值方差优化
│   ├── signals/                     # ★v7.6 信号分析
│   │   └── crowding_detector.py     # 信号拥挤度检测 (ETF资金流集中度→Alpha衰减)
│   ├── treasury/                    # ★v7.6 现金管理
│   │   └── cash_yield.py            # 闲置现金逆回购自动部署 (GC001滚动)
│   └── v76_integration.py           # ★v7.6 六模块集成桥接 (核心编排器)
├── auto_v76_runner.py               # ★v7.6 自动执行引擎 (CLI入口+增强报告生成)
├── daily_workflow.py                # 日度工作流
├── logs/                            # 日志
├── tests/
│   └── test_v75.py                  # 单元测试
├── main.py                          # 主入口
└── README.md                        # 本文件
```

---

## 2. 快速开始

### 2.1 环境准备

```bash
# 安装依赖
pip install numpy pandas scipy scikit-learn pyyaml
# 可选: NTP 同步
pip install ntplib
```

### 2.2 系统自检

```bash
cd v7.5_institutional

# 全套自检
python main.py --full-test

# 或分别测试
python main.py --risk-test       # 风控模块
python main.py --hedge-test      # 对冲模块
python main.py --exec-test       # 执行模块
python main.py --backtest-test   # 回测模块
```

### 2.3 交易计划文件

v7.6 采用 **主计划 + 当日计划** 双层结构：

```bash
# 主计划：20 标的 + 对冲工具 + 三阶段执行
trade_plans/auto_trade_plan_500w_2026-2030.json

# 当日计划：由主计划拆分生成
trade_plans/trade_plan_{YYYYMMDD}.json
```

### 2.4 自动交易任务

v7.6 提供 Windows 任务计划注册脚本，支持盘前/盘后自动执行：

```powershell
# 注册自动任务（管理员 PowerShell）
cd v7.5_institutional
.\register_v76_tasks.ps1

# 测试盘前任务
.\register_v76_tasks.ps1 -Test pre

# 测试盘后任务
.\register_v76_tasks.ps1 -Test post

# 卸载任务
.\register_v76_tasks.ps1 -Uninstall
```

自动任务说明：
- `v76_PreMarket`：每个交易日 07:00 执行（Wind 校准 + 持仓更新 + 主计划/当日计划加载）
- `v76_PostMarket`：每个交易日 15:30 执行（每日报告 + 盈亏统计 + 再平衡评估）
- 周末自动跳过，失败自动重试 3 次，间隔 5 分钟

### 2.5 单元测试

```bash
cd v7.5_institutional
python tests/test_v75.py
# 或使用 pytest
python -m pytest tests/test_v75.py -v
```

### 2.6 v7.6 日度增强决策引擎 — `auto_v76_runner.py`

v7.6 的核心新增：一键自动化执行引擎，读取当日 PnL 报告和交易计划后，串联六模块桥接产出完整日度操作清单。

```bash
# 日度一键执行（需先有当日 PnL 报告 JSON）
python auto_v76_runner.py --date 2026-07-20

# 可集成到现有每日工作流
python auto_v76_runner.py --date $(date +%Y-%m-%d)
```

产出两份文件：`reports/v76_enhanced_report_{date}.md`（十段增强报告）和 `reports/v76_enhanced_report_{date}.json`（结构化数据），含以下决策输出：

Vol 缩放因子、现金逆回购收入、紧急对冲指令、板块拥挤度信号乘数、PnL 归因分解、风控状态摘要和操作清单。

---

## 3. v7.6 六模块架构

v7.6 在原有 v7.5 风控/对冲/执行/回测四层之上，新增六个日度增强模块，通过 `src/v76_integration.py` 统一桥接编排，五阶段流水线执行：

```
Stage A (Vol Targeting) → Stage B (Cash Yield) → Stage C (Hedge Commander)
→ Stage D (Crowding Detection) → Stage E (PnL Attribution)
```

### 3.1 Vol Targeting — 波动率目标定仓

Bridgewater All-Weather / AQR 标准的波动率目标定仓引擎。双模型融合（EWMA 60% + GARCH(1,1) 40%），将仓位缩放至目标年化波动率 12%。含回撤保护覆盖：回撤 > 10% 时自动减半缩放因子。

- 文件：`src/risk/vol_targeting.py`
- 核心类：`VolTargetingEngine`
- 关键方法：`update(daily_return, drawdown_from_hwm) -> float` 返回缩放因子 (0.25-2.00)

### 3.2 Cash Yield — 闲置现金逆回购

Bridgewater 风格的闲置现金自动管理。将超额现金（高于缓冲垫部分）的 85% 自动部署至 GC001 逆回购，产生约 1.55% 年化收益。支持节假日前效应增强（周五 ×2.5 倍）。

- 文件：`src/treasury/cash_yield.py`
- 核心类：`CashYieldManager`
- 关键方法：`optimize(total_cash, daily_margin_need) -> dict` 返回逆回购金额和预期日收益

### 3.3 Hedge Commander — 对冲执行指挥官

Bridgewater 风格的每日 Beta 对齐强制执行模块。根据实际组合 Beta vs 目标 Beta (0.30) 的偏差，分四级紧急度（ROUTINE / ELEVATED / URGENT / CRITICAL）自动生成期货合约调整指令，含超时升级机制：连续 2 天未执行对冲 → 强制标记。

- 文件：`src/hedging/hedge_commander.py`
- 核心类：`HedgeExecutionCommander`
- 关键方法：`assess(actual_beta, hedge_offset, contracts, notional) -> dict` 返回紧急度等级和目标合约数

### 3.4 Crowding Detection — 信号拥挤度检测

RenTech / AQR 标准的信号拥挤度检测器。监控 ETF 资金流集中度，当某板块 ETF 流入超过 30 亿时标记为拥挤（MILD: 0.80x → MODERATE: 0.60x → EXTREME: 0.30x 信号乘数），防止追逐已定价信号。含 8 天半衰期衰减。

- 文件：`src/signals/crowding_detector.py`
- 核心类：`SignalCrowdingDetector`
- 关键方法：`update(etf_flows: Dict[str, float]) -> dict` 返回每板块信号乘数

### 3.5 Black-Litterman — 主观观点融合优化

Goldman Sachs 标准 Black-Litterman 模型。用动态均衡先验（市场隐含收益）替代静态 8% 预期收益，融合主观观点（P/Q/Omega 矩阵）生成后验收益，再做均值方差权重优化。当前为可用模块，供外部调用。

- 文件：`src/portfolio/black_litterman.py`
- 核心类：`BlackLittermanEngine`
- 关键方法：`blend_views(prior_returns, cov, views) -> np.ndarray` 融合主观观点；`optimize_weights(returns, cov) -> np.ndarray` 均值方差优化

### 3.6 PnL Attribution — 收益归因分解

Citadel 标准的日度/周度/月度 PnL 分解引擎。将每日收益分解为 Beta 贡献 + Alpha + 风格因子 + 行业因子 + 特质收益 + 对冲贡献 + 交易成本共七个成分，同时记录每笔交易的 TCA（滑点分析）。

- 文件：`src/pnl/pnl_attribution.py`
- 核心类：`PnLAttributionEngine`
- 关键方法：`attribute_daily(positions, total_pnl, total_nav) -> dict` 返回七项分解 + Top 3 贡献者/拖累者

### 3.7 桥接集成 — `v76_integration.py`

六模块统一编排器，对外暴露单一接口：

```python
from v76_integration import v76IntegrationBridge

bridge = v76IntegrationBridge(total_capital=5_000_000)
bridge.initialize()
result = bridge.run_daily_enhanced(context)  # context 包含持仓/收益/ETF资金流等
# result 包含 vol_scale, cash_income, hedge_action, crowding, alpha_bps, actions[]
```

可直接被现有 `daily_workflow.py` 导入调用，一行集成。

---

## 4. 数据源优先级

系统采用多层级数据源降级策略，确保在任何网络/服务异常时仍可获取行情与基本面数据：

| 优先级 | 数据源 | 说明 |
|--------|--------|------|
| P0 | Wind 数据终端 | 主数据源，优先使用 WindPy 原生客户端 |
| P1 | Wind MCP | 强制回退，通过 analytics_data / stock_data / fund_data 获取数据 |
| P2 | iFinD MCP | 强制回退，同花顺金融终端 MCP |
| P3 | AKShare | 免费回退，A股/期货/指数等公开数据 |
| P4 | 新浪财经 API | 免费实时行情兜底 |
| P5 | 本地缓存 | Parquet/JSON 最近一次成功缓存 |
| P6 | 预定义价格 | 保证系统永不崩溃的兜底价格 |

强制规则：
- Wind 数据终端不可用时，必须尝试 Wind MCP，不可直接跳到 iFinD MCP
- 需要 `WIND_API_KEY` 环境变量（Wind MCP 认证密钥）
- 需要 `IFIND_TOKEN` 环境变量（同花顺 API 认证令牌）
- 所有模块必须遵循统一降级顺序，不得跳过中间层

---

## 5. ETF 追踪与风格轮动

系统内置 ETF 资金流监控与社保基金风格映射模块，用于：

- 24 只核心 ETF 资金流向监控
- 国家队/社保基金信号识别
- 风格轮动与板块配置偏离提醒

核心文件：
- `src/etf_tracker.py` — ETF 资金流监控
- `src/social_security_etf.py` — 社保基金 ETF 追踪（风格分类 + 国家队信号）
- `reports/etf_flow_report_*.md` — 每日 ETF 资金流报告

运行方式：
```bash
python daily_workflow.py --etf-flow
```

---

## 6. 核心模块说明（v7.5 基础层）

### 6.1 风险预算 (Risk Budgeting)

**改进型 Kelly (James-Stein 收缩)**:
$$
f_{final} = \min\left( \frac{1}{2} \cdot \frac{\hat{\mu}_{shrink} - r_f}{\sigma^2},\ \frac{0.015 \cdot C}{\sigma \cdot |\beta|} \right)
$$

**三级回撤防御**:

| 回撤区间 | 模式 | 仓位系数 | 允许操作 |
|---------|------|---------|---------|
| DD < 10% | NORMAL | 1.0 | 开 / 平 / 对冲 |
| 10% ≤ DD < 14% | DEFENSE | 0.5 | 只减不加 |
| DD ≥ 14% | CIRCUIT_BREAKER | 0.0 | 强制清仓 + 停止 24h |

**Risk Parity (ERC)**:
$$
w_i \cdot (\Sigma w)_i = w_j \cdot (\Sigma w)_j, \quad \forall i,j
$$

协方差采用 Ledoit-Wolf 收缩估计。

### 6.2 三联对冲 (Auto-Hedging)

| 对冲类型 | 触发条件 | 工具 |
|---------|---------|------|
| Beta Hedge | β > 0.7 | IF/IC/IM 期货空头 |
| Vol Hedge | VIX > 30 | Put Spread / 裸 Put / 紧急 Put |
| Correlation Hedge | ρ̄ > 0.85 | 黄金 ETF + 国债逆回购 |

期权对冲成本自适应: VIX > 40 时实际覆盖率反比衰减。

### 6.3 智能执行 (SOR)

- **Iceberg**: 单笔委托 ≤ 盘口深度 10%
- **滑点三级熔断**:
  - 单笔滑点 > 0.5% → 立即撤单
  - 日内累计滑点 > 1.0% → 暂停该标的 30 分钟
  - 全局滑点中位数 > 0.3% → 全局降速 50%
- **NTP 时间戳对齐**: 漂移 > 50ms 自动校准

### 6.4 回测协议

**目标函数**:
$$
\mathcal{J} = \text{Sortino} + 0.5 \cdot \text{Calmar} - \lambda \|\mathbf{w}\|_2^2
$$

**Walk-Forward**: 训练 24 月 / 测试 3 月 / 步长 3 月

**三段压力测试** (必过):
- 2020-02-19 ~ 2020-03-23 (COVID 闪崩)
- 2022-05-01 ~ 2022-05-12 (Luna 崩盘)
- 2024-08-01 ~ 2024-08-05 (日元 Carry Trade 平仓)

**Deflated Sharpe Ratio**: DSR ≥ 0.95 方可通过 CRO Gate

---

## 7. 配置文件

主配置在 [config/settings.yaml](file:///e:/各种PY程序/28-终极量化交易系统7.1/v7.5_institutional/config/settings.yaml)。

关键参数:

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
  walk_forward_step: 3
```

---

## 8. CRO Gate (上线前必过)

- [ ] Walk-Forward 5 窗口拼接 Sortino ≥ 1.0
- [ ] 三段压力测试 Max DD < 15%
- [ ] Deflated Sharpe Ratio ≥ 0.95
- [ ] 无未来函数 (PIT 检查通过)
- [ ] NTP 漂移 < 50ms 持续 7 个交易日
- [ ] 滑点熔断在历史回放中正确触发
- [ ] CRO 签字

---

## 9. 与 v7.4 的关系

v7.5 是 v7.4 的**机构级升级**, 不替代 v7.4:

- v7.4 (现有): 黑天鹅防护 + 因果验证 + 个股建仓
- v7.5 (新增): Risk Parity + 三联对冲 + SOR + Walk-Forward

两者可并存: v7.4 的 `comprehensive_quant_system_v7.py` 继续运行, v7.5 作为新的实盘引擎逐步接管。

---

## 10. 文档

- 设计 Memo: [../QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL.md](file:///e:/各种PY程序/28-终极量化交易系统7.1/QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL.md)
- 主入口: [main.py](file:///e:/各种PY程序/28-终极量化交易系统7.1/v7.5_institutional/main.py)
- 单元测试: [tests/test_v75.py](file:///e:/各种PY程序/28-终极量化交易系统7.1/v7.5_institutional/tests/test_v75.py)

---

**作者**: ZCode Quantitative Team
**日期**: 2026-07-20
**版本**: v7.6.0-institutional
