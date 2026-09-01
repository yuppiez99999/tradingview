# 自动对冲再平衡进化系统 — 用户操作指南 v1.0

## 功能概述

本系统实现自主根据市场状态选择 ETF/期权/期货对冲工具并执行自动再平衡，约束年化收益 ≥ 8% 且最大回撤 < 20%。

### 核心能力

1. **对冲工具自动选择**: 按市场状态(CALM/MILD/HIGH/TAIL) + 组合Beta决策表选择最优对冲工具
2. **自动再平衡执行**: 动态阈值触发再平衡，联动康波周期板块轮动
3. **收益与回撤目标监控**: 滚动252日窗口计算实际指标，分级触发纠偏
4. **风险控制与降级策略**: 紧急熔断(单日跌幅>5%/回撤>25%) + 4层降级链

## CLI 命令使用

### EOD决策

```bash
python -m cli.modes.auto_hedge_rebalance --auto-hedge-rebalance \
    --volatility 0.18 --drawdown 0.05
```

### 盘中紧急再评估

```bash
python -m cli.modes.auto_hedge_rebalance --auto-hedge-intraday \
    --current-value 950 --previous-value 1000
```

### 监控报告

```bash
python -m cli.modes.auto_hedge_rebalance --auto-hedge-report
```

### 统一入口

```bash
python 量化策略系统_统一入口_v8.6.py --auto-hedge-rebalance
python 量化策略系统_统一入口_v8.6.py --auto-hedge-intraday
python 量化策略系统_统一入口_v8.6.py --auto-hedge-report
```

## UI 监控页操作指南

启动 Streamlit UI:

```bash
python -m streamlit run ui/pages/15_🛡️_自动对冲再平衡.py
```

页面功能:
- **目标达成监控**: 滚动年化收益、最大回撤、偏离度、回撤余量
- **策略等级状态**: 6档状态机当前等级(颜色编码)
- **熔断状态告警**: 熔断活跃时红色弹窗 + 解除操作入口
- **EOD决策**: 点击按钮执行完整10阶段决策闭环
- **盘中紧急检查**: 输入当前/上一时刻价值，执行5秒响应检查

## 策略切换审批流程

1. 系统自动触发策略切换时生成待审批事件
2. 风控管理员在UI页面查看待审批列表
3. 填写审批人姓名 + 通过/拒绝
4. 审批通过后策略等级切换生效

## 熔断解除流程

1. 单日跌幅>5%触发紧急再评估，回撤>25%触发熔断
2. 熔断后系统拒绝执行任何新交易
3. 风控管理员在UI页面填写审批人姓名 + 备注
4. 点击"解除熔断"按钮，系统恢复正常

## 配置参数说明

配置文件: `config/auto_hedge_rebalance.yaml`

| 参数 | 说明 | 默认值 |
|------|------|--------|
| target.annual_return | 年化收益目标 | 0.08 |
| target.max_drawdown | 最大回撤约束 | 0.20 |
| rolling_window.days | 滚动窗口交易日 | 252 |
| cost_benefit.threshold | 成本效益阈值 | 1.5 |
| correction.mild_deviation | 温和纠偏偏离度 | 0.02 |
| correction.moderate_deviation | 中度纠偏偏离度 | 0.04 |
| drawdown_action.warning_threshold | 回撤预警阈值 | 0.15 |
| drawdown_action.danger_threshold | 回撤危险阈值 | 0.18 |
| drawdown_action.breach_threshold | 回撤突破阈值 | 0.20 |
| circuit_breaker.daily_drop_trigger | 单日跌幅触发 | 0.05 |
| circuit_breaker.extreme_drawdown_trigger | 极端回撤触发 | 0.25 |
| cooldown.days | 冷却期交易日 | 5 |

## 常见问题 FAQ

**Q: 系统如何选择对冲工具？**
A: 按市场状态+组合Beta决策表选择:
- CALM → 无对冲
- MILD + 中小盘 → IC/IM期货
- MILD + 大盘 → IF/IH期货
- HIGH → 同MILD但比例25%
- TAIL_EVENT → ETF期权保护性看跌 + 期货辅助

**Q: 什么情况下触发熔断？**
A: 单日跌幅>5%触发紧急再评估(强制对冲至40%)，回撤>25%触发熔断(清仓高波动至50%)。熔断需管理员手动解除。

**Q: 策略等级如何升级？**
A: 升级需同时满足: (1)冷却期≥5交易日 (2)当前等级最小持续日(温和5/中度10/重度20/保守20日)。熔断等级只能由管理员解除。

**Q: 数据源全链失效怎么办？**
A: 系统自动降级至兜底预定义价格，标记"全行情降级"，保证永不崩溃。
