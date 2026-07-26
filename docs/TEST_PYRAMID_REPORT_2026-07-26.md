# 测试金字塔体系建立报告 (v8.6.7)

> **文档编号**：TEST_PYRAMID_REPORT_2026-07-26
> **建立日期**：2026-07-26
> **版本**：v8.6.7
> **方法论**：从"被动审计"转向"主动防御"
> **测试用例总数**：120 个（全部通过）
> **运行时间**：55 秒
> **覆盖率**：65.20%

---

## 一、背景与动机

### 1.1 问题陈述 — "每次都有 bug"的恶性循环

前两轮 CRO 审计（v8.6.1 / v8.6.5）虽然每次都发现并修复了一批 P0/P1 级 bug，但出现了一个令人担忧的模式：

| 审计轮次 | 发现 P0 数 | 发现 P1 数 | 修复后状态 | 下次审计结果 |
|---------|-----------|-----------|-----------|-------------|
| v8.6.1（第一轮） | 3 | 11 | 全部修复 | 仍然发现 3 个新 P0 |
| v8.6.5（第二轮） | 3 | 6 | 全部修复 | 待下次审计验证 |

**根本原因**：每次审计修复后，**没有回归测试保护已修复的 bug**，导致：
1. 修复 A 时可能引入 B（无测试发现）
2. 重构代码时旧 bug 复活（无测试拦截）
3. 同一类 bug 在不同模块重复出现（无系统性防护）

### 1.2 解决方案 — 测试金字塔

建立三层测试金字塔，将"被动审计发现 bug"转变为"主动测试守护已修复 bug"：

```
                  /\
                 /  \        E2E 层 (8 个, 6.7%)
                /----\       真实生产数据 + 真实文件 IO + 跨模块协作
               /      \      
              /--------\     集成层 (26 个, 21.7%)
             /          \    多模块协作 + Mock 外部依赖
            /____________\   单元层 (86 个, 71.7%)
                             单模块 + 全 Mock + <1s
```

### 1.3 与审计的关系

| 模式 | 优点 | 缺点 | 本次定位 |
|------|------|------|---------|
| **被动审计** | 发现新 bug 视角广 | 修复后无保护，易回归 | v8.6.1 / v8.6.5 已完成两轮 |
| **主动防御**（测试金字塔） | 已修复 bug 永不回归 | 无法发现新 bug | **v8.6.7 本次建立** |

**核心结论**：测试金字塔**不是替代审计，而是固化审计成果**。每次审计发现新 bug → 修复 → 写回归测试 → 下次审计只关注新领域。

---

## 二、5 条关键链路识别

通过分析 v8.6.1 / v8.6.5 两轮审计修复的 bug，识别出系统的 **5 条关键链路**，这些链路是 bug 高发区，也是测试金字塔的重点守护对象：

### 链路 1：EOD 七 Guard 链

```
pnl_report.json → RiskGuardIntegrator.run_all_guards() → 7 Guard 顺序执行 → trade_plan.json 修改
```

**为何关键**：EOD 流程是日常风控的核心入口，任一 Guard 崩溃都会导致风控失效。v8.6.5 的 P0-D 就发生在这里（KillSwitch 检查完全失效）。

**守护的 bug**：
- P0-D：margin_used 为 None 时崩溃
- L3 优先级被 L2 覆盖（v8.6.5 遗留问题，v8.6.7 修复）
- 单 Guard 崩溃中断整个链路

### 链路 2：KillSwitch 三级熔断协议

```
margin_usage → check_margin_status() → L1/L2/L3 响应 → 过滤 BUY / 清空订单 / 强平
```

**为何关键**：KillSwitch 是黑天鹅事件的最后一道防线，L2/L3 误判会导致过度反应或反应不足。

**守护的 bug**：
- P0-D：None/None 崩溃
- P1-G：broker_callback 未注册
- BUG#4：L2 被当作 L3 处理（清空所有订单而非只过滤 BUY）

### 链路 3：报告数据结构兼容

```
daily_pnl_report_*.json (3 种格式) → _extract_positions / _extract_summary → guard_kill_switch
```

**为何关键**：系统存在 3 种不同的 pnl_report 格式（完整带横杠 / 简化无横杠 / 字段缺失），任一格式解析失败都会导致后续 Guard 失效。

**守护的 bug**：
- 字段值为 None（vs 不存在）的语义差异
- 三种格式（完整/简化/字段缺失）的兼容性

### 链路 4：fail-closed 保守保护

```
外部数据源不可用 → 默认行为选择 → L2 (禁开仓) vs L3 (全平)
```

**为何关键**：黑天鹅事件往往伴随数据源失效（akshare 不可用 / 网络中断），此时系统的默认行为决定了是"过度反应"还是"反应不足"。

**守护的 bug**：
- BUG#1：FAIL_CLOSED_PCT=-0.04 误触发 L3（应为 L2）
- BUG#1b：FAIL_CLOSED_PCT=-0.08 误触发 L3（应为 L2）

### 链路 5：对冲执行 + 认沽 + 去重

```
positions.json → HedgeExecutionEngine → 期货订单 + Put 期权订单 → _deduplicate_put_orders
```

**为何关键**：对冲订单生成涉及遍历 `hedge_positions` 字典，该字典包含字符串字段（description/hedge_mode），容易触发类型错误。

**守护的 bug**：
- P0-E：`hedge_pos.get(...)` 抛 `'str' object has no attribute 'get'`
- PUT 订单重复生成（对冲引擎 + 认沽引擎对同一底层生成两次）

---

## 三、测试金字塔三层结构详解

### 3.1 单元测试层（86 个，71.7%）

**设计原则**：
- 全 Mock（不依赖外部数据源、文件系统、其他模块）
- 单个测试 < 1 秒
- 每个测试只验证一个行为
- 每个已修复 bug 对应一个回归测试，函数名含 bug 编号

**测试文件清单**：

| 文件 | 测试数 | 守护模块 | 守护的 bug |
|------|--------|---------|-----------|
| `test_kill_switch_unit.py` | 15 | `utils/kill_switch.py` | P0-D, P1-G |
| `test_hedge_execution_engine_unit.py` | 13 | `utils/hedge_execution_engine.py` | P0-E |
| `test_market_circuit_breaker_unit.py` | 14 | `utils/market_circuit_breaker.py` | BUG#1b |
| `test_overnight_gap_monitor_unit.py` | 20 | `utils/overnight_gap_monitor.py` | BUG#1 |
| `test_pnl_report_compat_unit.py` | 11 | `utils/risk_guard_integrator.py` | 三种报告格式兼容 |
| `test_risk_guard_integrator_unit.py` | 13 | `utils/risk_guard_integrator.py` | BUG#4 |
| **小计** | **86** | | |

**示例测试用例**（P0-D 回归）：

```python
@pytest.mark.unit
@pytest.mark.p0
@pytest.mark.bug("P0-D")
def test_p0d_production_none_margin_returns_l3_fail_closed(
    self, production_env, tmp_kill_switch_log
):
    """P0-D 回归: 生产环境 margin_used=None 必须进入 fail-closed L3"""
    ks = KillSwitch()
    result = ks.check_margin_status(margin_usage=None)
    assert result["level"] == 3, "生产环境数据不可用必须 fail-closed 到 L3"
    assert result["can_trade"] is False
    assert "FAIL_CLOSED" in result["action"]
```

### 3.2 集成测试层（26 个，21.7%）

**设计原则**：
- 多模块协作（保留模块间真实调用）
- Mock 外部数据源（akshare / astock_realtime / ExternalDataManager）
- 文件 IO 重定向到 tmp_path（保护真实生产文件）
- 验证模块间状态传递

**测试文件清单**：

| 文件 | 测试数 | 守护链路 | 关键场景 |
|------|--------|---------|---------|
| `test_eod_guard_chain_integration.py` | 3 | 链路 1 | 7 Guard 链顺序执行 + L3 优先级覆盖 L2 |
| `test_kill_switch_protocol_integration.py` | 5 | 链路 2 | L1/L2/L3 端到端响应 + broker_callback 注册 |
| `test_fail_closed_integration.py` | 6 | 链路 4 | 数据源不可用 → L2（非 L3）|
| `test_report_compat_integration.py` | 7 | 链路 3 | 真实报告数据流 + 字段缺失鲁棒性 |
| `test_hedge_dedup_integration.py` | 5 | 链路 5 | PUT 订单去重 + 期货不受影响 |
| **小计** | **26** | | |

**示例测试用例**（L3 优先级覆盖 L2）：

```python
@pytest.mark.integration
def test_l3_priority_overrides_l2_in_chain(
    self, isolated_integrator, sample_pnl_report_full,
    sample_trade_plan, monkeypatch
):
    """L3 (后触发) 应覆盖 L2 (先触发) 的 circuit_level"""
    # Mock KillSwitch 返回 L2, 大盘熔断返回 L3
    # 期望: 最终 circuit_level=CRITICAL (L3 优先)
    plan = integrator.run_all_guards(next_trade_date="2026-07-22")
    assert plan["market_state"]["circuit_level"] == "CRITICAL"
    assert plan["execution_plan"]["morning_orders"] == []
```

### 3.3 E2E 测试层（8 个，6.7%）

**设计原则**：
- 使用真实生产数据（`v8.3_institutional/reports/daily_pnl_report_2026-07-21.json`，37KB，26 标的完整格式）
- 不 mock 内部模块（与集成测试的关键区别）
- 仅 mock 必不可少的外部数据源（akshare / astock_realtime）
- 文件 IO 重定向到 tmp_path
- 验证端到端流程不崩溃

**测试用例清单**：

| # | 测试用例 | 验证目标 |
|---|---------|---------|
| 1 | `test_e2e_full_chain_does_not_crash_with_real_data` | 真实报告跑通完整 7 Guard 链不崩溃 |
| 2 | `test_e2e_real_pnl_report_positions_extracted_correctly` | 真实 26 标的全部成功提取 |
| 3 | `test_e2e_trade_plan_written_to_disk` | `_save_trade_plan` 真实写盘 |
| 4 | `test_e2e_guard_log_written_to_disk` | 7 个 Guard 标记 `[1/7]`~`[7/7]` 全部出现 |
| 5 | `test_e2e_kill_switch_level_reflects_real_margin` | P0-D 回归：level 反映真实 80.4% 保证金 |
| 6 | `test_e2e_hedge_execution_with_real_positions` | P0-E 回归：遍历真实 positions.json 不崩溃 |
| 7 | `test_e2e_seven_guards_all_executed` | 日志中 `[1/7]`~`[7/7]` + `[去重]` + `次日计划已更新` |
| 8 | `test_e2e_multi_day_reports_all_pass` | 多日回归（最近 3 份真实报告全部跑通） |

**为何使用真实生产数据**：
- 合成数据可能遗漏真实数据的边界情况
- 真实报告的 26 标的 + 完整字段结构是黄金样本
- 防止单日报告偶然通过的偶然性（多日回归测试）

---

## 四、测试基础设施

### 4.1 pytest 配置

**`pytest.ini`**：

```ini
[pytest]
testpaths = tests/unit tests/integration tests/e2e
markers =
    unit: 单元测试 (全 mock, <1s)
    integration: 集成测试 (多模块协作)
    e2e: 端到端测试 (真实历史数据)
    regression: 回归测试 (对应已修复的 bug 编号)
    p0: P0 致命级 bug 回归
    p1: P1 风险缺口级 bug 回归
    bug(id): 关联 bug 编号, 如 @pytest.mark.bug('P0-E')
addopts = --strict-markers -ra
```

### 4.2 覆盖率配置

**`.coveragerc`**：

```ini
[run]
source = utils
omit =
    */tests/*
    */__pycache__/*
    */research/*

[report]
fail_under = 40
show_missing = True
```

### 4.3 三层 conftest.py 设计

```
tests/
├── conftest.py                    # 顶层共享 fixture (跨层复用)
├── unit/conftest.py               # 单元测试专用 fixture
├── integration/conftest.py        # 集成测试专用 fixture
└── e2e/conftest.py                # E2E 黄金数据加载
```

**顶层 conftest.py 提供的共享 fixture**：
- `tmp_kill_switch_log`：隔离 kill_switch_events.jsonl 写入
- `clean_env`：清理 KillSwitch / Guard 相关环境变量
- `production_env`：设置 `TRADING_ENV=production` 触发 fail-closed
- `sample_pnl_report_full`：完整格式 pnl_report（3 标的）
- `sample_pnl_report_broken_p0d`：P0-D bug 重现样本（margin_used=None）
- `broken_hedge_positions_p0e`：P0-E bug 重现样本（字符串字段）
- `sample_trade_plan`：标准测试交易计划（含 BUY + SELL 订单）
- `real_pnl_report`：真实生产报告（黄金数据源）

---

## 五、关键模块测试覆盖率

### 5.1 覆盖率统计

| 模块 | 行数 | 覆盖率 | 关键路径覆盖情况 |
|------|------|--------|-----------------|
| `utils/hedge_execution_engine.py` | 174 | **78.26%** | P0-E 字符串字段过滤 + IF 期货订单 + Put 保护订单 |
| `utils/market_circuit_breaker.py` | 109 | **73.83%** | 大盘熔断阈值 + apply_to_plan L2/L3 区分 |
| `utils/overnight_gap_monitor.py` | 163 | **70.14%** | S&P500 数据获取 + fail-closed 触发 |
| `utils/risk_guard_integrator.py` | 686 | **62.19%** | guard_kill_switch + run_all_guards 七 Guard 链 |
| `utils/kill_switch.py` | 197 | **54.51%** | check_margin_status + _estimate_margin_from_positions |
| **总计** | **1329** | **65.20%** | 超过 40% 最低要求 |

### 5.2 未覆盖路径说明

**`kill_switch.py` (54.51%)**：
- L3 强平执行路径（`execute_kill_switch` 中的变现 ETF + 期权平仓）未覆盖
- 原因：需要真实券商 API 接入，单元测试无法模拟
- 解决方案：通过 `test_p1g_execute_without_callback_raises_runtime_error` 验证 callback 注册机制，间接守护

**`risk_guard_integrator.py` (62.19%)**：
- 部分 Guard 的边角分支（如相关性对冲的历史数据不足路径）未覆盖
- 原因：这些路径在生产环境极少触发
- 解决方案：E2E 测试 `test_e2e_full_chain_does_not_crash_with_real_data` 验证真实数据下全链路不崩溃

---

## 六、运行测试

### 6.1 常用命令

```bash
# 运行全部测试金字塔 (55 秒)
python -m pytest tests/unit tests/integration tests/e2e -v

# 只运行单元测试 (快速反馈, <10 秒)
python -m pytest tests/unit -v

# 只运行 P0 级 bug 回归
python -m pytest -m p0 -v

# 只运行 E2E 黄金路径
python -m pytest -m e2e -v

# 按链路运行 (如 KillSwitch 三级熔断)
python -m pytest tests/unit/test_kill_switch_unit.py \
                 tests/integration/test_kill_switch_protocol_integration.py -v

# 生成覆盖率报告
python -m pytest tests/ --cov=utils --cov-report=html

# 只运行特定 bug 的回归测试
python -m pytest -m "bug('P0-D')" -v
```

### 6.2 CI/CD 集成建议

```bash
# 提交前快速验证 (单元测试, <10 秒)
python -m pytest tests/unit -q

# PR 合并前完整验证 (全金字塔, 55 秒)
python -m pytest tests/unit tests/integration tests/e2e --cov=utils --cov-fail-under=60
```

---

## 七、本次修复的 v8.6.5 遗留 bug

在建立测试金字塔过程中，发现并修复了 v8.6.5 遗留的 2 个 bug：

### 7.1 L2 级 circuit_level 覆盖问题

**bug 描述**：
`OvernightGapMonitor` 和 `MarketCircuitBreaker` 在 L2 级别时无条件设置 `circuit_level=WARNING`，会覆盖前面 Guard 已设置的 `CRITICAL`（L3）状态。

**修复方案**：
仅当 `circuit_level` 不是 `CRITICAL` 时才设置为 `WARNING`。

```python
# v8.6.7 修复: 不能覆盖更高优先级 Guard 设置的 CRITICAL
if plan['market_state'].get('circuit_level') != 'CRITICAL':
    plan['market_state']['circuit_level'] = 'WARNING'
```

**回归测试**：`test_l3_priority_overrides_l2_in_chain`

### 7.2 集成测试 monkeypatch 写法错误

**bug 描述**：
`test_p0d_none_margin_in_real_report_handled` 试图 patch 类的 `__init__` 方法，但 MagicMock 不允许设置 magic method，抛 `AttributeError: Attempting to set unsupported magic method '__init__'`。

**修复方案**：
改为直接 patch `_estimate_margin_from_positions` 方法本身。

```python
# 修复前 (错误):
mp.setattr(KillSwitch, "__init__", _patched_init)

# 修复后 (正确):
monkeypatch.setattr(
    KillSwitch, "_estimate_margin_from_positions",
    lambda self: 0.40
)
```

---

## 八、成果总结

### 8.1 量化指标

| 指标 | 修复前 (v8.6.5) | 修复后 (v8.6.7) | 变化 |
|------|----------------|----------------|------|
| 测试用例总数 | 0 | 120 | +120 |
| 单元测试数 | 0 | 86 | +86 |
| 集成测试数 | 0 | 26 | +26 |
| E2E 测试数 | 0 | 8 | +8 |
| 关键模块覆盖率 | 0% | 65.20% | +65.20% |
| P0 级 bug 回归测试数 | 0 | 8 | +8 |
| P1 级 bug 回归测试数 | 0 | 30 | +30 |
| 测试运行时间 | N/A | 55 秒 | - |
| 测试金字塔比例 | N/A | 71.7/21.7/6.7% | 接近经典 70/25/5 |

### 8.2 守护的已修复 bug 清单

| Bug 编号 | 描述 | 守护测试数 | 测试标记 |
|---------|------|-----------|---------|
| P0-D | EOD Guard KillSwitch 检查完全失效（None/None 崩溃） | 4 | `@pytest.mark.bug("P0-D")` |
| P0-E | 对冲执行引擎崩溃（字符串字段遍历） | 3 | `@pytest.mark.bug("P0-E")` |
| P1-G | broker_callback 从未注册 | 2 | `@pytest.mark.bug("P1-G")` |
| BUG#1 | overnight_gap_monitor FAIL_CLOSED_PCT 误触发 L3 | 4 | `@pytest.mark.bug("BUG#1")` |
| BUG#1b | market_circuit_breaker FAIL_CLOSED_PCT 误触发 L3 | 4 | `@pytest.mark.bug("BUG#1b")` |
| BUG#4 | guard_kill_switch 中 L2 被当作 L3 处理 | 3 | `@pytest.mark.bug("BUG#4")` |
| L2 覆盖 L3 | circuit_level 覆盖问题（v8.6.7 新修复） | 2 | 集成测试 |

### 8.3 测试金字塔比例验证

```
单元测试  86 / 120 = 71.7%  ≈ 70% (经典比例) ✅
集成测试  26 / 120 = 21.7%  ≈ 25% (经典比例) ✅
E2E 测试   8 / 120 =  6.7%  ≈  5% (经典比例) ✅
```

测试金字塔比例接近经典的 70/25/5 分布，符合测试金字塔最佳实践。

---

## 九、未来改进方向

### 9.1 短期（v8.7+）

1. **提升 kill_switch.py 覆盖率**：目前 54.51%，需补充 L3 强平执行路径的测试（通过 mock 券商 API）
2. **补充 correlation_hedge 测试**：相关性对冲模块目前未单独测试
3. **添加 CI 集成**：将测试金字塔接入 GitHub Actions，每次 PR 自动运行

### 9.2 中期（v8.8+）

1. **性能测试层**：在 E2E 之上添加性能基准测试（如 7 Guard 链路 < 5 秒）
2. **混沌工程测试**：模拟部分模块崩溃，验证系统降级行为
3. **数据驱动测试**：使用历史所有 pnl_report（30+ 份）做参数化测试

### 9.3 长期（v9.0+）

1. **覆盖率提升至 80%+**：补齐未覆盖的边角分支
2. **变异测试**：使用 mutmut 等工具验证测试有效性
3. **生产影子测试**：在影子账户环境运行真实交易，对比测试预期

---

## 十、相关文档

- **最新版 README**：[README.md](../README.md)（v8.6.7）
- **测试金字塔章节**：[README.md#测试金字塔体系建立-v867](../README.md#-测试金字塔体系建立-v867-从被动审计转向主动防御)
- **版本历史**：[README.md#版本历史](../README.md#版本历史)
- **前序审计报告**：
  - [HEDGE_FUND_AUDIT_2026-07-26_SYSTEM_BUGS_AND_AUTOMATION.md](HEDGE_FUND_AUDIT_2026-07-26_SYSTEM_BUGS_AND_AUTOMATION.md)（第一轮）
  - [HEDGE_FUND_AUDIT_V2_2026-07-26.md](HEDGE_FUND_AUDIT_V2_2026-07-26.md)（第二轮 CRO）
- **修复记录**：
  - [AUDIT_FIX_CHANGELOG_2026-07-26.md](AUDIT_FIX_CHANGELOG_2026-07-26.md)（v8.6.4）
  - [AUDIT_FIX_CHANGELOG_V2_2026-07-26.md](AUDIT_FIX_CHANGELOG_V2_2026-07-26.md)（v8.6.5）

---

## 十一、附录：测试文件清单

### 11.1 单元测试（6 文件，86 个测试）

```
tests/unit/
├── conftest.py                              # 单元测试专用 fixture
├── test_kill_switch_unit.py                 # 15 个 — KillSwitch 三级熔断
├── test_hedge_execution_engine_unit.py      # 13 个 — 对冲执行引擎
├── test_market_circuit_breaker_unit.py      # 14 个 — 大盘熔断
├── test_overnight_gap_monitor_unit.py       # 20 个 — 隔夜跳空
├── test_pnl_report_compat_unit.py           # 11 个 — 报告兼容
└── test_risk_guard_integrator_unit.py       # 13 个 — 风控集成器
```

### 11.2 集成测试（5 文件，26 个测试）

```
tests/integration/
├── conftest.py                              # 集成测试专用 fixture
├── test_eod_guard_chain_integration.py      # 3 个 — 七 Guard 链
├── test_kill_switch_protocol_integration.py # 5 个 — 熔断协议
├── test_fail_closed_integration.py          # 6 个 — fail-closed
├── test_report_compat_integration.py        # 7 个 — 报告兼容
└── test_hedge_dedup_integration.py          # 5 个 — 对冲去重
```

### 11.3 E2E 测试（1 文件，8 个测试）

```
tests/e2e/
├── conftest.py                              # 黄金数据加载
└── test_eod_full_chain_e2e.py               # 8 个 — 全链路黄金路径
```

### 11.4 配置文件

```
项目根/
├── pytest.ini                               # pytest 配置 + 标记定义
├── .coveragerc                              # 覆盖率配置 (阈值 40%)
└── tests/
    └── conftest.py                          # 顶层共享 fixture
```

---

**报告完成日期**：2026-07-26
**下次审计建议**：2026-08-02（运行 1 周后）
**下次审计重点**：本次未覆盖的模块（correlation_hedge / portfolio_optimizer / shadow_account）+ 真实交易日（2026-07-27）的运行日志验证
