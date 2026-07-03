# 综合量化策略系统 v7.1

**期货+期权双层对冲 | 建仓计划执行 | 机构级风控 | 极端情景应对 v2.0 | 多维度整合 v3.0**

---

## 系统概述

综合量化策略系统 v7.1 是一个专业量化交易平台，在 v7.0.1 基础上整合了来自 `E:\各种PY程序` 代码库中的高价值模块，扩展了宏观分析、流动性控制、止损监控、多因子评估、增强回测和资金流向监控等能力。系统以 500 万元人民币为基础管理规模，目标年化收益 >= 8.5%，最大回撤控制在 15% 以内。

**v7.1 核心升级**：
- **实体经济指标**：7种工业品价格（瓦楞纸/废纸/水泥/螺纹钢/铜/铝/白酒）构建宏观体温计
- **流动性风险控制**：四层交易成本分解 + 分批执行策略 + 组合流动性评估
- **止损止盈监控**：多级预警（正常/预警/危险/触发）+ 移动止盈 + 综合风险评分
- **五维因子模型**：价值/质量/动量/增长/安全 + 事件驱动因子 + IC检验
- **增强回测引擎**：波动率自适应权重 + 回撤分级控制 + 风险平价 + 交易成本建模
- **ETF资金流向**：12只国家队ETF监控 + 三级信号 + 板块轮动 + 个股映射

**极端情景应对 v2.0 (2026-07-03)**：建仓工作流集成五级紧急响应系统、前瞻性压力测试（熊市-35%/黑天鹅-55%）、实时市场状态监控、动态建仓资金调整、保护性看跌期权对冲建议、增强型厚尾蒙特卡洛模拟。

## v7.0 对冲架构

```
总资金 500万
├── 权益多头 60% (300万) —— 五因子选股 + 风险平价
└── 对冲策略 40% (200万) —— 五层递进保护
    ├── Layer 1: 股指期货Delta对冲   10% (50万)  市场Beta对冲
    ├── Layer 2: 期权保护性看跌     10% (50万)  尾部风险凸性保护
    ├── Layer 3: 波动率对冲/套利     8% (40万)  IV/RV偏离交易
    ├── Layer 4: 绝对收益/市场中性   7% (35万)  行业中性多空配对
    └── Layer 5: 备兑开仓增强       5% (25万)  权利金增收
```

### 五层对冲策略详情

| 层级 | 策略 | 核心能力 | 市场状态自适应 |
|------|------|----------|----------------|
| Layer 1 | 期货Delta对冲 | 动态Beta目标、VIX联动比率、动量过滤 | 牛市Beta=0.3, 震荡Beta=0.1, 熊市Beta=-0.1 |
| Layer 2 | 期权保护性对冲 | Put Ladder (90/85/80%)、Put Spread Collar、Tail Hedge (75%) | 崩盘全开、高波增加、牛市仅Collar |
| Layer 3 | 波动率套利 | IV/RV偏离监测、历史分位数过滤 | IV>70分位做空、IV<30分位做多 |
| Layer 4 | 绝对收益 | 行业因子中性多空配对 | 全市场状态 |
| Layer 5 | 备兑开仓 | IV自适应行权价、到期智能展期 | 高IV卖8% OTM、低IV卖3% OTM |

### 历史压力测试验证 (回撤目标 < 15%)

| 场景 | 原始回撤 | 对冲后回撤 | 结果 |
|------|---------|-----------|------|
| 2024年初回调 | -10.0% | -5.1% | PASS |
| 2020疫情冲击 | -14.0% | -8.3% | PASS |
| 2022年下跌 | -22.0% | -11.8% | PASS |
| 2018年熊市 | -25.0% | -13.5% | PASS |
| 2015年股灾 | -33.0% | -19.6% | 需80%+对冲 |

### 前瞻性压力测试（v2.0 新增）

在建仓阶段每日开盘前自动运行，对接极端情景应对系统：

| 场景 | 市场冲击 | 波动率 | 流动性折价 | 相关崩溃 | 触发条件 |
|------|---------|--------|-----------|---------|---------|
| 2026-2027熊市 | -35% | 3.0x | 40% | 70% | 中美科技博弈+地产共振 |
| 台海冲突黑天鹅 | -55% | 4.5x | 80% | 95% | 地缘冲突+外资撤离+金融三重共振 |
| 半导体全面制裁 | -40% | 3.5x | 30% | 85% | 美国升级对华出口管制 |

压力测试结果自动反馈到建仓决策：黑天鹅损失>50%时强制暂停建仓，熊市损失>35%时升级风险等级。

## 极端情景应对系统（v2.0 新增）

### 五级紧急响应协议

建仓工作流在每个交易日自动检测市场状态，按五级协议响应：

| 等级 | 名称 | 触发条件 | 响应动作 | 建仓倍率 |
|------|------|---------|---------|---------|
| 0 | 正常 | VIX<30，跌幅正常 | 正常建仓 | 100% |
| 1 | 黄色 | VIX>30 或 单日>3% 或 两融降>5% | 建仓减半，增现金储备，仅核心仓位 | 50% |
| 2 | 橙色 | VIX>35 或 单周>8% | 暂停建仓，密切监控已建仓位 | 0% |
| 3 | 红色 | VIX>40 或 双周>15% 或 两融降>10% | 停止所有交易，启动期权对冲 | 0% |
| 4 | 极端 | VIX≥50 或 5日>12% 或 20日>25% | 全部停止，转入纯防御模式，联系券商 | 0% |

### 实时市场监控指标

每天开盘前自动采集以下指标并通过五级协议判定：

- **VIX代理**：50ETF期权隐含波动率或历史波动率×2.5
- **指数回报**：5日/20日滚动涨跌幅
- **两融余额**：5日变动比例（流出=负值）
- **行业健康度**：高端制造、半导体板块20日涨跌

### 保护性对冲建议

按紧急等级提供四级对冲方案：

| 等级 | 对冲方案 | 预算 | 保护效果 |
|------|---------|------|---------|
| 黄色 | 现金储备10-15% | 无直接成本 | 流动性缓冲，低位补仓 |
| 橙色 | OTM Put (科创50×30%名义,行权价90%) | 约3-5万元 | 科创50跌>10%时非线性赔付 |
| 红色 | ATM Put (科创50×50%名义,行权价95%) | 约8-15万元 | 接近1:1下行保护 |
| 极端 | 全面防御(国债+黄金+现金) + 深度OTM Put | 清仓成本+1-2%权利金 | 最大限度保护剩余资金 |

## v7.1 新增模块

### 宏观经济维度

**实体经济指标** (`utils/real_economy_indicator.py`)：基于7种工业品价格的宏观晴雨表。
- 瓦楞纸/废纸 → 消费活跃度；水泥 → 当日需求；螺纹钢 → 地产周期
- 铜/铝 → 制造业+新能源+AI需求；白酒 → 商务活动+财富效应
- Z-Score归一化 + 五级经济状态判定（过热/偏热/正常/偏冷/衰退）
- 输出风险信号：regime + capital_multiplier，直连 EnhancedRiskManager

### 执行风控维度

**流动性风险控制** (`utils/liquidity_risk.py`)：
- 四层交易成本分解（佣金/印花税/滑点/冲击成本）
- 代码前缀自动识别流动性特征（ETF=1.0, 主板=1.2, 科创=1.5）
- 大单自动分批执行（单批<=日成交量20%，最多30天）
- 组合流动性评估（评分/等级/退出天数）

**止损止盈监控** (`utils/stop_loss.py`)：
- 四级预警（正常→预警→危险→触发），距止损位距离驱动
- 移动止盈（从最高点回撤阈值触发止盈价更新）
- 综合风险评分（PnL 40% + 距止损 40% + 固有风险乘数）
- 格式化风险报告生成

### 策略研究维度

**五维因子模型** (`utils/factor_model.py`)：
- 价值/质量/动量/增长/安全五大维度
- 可扩展事件驱动因子（舆情情绪 + 事件冲击）
- 组合级别信号聚合（strong_buy/buy/hold/sell/strong_sell）
- 因子间相关性分析

**增强回测引擎** (`utils/enhanced_backtest.py`)：
- 波动率倒数加权（低波资产更高权重，60%基础+40%波动率混合）
- 回撤分级控制（6档：2%/4%/6%/8%/10%+）
- 自适应再平衡（时间间隔+权重偏差双触发）
- 风险平价权重（等风险贡献）

### 资金流向维度

**ETF资金流向监控** (`utils/etf_flow_monitor.py`)：
- 12只国家队ETF实时监控
- 三级信号（50亿/10亿/2亿阈值）
- ETF→板块→个股票池三层映射
- 板块轮动资金汇总 + 自动交易计划生成

## 系统架构

```
第一层：应用入口
  ├── comprehensive_quant_system.py     v6.0 主控 (保持兼容)
  ├── comprehensive_quant_system_v7.py  ★ v7.0 优化版主控
  ├── build_plan_executor.py            ★ 建仓计划执行器
  ├── trading_execution_v7_20260706.py  ★ 7月6日集成执行
  ├── trading_workflow.py              交易日自动工作流
  └── main.py                          硅能智投智能投资代理

第二层：对冲引擎 (v7.0核心)
  ├── enhanced_futures_hedge.py         期货Delta对冲 (市场状态自适应)
  ├── protective_options_hedge.py       期权保护性对冲 (Put Ladder + Collar)
  ├── enhanced_delta_hedge.py           v6.0 Delta对冲 (保留)
  ├── derivatives_trading_module.py     Black-Scholes定价 + Greeks计算
  ├── volatility_hedge.py               波动率对冲
  └── tail_risk_hedge.py                尾部风险对冲

第三层：风控与执行
  ├── enhanced_risk_manager.py           ★ 三级风控 + 前瞻压力测试 + 增强蒙特卡洛
  ├── dynamic_capital_manager.py         动态资金管理
  ├── automated_execution_system.py     自动执行引擎
  └── automated_rebalance_system.py     自动再平衡

第四层：策略与优化
  ├── enhanced_quant_strategy_optimizer.py  五维因子+风险平价+ML增强
  ├── deployment_and_execution.py           部署管理
  └── build_plan_executor.py                建仓计划执行

第五层：AI与数据
  └── investment_agent/                 AI投资代理
       ├── core/                        核心引擎 (InvestmentAgent)
       ├── data/                        数据源 (WindDataProvider)
       ├── ai_integration/              AI引擎 (SiliconFlowClient)
       └── config/                      代理配置
```

## 环境要求

### 硬件
- CPU: 8 核心以上
- 内存: 16GB 以上
- 硬盘: 500GB 以上可用空间
- 网络: 稳定的宽带连接

### 软件
- **Python**: 3.10+ (推荐 3.12+，当前可用 3.14)
- **操作系统**: Windows 10+ / Ubuntu 22.04+ / macOS 13+

### 依赖安装

```bash
# v7.0 核心依赖 (纯Python, 无numpy/pandas也可运行)
# 系统已内置 math/random 实现，无需额外依赖即可运行对冲模拟

# 如需真实数据回测 (可选):
pip install numpy pandas pyyaml matplotlib scipy scikit-learn
```

### 验证安装

```bash
# v7.0 系统自检
python comprehensive_quant_system_v7.py
```

## 快速开始

### 1. 运行 v7.0 对冲模拟

```bash
# 完整对冲模拟 + 历史压力测试
python comprehensive_quant_system_v7.py

# 输出内容:
#   - 五层对冲多场景模拟 (牛市/震荡/高波动/熊市/崩盘)
#   - 历史压力场景验证 (2015-2024)
#   - 系统配置导出
```

### 2. 建仓计划执行 (7月6日起)

```bash
# 生成 7月6日交易指令单
python build_plan_executor.py --date 2026-07-06

# JSON 格式输出 (机器可读)
python build_plan_executor.py --date 2026-07-06 --json

# 查看建仓进度
python build_plan_executor.py --check-status

# 查看紧急响应协议（传入市场状态）
python -c "
from build_plan_executor import BuildPlanExecutor
executor = BuildPlanExecutor()
market = {'vix_proxy': 32, 'index_return_5d': -0.04, 'index_return_20d': -0.06,
          'margin_balance_change': -0.06, 'sector_health': {'high_end_manufacturing_20d': -0.05}}
protocol = executor.get_emergency_protocol(market)
print(f'紧急等级: {protocol[\"level_name\"]}, 建仓倍率: {protocol[\"day_capital_multiplier\"]:.0%}')
print(f'操作清单: {len(protocol[\"actions\"])}条')
"
```

### 3. 7月6日整合执行 (建仓 + v7.0对冲)

```bash
# 一键执行: 输出建仓订单 + v7.0对冲方案
python trading_execution_v7_20260706.py

# 指定日期
python trading_execution_v7_20260706.py --date 2026-07-06

# 仅输出对冲方案
python trading_execution_v7_20260706.py --hedge-only
```

### 4. 一次性系统分析 (v6.0兼容)

```bash
# 完整模式 - 运行全部子系统
python comprehensive_quant_system.py --mode full

# 独立模式 - 不使用AI引擎
python comprehensive_quant_system.py --mode standalone

# 轻量模式 - 仅核心策略
python comprehensive_quant_system.py --mode lightweight
```

## 500万建仓计划

### 建仓时间线

| 阶段 | 时间 | 投入资金 | 比例 | 策略重点 |
|------|------|---------|------|---------|
| 第一阶段 | 2026-07-06 起 | 175万 | 35% | 高端制造核心仓位 + 对冲保护启动 |
| 第二阶段 | 2026-07-20 起 | 150万 | 30% | 配置完善 + 月中波动加仓 |
| 第三阶段 | 2026-08-10 起 | 100万 | 20% | 防御补充 + 中报窗口 |
| 第四阶段 | 2026-09-01 起 | 75万 | 15% | 最终调整 + 偏差修正 |

### 7月6日关键执行

7月6日是建仓首日，需要同时完成：
1. 首批 13 只标的建仓 (~175万)
2. 启动 v7.0 对冲保护 (初始对冲 ~70万)
3. 剩余 ~255万 配置短融ETF + 准备后续对冲资金

详细建仓计划见 `500万建仓计划_20260706.md`

## 配置管理

### 配置文件

| 文件 | 版本 | 用途 |
|------|------|------|
| `config.py` | v7.0 | ★ 系统配置(含紧急响应参数) |
| `configs/comprehensive_config.yaml` | v6.0 | 主系统配置 |
| `configs/comprehensive_config_v7.yaml` | v7.0 | ★ 优化版配置 (含期权层) |
| `configs/settings.yaml` | v6.0 | 系统设置 |
| `configs/portfolio.yaml` | — | 投资组合配置 |

### v7.0 关键配置

```yaml
# configs/comprehensive_config_v7.yaml

version: "v7.0"
total_capital: 5000000

# 资金配置
capital_allocation:
  equity_long: 0.60          # 权益多头 300万
  hedge_total: 0.40          # 对冲总计 200万

# 业绩目标
performance_targets:
  annual_return: 0.085       # 年化收益 >= 8.5%
  max_drawdown: 0.15         # 最大回撤 < 15%
  sharpe_ratio: 1.5
  alpha: 0.04

# 对冲层参数
hedge_layers:
  layer1_futures:
    target_beta_map:
      bull: 0.30 / bear: -0.10 / crash: -0.15
  layer2_options:
    strategies: [put_ladder, put_spread_collar, tail_hedge]
    put_ladder_strikes: [0.90, 0.85, 0.80]
```

### 紧急响应配置（v2.0 新增）

```python
# config.py - HedgeConfig 和 RiskConfig

# 建仓阶段保护性看跌期权
build_put_budget_ratio: 0.03     # 建仓期Put预算(总资金3%)
build_put_strike_otm: 0.10       # Put行权价虚值幅度(10%)
build_put_duration_months: 3     # Put期限(月)

# 紧急响应VIX阈值
emergency_yellow_vix:    30.0    # 黄色预警
emergency_orange_vix:    35.0    # 橙色预警
emergency_red_vix:       40.0    # 红色预警
emergency_extreme_vix:   50.0    # 极端预警

# 紧急响应跌幅阈值
emergency_yellow_daily_drop:   0.03   # 单日>3%
emergency_orange_weekly_drop:  0.08   # 单周>8%
emergency_red_biweekly_drop:   0.15   # 双周>15%
emergency_extreme_daily_drop:  0.07   # 单日>7%

# 两融余额阈值
emergency_margin_yellow:  -0.05       # 5日降5%
emergency_margin_red:     -0.10       # 5日降10%

# 建仓资金倍率(应急)
emergency_yellow_capital_mult:   0.50  # 黄色: 减半
emergency_orange_capital_mult:   0.0   # 橙色: 暂停
emergency_red_capital_mult:      0.0   # 红色: 暂停
emergency_extreme_capital_mult:  0.0   # 极端: 全部停止

# 建仓阶段风控开关
build_plan_stress_test_enabled: True       # 启用前瞻压力测试
build_plan_market_monitor_enabled: True    # 启用实时市场监控
build_plan_stress_black_swan_max_loss: 0.50  # 黑天鹅最大可接受损失
build_plan_stress_bear_max_loss: 0.35        # 熊市最大可接受损失
```

## 交易日工作流

### 工作流阶段（建仓模式）

```
7:00  Phase 1: 盘前检查 (交易日验证/文件完整性)
7:02  Phase 2: 数据加载 (YAML配置/Wind数据/AI引擎)
7:05  Phase 3: 策略执行 (系统初始化)
7:08  Phase 4: 增强风险评估 ★ v2.0
            ├── 静态仓位限制检查
            ├── 实时市场状态检测 (VIX/跌幅/两融/行业)
            ├── 五级紧急响应协议判定
            ├── 前瞻性压力测试（黑天鹅/熊市）
            └── 输出: 紧急等级 + 建仓倍率 + 操作清单
7:12  Phase 5: 订单生成 (受风险评估控制)
            ├── 黄色: 建仓金额减半
            ├── 橙色/红色: 暂停建仓 + 生成停仓报告
            └── 极端: 全部停止 + 转入防御
7:15  Phase 6: 报告生成 (综合报告/工作流摘要)
7:20  Phase 7: 盘后处理 (执行记录/日志轮转)
```

### 建仓模式特有工作流

```
build_plan 模式:
  Phase 1 → Phase 2 → Phase 3(skip) → Phase 4(增强风控) → Phase 5(建仓订单) → Phase 6

  风险评估(Phase 4) 与 订单生成(Phase 5) 联动：
  - Phase 4 返回 emergency_level + day_capital_multiplier
  - Phase 5 读取 risk 结果：
    * emergency_level >= 2 → 阻断建仓，生成停仓通知
    * emergency_level == 1 → 资金倍率0.5，减半执行
    * emergency_level == 0 → 正常执行
```

### 自动调度

```bash
# Windows Task Scheduler
python trading_workflow.py --generate-task-xml > task_config.xml
schtasks /create /xml task_config.xml /tn "QuantSystem_v7_Daily"

# Linux cron
python trading_workflow.py --generate-cron
```

## 报告与日志

### 报告类型

| 报告 | 路径 | 说明 |
|------|------|------|
| 综合策略报告 | `comprehensive_quant_system_report.md` | 完整分析 |
| 交易日报告 | `reports/YYYYMMDD/comprehensive_report_YYYYMMDD.md` | 每日交易报告 |
| 建仓交易指令单 | `reports/trade_orders_YYYYMMDD.md` | ★ 每日可执行指令 |
| 工作流摘要 | `reports/YYYYMMDD/workflow_summary_YYYYMMDD.json` | 执行状态 |
| 执行历史 | `logs/execution_history.jsonl` | 历史记录 |

### 日志文件

```
logs/
  ├── comprehensive_quant_system_v7.log   # v7.0 系统日志 ★
  ├── comprehensive_quant_system.log      # v6.0 系统日志
  ├── trading_workflow_YYYYMMDD.log       # 工作流日志
  └── execution_history.jsonl             # 执行历史
```

## 故障排除

### import numpy 失败

v7.0 核心系统不依赖 numpy/pandas，纯 Python math 库即可运行。仅在需要真实数据回测时才需要这些依赖。

```bash
# 如需回测:
pip install numpy pandas
# 检查 Python 版本兼容性
python -c "import sys; print(sys.version)"
```

### 工作流跳过执行

```bash
python trading_workflow.py --check-today
# 如果是周末或节假日自动跳过，可用 --date 强制执行测试
```

### Windows 计划任务未触发

```bash
schtasks /query /tn "QuantSystem_v7_Daily" /v
schtasks /run /tn "QuantSystem_v7_Daily"
```

## 性能基准

| 指标 | v6.0 目标 | v7.0 目标 | 说明 |
|------|----------|----------|------|
| 年化收益 | >= 8% | >= 8.5% | 期权增收+套利增强 |
| 最大回撤 | <= 8% | < 15% | 放开约束换取更高收益 |
| 夏普比率 | >= 1.5 | >= 1.5 | 维持 |
| Alpha | >= 3% | >= 4% | 多策略Alpha叠加 |

## 系统命令速查

```bash
# ---- v7.0 核心 ----
python comprehensive_quant_system_v7.py                     # ★ v7.0 完整对冲模拟
python trading_execution_v7_20260706.py                     # ★ 7月6日整合执行

# ---- 建仓计划 ----
python build_plan_executor.py --date 2026-07-06             # 生成交易指令单
python build_plan_executor.py --date 2026-07-06 --json      # JSON格式
python build_plan_executor.py --check-status                # 查看建仓状态

# ---- v6.0 兼容 ----
python comprehensive_quant_system.py --mode full            # 完整分析
python trading_workflow.py                                  # 交易日工作流

# ---- 极端情景应对 (v2.0) ----
python trading_workflow.py --mode build_plan                # ★ 建仓模式(含增强风控)
python enhanced_risk_manager.py                             # ★ 压力测试引擎(含新场景)
python portfolio_return_projection.py                       # 组合收益情景预测

# ---- 系统验证 ----
python _system_verify.py                                    # 完整性验证
```

## 目录结构

```
ZCodeProject/
├── comprehensive_quant_system.py         v6.0 主控入口
├── comprehensive_quant_system_v7.py      ★ v7.0 优化版主控
├── build_plan_executor.py                ★ 建仓计划执行器
├── trading_execution_v7_20260706.py      ★ 7月6日集成执行
├── trading_workflow.py                   交易日自动工作流
├── main.py                               投资代理入口
├── 500万建仓计划_20260706.md             ★ 完整建仓计划
│
├── configs/                              配置文件目录
│   ├── comprehensive_config.yaml         v6.0 主配置
│   ├── comprehensive_config_v7.yaml      ★ v7.0 优化配置
│   ├── portfolio.yaml                    组合配置
│   ├── institutional_config.yaml         机构交易配置
│   └── settings.yaml                     系统设置
│
├── investment_agent/                     AI投资代理
├── utils/                                工具模块
├── logs/                                 日志文件
├── reports/                              分析报告
│   └── trade_orders_YYYYMMDD.md/json     ★ 每日交易指令单
├── portfolio_return_projection.md        ★ 组合收益情景预测
├── research_report_extreme_scenario_resilience.md  ★ 极端情景应对评估
├── 2026年交易计划.md                      年度交易计划
└── [策略模块].py                          各策略与子系统
```

## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| v7.1 | 2026-07-03 | 多维度整合 v3.0：实体经济指标、流动性风控、止损监控、五维因子、增强回测、ETF资金流向 |
| v7.0.1 | 2026-07-03 | 极端情景应对 v2.0：五级紧急响应协议、前瞻压力测试、实时市场监控、建仓资金动态调整、增强蒙特卡洛 |
| v7.0 | 2026-07-03 | 期货+期权双层对冲、保护性看跌阶梯、市场状态自适应、波动率套利、备兑增强 |
| v6.0 | 2026-07-03 | 五层对冲体系、AI投资代理、三级风控、机构级执行、交易日工作流 |

## 版本信息

- **版本**: v7.1 (多维度整合 v3.0)
- **发布日期**: 2026-07-03
- **运行环境**: Python 3.10+
- **当前测试环境**: Python 3.14
- **许可证**: 私有商业许可

---

*注意：本系统为专业的量化交易工具，不构成投资建议。所有策略执行前请充分理解风险，并在专业人士指导下使用。*

