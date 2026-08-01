# TASK — 8.4 工程化达标任务拆分

> 创建日期：2026-07-27
> 共 18 个任务，4 个 Phase
> 每个任务明确：输入 / 输出 / 验收标准

## Phase 1: 诚实回测（M1-M3）

### T01 — mypy 存量错误清零
- **输入**: utils/ 目录 560 个 mypy 错误
- **输出**: `mypy --config-file mypy.ini utils/` 退出码 0
- **方法**:
  1. 按 Top error files 优先级处理（wt_spread_strategy.py 38 个 → ifind_client.py 35 → ...）
  2. 类型缺失：补 type hints
  3. 类型冲突：修 typing
  4. 实在无法修：用 `# type: ignore[error-code]` 压制（必须带 error-code）
- **验收标准**: `mypy utils/` exit 0，无 `# type: ignore` 滥用（每个 ignore 必须有理由注释）
- **预计**: 1 周

### T02 — pylint broad-except 升级为 error
- **输入**: 现有 `.pylintrc` 中 broad-except 为 warning
- **输出**: broad-except 升级为 error，0 error
- **方法**:
  1. 修改 `.pylintrc`: `disable=broad-except` → `enable=broad-except` 且 `severity=error`
  2. 全局搜索 `except Exception:` / `except:`
  3. 改为具体异常类型（如 `except (KeyError, ValueError):`）
  4. 风控路径必须 fail-closed（不能吞异常）
- **验收标准**: `pylint --rcfile=.pylintrc utils/` 0 error
- **预计**: 3 天

### T03 — pytest 覆盖率基线测量
- **输入**: ~75 个测试文件，覆盖率未知
- **输出**: BASELINE_COVERAGE.md 报告
- **方法**:
  1. `pip install pytest-cov`
  2. `pytest --cov=utils --cov=ms_strategy --cov-report=html tests/`
  3. 生成 baseline 报告
  4. 识别覆盖率 < 30% 的关键模块（KillSwitch / circuit_breaker / broker_api）
- **验收标准**: 生成 htmlcov/ 报告 + 关键模块覆盖率清单
- **预计**: 1 天

### T04 — 补齐 20 个 GTJA191 低相关因子
- **输入**: [gtja191_factors.py](file:///E:/各种PY程序/28-终极量化交易系统8.4/ms_strategy/factors/gtja191_factors.py) 仅 alpha144
- **输出**: 20 个 alpha 函数，相关性矩阵 ρ < 0.7
- **方法**:
  1. 选因子：Alpha6/12/40/54/101/155 等（量价/动量/波动/反转 各 5）
  2. 参考国泰君安191原文 + Qlib 实现
  3. 每个因子: docstring + 单元测试 + IC 计算
  4. 跑相关性矩阵，剔除 ρ > 0.7 的因子
- **验收标准**:
  - 20 个 alpha 函数实现
  - 单元测试覆盖每个因子
  - 因子相关性矩阵 max ρ < 0.7
  - 至少 5 个因子 IC > 0.03
- **预计**: 2-3 周

### T05 — SimulatedBroker 接入 Almgren-Chriss
- **输入**: [broker_api.py:178](file:///E:/各种PY程序/28-终极量化交易系统8.4/ms_strategy/src/execution/broker_api.py#L178) 固定滑点 + [cost_model.py:56](file:///E:/各种PY程序/28-终极量化交易系统8.4/ms_strategy/src/backtest/cost_model.py#L56) Almgren-Chriss
- **输出**: SimulatedBroker.place() 调用 Almgren-Chriss
- **方法**:
  1. 在 SimulatedBroker 注入 cost_model 实例
  2. place() 时传入 (qty, volume, volatility)
  3. 滑点 = η*σ*sqrt(|q|/V) + γ*σ*(|q|/V)
  4. 单元测试: 大单滑点 > 小单滑点，高波动 > 低波动
- **验收标准**:
  - 大单 1000 股滑点 > 小单 100 股
  - 高波动日滑点 > 低波动日
  - 极端流动性枯竭时滑点 ≥ 10bps
- **预计**: 1 周

### T06 — WalkForward 改 Combinatorial Purged CV
- **输入**: [walk_forward.py](file:///E:/各种PY程序/28-终极量化交易系统8.4/ms_strategy/src/backtest/walk_forward.py) 简单切分
- **输出**: CPCV 实现，N=6, k=2，15 个组合回测
- **方法**:
  1. 参考 López de Prado afml 第 7 章
  2. 实现 `combinatorial_purged_kfold(N=6, k=2, purge_days=5, embargo_pct=0.01)`
  3. 输出: 15 个 backtest 结果 + SR 分布
  4. 计算 SR 的 5%/25%/50%/75%/95% 分位
- **验收标准**:
  - 15 个组合回测结果
  - SR 分布的下 5% 分位 > 0 才算通过
  - 上 95% 分位 < 2.0（否则过拟合）
- **预计**: 1-2 周

### T07 — DSR 改 bootstrap 估计 E[SR_max]
- **输入**: [metrics.py:176](file:///E:/各种PY程序/28-终极量化交易系统8.4/ms_strategy/src/backtest/metrics.py#L176) 简化公式
- **输出**: bootstrap DSR 实现
- **方法**:
  1. 输入 N 次试验的 SR 序列
  2. 1000 次 bootstrap resample
  3. 每次取 max(SR)
  4. 拟合 GEV 分布 → E[SR_max]
  5. DSR = SR_observed - E[SR_max]
- **验收标准**:
  - N=1 时 DSR = SR_observed
  - N=100 时 DSR ≥ SR_observed - 0.5
  - 单元测试通过
- **预计**: 3 天

### T08 — 1000 次 noise injection 稳定性测试
- **输入**: T04-T07 完成
- **输出**: noise_injection_report.md
- **方法**:
  1. 对策略参数注入 ±5% 噪声
  2. 跑 1000 次回测
  3. 计算 SR 的均值 / 标准差 / 5% 分位
  4. 若标准差 > 0.3 → 过拟合
- **验收标准**:
  - 1000 次回测完成
  - SR 标准差 < 0.3
  - SR 5% 分位 > 0
- **预计**: 1 周

**Phase 1 出口标准**: T01-T08 全部通过 + 回测 SR ≤ 1.0 + 3 年样本外年化 ≥ 沪深300 + 3%

---

## Phase 2: 不崩风控（M4-M6）

### T09 — daily_workflow 注册 KillSwitch.broker_callback
- **输入**: [daily_workflow.py](file:///E:/各种PY程序/28-终极量化交易系统8.4/) KillSwitch 未注册 callback
- **输出**: L2/L3 触发能真实撤单
- **方法**:
  1. 定位 daily_workflow 创建 KillSwitch 的位置
  2. 调用 `kill_switch.set_broker_callback(broker.cancel_all)`
  3. 测试: 模拟 L2/L3 触发，验证撤单执行
- **验收标准**: 模拟 L3 触发后，所有未成交订单被撤销
- **预计**: 1 周

### T10 — 新增大盘熔断 Guard
- **输入**: [circuit_breaker.py](file:///E:/各种PY程序/28-终极量化交易系统8.4/ms_strategy/src/risk/circuit_breaker.py)
- **输出**: 沪深300 单日 < -4% 触发降仓
- **验收标准**: 2015-07-08 / 2020-03-16 历史回放触发
- **预计**: 3 天

### T11 — 新增隔夜跳空 Guard
- **输出**: 标的跳空 > 3% 降仓 50%
- **验收标准**: 历史跳空数据回放触发
- **预计**: 3 天

### T12 — 新增全局撤单 Guard
- **输出**: L3 KillSwitch 触发撤所有
- **验收标准**: 模拟 L3 后 100ms 内撤单完成
- **预计**: 3 天

### T13 — pytest 覆盖率提升至 ≥ 80%
- **方法**: 识别 < 30% 模块 → 补测试
- **验收标准**: CI 强制覆盖率门槛 80%
- **预计**: 持续

### T14 — Shadow Account 14 天 + DSR ≥ 5
- **方法**: 纸面交易 14 天，DSR 计算
- **验收标准**: 14 天 DSR ≥ 5
- **预计**: 2 周

**Phase 2 出口标准**: 2015/2020/2024 极端行情 100 次模拟不崩

---

## Phase 3: 实盘验证（M7-M12）

### T15 — 50 万小资金实盘 3 个月
- **验收标准**: live-vs-backtest gap ≤ 3%
- **预计**: 3 个月

### T16 — 每周 live-vs-backtest attribution
- **验收标准**: 找出 alpha 衰减点
- **预计**: 持续

### T17 — drift_detector 增加 OOS Performance Gap
- **验收标准**: IC 衰减早发现
- **预计**: 1 周

### T18 — 月度参数调整（防 chasing）
- **验收标准**: 1 月 1 次为限
- **预计**: 持续

**Phase 3 出口标准**: 3 个月实盘年化 ≥ 6%，gap ≤ 3%
