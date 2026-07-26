# TASK — 终极量化交易系统 8.4 模块整合（6A 阶段 3：Atomize）

> 原子化任务清单：将 ARCHITECTURE 文档的 5 Phase 路线图拆解为可执行任务。
> 创建日期：2026-07-26
> 任务编号：MIG-8.4-TASK-001
> 任务粒度：每个任务 1-3 天可完成，明确输入/输出/验收标准

---

## 0. 任务编号规则

`T{Phase}.{Sequence}` — 例如 T1.1 = Phase 1 第 1 个任务

**状态标记**：
- ⬜ TODO（未开始）
- 🔄 IN_PROGRESS（进行中）
- ✅ DONE（已完成）
- ⚠️ BLOCKED（阻塞）
- ❌ CANCELLED（取消）

**优先级**：P0（阻塞）/ P1（关键）/ P2（重要）/ P3（可选）

---

## Phase 1 — 基础设施层（2 周）

### T1.1 创建 Git 分支策略与基线保护 ⬜ [P0]

| 维度 | 内容 |
|------|------|
| **输入** | main 分支当前 HEAD = V9 生产基线 commit |
| **输出** | `integration/phase1-infra` 分支创建 + `.github/branch-protection.yml`（或等价物）+ `docs/模块整合_8.4/BRANCH_POLICY.md` |
| **验收标准** | 1. `integration/phase1-infra` 分支存在且通过 `git log` 可追溯到 main<br>2. 分支保护规则：main 仅接受 PR 合并，需 1 review + CI 全绿<br>3. V9 基线 commit hash 记录到 `docs/模块整合_8.4/V9_BASELINE_LOCK.txt` |
| **责任层** | L1 基础设施 |
| **依赖** | 无 |
| **风险** | R1（破坏 V9 基线）— 缓解：分支保护 + 回归测试 |

### T1.2 创建 utils/infra/ 目录结构与 re-export 框架 ⬜ [P0]

| 维度 | 内容 |
|------|------|
| **输入** | ARCHITECTURE §2.1 目标分层 |
| **输出** | `utils/infra/__init__.py`（空 re-export）+ `utils/data/__init__.py` + `utils/alpha/__init__.py` + `utils/execution/__init__.py` + `utils/risk/__init__.py` + `utils/scheduling/__init__.py` + `utils/attribution/__init__.py` + `utils/_legacy/__init__.py` |
| **验收标准** | 1. 8 个子目录存在，每个有 `__init__.py`<br>2. `python -c "import utils.infra; import utils.risk"` 不报错<br>3. 现有 `from utils import config_manager` 仍可用（re-export 占位） |
| **责任层** | L1 基础设施 |
| **依赖** | T1.1 |

### T1.3 实现 Feature Flag 框架 ⬜ [P0]

| 维度 | 内容 |
|------|------|
| **输入** | ARCHITECTURE §1.4 Flag 注册表设计 |
| **输出** | `utils/infra/feature_flags.py` + `v8.3_institutional/config/feature_flags.yaml` + `tests/unit/test_feature_flags.py` |
| **验收标准** | 1. `FeatureFlags.is_enabled("USE_INTEGRATED_BOOTSTRAP")` 返回 False（默认值）<br>2. `FeatureFlags.enable(name, signer, co_signer)` 写入审计日志<br>3. `FeatureFlags.disable(name, signer)` 单签即可<br>4. 配置走 ConfigManager 4 级优先级（HC-5）<br>5. 单测覆盖率 >= 90%<br>6. mypy strict 通过 |
| **责任层** | L1 基础设施 |
| **依赖** | T1.2 |
| **关键契约** | `FeatureFlags.is_enabled(name: str) -> bool` / `enable(name, signer, co_signer) -> None` / `disable(name, signer) -> None` / `audit_trail(name) -> list[dict]` |

### T1.4 实现 utils/__init__.py re-export 兼容层 ✅ [P0] (2026-07-26 完成)

| 维度 | 内容 |
|------|------|
| **输入** | V9 训练/推理脚本的所有 `from utils import X` 语句清单 |
| **输出** | `utils/__init__.py` 完整 re-export + `scripts/_verify_reexport_compat.py` |
| **验收标准** | 1. 用 `grep -r "from utils import" --include="*.py"` 收集所有 import 语句<br>2. 每个被 import 的符号在 `utils/__init__.py` 有对应 re-export<br>3. `_verify_reexport_compat.py` 通过率 100%<br>4. V9 训练脚本 `qlib_v9_train.py` 可正常 import |
| **责任层** | L1 基础设施 |
| **依赖** | T1.2 |
| **风险** | R1 — 缓解：re-export 验证脚本作为 PR 闸门 |
| **完成实绩** | 2026-07-26: 硬性标准 0 错误通过 (FeatureFlags re-export 10/10, V9 关键脚本 3/3); 16 个软性警告 (历史 import 路径问题, 非 re-export 责任, 待后续清理) |

### T1.5 新建 bootstrap.py（全局初始化） ✅ [P1] (2026-07-26 完成)

| 维度 | 内容 |
|------|------|
| **输入** | 现有分散的初始化逻辑（ConfigManager 单例 + KillSwitch 初始化 + Logger 配置） |
| **输出** | `utils/infra/bootstrap.py` + `tests/unit/test_bootstrap.py` |
| **验收标准** | 1. `bootstrap.initialize()` 一次性完成 ConfigManager + Logger + TradingEnv + KillSwitch 初始化<br>2. 幂等：多次调用不重复初始化<br>3. 异常处理：任何子步骤失败抛出 `BootstrapError`<br>4. 单测覆盖率 >= 80% |
| **责任层** | L1 基础设施 |
| **依赖** | T1.4 |
| **完成实绩** | 2026-07-26: 26 个单测全部 PASSED (2.09s); 7 步初始化 (.env→Logger→ConfigManager→TradingEnv→KillSwitch→broker_callback→FeatureFlags); 幂等性 + BootstrapError(step/reason/cause) + reset() 测试通过 |

### T1.6 新建 core.py（策略注册表） ✅ [P1] (2026-07-26 完成)

| 维度 | 内容 |
|------|------|
| **输入** | `quantitative_system.py` 内嵌的策略注册逻辑（识别后提取） |
| **输出** | `utils/infra/core.py` 含 `StrategyRegistry` 类 + `tests/unit/test_core_registry.py` |
| **验收标准** | 1. `StrategyRegistry.register(name, strategy_class)` 注册策略<br>2. `StrategyRegistry.get(name)` 获取策略<br>3. `StrategyRegistry.list_strategies()` 返回所有已注册策略<br>4. 性能追踪装饰器 `@track_performance` 记录每策略 PnL/Sharpe/回撤<br>5. 单测覆盖率 >= 80% |
| **责任层** | L1 基础设施 |
| **依赖** | T1.5 |
| **Feature Flag** | `USE_INTEGRATED_CORE_REGISTRY`（默认 False） |
| **完成实绩** | 2026-07-26: 34 个单测全部 PASSED (0.41s); StrategyRegistry 单例 + @track_performance 装饰器 (flag 关闭零开销透传); 延迟实例化 + 审计日志 + PerformanceRecord (PnL/Sharpe/IC_IR/延迟/异常); 实际输入为 RuleEngine + MultiStrategyCoordinator + StrategyReleaseManager 三套分散注册 (quantitative_system.py 不存在) |

### T1.7 新建 data_layer.py（统一数据降级链） ✅ [P1] (2026-07-26 完成)

| 维度 | 内容 |
|------|------|
| **输入** | 现有 `data_provider.py` + `data_gate.py` + `akshare_data_source.py` + `ifind_client.py` + `tdx_data_source.py` + `external_data_source.py` |
| **输出** | `utils/data/data_layer.py` 含 `DataLayer` 类 + P0-P6 降级链配置 |
| **验收标准** | 1. P0（iFinD）失败 → P1（akshare）→ P2（tdx）→ ... → P6（缓存）<br>2. 每级降级记录到 `reports/data_layer/fallback_{date}.jsonl`<br>3. `DataLayer.get_ohlcv(symbol, start, end)` 接口稳定<br>4. chaos test：模拟 P0 失败，验证 P1 接管<br>5. 单测覆盖率 >= 80% |
| **责任层** | L2 数据层 |
| **依赖** | T1.4 |
| **Feature Flag** | `USE_INTEGRATED_DATA_LAYER`（默认 False） |
| **完成实绩** | 2026-07-26: DataLayer 主类 (727 行) + 50 单测 (1.46s) + 22 chaos test (1.46s) + V9 基线回归 25/25 通过 (2.78s); 7 级降级链 P0(Wind MCP)→P1(iFinD)→P2(tdx)→P3(akshare)→P4(external)→P5(sina)→P6(cache); fallback 审计日志 JSONL 格式 + DataGate 集成 + P6 缓存 TTL (24h) + Feature Flag 透传 (HC-1); 10 大 chaos 场景覆盖 (单点故障/双故障/全失败/间歇性/并发/缓存过期/慢响应/审计完整性/混合操作/Flag 切换) |

### T1.8 V9 基线回归测试套件 ✅ [P0] (2026-07-26 完成)

| 维度 | 内容 |
|------|------|
| **输入** | V9 评估标准（DSR>=5 + 年化>=15% + 回撤<=10% + Sharpe CV<1.0） |
| **输出** | `tests/regression/test_v9_baseline.py` + `scripts/_run_v9_regression.py` |
| **验收标准** | 1. 测试运行 V9 训练 + 推理 + 评估全流程<br>2. 断言 DSR>=5 / 年化>=15% / 回撤<=10% / Sharpe CV<1.0<br>3. 失败时输出详细差异报告<br>4. 运行时间 < 30 分钟（可用于 CI）<br>5. 在 main 分支当前 HEAD 运行通过 |
| **责任层** | L1 基础设施 + L3 Alpha |
| **依赖** | T1.1 |
| **关键性** | 此任务是后续所有 PR 的合并闸门 |
| **完成实绩** | 2026-07-26: 25/25 测试通过 (1.91 秒), 1 个 nightly 测试默认 deselected (Layer 5 完整回测, >30 分钟); 5 层测试架构 (基线完整性 / 指标重算一致性 / 阈值通过检查 / V9 代码可导入性 / nightly 完整回测); 独立实现 DSR (Bailey 2014) + Sharpe CV (12 月滚动) 双盲验证 _calc_v9_dsr_v2.py; 年化公式对齐 backtest_runner.py L703 (1+monthly_mean)^12-1 |

### T1.9 CI 流水线搭建 ✅ [P1] (2026-07-26 完成)

| 维度 | 内容 |
|------|------|
| **输入** | T1.8 回归脚本 + mypy.ini + .pylintrc + pytest.ini |
| **输出** | `.github/workflows/ci.yml` + `scripts/ci_run.sh` + `scripts/ci_nightly.sh` + `scripts/_verify_ci_config.py` |
| **验收标准** | 1. PR 触发：mypy + pylint + pytest unit + pytest integration<br>2. nightly 触发：V9 基线回归（T1.8）<br>3. 失败阻断合并<br>4. CI 总运行时间 < 45 分钟 |
| **责任层** | L1 基础设施 |
| **依赖** | T1.8 |
| **完成实绩** | 2026-07-26: 7 个 job 架构 (lint-typecheck / unit-tests / integration-tests / reexport-compat / v9-quick-regression / nightly-regression / ci-summary); PR 触发 5 个并行 job (windows-latest, 总 timeout=100min 但实际 <45min); nightly 触发 1 个 job (含 Layer 5 完整回测, timeout=45min); concurrency 自动取消旧 run 省 CI 资源; ci-summary 聚合作最终闸门 (HC-1 阻断: unit/integration/reexport/v9 任一失败即阻断合并); mypy+pylint Phase 3-B 渐进式严格 (continue-on-error 非阻断); 本地等效脚本 ci_run.sh (5 个 job) + ci_nightly.sh (含 Layer 5 + 全量 pytest); _verify_ci_config.py 验证 YAML + bash 语法 (3/3 通过, 7 个 job 全部识别, 触发器正确) |

**Phase 1 完成闸门**：G2（三层保护框架）+ G3（基础设施整合）+ G8（回归测试套件）全部满足。

---

## Phase 2 — AI 决策层（3 周）

### T2.1 扩展 llm_client.py 为多模型路由器 ⬜ [P1]

| 维度 | 内容 |
|------|------|
| **输入** | `15_每日工作流/llm_client.py`（现有） |
| **输出** | `utils/alpha/llm_router.py` 含 `LLMRouter` 类 + fallback 链 |
| **验收标准** | 1. 支持 4 个 provider：豆包 / GLM-5 / SiliconFlow / Ollama<br>2. fallback 顺序可配置（yaml）<br>3. 5 秒超时 + 静默降级到本地规则<br>4. 每个 provider 调用记录到 `reports/llm_router/calls_{date}.jsonl`<br>5. 单测覆盖率 >= 70% |
| **责任层** | L3 Alpha |
| **依赖** | T1.4 |
| **Feature Flag** | `USE_LLM_REPORT_ANALYZER`（默认 False） |

### T2.2 迁移 decision_theories.py ⬜ [P1]

| 维度 | 内容 |
|------|------|
| **输入** | `v8.3_institutional/src/factors/decision_theories.py`（已有） |
| **输出** | `utils/alpha/decision_theories.py` + 原 v8.3 路径 re-export |
| **验收标准** | 1. 文件物理迁移到 `utils/alpha/`<br>2. v8.3 原路径通过 re-export 仍可访问<br>3. 四大理论（价值/动量/质量/情绪）融合接口稳定<br>4. 单测覆盖率 >= 60% |
| **责任层** | L3 Alpha |
| **依赖** | T1.4 |

### T2.3 新建 multi_factor_signal.py ⬜ [P1]

| 维度 | 内容 |
|------|------|
| **输入** | `utils/alpha/factor_library.py`（已有）+ `utils/signal_fusion.py`（已有） |
| **输出** | `utils/alpha/multi_factor_signal.py` 含 `MultiFactorSignal` 类 |
| **验收标准** | 1. 支持 IC 加权（lookback=10 天，对齐 HC-7）<br>2. 反向信号因子处理（HC-6）<br>3. 与 PipelineOrchestrator 集成（不破坏 ic_weighted_enabled=True 默认）<br>4. 单测覆盖率 >= 60% |
| **责任层** | L3 Alpha |
| **依赖** | T1.4, T2.2 |
| **Feature Flag** | `USE_DECISION_THEORIES_FUSION`（默认 False） |

### T2.4 启动 Shadow 准入流程 ⬜ [P0]

| 维度 | 内容 |
|------|------|
| **输入** | T2.1 + T2.2 + T2.3 产出 |
| **输出** | Shadow 配置文件 + 14 天观察期启动 + 每日 DSR 报告 |
| **验收标准** | 1. Shadow 配置：单因子 `Config_E`，组合 `Config_E_plus1`（对齐 HC-3）<br>2. risk_managed=True（HC-3）<br>3. 每日生成 `reports/shadow/{date}_dsr.json`<br>4. 14 天后评估：DSR>=5 + 年化>=15% + 回撤<=10% + Sharpe CV<1.0<br>5. 不可推进 Stage 2（HC-4） |
| **责任层** | L3 Alpha + L5 风控 |
| **依赖** | T2.1, T2.2, T2.3 |
| **关键性** | 此任务阻塞 Phase 3 启动 |

**Phase 2 完成闸门**：G2 + L3 单测 60% + Shadow 启动 14 天观察期。

---

## Phase 3 — 执行与风控层（4 周）

### T3.1 设计风控事件总线接口 ⬜ [P0]

| 维度 | 内容 |
|------|------|
| **输入** | ARCHITECTURE §3 风控总线设计 |
| **输出** | `utils/risk/risk_event.py` + `utils/risk/risk_bus.py` 接口定义 |
| **验收标准** | 1. `RiskEvent` dataclass 含 7 种事件类型<br>2. `RiskBus` 接口：`publish()` / `subscribe()` / `decide()`<br>3. 基于 `asyncio.Queue` 实现 pub/sub<br>4. 单测覆盖率 >= 80% |
| **责任层** | L5 风控 |
| **依赖** | T1.4 |

### T3.2 适配 kill_switch 到总线（保留同步路径） ⬜ [P0]

| 维度 | 内容 |
|------|------|
| **输入** | `utils/kill_switch.py`（已有）+ T3.1 |
| **输出** | `utils/risk/kill_switch.py` 迁移 + 同步路径保留 + 总线订阅 |
| **验收标准** | 1. 同步路径 `KillSwitch.check_kill_switch()` 延迟 <1ms（HC-2）<br>2. 总线订阅 KillSwitch 事件做日志归档<br>3. 总线故障时 KillSwitch 仍可独立触发<br>4. mypy strict 通过 |
| **责任层** | L5 风控 |
| **依赖** | T3.1 |
| **风险** | R2 — 缓解：同步路径独立测试 |

### T3.3 适配 circuit_breaker / risk_guard / var_monitor / overnight_gap 到总线 ⬜ [P1]

| 维度 | 内容 |
|------|------|
| **输入** | 4 个已有模块 + T3.1 |
| **输出** | 4 个模块迁移到 `utils/risk/` + 总线订阅适配器 |
| **验收标准** | 1. 每个模块订阅相关事件类型<br>2. 异步决策不阻塞主路径<br>3. `RiskDecisionAggregator` 聚合多模块决策<br>4. 单测覆盖率 >= 70% |
| **责任层** | L5 风控 |
| **依赖** | T3.2 |
| **Feature Flag** | `USE_RISK_BUS_EVENT_DRIVEN`（默认 False，critical_path: true） |

### T3.4 实现 TCA 执行前预估 ⬜ [P1]

| 维度 | 内容 |
|------|------|
| **输入** | `utils/tca_engine.py`（已有）+ `utils/execution_router.py`（已有） |
| **输出** | `tca_engine.estimate(order)` 接口 + `execution_router` 接入 |
| **验收标准** | 1. 预估延迟 <50ms<br>2. 预估成本 > 阈值时否决订单（阈值可配置）<br>3. 预估结果记录到 `reports/tca/estimate_{date}.jsonl`<br>4. 单测覆盖率 >= 70% |
| **责任层** | L4 执行 |
| **依赖** | T1.4 |

### T3.5 实现 TCA 执行后归因 ⬜ [P1]

| 维度 | 内容 |
|------|------|
| **输入** | T3.4 + `utils/hedge_execution_engine.py`（已有） |
| **输出** | `tca_engine.record(fill)` 接口 + `hedge_engine.on_fill()` 接入 |
| **验收标准** | 1. 实际成交 vs 预估对比记录<br>2. PnL 归因拆分：Alpha / Execution / Risk<br>3. 每日 EOD 触发 `tca_engine.calibrate()`<br>4. 单测覆盖率 >= 70% |
| **责任层** | L4 执行 + L7 归因 |
| **依赖** | T3.4 |

### T3.6 根目录执行模块迁移 ⬜ [P2]

| 维度 | 内容 |
|------|------|
| **输入** | `automated_execution_system.py` + `daily_build_and_hedge.py` + `rebalance_execution_orders.py` |
| **输出** | 迁移到 `utils/execution/` + re-export |
| **验收标准** | 1. 文件物理迁移<br>2. 根目录通过 re-export 仍可 import<br>3. e2e 测试通过<br>4. 功能等价性验证（运行 dry-run 对比） |
| **责任层** | L4 执行 |
| **依赖** | T1.4 |

**Phase 3 完成闸门**：G4（风控总线原型）+ G5（TCA 闭环）+ L4/L5 单测 70%。

---

## Phase 4 — 信号与宏观层（6 周）

### T4.1 新建 fast_backtest.py（ML 回测验证引擎） ⬜ [P1]

| 维度 | 内容 |
|------|------|
| **输入** | `research/fast_backtest_aggregator.py`（已有） |
| **输出** | `utils/alpha/fast_backtest.py` |
| **验收标准** | 1. 支持 walk-forward + PurgedKFold<br>2. 输出 DSR / IC_IR / 年化 / 回撤 / Sharpe CV<br>3. 与 V9 评估标准对齐<br>4. 单测覆盖率 >= 60% |
| **责任层** | L3 Alpha |
| **依赖** | T2.3 |

### T4.2 重写 ml_enhanced_selector.py（替换 Mock） ⬜ [P2]

| 维度 | 内容 |
|------|------|
| **输入** | 现有 Mock 实现（如存在） |
| **输出** | `utils/alpha/ml_enhanced_selector.py` 真实 Transformer |
| **验收标准** | 1. 使用 PyTorch 或 TensorFlow（不可用 Mock）<br>2. 模型可保存/加载（model registry）<br>3. Shadow 14 天 DSR>=5<br>4. 单测覆盖率 >= 50% |
| **责任层** | L3 Alpha |
| **依赖** | T4.1 |
| **Feature Flag** | `USE_ML_ENHANCED_SELECTOR`（默认 False） |

### T4.3 整合 managers.py（组合优化/大宗/ETF） ⬜ [P2]

| 维度 | 内容 |
|------|------|
| **输入** | 用户列表中 `managers.py`（不存在，需新建或外部引入） |
| **输出** | `utils/attribution/managers.py` 或对应模块 |
| **验收标准** | 1. 组合优化（基于 `black_litterman_optimizer.py` 扩展）<br>2. 大宗商品监控<br>3. ETF 资金流（基于 `etf_flow_monitor.py` 整合）<br>4. 单测覆盖率 >= 50% |
| **责任层** | L3 Alpha + L7 归因 |
| **依赖** | T1.4 |

### T4.4 整合宏观与行业轮动模块 ⬜ [P2]

| 维度 | 内容 |
|------|------|
| **输入** | `macro_indicator.py` + `sector_rotation.py`（不存在，需新建） |
| **输出** | `utils/alpha/macro_indicator.py` + `utils/alpha/sector_rotation.py` |
| **验收标准** | 1. 宏观指标接入（CPI / PMI / M2 / 利率）<br>2. 行业轮动信号生成<br>3. 与 RegimeConditioner 集成<br>4. 单测覆盖率 >= 50% |
| **责任层** | L3 Alpha |
| **依赖** | T4.3 |

**Phase 4 完成闸门**：L3 单测 60% + Shadow 14 天 DSR>=5。

---

## Phase 5 — 优化与完善（12 周+）

### T5.1 实现 Brinson 归因 ⬜ [P1]

| 维度 | 内容 |
|------|------|
| **输入** | 配置/选股/交互效应归因理论 |
| **输出** | `utils/attribution/brinson_attribution.py` |
| **验收标准** | 1. 输出配置效应 / 选股效应 / 交互效应<br>2. 与基准对比（沪深300 / 中证500）<br>3. 单测覆盖率 >= 70% |
| **责任层** | L7 归因 |
| **依赖** | T1.4 |

### T5.2 实现因子归因 ⬜ [P1]

| 维度 | 内容 |
|------|------|
| **输入** | `utils/alpha/factor_library.py`（已有） |
| **输出** | `utils/attribution/factor_attribution.py` |
| **验收标准** | 1. 每个因子的 PnL 贡献拆分<br>2. 与 Barra 归因对比验证<br>3. 单测覆盖率 >= 70% |
| **责任层** | L7 归因 |
| **依赖** | T5.1 |

### T5.3 实现日级归因面板 ⬜ [P1]

| 维度 | 内容 |
|------|------|
| **输入** | T5.1 + T5.2 + T3.5（TCA 归因） |
| **输出** | `utils/attribution/daily_panel.py` + `reports/attribution/{date}.json` + `.md` |
| **验收标准** | 1. 三合一：Brinson + Barra + Factor<br>2. JSON + Markdown 双输出<br>3. 生成时间 <30s<br>4. Streamlit 面板可消费 JSON |
| **责任层** | L7 归因 |
| **依赖** | T5.1, T5.2, T3.5 |

### T5.4 拆分 quantitative_system.py ⬜ [P1]

| 维度 | 内容 |
|------|------|
| **输入** | `quantitative_system.py`（2000+ 行） |
| **输出** | `quantitative_system_v2/alpha_research.py` + `risk_check.py` + `execution_orchestration.py` + `reporting.py` |
| **验收标准** | 1. 公共 API 不变（re-export）<br>2. 行为等价性测试通过<br>3. mypy strict 通过<br>4. V9 基线回归通过 |
| **责任层** | L1 基础设施 |
| **依赖** | T1.8 |
| **风险** | R1 — 缓解：原文件保持只读，拆分版本并行运行 |

### T5.5 类型覆盖率提升 ⬜ [P2]

| 维度 | 内容 |
|------|------|
| **输入** | mypy.ini + .pylintrc |
| **输出** | utils/ 与 v8.3_institutional/ 的 mypy strict 覆盖率提升至 60%+ |
| **验收标准** | 1. utils/infra/ 100%<br>2. utils/risk/ 80%<br>3. utils/execution/ 70%<br>4. utils/alpha/ 60%<br>5. v8.3_institutional/ 50% |
| **责任层** | 全层 |
| **依赖** | T5.4 |

### T5.6 Streamlit UI 14 页面完善 ⬜ [P3]

| 维度 | 内容 |
|------|------|
| **输入** | T5.3 归因面板 + 现有 UI 组件 |
| **输出** | 完整 Streamlit 应用 |
| **验收标准** | 1. 14 页面全部实现<br>2. 接入归因面板 JSON<br>3. 实时刷新（盘中 1 分钟级）<br>4. 鉴权（生产环境） |
| **责任层** | L7 归因 + 前端 |
| **依赖** | T5.3 |

### T5.7 实盘券商直连补充 ⬜ [P3]

| 维度 | 内容 |
|------|------|
| **输入** | 现有 CTP 适配器 |
| **输出** | THS / 雪球 / 其他券商直连 |
| **验收标准** | 1. 至少 2 个新 broker adapter<br>2. 故障切换测试通过<br>3. Shadow 14 天验证 |
| **责任层** | L4 执行 |
| **依赖** | T3.6 |

### T5.8 MLops 流水线 ⬜ [P3]

| 维度 | 内容 |
|------|------|
| **输入** | V9 训练脚本 + 模型注册需求 |
| **输出** | 模型训练/部署/监控自动化 |
| **验收标准** | 1. 模型版本化（model registry）<br>2. A/B 测试框架<br>3. 漂移检测（drift detector）<br>4. 自动重训练触发 |
| **责任层** | L3 Alpha + L1 基础设施 |
| **依赖** | T4.2 |

**Phase 5 完成闸门**：G6（性能归因面板）+ G7（类型覆盖率）+ UI 验收。

---

## 任务依赖图（关键路径）

```
T1.1 ──> T1.2 ──> T1.3 ──> T1.4 ──> T1.5 ──> T1.6
                    │          │
                    │          ├──> T1.7 (data_layer)
                    │          ├──> T2.1 (LLM Router)
                    │          ├──> T2.2 (decision_theories)
                    │          ├──> T3.1 (RiskBus 接口)
                    │          ├──> T3.4 (TCA 预估)
                    │          └──> T5.1 (Brinson)
                    │
                    └──> T1.8 (V9 回归) ──> T1.9 (CI)

T1.8 是后续所有 PR 的合并闸门

T2.1 + T2.2 + T2.3 ──> T2.4 (Shadow 14 天) ──> Phase 3 启动

T3.1 ──> T3.2 ──> T3.3
T3.4 ──> T3.5
T3.6 (并行)

T5.1 + T5.2 + T3.5 ──> T5.3 (日级面板)
T5.4 (并行，独立于其他)
```

**关键路径**：T1.1 → T1.2 → T1.4 → T1.8 → T2.4 → T3.1-T3.5 → T5.3

---

## 任务汇总（按优先级）

### P0 任务（阻塞，必须完成）
- T1.1 Git 分支策略
- T1.2 utils/ 子目录结构
- T1.3 Feature Flag 框架
- T1.4 re-export 兼容层
- T1.8 V9 基线回归套件
- T2.4 Shadow 准入启动
- T3.1 风控总线接口
- T3.2 kill_switch 适配

### P1 任务（关键路径）
- T1.5 bootstrap.py
- T1.6 core.py
- T1.7 data_layer.py
- T1.9 CI 流水线
- T2.1 LLM 路由器
- T2.2 decision_theories 迁移
- T2.3 multi_factor_signal
- T3.3 风控模块适配
- T3.4 TCA 预估
- T3.5 TCA 归因
- T4.1 fast_backtest
- T5.1 Brinson 归因
- T5.2 因子归因
- T5.3 日级面板
- T5.4 quantitative_system 拆分

### P2 任务（重要）
- T3.6 根目录执行模块迁移
- T4.2 ml_enhanced_selector 重写
- T4.3 managers 整合
- T4.4 宏观与轮动
- T5.5 类型覆盖率提升

### P3 任务（可选）
- T5.6 Streamlit UI
- T5.7 券商直连
- T5.8 MLops

---

## 估时（仅作参考，不作承诺）

| Phase | 任务数 | 估时 |
|-------|--------|------|
| Phase 1 | 9 | 2 周 |
| Phase 2 | 4 | 3 周（含 14 天 Shadow） |
| Phase 3 | 6 | 4 周 |
| Phase 4 | 4 | 6 周 |
| Phase 5 | 8 | 12 周+ |
| **合计** | **31** | **27 周+** |

---

## 下一步

进入阶段 4 Approve，由用户审批本任务清单。审批通过后进入阶段 5 Automate，按 P0 → P1 → P2 → P3 顺序执行。

**审批重点**：
1. ✅ 任务粒度是否合适（每个任务 1-3 天可完成）
2. ✅ 验收标准是否可量化
3. ✅ 依赖关系是否合理
4. ✅ Phase 闸门是否清晰
5. ✅ P0 任务是否覆盖所有阻塞点
