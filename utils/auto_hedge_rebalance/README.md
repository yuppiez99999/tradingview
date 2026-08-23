<!-- 自动对冲再平衡进化系统 README -->
<!-- 文档版本: v1.0.0 | 最后更新: 2026-08-22 | 系统版本: v1.0 + 主系统 v8.6 -->

# 自动对冲再平衡进化系统

> **硬性目标**：年化收益 ≥ **8%** 且最大回撤 < **20%**（概率性约束，非确定性承诺）

## 目录

- [1. 功能概述](#1-功能概述)
- [2. 架构设计](#2-架构设计)
- [3. 核心组件说明](#3-核心组件说明)
- [4. 安装使用](#4-安装使用)
- [5. CLI 命令](#5-cli-命令)
- [6. UI 操作](#6-ui-操作)
- [7. 配置说明](#7-配置说明)
- [8. 测试说明](#8-测试说明)
- [信息来源](#信息来源)

---

## 1. 功能概述

本系统是量化交易策略系统 v8.6 的子模块，实现**自主根据市场状态选择 ETF/期权/期货对冲工具并执行自动再平衡**，约束年化收益 ≥ 8% 且最大回撤 < 20%。系统位于 `utils/auto_hedge_rebalance/` 包下，复用主系统对冲引擎 v5.9 与再平衡联动引擎，不修改现有接口契约。

### 四大核心能力

| 能力 | 说明 | 详细章节 |
|------|------|----------|
| 对冲工具自动选择 | 按市场状态(CALM/MILD/HIGH/TAIL) + 组合Beta决策表选择最优工具 | [§3 核心组件](#3-核心组件说明) |
| 自动再平衡执行 | 动态阈值触发再平衡，联动康波周期板块轮动 | [§3 核心组件](#3-核心组件说明) |
| 收益与回撤目标监控 | 滚动252日窗口计算实际指标，分级触发纠偏 | [§3 核心组件](#3-核心组件说明) |
| 风险控制与降级策略 | 紧急熔断 + 4层降级链 + 管理员解除 | [§3 核心组件](#3-核心组件说明) |

---

## 2. 架构设计

### 系统上下文

本系统居中协调，与主系统9个外部模块交互：

```text
                    ┌─────────────────────────┐
                    │  AutoHedgeRebalanceEngine │
                    │    (主协调器 - 10阶段)    │
                    └────────────┬────────────┘
           ┌─────────────────────┼─────────────────────┐
           ▼                     ▼                     ▼
   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
   │ HedgeEngine   │   │ Rebalance     │   │ Backtest      │
   │ v5.9 对冲引擎 │   │ Integrator    │   │ Engine v2.0   │
   └───────────────┘   └───────────────┘   └───────────────┘
           ▼                     ▼                     ▼
   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
   │ SignalFusion  │   │ Kondratiev    │   │ DataLayer     │
   │ 信号融合      │   │ 康波周期      │   │ 数据层        │
   └───────────────┘   └───────────────┘   └───────────────┘
```

### 十阶段EOD决策闭环

1. 加载持仓 → 2. 风险评估 → 3. 市场状态判定 → 4. 熔断检查 → 5. 工具选择
→ 6. 成本过滤 → 7. 再平衡检查 → 8. 联合优化 → 9. 目标监控 → 10. 策略纠偏+执行计划

**端到端数据流**：

`组合持仓 → 风险评估(波动率/回撤) → 市场状态(CALM/MILD/HIGH/TAIL) → 工具选择(期货/期权) → 联合计划 → 执行 → 净值更新 → 目标监控(年化/回撤)`

---

## 3. 核心组件说明

### AutoHedgeRebalanceEngine（主协调器）

- **职责**：编排完整10阶段EOD决策闭环，协调8个子组件
- **主方法**：`run_eod_decision(portfolio_volatility, portfolio_drawdown_60d) -> AutoHedgePlan`
- **依赖**：全部7个子组件 + HedgeRebalanceIntegrator
- **源文件**：`engine.py`

### HedgeToolSelector（对冲工具自动选择器）

- **职责**：按市场状态+组合Beta决策表选择对冲工具
- **主方法**：`select_tools(regime, risk, hedge_ratio, prices) -> ToolSelection`
- **依赖**：HedgeEngine, HedgeToolDataFetcher
- **源文件**：`tool_selector.py`

**决策表**：

| 市场状态 | 大盘Beta | 中小盘Beta | TAIL_EVENT |
|----------|----------|------------|------------|
| CALM | 无对冲 | 无对冲 | - |
| MILD | IF/IH期货 | IC/IM期货 | - |
| HIGH | IF/IH(25%) | IC/IM(25%) | - |
| TAIL | - | - | ETF期权+期货辅助 |

### TargetMonitor（目标达成监控器）

- **职责**：滚动252日窗口计算年化收益与最大回撤，分级触发纠偏
- **主方法**：`monitor(current_strategy_level) -> MonitorResult`
- **依赖**：HedgeRebalanceBacktest（预检）
- **源文件**：`target_monitor.py`

### StrategyStateMachine（策略等级状态机）

- **职责**：6档单向降级+冷却期升级，状态持久化
- **主方法**：`transition(current, action) -> TransitionResult`
- **依赖**：AuditLogger
- **源文件**：`strategy_state_machine.py`

**6档状态**：NORMAL → MILD_CORRECTION → MODERATE_CORRECTION → SEVERE_CORRECTION → CONSERVATIVE_DEFENSE → CIRCUIT_BREAKER

### CircuitBreaker（紧急熔断器）

- **职责**：盘中紧急保护，单日跌幅>5%或回撤>25%触发熔断
- **主方法**：`check(daily_drop, max_drawdown) -> BreakerStatus`
- **依赖**：AuditLogger
- **源文件**：`circuit_breaker.py`

### CostBenefitFilter（成本效益过滤器）

- **职责**：计算对冲成本与预期收益，按1.5倍阈值过滤
- **主方法**：`filter(selection, risk, prices) -> FilterResult`
- **依赖**：无
- **源文件**：`cost_benefit_filter.py`

### HedgeToolDataFetcher（对冲工具行情获取器）

- **职责**：三类工具行情获取，全降级链保证永不崩溃
- **主方法**：`fetch_futures(codes) -> dict` / `fetch_etf_options(codes) -> dict`
- **依赖**：HedgeEngine, Wind MCP, AKShare
- **源文件**：`data_fetcher.py`

### AuditLogger（决策审计日志）

- **职责**：SQLite持久化审计记录，WAL模式+索引，10万条查询<1秒
- **主方法**：`log_strategy_switch(event) -> str` / `query(start, end, type) -> list`
- **依赖**：无
- **源文件**：`audit_logger.py`

### 对冲工具池

| 工具类型 | 标的 | 适用状态 |
|----------|------|----------|
| 股指期货 | IF(沪深300), IC(中证500), IM(中证1000), IH(上证50) | MILD/HIGH |
| ETF期权 | 510300(沪深300ETF), 510050(上证50ETF), 000300 | TAIL_EVENT |
| 反向ETF | 按需配置 | 备用 |

---

## 4. 安装使用

### 前置依赖

- Python 3.8+（优先 3.8 → 3.14）
- 第三方库：`pyyaml`, `pandas`, `akshare`（可选）, `pytdx`（可选）
- 环境变量：`WIND_API_KEY`（Wind MCP认证，可选）

本系统是主系统子模块，**在主系统目录 `28-终极量化交易系统8.4/` 下运行**，非独立安装包。

### 数据源降级链

```text
Wind数据终端(P0) → Wind MCP(P1) → TDX通达信(P2) → AKShare(P3)
→ 新浪API(P4) → 本地缓存(P5) → 兜底预定义价格(P6)
```

任一数据源失败自动降级，全链失效时返回兜底价格并标记"全行情降级"。

### 首次运行验证

```bash
cd 28-终极量化交易系统8.4
python 量化策略系统_统一入口_v8.6.py --auto-hedge-report
```

预期输出：控制台显示滚动年化收益、最大回撤、策略等级等指标，生成 `config/portfolio_nav_history.json` 净值历史文件。

---

## 5. CLI 命令

### EOD决策

```bash
# 统一入口
python 量化策略系统_统一入口_v8.6.py --auto-hedge-rebalance \
    --volatility 0.18 --drawdown 0.05

# 模块直调
python -m cli.modes.auto_hedge_rebalance --auto-hedge-rebalance
```

**参数**：

| 参数 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `--volatility` | float | 0.18 | 组合年化波动率 |
| `--drawdown` | float | 0.0 | 60日最大回撤 |

**输出**：控制台显示工具类型、对冲比例、滚动年化收益、最大回撤、策略等级、降级标记。

### 盘中紧急再评估

```bash
python 量化策略系统_统一入口_v8.6.py --auto-hedge-intraday \
    --current-value 950 --previous-value 1000
```

**参数**：

| 参数 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `--current-value` | float | 1000.0 | 当前组合价值 |
| `--previous-value` | float | 1000.0 | 上一时刻组合价值 |

**输出**：动作类型（none/emergency_reassess/circuit_break）与描述。单日跌幅>5%触发紧急再评估。

### 监控报告

```bash
python 量化策略系统_统一入口_v8.6.py --auto-hedge-report
```

**输出**：滚动年化收益、最大回撤、收益偏离度、回撤余量、纠偏动作、样本不足标志。

---

## 6. UI 操作

### 启动方式

```bash
# 方式1: 直接启动监控页
python -m streamlit run ui/pages/15_🛡️_自动对冲再平衡.py

# 方式2: 通过主入口导航
python run_ui.py
```

### 页面功能

| 功能区域 | 说明 |
|----------|------|
| 目标达成监控 | 滚动年化收益、最大回撤、偏离度、回撤余量四指标 |
| 策略等级状态 | 6档状态机当前等级（颜色编码：绿/黄/橙/红/深红/黑） |
| 熔断状态告警 | 熔断活跃时红色弹窗 + 解除操作入口 |
| EOD决策触发 | 点击按钮执行完整10阶段决策闭环 |
| 盘中紧急检查 | 输入当前/上一时刻价值，5秒响应 |

### 策略切换审批流程

1. 系统自动触发策略切换时生成待审批事件
2. 风控管理员在UI页面查看待审批列表
3. 填写审批人姓名 + 通过/拒绝
4. 审批通过后策略等级切换生效

### 熔断解除流程

1. 单日跌幅>5%触发紧急再评估，回撤>25%触发熔断
2. 熔断后系统拒绝执行任何新交易
3. 风控管理员填写审批人姓名 + 备注，点击"解除熔断"

---

## 7. 配置说明

### 配置文件位置

| 文件 | 用途 |
|------|------|
| `config/auto_hedge_rebalance.yaml` | 主配置（全部阈值与参数） |
| `config/auto_hedge_rebalance_state.json` | 状态持久化（策略状态机+熔断器） |
| `config/portfolio_nav_history.json` | 组合净值历史轨迹 |

### 配置参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `target.annual_return` | 年化收益目标 | 0.08 |
| `target.max_drawdown` | 最大回撤约束 | 0.20 |
| `rolling_window.days` | 滚动窗口交易日 | 252 |
| `rolling_window.min_sample_days` | 最小样本日 | 30 |
| `cost_benefit.threshold` | 成本效益阈值 | 1.5 |
| `cost_benefit.roll_cost_annual` | 年化展期成本 | 0.025 |
| `cost_benefit.margin_opp_cost` | 保证金机会成本 | 0.020 |
| `correction.mild_deviation` | 温和纠偏偏离度 | 0.02 |
| `correction.moderate_deviation` | 中度纠偏偏离度 | 0.04 |
| `drawdown_action.warning_threshold` | 回撤预警阈值 | 0.15 |
| `drawdown_action.danger_threshold` | 回撤危险阈值 | 0.18 |
| `drawdown_action.breach_threshold` | 回撤突破阈值 | 0.20 |
| `circuit_breaker.daily_drop_trigger` | 单日跌幅触发 | 0.05 |
| `circuit_breaker.extreme_drawdown_trigger` | 极端回撤触发 | 0.25 |
| `cooldown.days` | 冷却期交易日 | 5 |

### 状态文件结构

`auto_hedge_rebalance_state.json` 含4个顶层字段：

- `strategy_state`：策略状态机当前状态（current_level / last_transition_time / cooldown_until / pending_switch_event_id / level_min_hold_days）
- `breaker_status`：熔断器状态（active / trigger_reason / trigger_time / emergency_action）
- `last_decision_time`：上次决策时间
- `version`：状态文件版本

---

## 8. 测试说明

### 测试目录结构

测试文件位于 `tests/test_auto_hedge_rebalance/`，命名约定 `test_<组件名>.py`：

| 测试文件 | 对应组件 | 覆盖率要求 |
|----------|----------|------------|
| `test_audit_logger.py` | AuditLogger | ≥90% |
| `test_data_fetcher.py` | HedgeToolDataFetcher | ≥85% |
| `test_cost_benefit_filter.py` | CostBenefitFilter | ≥95% |
| `test_tool_selector.py` | HedgeToolSelector | ≥90% |
| `test_strategy_state_machine.py` | StrategyStateMachine | ≥95% |
| `test_circuit_breaker.py` | CircuitBreaker | ≥95% |
| `test_target_monitor.py` | TargetMonitor | ≥85% |
| `test_engine.py` | AutoHedgeRebalanceEngine | ≥85% |
| `test_integration.py` | 集成测试 | - |

### 运行命令

```bash
# 运行全部测试
pytest tests/test_auto_hedge_rebalance/

# 运行单个组件测试
pytest tests/test_auto_hedge_rebalance/test_engine.py -v
```

### 测试约定

- **AAA模式**：Arrange-Act-Assert，每个测试用例遵循三段式
- **临时隔离**：使用临时数据库/状态文件避免污染生产数据
- **Mock数据源**：不依赖真实Wind/TDX连接，使用mock模拟数据层响应

---

## 信息来源

- 需求规格：`../../.codeartsdoer/specs/auto_hedge_rebalance/spec.md`
- 技术设计：`../../.codeartsdoer/specs/auto_hedge_rebalance/design.md`
- 任务清单：`../../.codeartsdoer/specs/auto_hedge_rebalance/tasks.md`
- 用户指南：`../../docs/auto_hedge_rebalance_user_guide.md`
- 代码包：`./` 下11个.py文件
- 主系统上下文：`../../AGENTS.md`