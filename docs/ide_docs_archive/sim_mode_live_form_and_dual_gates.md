# 模拟盘以实盘形式跑 + 收益/对冲双门禁

## Context（背景）

### 问题
当前系统存在三个互相关联的问题，阻碍实盘接入：

1. **sim_mode 对冲执行缺口**：`daily_workflow.py` 的 `phase_hedge`（line 2280-2505）只有 `if not self.dry_run` 和 `elif self.dry_run` 两个分支，**没有 sim_mode 分支**。sim_mode=True 时对冲订单会走"非 dry_run"路径，尝试连接 CTP/THS 真实券商网关，失败后静默降级到 MockBroker — 这与 sim_mode 应走 `SimExecutionEngine` 的语义不一致，导致模拟盘对冲环节形同虚设。

2. **预期收益无门禁**：`predict_annual_return.py`（309 行）是独立 print-only 脚本，不被工作流调用，无任何阈值判断。无法在启动前回答"模型预测是否达到 V9 基线"。

3. **对冲完整性无事后校验**：`hedge_execution_engine.py` 的 `hedge_effectiveness`（line 471-475）只是事前预估，phase_hedge 完成后没有校验实际 portfolio_beta 和 net_delta 是否达标。

### 目标
- 让 `--sim` 模式走完整实盘代码路径（含对冲执行、希腊字母管理），但用 SimBroker 模拟成交
- 在 `run()` 启动前 + `phase_hedge` 完成后插入双门禁
- sim_mode 下门禁不达标仅告警（继续积累数据），`--live` 模式下不达标直接 fail-closed 终止
- 阈值对齐 V9 基线：年化≥15% / 回撤≤10% / Sharpe≥1.0；对冲后 portfolio_beta≤0.30 且 |net_delta|<0.05

### 已确认设计决策（用户已选）
| 决策点 | 选择 |
|---|---|
| 模拟实盘形态 | 增强 sim_mode（补齐对冲执行分支，不新建模式） |
| 收益门槛 | 对齐 V9 基线（15%/10%/1.0） |
| 对冲完整性 | Beta+Delta 双约束（beta≤0.30 且 |delta|<0.05） |
| 门禁接入点 | 启动前+对冲后双门禁（sim 告警/live fail-closed） |

---

## 关键文件与改动点

### 新增文件

#### 1. `v8.3_institutional/gate_manager.py`（核心新模块）
承载两个门禁类 + 统一结果数据结构。

**公共 API**：
```python
@dataclass
class GateResult:
    gate_name: str            # "return_expectation" / "hedge_completeness"
    passed: bool
    mode: str                 # "sim" / "live" / "dry"
    action: str               # "pass" / "warn" / "block"
    metrics: Dict[str, Any]
    thresholds: Dict[str, Any]
    blockers: List[str]
    promoters: List[str]
    timestamp: str
    recommendation: str
    def to_dict(self) -> Dict: ...

class ReturnExpectationGate:
    DEFAULT_THRESHOLDS = {"min_annual_return": 0.15, "max_drawdown": 0.10,
                          "min_sharpe": 1.0, "phase_factor": 0.70,
                          "use_phase_adjusted": True}
    def __init__(self, thresholds=None, config_path=None, inherit_shadow=True): ...
    def evaluate(self, mode: str = "sim") -> GateResult: ...
    def enforce(self, result: GateResult, live_mode: bool) -> bool: ...

class HedgeCompletenessGate:
    DEFAULT_THRESHOLDS = {"max_portfolio_beta": 0.30, "max_abs_net_delta": 0.05}
    def __init__(self, thresholds=None, config_path=None): ...
    def evaluate(self, portfolio_beta_before: float, portfolio_value: float,
                 hedge_orders_executed: List[Dict],
                 sim_engine=None, if_multiplier=300, if_beta=1.0) -> GateResult: ...
    def enforce(self, result: GateResult, live_mode: bool) -> bool: ...
```

**`ReturnExpectationGate.evaluate()` 核心逻辑**：
- 调用 `predict_annual_return.predict_annual_return_struct()` 获取结构化预测
- 从 `config/shadow_account_config.json` 的 `backtest_benchmark` 读取 V9 回测基准（annual_return=0.1962, max_drawdown=0.0995, sharpe_annual=1.315）
- 用 `phase_adjusted_return`（建仓期更严格）作为校验值
- 三项校验：年化≥15%、回撤≤10%（取 benchmark.max_drawdown）、Sharpe≥1.0
- 返回 blockers/promoters 列表

**`HedgeCompletenessGate.evaluate()` 核心计算**：
```
1. if_hedge_notional = sum(SHORT_FUTURES 订单 contracts * price * if_multiplier)
2. portfolio_beta_after = portfolio_beta_before - (if_hedge_notional * if_beta) / portfolio_value
3. options_delta = sim_engine.get_greek_exposure()['delta']  (sim_mode)
                 或从 BUY_PUT 类订单 budget 估算  (live_mode, 1万预算≈1张Put, delta≈-0.4)
4. futures_delta = -if_contracts * if_multiplier * if_beta
5. equity_delta_normalized = portfolio_beta_after * portfolio_value
6. net_delta = (equity_delta_normalized + options_delta + futures_delta) / portfolio_value
7. 校验: portfolio_beta_after ≤ 0.30 AND |net_delta| < 0.05
```

**复用现有工具**：
- `SimExecutionEngine.get_greek_exposure()` ([sim_broker_integration.py:800](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/sim_broker_integration.py#L800)) — 返回 `{"delta", "gamma", "theta", "vega"}`
- `SimExecutionEngine.execute_futures_orders/execute_options_orders/execute_stock_orders` ([sim_broker_integration.py:749,772,741](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/sim_broker_integration.py#L749))
- `HedgeExecutionEngine.TARGET_BETA=0.30` ([utils/hedge_execution_engine.py:61](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/hedge_execution_engine.py#L61)) — 阈值对齐来源
- `shadow_account_config.json` 的 `backtest_benchmark` — 基准值来源

#### 2. `config/gate_thresholds.json`
```json
{
  "enabled": true,
  "return_expectation_gate": {
    "min_annual_return": 0.15,
    "max_drawdown": 0.10,
    "min_sharpe": 1.0,
    "phase_factor": 0.70,
    "use_phase_adjusted": true
  },
  "hedge_completeness_gate": {
    "max_portfolio_beta": 0.30,
    "max_abs_net_delta": 0.05
  },
  "mode_action": {"sim": "warn_only", "live": "fail_closed", "dry": "skip"},
  "inherit_shadow_thresholds": true
}
```
**与 shadow_account_config.json 关系**：物理隔离（用途不同 — 本文件是即时门禁，shadow 是 14 天晋级机制），但数值对齐；通过 `inherit_shadow_thresholds=true` 单向继承 `backtest_benchmark` 作为校验基准。

### 修改的现有文件

#### 3. `v8.3_institutional/predict_annual_return.py`（重构）
- 拆出 `predict_annual_return_struct() -> Dict[str, Any]` 返回结构化数据（scenarios/expected_return/expected_vol/sharpe_estimate/phase_adjusted_return/contributions）
- 保留 `predict_annual_return()` 作为 print wrapper（向后兼容 CLI）
- 返回结构示例：
```python
{
    "scenarios": {"bull": {"return": 0.21, "prob": 0.35}, "base": {...}, "bear": {...}},
    "expected_return": 0.141,
    "expected_vol": 0.092,
    "sharpe_estimate": 1.26,
    "phase_adjusted_return": 0.099,
    "assumptions": {"total_capital": 5_000_000, "build_ratio": 0.40, "phase_factor": 0.70},
    "contributions": [{"name": "权益投资", "amount": ..., "pct": ...}, ...]
}
```

#### 4. `v8.3_institutional/daily_workflow.py`（4 处改动）

**改动 A：`phase_hedge` 新增 sim_mode 分支**（[daily_workflow.py:2280](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/daily_workflow.py#L2280)）
- 把 `if hedge_orders and not self.dry_run` 改为三分支：
  - `if hedge_orders and self.sim_mode and self.sim_engine is not None:` → 调用新方法 `_execute_sim_hedge_orders()`
  - `elif hedge_orders and not self.dry_run:` → 保持原 CTP/THS 实盘逻辑
  - `elif hedge_orders and self.dry_run:` → 保持原 DRY_RUN 逻辑

**改动 B：新增 `_execute_sim_hedge_orders()` 方法**（DailyWorkflow 类内）
- 按 action 拆分对冲订单到三类：
  - `SHORT_FUTURES` → `sim_engine.execute_futures_orders()`
  - `BUY_PUT_SPREAD` / `BUY_BARE_PUT` / `BUY_EMERGENCY_PUT` → `sim_engine.execute_options_orders()`
  - `SAFE_HAVEN_ALLOC` → `sim_engine.execute_stock_orders()`（黄金 ETF 518880）
  - `DOWNGRADE_TO_PUT_SPREAD` → 仅记录跳过
- 返回结构与原 `executed_orders` 对齐（含 type/action/instrument/side/contracts/price/status/fill_record）

**改动 C：`phase_hedge` 末尾插入对冲完整性门禁**（[daily_workflow.py:2561](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/daily_workflow.py#L2561) `return hedge_status` 之前）
```python
# === v8.4: 对冲完整性门禁 ===
try:
    from gate_manager import HedgeCompletenessGate
    gate = HedgeCompletenessGate()
    sim_engine_ref = self.sim_engine if (self.sim_mode and self.sim_engine) else None
    result = gate.evaluate(
        portfolio_beta_before=coordinated.get("portfolio_beta", 0),
        portfolio_value=self.capital,
        hedge_orders_executed=executed_orders,
        sim_engine=sim_engine_ref,
    )
    hedge_status["hedge_completeness_gate"] = result.to_dict()
    if not result.passed:
        if self.live_mode:
            logger.critical(f"[Gate-HEDGE] live fail-closed: {result.blockers}")
            self.state["fail_closed"] = True
            self.state["fail_closed_reason"] = f"HedgeCompletenessGate: {result.blockers}"
        else:
            logger.warning(f"[Gate-HEDGE] sim 告警不阻断: {result.blockers}")
except Exception as e:
    logger.error(f"对冲完整性门禁异常 (不阻断): {e}", exc_info=True)
```

**改动 D：`run()` 启动前插入预期收益门禁**（[daily_workflow.py:8580](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/daily_workflow.py#L8580) `phases = [...]` 之前）
```python
# === v8.4: 启动前预期收益门禁 ===
try:
    from gate_manager import ReturnExpectationGate
    gate = ReturnExpectationGate()
    mode = "live" if self.live_mode else ("sim" if self.sim_mode else "dry")
    if mode != "dry":
        result = gate.evaluate(mode=mode)
        self.state["pre_launch_gate"] = result.to_dict()
        if not gate.enforce(result, self.live_mode):
            self.state["fail_closed"] = True
            self.state["fail_closed_reason"] = f"ReturnExpectationGate: {result.blockers}"
            return self.state
except Exception as e:
    logger.error(f"启动前门禁异常 (不阻断): {e}", exc_info=True)
```

**改动 E：`run()` phases 循环增加 fail_closed 早退**（[daily_workflow.py:8603](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/daily_workflow.py#L8603) 循环开头）
```python
for i, (phase_name, phase_func) in enumerate(phases):
    # v8.4: 门禁 fail_closed 早退
    if self.state.get("fail_closed", False):
        logger.critical(f"[Gate] fail_closed 已触发, 跳过剩余 phase: {phase_name}")
        break
    # ... 原 phase 逻辑
```

---

## 测试策略

### 新增测试文件
| 文件 | 覆盖范围 |
|---|---|
| `tests/unit/test_predict_annual_return_struct.py` | 重构后结构化输出 |
| `tests/unit/test_gate_manager.py` | 两个 Gate 类单元测试 |
| `tests/unit/test_phase_hedge_sim_branch.py` | sim_mode 对冲分支集成 |
| `tests/integration/test_gate_integration.py` | run() + phase_hedge 端到端 |

### 关键测试场景
**ReturnExpectationGate**：
- 全部达标 → passed=True
- 年化不达标 → passed=False, blockers 含 "annual_return"
- sim_mode 不达标 → enforce 返回 True（继续）
- live_mode 不达标 → enforce 返回 False（阻断）
- 从 config 文件加载阈值

**HedgeCompletenessGate**：
- 3 张 IF 空头 + 期权对冲 → beta_after=0.044 < 0.30, delta 中性 → passed=True
- 1 张 IF 空头 → beta_after=0.548 > 0.30 → passed=False
- 期权大正 delta → |net_delta|≥0.05 → passed=False
- live 模式 sim_engine=None → 从 hedge_orders 估算 delta

**phase_hedge sim 分支**：
- sim_mode 下不 import CTPGateway/THSRealBroker
- 三种 action 正确拆分到 futures/options/stock
- 日志含 `[对冲执行-sim]` 标识

### Mock SimExecutionEngine
```python
class MockSimEngine:
    def __init__(self, delta=0.0):
        self._greek = {"delta": delta, "gamma": 0, "theta": 0, "vega": 0}
        self.router = MagicMock()
        self.calls = []
    def execute_futures_orders(self, orders, session="day"): ...
    def execute_options_orders(self, orders, session="day"): ...
    def execute_stock_orders(self, orders, session="day"): ...
    def get_greek_exposure(self): return self._greek
```

---

## 实施步骤

| 步骤 | 内容 | 依赖 | 验收标准 |
|---|---|---|---|
| S1 | 重构 `predict_annual_return.py`：拆出 `predict_annual_return_struct()` | 无 | CLI 输出与原版一致；struct 返回 dict 且 expected_return 为 float |
| S2 | 创建 `config/gate_thresholds.json` | 无 | JSON 合法，字段齐全 |
| S3 | 创建 `v8.3_institutional/gate_manager.py` | S1 | 单元测试 `test_gate_manager.py` 全通过（≥8 用例） |
| S4 | 改造 `daily_workflow.py phase_hedge`：新增 sim_mode 分支 + `_execute_sim_hedge_orders()` | S3 | sim_mode 下不再 import CTP/THS；hedge_orders 通过 sim_engine 执行 |
| S5 | 改造 `phase_hedge` 末尾：插入 HedgeCompletenessGate | S3, S4 | hedge_status 含 `hedge_completeness_gate` 字段；sim 告警/live 阻断 |
| S6 | 改造 `run()`：启动前 ReturnExpectationGate + phases 循环 fail_closed 早退 | S3 | sim 启动日志含 `[Gate-REVENUE]`；live 不达标直接 return |
| S7 | 集成测试 `test_gate_integration.py` + 全量回归 | S4-S6 | 新测试通过；现有 2310+ 测试无回归 |

**依赖关系**：S1 → S3 → {S4, S5, S6 可并行} → S7

---

## 风险与回退

### 影响面
| 改动 | 影响范围 | 风险 | 缓解 |
|---|---|---|---|
| predict_annual_return.py 重构 | CLI 输出 + 新增 API | 低 | 保留 print wrapper，CLI 行为不变 |
| phase_hedge sim_mode 新分支 | 所有 `--sim` 启动 | 中 | config 的 `enabled=false` 可热关闭；失败回退原降级路径 |
| phase_hedge 末尾门禁 | 所有 phase_hedge 调用 | 低 | 全 try-catch；仅 live 阻断 |
| run() 启动前门禁 | 所有 run() 调用 | 低 | dry 跳过；sim 仅告警；live 阻断有 try-catch 兜底 |
| _execute_sim_hedge_orders | sim_mode 对冲执行 | 中 | 黄金 ETF 价格缺失时跳过该订单不阻断其他 |

### 已知边界情况
1. **SAFE_HAVEN_ALLOC 黄金 ETF 价格缺失**：`MOCK_PRICES.get("518880", 5.85)` 可能返回默认价 → 检测 price≤0 时标记 `status=SKIP_NO_PRICE`
2. **phase_factor 与实际建仓进度不符**：默认 `use_phase_adjusted=true`，必要时改 false 用 expected_return
3. **live 模式 hedge_completeness 不达标时对冲已执行**：fail_closed 后 run() 跳过 signal/execute phase（不下新单），下一交易日重新评估
4. **coordinated["portfolio_beta"] 可能是风格代理**：HedgeCompletenessGate 记录 `portfolio_beta_source` 字段便于审计

### 回滚策略
- 5 个独立 commit（S1/S2+S3/S4/S5/S6），可单独 revert
- 热回滚：`config/gate_thresholds.json` 设 `"enabled": false`，下次 run() 所有门禁跳过
- sim_mode 分支无法热回滚，但可用 `--dry-run` 替代 `--sim` 临时绕过

---

## 验证方法

### 端到端验证
```powershell
# 1. 预期收益门禁 (sim 告警)
cd e:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional
python daily_workflow.py --date 2026-07-28 --sim
# 预期: 日志含 [Gate-REVENUE] passed=True/False, action=warn
# 预期: 日志含 [对冲执行-sim] X 笔对冲指令通过 SimExecutionEngine 执行
# 预期: 日志含 [Gate-HEDGE] passed=True/False, beta_after=..., net_delta=...

# 2. live 模式 fail-closed (不达标时)
$env:TRADING_ENV="production"
python daily_workflow.py --date 2026-07-28 --live
# 预期: 不达标时直接 return, 不进入 phases 循环

# 3. 单元测试
python -m pytest tests/unit/test_gate_manager.py -v
python -m pytest tests/unit/test_phase_hedge_sim_branch.py -v
python -m pytest tests/integration/test_gate_integration.py -v

# 4. 全量回归
python -m pytest tests/ -x --tb=short
```

### 成功标准
- 新增测试全部通过
- 现有 2310+ 测试无回归
- sim_mode 下 phase_hedge 日志含 `[对冲执行-sim]` 且不出现 CTP/THS import
- hedge_execution_fill_*.json 中对冲订单 status 从 `DRY_RUN` 变为 `FILLED`（sim_mode 下）
- live_mode 不达标时进程退出码非 0 且日志含 `fail_closed`
