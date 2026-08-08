# 工业级量化系统工作计划 v9.2

> **生成时间**: 2026-08-06
> **系统版本**: v9.1 → v9.2 (工业级达标冲刺)
> **主目录**: `e:\各种PY程序\28-终极量化交易系统8.4`
> **前置文档**:
> - `docs/UPGRADE_PLAN_v9.0_工程地基修复_20260806.md` (已完成)
> - `docs/COMPLETION_REPORT_v9.0_工程地基修复_20260806.md` (已完成)
> - `docs/GAP_ASSESSMENT_v9.1_工业级达标计划_20260806.md` (差距评估)
> - `cairn/industrial-grade-anti-regression-framework.md` (防复发机制)
> - `research_report_industrial_grade_evaluation.md` (工业级成熟度评估)

---

## 摘要

v9.0 已完成工程地基修复（测试 0 errors / CI 脚本恢复 / 告警模块实现 / 陈旧版本清理）。
v9.1 已修复 G3 压力测试空持仓、悬挂引用、防复发工具假阳性。
本工作计划 v9.2 聚焦**剩余的工业级达标差距**，分四个阶段推进，目标在 **2026-08-22** 达到中期验收（可实盘标准），**2026-09-05** 全面验收。

---

## 一、当前差距状态总览

### 已修复 (不再列为差距)

| 编号 | 问题 | 修复日期 | 验证 |
|------|------|---------|------|
| E1 | CI 引用的 7 个脚本被归档 | 08-06 | 薄包装创建, import OK |
| E2 | 测试 collection 12 个 ERROR | 08-06 | 4279 tests collected, 0 errors |
| E3 | utils/notify 从未实现 | 08-06 | 三通道告警, import OK |
| E4 | system_config.json iFinD 残留 | 08-06 | secondary: tdx |
| E6 | 根目录临时文件堆积 | 08-06 | 26 个文件清理/归档 |
| E7 (G3) | 压力测试空持仓 | 08-06 | actual_pnl 非零 (-779780/-258441/...) |
| E8 | automated_execution_system 导入路径错误 | 08-06 | _HEDGE_AVAILABLE=True |
| E9 | check_dangling_refs.py 假阳性 | 08-06 | 误报清单修正, exit 0 |

### 剩余差距 (本计划修复目标)

| 编号 | 问题 | 工业级标准 | 当前状态 | 阶段 |
|------|------|-----------|---------|------|
| **G1** | 真实券商下单未接线 | QMT 真实下单, 成交回报驱动 | dry_run=true, QmtBrokerAPI 未接入主链路 | Phase 2 |
| **G2** | 再平衡执行断链 | 订单→撮合→成交→持仓更新闭环 | `generate_rebalance_orders` 只生成, 无 execute/fill | Phase 2 |
| **G4** | 成交回报与 PnL 脱节 | fills 是 PnL 单一事实源 | PnL 从行情估算, 非 fills 驱动 | Phase 2 |
| **G5** | 研究/生产未隔离 | 物理隔离, 移除 import research.* | portfolio_optimizer.py, auto_factor_factory.py 6 处 | Phase 3 |
| **G6** | mypy 681 错误 | CI mypy 阻断新增错误 | 存量错误过多 | Phase 3 |
| **G7** | 覆盖率无报告产物 | ≥80% + htmlcov/coverage.xml | 文档声称 65.20% 但无产物 | Phase 3 |
| **G8** | 告警未接入生产 | 风控触发→告警 5 秒触达 | utils/notify 已实现但 utils/risk/ 0 调用 | Phase 1 |
| **G11** | CVaR 非蒙特卡洛 | 蒙特卡洛 99% CVaR | 解析公式近似 | Phase 4 |
| **G13** | VIX 口径差异 | 数据契约一致 | VolRegime 报 7.24, daily_pnl 报 18.5 | Phase 1 |
| **G14** | 多源交叉校验弱 | 多源并行取一致 | 按优先级取单源 | Phase 4 |

---

## 二、四阶段工作计划

### Phase 1: 可信度修复 (08-07 ~ 08-08, 2 天)

**目标**: 修复"系统能跑但结果不可信"的沉默失败，确保所有告警通道真正可用。

#### P1.1 G8 告警接入生产 (0.5 天)

**问题**: `utils/notify.py` 已实现，但 `utils/risk/` 下 6 个文件（kill_switch_adapter / risk_bus / risk_event / risk_module_adapters / style_beta / __init__）**0 处调用 `send_alert`**。风控触发时不会告警任何人。

**修复**:
- `kill_switch_adapter.py`: 熔断触发时调用 `send_alert(level='critical', title='KILL_SWITCH 触发', message=...)`
- `risk_bus.py`: 风控事件发布时调用 `send_alert(level='warning', title='风险事件', message=...)`
- `risk_module_adapters.py`: 各风控模块超限时调用 `send_alert`
- 确保告警 fail-open（告警失败不阻断风控本身）

**验收**: 构造一个模拟熔断场景，确认钉钉/飞书/日志三通道都能收到告警。

#### P1.2 G13 VIX 口径核实与统一 (0.5 天)

**问题**: VolRegime 报 VIX=7.24，daily_pnl 报 VIX=18.5，2.5 倍差异。这不是"待核实"，是数据契约破裂。

**修复**:
- 定位两个模块各自从哪里读取 VIX（哪个文件、哪个字段、哪个 API）
- 确认是同一指标的两种口径（如年化 vs 月化、历史 vs 隐含）还是其中一个有 bug
- 统一到同一口径，在 `system_config.json` 增加 `vix_definition` 字段明确口径
- 增加 `assert_data_validity.py` 的 D7 断言：VolRegime 和 daily_pnl 的 VIX 值差异不超过 20%

**验收**: 两模块 VIX 值一致或差异在合理范围内，D7 断言通过。

#### P1.3 G12 小单执行算法保护 (0.5 天)

**问题**: `daily_workflow.py` 仅当 `shares>=5000 or notional>=200_000` 时才调用 TWAP/VWAP，小单直接市价执行。

**修复**:
- 所有订单都先经冲击成本估算（Almgren-Chriss 简化版）
- 冲击成本 > Alpha 预期 25% 的小单也走 TWAP 拆分
- 仅当冲击成本 < 阈值且 notional < 5 万的小单才允许市价

**验收**: 回测中所有订单都有冲击成本记录，无"裸市价"订单。

#### P1.4 覆盖率产物与 mypy 基线 (0.5 天)

**问题**: 文档声称覆盖率 65.20% 但无 `htmlcov/` 或 `coverage.xml` 产物；mypy 681 错误无基线。

**修复**:
- 运行 `pytest --cov=utils --cov=ms_strategy --cov-report=html --cov-report=xml` 生成产物
- 将 mypy 681 错误存为 `docs/mypy_baseline_v9.2.txt`，CI 改为 `--baseline` 模式（存量不阻断，新增阻断）
- `.coveragerc` 的 `fail_under` 从 60 调到当前实际值

**验收**: `htmlcov/index.html` 和 `coverage.xml` 存在；mypy baseline 文件存在；CI mypy job 能通过。

---

### Phase 2: 执行闭环补齐 (08-09 ~ 08-15, 1 周)

**目标**: 让系统从"生成订单但不执行"变成"订单→撮合→成交→持仓→PnL"完整闭环。**建仓期结束后执行。**

#### P2.1 G1 QMT 真实券商下单接线 (3 天)

**问题**: `ms_strategy/src/execution/qmt_broker.py` 已实现 `QmtBrokerAPI`（xtquant），但 `system_config.json` 的 `broker.enable=false`，主链路用 MockBroker。

**修复步骤**:
1. **环境准备**: 确认 QMT 客户端已安装、xtquant 可导入、`WIND_API_KEY` / QMT 账号配置就绪
2. **接线**: 在 `daily_trade_executor.py` 主链路中将 `MockBroker` 替换为 `QmtBrokerAPI`
3. **降级链**: QMT 不可用时降级到 MockBroker（保持 dry_run 兼容）
4. **灰度**: 先 dry_run=true 跑 3 天，确认订单生成逻辑无误；再 10% 资金灰度 3 天；最后全量
5. **成交回报**: QMT 的成交回报写入 `reports/fills/{date}.json`，作为 PnL 单一事实源

**验收**: 真实下单 1 笔（最小手数），成交回报回写 positions.json 和 fills JSON。

#### P2.2 G2 再平衡执行撮合 (2 天)

**问题**: `utils/execution/rebalance_execution_orders.py` 只有 `generate_rebalance_orders()`（生成订单列表），**没有 execute/fill/place_order 方法**。订单生成后无人执行。

**修复步骤**:
1. 新增 `RebalanceExecutor` 类，从 `generate_rebalance_orders` 输出读取订单
2. 通过 `QmtBrokerAPI` 或 `SimulatedBroker`（回测/模拟模式）撮合
3. 成交回报写入 `reports/rebalance_fills_{date}.json`
4. 更新 `positions.json` 的持仓数量和均价
5. 集成到 `daily_workflow.py` 的再平衡阶段

**验收**: `generate_rebalance_orders` → `RebalanceExecutor.execute` → positions.json 更新 → fills JSON 落盘，完整闭环。

#### P2.3 G4 成交回报驱动 PnL (2 天)

**问题**: PnL 从行情价格估算，非成交回报驱动。持仓和 PnL 可能对不上。

**修复步骤**:
1. 定义 `FillsStore` 接口：`reports/fills/{date}.json` 为唯一事实源
2. `daily_pnl.py` 改为从 `FillsStore` 读取成交记录计算已实现 PnL
3. 未实现 PnL 仍从行情估算，但已实现 PnL 必须由 fills 驱动
4. 增加 `assert_data_validity.py` 断言：`sum(fills.pnl) == daily_pnl.realized_pnl`

**验收**: 已实现 PnL = fills PnL 之和，无偏差。

---

### Phase 3: 环境隔离与 CI 达标 (08-16 ~ 08-22, 1 周)

**目标**: 研究/生产物理隔离，CI 全面通过，工程债务降至 GREEN。

#### P3.1 G5 移除生产对 research.* 的 import (3 天)

**问题**: `portfolio_optimizer.py` L460、`auto_factor_factory.py` L400/434/440/584 直接 `import research.*`，研究代码变更直接影响生产。

**修复步骤**:
1. 识别 6 处 import 的具体用途（哪些函数/类被引用）
2. 将被引用的研究代码提取为独立的生产模块（如 `utils/alpha/factor_discovery.py`）
3. research.* 保留为研究端的探索版本，生产端用提取后的稳定版本
4. `check_dangling_refs.py` 增加 `import research.*` 检测（生产目录禁止引用 research）

**验收**: `industrial_grade_check.py` 的 C3 判据从 WARN 变 PASS。

#### P3.2 G6 mypy 基线 + 新增阻断 (2 天)

**问题**: 681 错误存量，CI 无法通过。

**修复步骤**:
1. `mypy --baseline docs/mypy_baseline_v9.2.txt`（Phase 1 已生成）
2. CI mypy job 改为 `--baseline` 模式：存量不阻断，新增阻断
3. 逐步修复高优先级错误（执行/风控/数据路径优先）
4. 每周更新 baseline，目标 08-22 降至 500 以下

**验收**: CI mypy job 通过；baseline 错误数持续下降。

#### P3.3 G7 覆盖率提升 (2 天)

**问题**: 覆盖率 65.20% 低于工业级 80% 标准。

**修复步骤**:
1. 从 Phase 1 生成的 `coverage.xml` 识别未覆盖的关键路径
2. 优先补齐执行/风控/数据路径的测试
3. `.coveragerc` 的 `fail_under` 阶梯提升：65 → 70 → 75 → 80
4. CI 增加覆盖率趋势检查（`_check_coverage_trend.py` 已有）

**验收**: 覆盖率 ≥ 80%，CI 覆盖率 job 通过。

---

### Phase 4: 架构升级 (08-23 后, 中长期)

**目标**: 补齐工业级架构的高级特性，达到顶级对冲基金水准。

#### P4.1 G11 蒙特卡洛 CVaR (1 周)

**问题**: `wt_risk_control.py` 的 `calculate_cvar` 用解析公式近似，非路径模拟。

**修复**:
- 实现蒙特卡洛 2000 条路径的 99% CVaR
- 复用 `monte_carlo.py` 的路径生成逻辑
- 压力测试场景用真实持仓跑蒙特卡洛

#### P4.2 G14 多源交叉校验 (1 周)

**问题**: 数据接入按优先级取单源，非多源并行校验。

**修复**:
- 接入层改为多源并行获取
- 实现一致性校验算法（多数投票 + 偏差检测）
- 偏差 > 阈值时告警并降级到最可信源

#### P4.3 G9 Feature Store (2-3 周)

**问题**: 因子值每次重算，无持久化缓存。

**修复**:
- 实现 Feature Store（Parquet 列式存储 + 内存缓存）
- 因子计算结果落盘，按日期查询
- point-in-time 正确性保证

#### P4.4 G10 核心热路径 C++/Rust (长期)

**问题**: 全部纯 Python，无低延迟优化。

**修复**: 评估延迟瓶颈后决定是否启动。当前日频架构下 Python 够用，此项为远期储备。

---

## 三、里程碑与验收

### 里程碑 1: Phase 1 完成 (08-08)

**验收清单**:
- [ ] G8: kill_switch 触发时钉钉/飞书收到告警
- [ ] G13: VIX 口径统一, D7 断言通过
- [ ] G12: 所有订单有冲击成本记录
- [ ] G7: htmlcov/ 和 coverage.xml 产物存在
- [ ] G6: mypy baseline 文件存在, CI mypy 通过

### 里程碑 2: Phase 2 完成 (08-15)

**验收清单**:
- [ ] G1: QMT 真实下单 1 笔, 成交回报回写
- [ ] G2: 再平衡订单→撮合→positions 更新闭环
- [ ] G4: 已实现 PnL = fills PnL 之和

### 里程碑 3: Phase 3 完成 (08-22) — 中期验收

**验收清单**:
- [ ] G5: 生产目录 0 处 import research.* (C3 PASS)
- [ ] G6: mypy baseline < 500, CI 通过
- [ ] G7: 覆盖率 ≥ 80%
- [ ] `industrial_grade_check.py`: 9 判据全 PASS 或 ≤ 2 WARN
- [ ] `engineering_debt_gate.py`: GREEN 级

### 里程碑 4: Phase 4 完成 (09-05) — 全面验收

**验收清单**:
- [ ] G11: 蒙特卡洛 CVaR 2000 路径
- [ ] G14: 多源交叉校验
- [ ] G9: Feature Store 落盘
- [ ] 全部 15 项验收清单通过 = 工业级可实盘

---

## 四、防复发机制 (已落地, 持续运行)

本计划执行过程中，以下防复发机制持续守护：

| 机制 | 脚本 | 触发时机 | 守护内容 |
|------|------|---------|---------|
| #1 工业级判据 | `industrial_grade_check.py` | CI / 盘前 | 9 判据退化检测 |
| #2 悬挂引用 | `check_dangling_refs.py` | pre-commit | 删模块时下游依赖检查 |
| #3 工程债务 | `engineering_debt_gate.py` | CI | 债务超标冻结新功能 |
| #4 计划同步 | `sync_upgrade_status.py` | CI | 计划文档与 git log 一致性 |
| #5 沉默失败 | `assert_data_validity.py` | 盘前 | 7 项非零断言 + 告警 |

**commit message 约定**: `[P1.1] G8 告警接入 kill_switch` / `[P2.1] G1 QMT 接线灰度` / `[P3.1] G5 移除 research import`

---

## 五、风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| QMT 接线需真实账户环境 | Phase 2 延迟 | 先 dry_run 3 天, 灰度 10% 3 天, 全量 |
| 再平衡撮合改动持仓数据 | 持仓错误 | 先在 shadow account 跑, 对比 positions.json |
| 研究/生产隔离改动大 | 回归风险 | 分批迁移, 每批跑 e2e 测试 |
| mypy baseline 不降反升 | CI 阻断 | 每周审查, 新增错误必须当周修复 |

---

## 六、执行原则

1. **先还债再加功能**: Phase 1-3 完成前, U7/F1/V1/W5 等功能升级暂停
2. **每个修复必须验证**: 修复后立即跑 `industrial_grade_check.py` + `assert_data_validity.py` + 测试 collection
3. **commit 带标记**: `[P{phase}.{step}] G{编号} {描述}`, 便于 `sync_upgrade_status.py` 追踪
4. **告警 fail-open**: 任何告警失败不阻断交易/风控本身
5. **灰度优先**: 执行闭环改动先 dry_run → 10% 灰度 → 全量

---

## 七、当前状态快照 (2026-08-06)

```
测试 collection:     4279 tests, 0 errors     [PASS]
industrial_grade:    6 PASS, 3 WARN, 0 FAIL   [需修复 C1/C3/C8]
engineering_debt:     YELLOW (T4 环境隔离 FAIL)
assert_data_validity: 7/7 PASS                 [D1 已修复]
check_dangling_refs:  0 悬挂引用               [假阳性已修正]
覆盖率:              ~65% (无产物)              [待生成]
mypy:               681 errors (无基线)        [待基线]
告警:                utils/notify 已实现       [待接入生产]
```

**下一行动**: Phase 1.1 — 将 `send_alert` 接入 `kill_switch_adapter.py` 和 `risk_bus.py`。
