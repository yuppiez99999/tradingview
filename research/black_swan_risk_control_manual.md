# 黑天鹅风控手册（可执行版）

> 目标：黑天鹅/极端市场下，500 万账户最多亏损不超过 30%，剩余资金不低于 350 万。
> 适用系统：`e:\各种PY程序\28-终极量化交易系统7.1`
> 关联文件：`config.py`、`black_swan_optimizer.py`、`tail_risk_hedge.py`、`comprehensive_quant_system_v7.py`、`generate_500w_build_plan.py`

---

## 1. 核心硬约束（已写入 config.py）

| 参数 | 当前值 | 含义 |
|:-----|:------|:-----|
| `black_swan_capital_floor_ratio` | 0.70 | 黑天鹅下最低保留资金比例 |
| `emergency_equity_cap` | 0.45 | 极端市场权益仓位上限 |
| `min_gold_bond_ratio` | 0.15 | 黄金+国债最低配置比例 |
| `tail_protection_max_drawdown` | 0.30 | 尾部保护最大回撤目标 |
| `drawdown_breach_warning` | -0.10 | 回撤 10% 预警 |
| `drawdown_breach_emergency` | -0.15 | 回撤 15% 全面风控 |
| `drawdown_breach_extreme` | -0.20 | 回撤 20% 强制降仓 |

---

## 2. 组合 baseline（已写入 generate_500w_build_plan.py）

| 板块 | 权重 | 金额 | 作用 |
|:-----|:-----|-----:|:-----|
| 高端制造 | 50% | 250 万 | 增长引擎 |
| 防御 | 25% | 125 万 | 安全垫 |
| 资源 | 20% | 100 万 | 通胀/地缘对冲 |
| 顺周期 | 5% | 25 万 | 周期弹性 |
| 对冲资金 | 40% | 200 万 | 极端保护 |

**关键变化**：高端制造从 63.7% 降至 50%，防御+资源从 29.4% 升至 45%。

---

## 3. 五层对冲 baseline（已写入 comprehensive_quant_system_v7.py）

| 层级 | 工具 | 资金 | 占总资金 | 黑天鹅行为 |
|:-----|:-----|-----:|:--------|:-----------|
| Layer 1 | 股指期货 | 75 万 | 15% | 对冲比率拉至 54.7%+ |
| Layer 2 | 看跌期权 | 75 万 | 15% | 三层深度 OTM Put |
| Layer 3 | 波动率策略 | 30 万 | 6% | Vega 主动管理 |
| Layer 4 | 绝对收益 | 25 万 | 5% | 与权益低相关 |
| Layer 5 | 备兑开仓 | 20 万 | 4% | 权利金增收 |

---

## 4. 执行流程：回撤熔断触发 → 自动减仓 → 期权加厚 → 期货加仓 → 资金底线监控

### 4.1 触发源

系统通过 `IntradayCircuitBreaker.evaluate()` 持续监控两个维度：

1. **日内熔断**：单日跌幅、VIX、板块异动、跌停数量
2. **回撤熔断**：组合净值相对 `high_water_mark` 的回撤幅度

代码入口：
```python
from black_swan_optimizer import IntradayCircuitBreaker

cb = IntradayCircuitBreaker(total_capital=5_000_000)
alert = cb.evaluate(
    current_price=...,
    vix_level=...,
    sector_drops={...},
    limit_down_count=...,
    portfolio_value=current_portfolio_value  # 新增参数，用于回撤熔断
)
```

### 4.2 三级响应动作

| 触发条件 | 系统级别 | 自动动作 | 目标权益暴露 | 目标金额 |
|:---------|:--------|:---------|:------------|:--------|
| 回撤 ≤ -10% | LEVEL_2 | 预警并降权益至 45% | 45% | 225 万 |
| 回撤 ≤ -15% | LEVEL_3 | 紧急减仓至 30%，期货对冲至 70% | 30% | 150 万 |
| 回撤 ≤ -20% | LEVEL_4 | 强制降至 20% 权益，最大化对冲 | 20% | 100 万 |

**执行逻辑**（已写入 `black_swan_optimizer.py`）：
```python
# 回撤熔断检查
if drawdown <= -0.20:
    new_level = CircuitBreakerLevel.LEVEL_4
    trigger_reason = "回撤熔断(-20%)，强制减仓至20%权益"
elif drawdown <= -0.15:
    new_level = CircuitBreakerLevel.LEVEL_3
    trigger_reason = "回撤熔断(-15%)，紧急减仓至30%权益"
elif drawdown <= -0.10:
    new_level = CircuitBreakerLevel.LEVEL_2
    trigger_reason = "回撤熔断(-10%)，预警降权益至45%"
```

### 4.3 期权加厚：三层深度 OTM Put

当回撤熔断触发时，`tail_risk_hedge.py` 自动执行三层保护：

| 保护层 | 行权价 | 资金占比 | 触发条件 |
|:-------|:-------|:--------|:---------|
| Layer 1 轻度 | 85% OTM | 40% 期权资本 | 日常震荡 |
| Layer 2 中度 | 80% OTM | 35% 期权资本 | VIX > 30 或 单日跌 > 3% |
| Layer 3 深度 | 75% OTM | 25% 期权资本 | 双周跌 > 10% 或 crisis 状态 |

```python
# tail_risk_hedge.py 核心逻辑
if vix_level > 30 or daily_drop < -0.03:
    protection_layers.append({
        'name': '中度保护',
        'moneyness': 0.80,
        'capital_ratio': 0.35
    })
if weekly_drop < -0.10 or self.market_regime == 'crisis':
    protection_layers.append({
        'name': '深度保护',
        'moneyness': 0.75,
        'capital_ratio': 0.25
    })
```

### 4.4 期货加仓：Delta 对冲动态放大

熔断级别映射期货对冲目标：

| 熔断级别 | 期货对冲目标 | 对冲比率 |
|:---------|:------------|:--------|
| NORMAL | 0% | 0% |
| LEVEL_1 | 20% 减仓 | 30% |
| LEVEL_2 | 20% 减仓 | 50% |
| LEVEL_3 | 40% 减仓 | 70% |
| LEVEL_4 | 60%+ 减仓 | 最大化 |

期货对冲资本已上调至 75 万（15% 总资金），极端时可覆盖更多名义空头。

### 4.5 资金底线监控

每日/实时监控以下硬指标：

| 监控项 | 公式 | 阈值 | 动作 |
|:-------|:-----|:-----|:-----|
| 组合回撤 | (现值 - 高点) / 高点 | -10%/-15%/-20% | 触发熔断 |
| 权益暴露 | 权益市值 / 总资产 | >45%/>30%/>20% | 强制降仓 |
| 期权保护层 | 已购 Put 数量 / 目标数量 | <100% | 自动补仓 |
| 期货对冲比率 | 期货名义空头 / 组合Beta | <50%/<70% | 加仓期货 |
| 现金储备 | 现金 / 总资产 | <15% | 停止新增对冲 |

---

## 5. 单文件执行清单

### 5.1 每日盘前（07:00）

```bash
# 1. 加载最新配置
python -c "import config; print(config.Config()._config.risk_config.drawdown_breach_warning)"

# 2. 检查组合权重是否符合新 baseline
python generate_500w_build_plan.py

# 3. 初始化对冲系统
python -c "from comprehensive_quant_system_v7 import ComprehensiveQuantSystemV7; print('OK')"
```

### 5.2 交易时段（每 30 秒）

```python
from black_swan_optimizer import IntradayCircuitBreaker
from tail_risk_hedge import TailRiskHedge

cb = IntradayCircuitBreaker(total_capital=5_000_000)
tail = TailRiskHedge(capital=750_000)

alert = cb.evaluate(
    current_price=index_level,
    vix_level=vix,
    portfolio_value=current_nav
)

if alert.level.value >= 2:
    # 触发减仓 + 期货加仓
    reduce_equity_to(target=alert.level)
    increase_futures_hedge(to=alert.level)

if alert.level.value >= 3:
    # 期权加厚
    trades = tail.execute_protection_strategy(protection_needed, market_data)
    submit_trades(trades)
```

### 5.3 收盘后

```bash
# 1. 生成当日风控报告
python risk_monitor_system.py --report

# 2. 更新 high_water_mark
python -c "from black_swan_optimizer import IntradayCircuitBreaker; cb=IntradayCircuitBreaker(); print(cb.high_water_mark)"
```

---

## 6. 压力测试标准

| 极端事件 | 无对冲回撤 | 目标回撤 | 是否达标 |
|:---------|:----------|:--------|:--------|
| 2015 股灾 | -33.0% | ≤ -19.6% | FAIL（需继续优化） |
| 2018 熊市 | -25.0% | ≤ -13.5% | PASS |
| 2020 疫情 | -14.0% | ≤ -8.3% | PASS |
| 2022 下跌 | -22.0% | ≤ -11.8% | PASS |
| 黑天鹅自定义 | -64.9% | ≤ -30.0% | 待实测 |

---

## 7. 失效边界与应急预案

| 失效情形 | 当前状态 | 应急预案 |
|:---------|:---------|:---------|
| 连续跌停，期权无法成交 | 有 `OptionLiquidityAdjuster` | 切换为国债期货 + 现金 |
| 所有资产同跌 | 黄金+国债 20% | 提高至 25%+ |
| 政策突变跳空 | VIX 联动有 lag | 增加事件型期权保护 |
| 保证金不足 | 有监控但未自动降仓 | 手动触发 LEVEL_4 |

---

## 8. 关键代码索引

| 功能 | 文件 | 行号/类 |
|:-----|:-----|:--------|
| 回撤熔断阈值 | `config.py` | `RiskConfig` |
| 回撤熔断引擎 | `black_swan_optimizer.py` | `IntradayCircuitBreaker.evaluate()` |
| 三层期权保护 | `tail_risk_hedge.py` | `_execute_option_protection()` |
| 对冲资金分配 | `comprehensive_quant_system_v7.py` | `__init__` 中 Layer 1-5 |
| 组合 baseline | `generate_500w_build_plan.py` | `TARGET_PORTFOLIO` |

---

## 9. 验证命令

```bash
# 验证配置加载
cd "e:\各种PY程序\28-终极量化交易系统7.1"
py -c "import config; c=config.Config(); print('floor_ratio=', c._config.risk_config.black_swan_capital_floor_ratio)"

# 验证组合权重
py -c "import generate_500w_build_plan as p; total=sum(v['weight'] for v in p.TARGET_PORTFOLIO.values()); print('weight_sum=', total)"

# 验证熔断阈值
py -c "from black_swan_optimizer import IntradayCircuitBreaker; b=IntradayCircuitBreaker(); print(b.thresholds['drawdown_warning'], b.thresholds['drawdown_emergency'], b.thresholds['drawdown_extreme'])"

# 验证对冲分配
py -c "from comprehensive_quant_system_v7 import ComprehensiveQuantSystemV7; m=ComprehensiveQuantSystemV7(5_000_000); print(m.futures_hedge.allocated_capital, m.options_hedge.allocated_capital)"
```

---

## 10. 使用说明

1. **本手册为可执行手册**，所有触发条件、阈值、动作均已有代码实现。
2. 如需调整阈值，直接修改 `config.py` 中 `RiskConfig` 对应字段。
3. 如需调整组合权重，直接修改 `generate_500w_build_plan.py` 中 `TARGET_PORTFOLIO`。
4. 如需调整对冲层分配，直接修改 `comprehensive_quant_system_v7.py` 中 `__init__` 的 Layer 1-5 资本分配。
5. 如需调整期权层数，直接修改 `tail_risk_hedge.py` 中 `_execute_option_protection` 的 `protection_layers`。

> 修改后请运行第 9 节验证命令，确保配置一致性。
