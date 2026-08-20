# v8.7 Release Notes (Draft — 2026-08-20)

> 状态：Sprint 1 进行中 (3/4 门禁达标)
> 目标发布日期：2026-12-31

## 1. 代码质量修复成果

### 1.1 安全扫描 (bandit)
- B104 中危修复：`qmt_rpc_server.py` host 默认 "0.0.0.0"→"127.0.0.1"（本机绑定）
- B101 低危修复：`kill_switch_manager.py` 4 处 assert→raise（防 -O 优化移除风控校验）
- B105/B107/B110 低危：nosec 注释说明（枚举值/默认空值/fail-safe）
- **结果**：bandit 中危 1→0，低危 7→0

### 1.2 代码风格 (ruff)
- SIM 自动修复 96 项（SIM300/SIM114/SIM118 等零风险项）
- N806/N803 金融符号豁免 30→0（T/R/X/S/K 行业惯例）
- N818 异常命名修复：`BrokerLiveModeDisabled`→`BrokerLiveModeDisabledError`
- ANN 类型注解非核心模块豁免 290→93
- C901 复杂度非核心模块豁免 70→37
- **结果**：ruff 总违规 434→169（61% 降幅）

### 1.3 CI 门禁
- bandit 安全门禁接入 quality-gate.yml（中危即 FAIL）
- 工作区变更数门禁 ≤100 文件

## 2. Phase B 可观测性

### 2.1 结构化日志 (structlog)
- `utils/observability/structured_logger.py`：structlog JSON 后端 + 标准logging回退
- `bind()` 上下文绑定支持

### 2.2 事件 Schema (pydantic v2)
- `utils/observability/event_schema.py`：
  - `OrderEvent`：订单事件（order_id/symbol/side/qty/price/filled_qty）
  - `RiskEvent`：风控事件（risk_level/trigger/action）
  - `ExecutionEvent`：执行事件（phase/duration_ms）
  - `PipelineEvent`：管线事件（pipeline_name/step/step_index）

### 2.3 性能基准 (pytest-benchmark)
- `tests/perf/test_phase_b_benchmark.py`：6 个关键路径基准
  - KillSwitch 风控检查：2,327 Kops/s
  - Event Schema 序列化：268-308 Kops/s
  - TradingCalendar 交易日判断：174 Kops/s
  - StructuredLogger info 输出：25 Kops/s

## 3. Phase C 战略级

### 3.1 分布式追踪 (OpenTelemetry)
- `utils/observability/tracing.py`：
  - `trace_order()`：订单链路 span 埋点
  - `trace_risk()`：风控链路 span 埋点
  - `trace_pipeline()`：管线链路 span 埋点
- 当前 ConsoleSpanExporter，后续可替换为 OTLPExporter

### 3.2 mypy strict 推进
- `utils/observability.*`：全 strict（disallow_any_generics + warn_return_any + disallow_untyped_defs）
- `utils.risk.*`：启用 disallow_any_generics

## 4. v8.7 发布三门禁冲刺 (2026-08-20)

### 4.1 D9 覆盖率 Sprint4 0.80 门禁
- `_check_d9_coverage_sprint4_target()` 注册到 engineering_debt_gate.py (阻断 RED)
- 当前 line_rate=0.6855 < 0.80, 门禁未达标 (Sprint 4 目标)
- 检出器: `_detect_lookahead_tests.py` (前视偏差, 检出90处) + `_detect_mock_inflation.py` (mock虚增) + `_detect_coverage_stagnation.py` (停滞检测)
- `_check_coverage_trend.py --min-line-rate` 0.05→0.80

### 4.2 D10 超大文件拆分门禁
- `_check_d10_oversized_file_split()` 注册 (阻断 RED, ≤2000行)
- institutional_pipeline_runner.py 2620行 + automated_execution_system.py 2691行, 均未达标
- 拆分待非交易时段执行 (A股交易时段 9:30-15:00 禁止)

### 4.3 D11 Phase B shadow 7天稳定门禁
- `_check_d11_phase_b_shadow_stable()` 注册 (阻断 RED)
- PhaseBStatus 扩展: consecutive_stable_days / stable_days_target / min_shadow_samples / daily_health_log
- DailyHealthVerdict + evaluate_daily_shadow_health (三维健康判定) + update_stable_days (异常归零)
- 当前 0/7 天稳定, 0/20 样本, 门禁未达标

### 4.4 V87GateSummary 三门禁汇总
- `V87GateSummary` frozen dataclass + `check_v87_release_gate_summary()` 聚合 D9+D10+D11
- `reports/v87_release_gate_summary.json` 阻断/放行判定输出
- 当前判定: [BLOCK] v8.7 发布阻断 (三门禁均未达标)

### 4.5 测试覆盖
- test_phase_b_shadow_stable.py: 20 passed (Phase B shadow 守卫核心逻辑)
- test_coverage_sprint4_gate.py: 19 passed (覆盖率守卫门禁)
- test_v87_release_gate_summary.py: 12 passed (三门禁汇总端到端)

### 4.6 CI 门禁配置
- quality-gate.yml 追加 Engineering debt gate 步骤 (D9+D10+D11+v8.7 summary)
- 退出码 2 阻断合并, 0 放行

## 5. v8.7 验收清单状态

| 验收项 | 状态 | 说明 |
|---|---|---|
| D9 覆盖率 0.80 | ❌ 0.6855 | Sprint 4 目标, 差 0.1145 |
| D10 超大文件 ≤2000行 | ❌ 2620+2691 | 待非交易时段拆分 |
| D11 PhaseB 7天稳定 | ❌ 0/7天 | shadow 未启动 |
| v8.7 汇总判定 | ❌ BLOCK | 三门禁均未达标 |
| Phase B 4 flag 稳定≥30天 | ⏳ 待达标 | 依赖 D11 |
| T15-T18 | ✅ 通过 | 实盘四件套模块自检 |
| G7 覆盖率 Sprint1 0.55 | ✅ 0.6855 | 已达标 |
| 门禁 21 天 0 FAIL | ⏳ 观察期 | bandit+debt gate 已接入 |

## 6. 待办项

- [ ] 任务2: 超大文件拆分 (非交易时段执行, 15:00 后)
- [ ] 任务3.6: 覆盖率补测 P0-P2 链路 → 0.80
- [ ] 任务5.2: 超大文件拆分执行器单元测试
- [ ] Phase B shadow 稳定运行 7 天 (D11 达标前提)
- [ ] mypy strict 验证 (mypy 2.3.1 内部错误待解决)
- [ ] v8.7 验收清单剩余项评估