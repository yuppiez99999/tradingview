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

## 4. v8.7 验收清单状态

| 验收项 | 状态 | 说明 |
|---|---|---|
| Phase B 4 flag 稳定≥30天 | ⏳ 待达标 | Phase B shadow 稳定天数 0/7 |
| T15-T18 | 待评估 | |
| S6+S7 | 待评估 | |
| G7 覆盖率 0.80 | ⏳ 0.6855 | Sprint 1 目标 0.55 已达标 |
| G9 FeatureStore | 待评估 | |
| 工程基础层 Phase 0-3 | 待评估 | |
| 门禁 21 天 0 FAIL | ⏳ 观察期 | bandit 门禁已接入 |
| 影子账户 2 周稳定 | ⏳ 待达标 | |

## 5. 待办项

- [ ] Phase B shadow 稳定运行 7 天（Sprint 1 门禁阻断项）
- [ ] 覆盖率推进 0.6855→0.80（Sprint 4 目标）
- [ ] 超大文件拆分（automated_execution_system.py / institutional_pipeline_runner.py）
- [ ] mypy strict 验证（mypy 2.3.1 内部错误待解决）
- [ ] v8.7 验收清单剩余项评估