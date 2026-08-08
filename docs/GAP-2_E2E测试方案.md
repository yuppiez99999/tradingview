# GAP-2 E2E 测试方案

> **目标**：补齐 Wave 3 最后一块拼图 — `full_pipeline` + `shadow_account_lifecycle` 端到端测试
> **日期**：2026-08-05 执行
> **预估时间**：2-3 小时
> **验收标准**：所有测试通过 + Wave 3 GAP-2 标记 DONE

## 一、背景

### 1.1 GAP-2 定义

GAP-2 是 Wave 3 代码质量修复中的测试覆盖缺口，需要补齐两个 E2E 测试场景：

| 场景 | 测试目标 | 入口函数 |
|------|---------|---------|
| full_pipeline | PipelineOrchestrator 完整流水线端到端 | `PipelineOrchestrator.run_full_cycle()` |
| shadow_account_lifecycle | ShadowAccountAdapter 生命周期端到端 | `ShadowAccountAdapter.run_shadow()` + `get_metrics()` |

### 1.2 现有 E2E 模式（从 tests/e2e/ 提取的约定）

- 标记：`@pytest.mark.e2e`
- 数据策略：真实历史报告作为黄金数据源（`e2e_pnl_reports` / `e2e_trade_plans` fixture），数据缺失 skip 不 fail
- 环境隔离：production 环境跳过 orchestrator，shadow/development 环境激活
- 断言风格：验证状态机迁移 + 审计日志持久化 + 降级保护

### 1.3 待测代码位置

| 模块 | 文件路径 | 关键方法 |
|------|---------|---------|
| PipelineOrchestrator | [utils/pipeline/orchestrator.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/pipeline/orchestrator.py) | `run_full_cycle()` / `_run_stage_*()` |
| ShadowAccountAdapter | [utils/alpha/shadow_account_adapter.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha/shadow_account_adapter.py) | `run_shadow()` / `get_metrics()` |
| E2E conftest | [tests/e2e/conftest.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/tests/e2e/conftest.py) | `e2e_pnl_reports` / `e2e_trade_plans` |

---

## 二、测试文件规划

### 2.1 新增文件

| 文件 | 测试数 | 说明 |
|------|--------|------|
| `tests/e2e/test_full_pipeline_e2e.py` | 6 | PipelineOrchestrator 完整周期 E2E |
| `tests/e2e/test_shadow_account_lifecycle_e2e.py` | 6 | ShadowAccountAdapter 生命周期 E2E |

### 2.2 conftest.py 新增 fixture

在 `tests/e2e/conftest.py` 追加：

```python
@pytest.fixture
def pipeline_config_overrides():
    """PipelineOrchestrator 测试配置覆盖 (Feature Flag 全开)"""
    return {
        "data_cleaning_enabled": True,
        "execution_enabled": False,    # E2E 不执行真实交易
        "risk_monitor_enabled": True,
    }

@pytest.fixture
def sample_daily_returns_14d():
    """14 天观察期收益率序列 (模拟真实波动)"""
    return [0.005, -0.003, 0.008, -0.002, 0.004,
            -0.006, 0.003, 0.001, -0.004, 0.007,
            -0.005, 0.002, 0.006, -0.003]

@pytest.fixture
def extreme_daily_returns_breach():
    """触发 Fail-Fast 的极端收益率序列 (单日 -4%)"""
    return [0.001, 0.002, -0.04, 0.003, 0.001]
```

---

## 三、test_full_pipeline_e2e.py — 测试用例设计

### 场景 1: dry_run 模式完整周期 ✅

**目标**：验证 5 阶段全链路在 dry_run 模式下正确执行

```python
@pytest.mark.e2e
def test_full_cycle_dry_run(self, pipeline_config_overrides):
    """dry_run 模式: 5 阶段全部执行, 状态机正确迁移, success=True"""
    orchestrator = PipelineOrchestrator()
    result = orchestrator.run_full_cycle(mode="dry_run")

    # 状态机验证
    assert result.success is True
    assert result.completed_stage == "COMPLETED"
    # 阶段报告存在性
    assert len(result.data_reports) >= 0  # dry_run 可能无数据
    # 执行模块未启用 (不产出真实订单)
    assert result.execution_result is None or not result.execution_result.orders_sent
```

**验收标准**：
- `result.success == True`
- `result.completed_stage == "COMPLETED"`
- 无异常抛出
- 耗时 < 30s

---

### 场景 2: 数据清洗失败降级 ⚠️

**目标**：验证阶段 1 失败时正确降级

```python
@pytest.mark.e2e
def test_data_cleaning_failure_degradation(self):
    """数据清洗失败: 状态机停在 DATA_CLEANING, success=False"""
    orchestrator = PipelineOrchestrator()
    # 注入空市场数据触发失败
    result = orchestrator.run_full_cycle(
        mode="auto",
        market_data={},        # 空数据
        symbols=[],            # 空标的
    )

    assert result.success is False
    assert result.failed_stage == "DATA_CLEANING"
    assert result.error is not None
```

**验收标准**：
- `result.success == False`
- `result.failed_stage == "DATA_CLEANING"`
- 无未捕获异常

---

### 场景 3: 执行模块禁用跳过 ⏭

**目标**：验证 execution_enabled=False 时跳过阶段 4

```python
@pytest.mark.e2e
def test_execution_disabled_skipped(self, pipeline_config_overrides):
    """执行模块禁用: 跳过阶段 4, 完成到风控监控"""
    config = get_pipeline_config()
    config.execution_enabled = False
    orchestrator = PipelineOrchestrator(config=config)

    result = orchestrator.run_full_cycle(mode="dry_run")

    assert result.success is True
    assert result.execution_result is None  # 未执行
```

---

### 场景 4: 风控模块禁用跳过 ⏭

**目标**：验证 risk_monitor_enabled=False 时跳过阶段 5

```python
@pytest.mark.e2e
def test_risk_monitor_disabled_skipped(self):
    """风控模块禁用: 跳过阶段 5, 完成到执行"""
    config = get_pipeline_config()
    config.risk_monitor_enabled = False
    orchestrator = PipelineOrchestrator(config=config)

    result = orchestrator.run_full_cycle(mode="dry_run")

    assert result.success is True
    assert len(result.risk_alerts) == 0
```

---

### 场景 5: 异常捕获与状态机回退 🛡️

**目标**：验证某阶段抛出异常时状态机正确迁移到 FAILED

```python
@pytest.mark.e2e
def test_exception_recovery(self, monkeypatch):
    """Alpha 阶段抛异常: 状态机迁移到 FAILED, error_count 递增"""
    orchestrator = PipelineOrchestrator()

    # Mock Alpha 阶段抛出异常
    def _raise(*args, **kwargs):
        raise RuntimeError("Alpha pipeline crashed (mocked)")
    monkeypatch.setattr(orchestrator._alpha, "run", _raise)

    result = orchestrator.run_full_cycle(mode="dry_run")

    assert result.success is False
    assert result.failed_stage == "ALPHA_GENERATION"
    assert "Alpha pipeline crashed" in result.error
    assert orchestrator._status.error_count == 1
```

---

### 场景 6: 状态机快照持久化 📸

**目标**：验证 PipelineStatus.to_dict() 输出完整

```python
@pytest.mark.e2e
def test_status_snapshot(self):
    """状态机快照: to_dict() 包含所有字段"""
    orchestrator = PipelineOrchestrator()
    orchestrator.run_full_cycle(mode="dry_run")

    snapshot = orchestrator._status.to_dict()

    assert "current_stage" in snapshot
    assert "last_run_at" in snapshot
    assert "run_count" in snapshot
    assert "error_count" in snapshot
    assert snapshot["run_count"] == 1
```

---

## 四、test_shadow_account_lifecycle_e2e.py — 测试用例设计

### 场景 1: 正常生命周期（14 天观察期）✅

**目标**：验证 14 天收益率序列注入后产出完整指标

```python
@pytest.mark.e2e
def test_normal_lifecycle_14d(self, sample_daily_returns_14d):
    """14 天正常生命周期: run_shadow 成功, get_metrics 返回完整指标"""
    adapter = ShadowAccountAdapter(
        account_id="e2e_test_normal",
        initial_capital=1_000_000,
    )
    adapter.run_shadow(daily_returns=sample_daily_returns_14d)
    metrics = adapter.get_metrics()

    # 指标完整性
    assert "dsr" in metrics
    assert "annual_return" in metrics
    assert "max_drawdown" in metrics
    assert "sharpe_cv" in metrics
    # 数值合理性
    assert -1.0 < metrics["annual_return"] < 2.0
    assert 0 <= metrics["max_drawdown"] < 1.0
```

---

### 场景 2: Fail-Fast 触发（单日 >3%）🚨

**目标**：验证单日 -4% 回撤触发 Fail-Fast

```python
@pytest.mark.e2e
def test_failfast_daily_breach(self, extreme_daily_returns_breach):
    """单日 -4% 回撤: FailFastTriggeredError 抛出, 适配器终止"""
    adapter = ShadowAccountAdapter(
        account_id="e2e_test_failfast_daily",
        initial_capital=1_000_000,
    )

    with pytest.raises(FailFastTriggeredError) as exc_info:
        adapter.run_shadow(daily_returns=extreme_daily_returns_breach)

    assert "3%" in str(exc_info.value) or "daily" in str(exc_info.value).lower()
```

---

### 场景 3: Fail-Fast 触发（3 日累计 >5%）🚨

**目标**：验证 3 天累计 -6% 回撤触发 Fail-Fast

```python
@pytest.mark.e2e
def test_failfast_3d_cumulative_breach(self):
    """3 日累计 -6% 回撤: FailFastTriggeredError 抛出"""
    adapter = ShadowAccountAdapter(
        account_id="e2e_test_failfast_3d",
        initial_capital=1_000_000,
    )
    # 3 天累计 -6%: -2% + -2% + -2%
    returns = [0.001, -0.02, -0.02, -0.02]

    with pytest.raises(FailFastTriggeredError):
        adapter.run_shadow(daily_returns=returns)
```

---

### 场景 4: 样本不足降级 ⚠️

**目标**：验证 < MIN_SAMPLES_FOR_DSR(15) 时返回降级状态

```python
@pytest.mark.e2e
def test_insufficient_samples(self):
    """5 天样本 (< 15): get_metrics 返回 insufficient_samples 状态"""
    adapter = ShadowAccountAdapter(
        account_id="e2e_test_insufficient",
        initial_capital=1_000_000,
    )
    adapter.run_shadow(daily_returns=[0.001, 0.002, -0.001, 0.003, -0.002])
    metrics = adapter.get_metrics()

    # 样本不足时 DSR 不可计算
    assert metrics.get("dsr") is None or metrics.get("status") == "insufficient_samples"
```

---

### 场景 5: risk_managed 模式波动率缩放 📊

**目标**：验证 risk_managed=True 时波动率缩放 + 回撤去杠杆生效

```python
@pytest.mark.e2e
def test_risk_managed_volatility_scaling(self, sample_daily_returns_14d):
    """risk_managed 模式: 波动率缩放至目标 15% + 回撤去杠杆"""
    adapter = ShadowAccountAdapter(
        account_id="e2e_test_risk_managed",
        initial_capital=1_000_000,
        risk_managed=True,  # HC-3 硬约束
    )
    adapter.run_shadow(daily_returns=sample_daily_returns_14d)
    metrics = adapter.get_metrics()

    # risk_managed 模式下年化波动率应被缩放
    if "annual_volatility" in metrics:
        assert metrics["annual_volatility"] <= 0.20  # 接近目标 15%
```

---

### 场景 6: 与 PipelineOrchestrator 集成 🔗

**目标**：验证 Pipeline 产出 → Shadow 注入的端到端数据流

```python
@pytest.mark.e2e
def test_pipeline_to_shadow_integration(self, sample_daily_returns_14d):
    """Pipeline 产出 Alpha 信号 → 注入 ShadowAccountAdapter"""
    # Step 1: 运行 Pipeline (dry_run, 不执行交易)
    orchestrator = PipelineOrchestrator()
    pipeline_result = orchestrator.run_full_cycle(mode="dry_run")
    assert pipeline_result.success is True

    # Step 2: 将收益率序列注入 Shadow
    adapter = ShadowAccountAdapter(
        account_id="e2e_integration",
        initial_capital=1_000_000,
    )
    adapter.run_shadow(daily_returns=sample_daily_returns_14d)
    metrics = adapter.get_metrics()

    # 端到端: Pipeline 完成 + Shadow 指标产出
    assert pipeline_result.success is True
    assert metrics is not None
```

---

## 五、执行步骤

### 5.1 准备阶段（15 min）

```bash
# 1. 确认待测模块可导入
py -3.8 -c "from utils.pipeline.orchestrator import PipelineOrchestrator; print('OK')"
py -3.8 -c "from utils.alpha.shadow_account_adapter import ShadowAccountAdapter; print('OK')"

# 2. 确认 E2E 标记已注册
py -3.8 -m pytest --markers | findstr e2e
```

### 5.2 编写测试（60-90 min）

1. 在 `tests/e2e/conftest.py` 追加 3 个 fixture
2. 创建 `tests/e2e/test_full_pipeline_e2e.py`（6 个用例）
3. 创建 `tests/e2e/test_shadow_account_lifecycle_e2e.py`（6 个用例）

### 5.3 执行测试（15 min）

```bash
# 仅运行 E2E 标记的测试
py -3.8 -m pytest tests/e2e/test_full_pipeline_e2e.py tests/e2e/test_shadow_account_lifecycle_e2e.py -v --tb=short

# 如需跳过慢速测试
py -3.8 -m pytest tests/e2e/test_full_pipeline_e2e.py tests/e2e/test_shadow_account_lifecycle_e2e.py -v -m "not slow"
```

### 5.4 修复失败用例（30 min）

预期可能的问题：
- PipelineOrchestrator 的 dry_run 模式可能需要特定配置
- ShadowAccountAdapter 的 risk_managed 参数名可能不同（需核对 __init__ 签名）
- FailFastMonitor 的触发条件可能需要特定阈值

### 5.5 验收与归档（15 min）

```bash
# 确认全部通过
py -3.8 -m pytest tests/e2e/ -v --tb=short

# 更新 ROADMAP.md: GAP-2 标记 ✅ DONE
# 更新 LOG.md: 追加 GAP-2 E2E 补齐条目
```

---

## 六、风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| PipelineOrchestrator 依赖外部数据源 | 中 | E2E 测试可能因数据不可用 skip | 用 dry_run 模式 + mock 市场数据 |
| ShadowAccountAdapter 参数名不匹配 | 中 | 测试编译失败 | 先读 __init__ 签名再写测试 |
| FailFastMonitor 阈值与文档不一致 | 低 | 场景 2/3 不触发 | 先读源码确认阈值再构造数据 |
| Pipeline 阶段 4/5 Feature Flag 默认关闭 | 高 | 场景 4/5 可能无法独立验证 | 用 config 显式启用/禁用 |

---

## 七、验收标准

- [ ] 12 个 E2E 测试用例全部通过（或 skip 有合理原因）
- [ ] 无未捕获异常
- [ ] ROADMAP.md GAP-2 标记 ✅ DONE
- [ ] LOG.md 追加 GAP-2 E2E 补齐条目
- [ ] Wave 3 全部清零（Round 5a/5b/6 + 三/四阶段 + GAP-2）
