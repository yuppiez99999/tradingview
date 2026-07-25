# v7.5 Institutional — 机构级量化交易系统升级文档

| 属性 | 值 |
|------|-----|
| 版本 | v7.5 Institutional |
| 基于 | QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL (QRM-2026-07-05-001) |
| 升级日期 | 2026-07-05 |
| 基础资金 | 500 万 RMB |
| 目标年化 | ≥ 8% (扣除成本后) |
| 最大回撤 | < 15% |

---

## 升级动因

v7.4 已具备"预警 + 风控决策 + 执行订单"闭环，但在四个维度距离 Citadel / Point72 级别实盘系统仍有差距：

1. **风险预算**：固定阈值 (VaR 5%, DD 15%)，缺少 Risk Parity + Improved Kelly 统一框架
2. **对冲联动**：偏静态情景分析，缺少 Beta / Vol / Correlation 三类对冲的实时联动
3. **执行算法**：仅三时点固定执行，无冰山订单、无滑点熔断、无 NTP 时间戳
4. **回测严谨性**：全样本 in-sample 拟合，缺少 Walk-Forward 与三段极端行情压力测试

## 核心升级

### 1. 风险预算 (Risk Budgeting)

**Risk Parity (ERC)**
- Ledoit-Wolf 协方差收缩 + Newton-Raphson 优化
- 各资产风险贡献相等原则分配仓位

**Improved Kelly Criterion**
- James-Stein Bayesian Shrinkage 收缩历史收益
- CAPM 隐含收益作为先验
- Half-Kelly + 单笔 1.5% 硬约束

**三级回撤防御**
| DD 区间 | 模式 | 动作 |
|---------|------|------|
| < 10% | NORMAL | 全仓位 |
| 10-14% | DEFENSE | 仓位减半，只减不加 |
| ≥ 14% | CIRCUIT_BREAKER | 强制清仓 + 24h 禁交易 |

### 2. 三联对冲 (Auto-Hedging)

**Beta Hedge**: EWMA Beta (λ=0.94) 实时估计，组合 Beta > 0.7 触发，对冲至 ≤ 0.3
- 工具: IF/IC/IM 期货空头
- 成本阈值: 对冲成本 / 组合市值 < 0.3%

**Volatility Hedge**: VIX > 30 触发
- VIX 30-40: Put Spread
- VIX 40-60: 裸 Put (Delta ≈ -0.2)
- VIX > 60: 紧急 Put + 流动性折价

**Correlation Hedge**: 平均相关 > 0.85 触发
- 黄金 ETF (518880) + 国债逆回购

### 3. 智能执行 (Smart Order Routing)

- **Iceberg**: 单笔 ≤ 盘口深度 10%
- **TWAP / VWAP / POV** 三算法
- **滑点熔断**: 单笔 0.5% + 累计 1.0% + 全局中位数检测
- **NTP 时间同步**: pool.ntp.org, 漂移 < 50ms

### 4. Walk-Forward + 压力测试

**Walk-Forward**: train 24m / test 3m / step 3m, 5-fold CV
**三段压力测试**:
| 场景 | 时间 | 冲击 |
|------|------|------|
| COVID Crash | 2020-02~03 | S&P -33.5% |
| Luna Crash | 2022-05 | LUNA -99.9% |
| Yen Carry | 2024-08 | 日经 -12.4% |

**通过判据**: Max DD < 15%, Sortino ≥ 1.0, Calmar ≥ 0.5, DSR ≥ 0.95

---

## 文件结构

```
v7.5_institutional/
├── config/
│   ├── settings.yaml          # 全局配置
│   ├── portfolio.yaml         # 组合配置 (28 标的)
│   ├── execution.yaml         # 执行算法参数
│   ├── backtest.yaml          # 回测协议
│   └── risk_budget.yaml       # 风险预算参数
├── src/
│   ├── alpha/                 # Alpha 信号模块
│   │   ├── factor_library.py  # 五维因子库
│   │   ├── signal_generator.py # LASSO + Ridge
│   │   └── signal_fusion.py   # ML+AI+康波融合
│   ├── hedging/               # 对冲模块
│   │   ├── beta_hedger.py     # IF/IC/IM 期货空头
│   │   ├── vol_hedger.py      # 期权保护性 Put
│   │   ├── correlation_hedger.py # 黄金+逆回购
│   │   └── hedge_coordinator.py  # 三联协调器
│   ├── execution/             # 执行模块
│   │   ├── smart_order_router.py  # Iceberg + 滑点熔断
│   │   ├── algo_engine.py     # TWAP/VWAP/POV
│   │   ├── broker_api.py      # CTP/Wind 接口
│   │   └── ntp_sync.py        # NTP 时间同步
│   ├── risk/                  # 风控模块
│   │   ├── risk_manager.py    # 三级回撤 + Kelly + RP
│   │   ├── risk_budgeter.py   # 风险预算分配
│   │   ├── circuit_breaker.py # 熔断引擎
│   │   └── stress_tester.py   # 压力测试
│   └── backtest/              # 回测模块
│       ├── walk_forward.py    # 滚动样本外
│       ├── cost_model.py      # Almgren-Chriss 成本
│       ├── metrics.py         # Sortino/Calmar/DSR
│       └── scenario_lib.py    # 三段极端场景
├── tests/                     # 单元测试
├── main.py                    # 启动入口
├── monitor.py                 # Streamlit 监控
└── README_v7.5.md            # 本文档
```

---

## 快速开始

```bash
cd v7.5_institutional/

# 系统检查
python main.py --mode check

# 压力测试
python main.py --mode stress

# 回测
python main.py --mode backtest

# 运行 (模拟)
python main.py --mode run

# 监控面板
streamlit run monitor.py
```

## 上线 Gate (CRO 签字项)

- [ ] Walk-Forward 全部窗口拼接 Sortino ≥ 1.0
- [ ] 三段压力测试 Max DD < 15%
- [ ] Deflated Sharpe Ratio ≥ 0.95
- [ ] 无未来函数 (PIT 检查通过)
- [ ] NTP 漂移 < 50ms 持续 7 交易日
- [ ] 滑点熔断在历史回放中正确触发
- [ ] CRO 签字

---

## 风险提示

本系统所有数学模型与参数均为研究建议，非实盘配置。实盘部署前必须通过 Risk Committee 三审。Walk-Forward 与压力测试结果不可作为未来收益保证。Kelly 公式与 Risk Parity 在极端尾部行情下可能失效，必须配合三联对冲与熔断机制使用。

---

*Prepared by Quant Research Desk — v7.5 Institutional Upgrade Initiative*
