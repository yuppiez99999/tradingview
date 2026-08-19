# ai_decision 路线图逐步推进实施计划

## Context

**为什么做这个：** `ai_decision/` 包（多 AI 辩论共识决策系统）已完成阶段二主体（`execution_bridge.py` 闭环 + L2 复用 L1 风控），但路线图 [docs/ai_decision_roadmap_later_stages.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/docs/ai_decision_roadmap_later_stages.md) 阶段二仍有 4 项缺口，阶段一/三/五也未启动。用户希望"先设计计划再逐步推进"，按 6A 工作流产出可执行的实施路径。

**当前状态：**
- ✅ `execution_bridge.py` 已实现 shadow/paper/auto 三模式 + 灰度状态机 + L2 复用 L1 `run_hard_risk`
- ✅ `decision_gate.run_hard_risk()` 已实现 5 项检查（黑名单/RiskAgent否决/涨跌停/单笔上限/日内累计）
- ✅ `TradingDecision` 已有 `escalation`/`escalation_reason` 字段（[models.py:225-226](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/models.py#L225-L226)）
- ✅ `utils/tca_pre_trade_estimator.py` 的 `PreTradeEstimator.estimate()` 已就绪
- ✅ `utils/tca_engine.py` 的 `TCAManager.analyze()` 已就绪
- ⚠️ Shadow 账户运行 1/14 天（项目记忆硬约束，未满期不可推进灰度）
- ⚠️ `execute_decision()` 5 个失败分支均未写入 `escalation`（治理漏洞）

**预期产出：** 按 7 步路径逐步推进，每步独立可执行、可测试、可回滚，最终把"能跑通的 Shadow 决策中台"演进为"可灰度上线的可信自动执行系统"。

---

## 7 步路线图总览

| 步骤 | 名称 | 优先级 | 依赖 | 复杂度 | 状态 |
|------|------|--------|------|--------|------|
| 1 | C. 升级人工确认逻辑（escalation 回写） | P0 最高 | 无 | 低 | ✅ 已完成 |
| 2 | B. TCA Implementation Shortfall 集成 | P1 高 | 步骤 1 | 中 | ✅ 已完成 |
| 3 | 阶段一：模型健康检查 + 熔断器 | P1 高 | 无（可与 2 并行） | 中 | ✅ 已完成 |
| 4 | 阶段一：延迟/成本基准看板 | P2 中 | 步骤 3 | 中 | ✅ 已完成 |
| 5 | D. 灰度自动推进（3天/1周） | P3 暂缓 | Shadow 满 14 天 | 低 | ✅ 代码就绪 (等 Shadow 14 天自动激活) |
| 6 | 阶段三：历史回放 + 基线对比 | P2 中 | 步骤 1、3 | 高 | ✅ 已完成 |
| 7 | 阶段五：审计日志自动复盘 | P2 中 | 步骤 1 | 中 | ✅ 已完成 |

**执行顺序：** `1 → 2/3 并行 → 4 → 6 → 7 →（5 待 Shadow 满 14 天）`

**依赖关系图：**
```
步骤 1 (escalation 回写) ──┬─→ 步骤 2 (TCA 集成)
                           ├─→ 步骤 7 (审计复盘)
                           └─→ 步骤 6 (历史回放) ──← 步骤 3 (熔断器)
步骤 3 (熔断器) ────────────→ 步骤 4 (成本看板)
步骤 5 (灰度推进) ────────── 暂缓, 等 Shadow 14 天
```

---

## 步骤 1：C. 升级人工确认逻辑（escalation 回写）

### 目标
让 `execute_decision()` 的 5 个失败分支全部输出 `escalation=True/escalation_reason=...`，通过 `cli.py` 同步回写 `TradingDecision`，确保所有执行失败都进入人工复核队列。

### 改动文件
- [ai_decision/execution_bridge.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/execution_bridge.py) — 5 个失败分支补 escalation；返回 dict 增加 `veto`/`veto_reason`/`escalation`/`escalation_reason` 4 字段
- [ai_decision/cli.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/cli.py) — 完善同步逻辑（line 96-99），顺带修复 `dec.veto` 被覆盖为 False 的预存 bug
- [tests/unit/test_execution_bridge.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/tests/unit/test_execution_bridge.py) — 新增 5 个分支的 escalation 测试

### 关键设计

**5 个失败分支映射：**

| 分支 | 位置 | veto | escalation | escalation_reason 模板 |
|------|------|------|------------|----------------------|
| 1. L2 风控否决 | line 395-415 | True | True | `f"L2 风控否决: {risk_result.veto_reason}"` |
| 2. 灰度回滚到 0 | line 442-461 | True | True | `f"灰度回滚至 {new_stage}, 暂停执行: {rb_reason}"` |
| 3. 下单失败 | line 484-485 | False | True | `f"下单失败: {execution_result.get('error', 'broker 拒单')}"` |
| 4. 下单异常 | line 487-490 | False | True | `f"下单异常: {exc}"` |
| 5. 未知模式 | line 491-492 | False | True | `f"未知模式 {mode}, 按 shadow 处理"` |

**设计原则：** `veto` 是硬阈值（风控直接拦截），`escalation` 是软阈值（需人工确认）。分支 3/4/5 不设 `veto=True` 但 `escalation=True`，可区分"硬风控拦截"和"执行异常"。

**返回 dict 扩展（4 字段）：**
```python
{
    # 原有 7 字段不变
    'executed', 'mode', 'execution_plan', 'execution_result',
    'risk_result', 'audit_path', 'message',
    # 新增
    'veto': bool,                  # 风控否决
    'veto_reason': str,            # 否决原因
    'escalation': bool,            # 需人工确认
    'escalation_reason': str,      # 升级原因
}
```

**cli.py 同步逻辑修复（line 96-99）：**
```python
# 修复前 (bug): dec.veto = execution_result.get("veto", False)  ← 覆盖 apply_mode 已设的 veto
# 修复后:
dec.executed = result.get("executed", dec.executed)
dec.veto = result.get("veto", dec.veto)                              # 保留 L1 已设 veto
dec.veto_reason = result.get("veto_reason") or dec.veto_reason
dec.escalation = result.get("escalation", dec.escalation)            # 新增
dec.escalation_reason = result.get("escalation_reason") or dec.escalation_reason  # 新增
```

### 复用现有
- `ai_decision.models.TradingDecision`（escalation 字段已就绪，无需改 dataclass）
- `ai_decision.decision_gate.apply_mode()`（已设置硬风控否决的 escalation）
- `ai_decision.execution_bridge._write_execution_audit()`（审计写入）

### 验收标准
1. 5 个失败分支单测：构造触发条件，断言 `result["escalation"] is True` 且 `escalation_reason` 非空
2. 成功分支不 escalation：`execute_decision(dec, mode="paper")` 返回 `escalation=False`
3. 端到端：`python -m ai_decision.cli --symbol 600519 --mode auto --execute --blacklist 600519` 后 `dec.escalation=True` 且 `dec.escalation_reason` 含"黑名单"
4. 审计 JSONL：`reports/ai_decision/execution/exec_*.jsonl` 含 `escalation` 字段
5. 回归：现有 `test_execute_decision_*` 测试全部通过

### 风险
- cli.py 修改后若 `execute_decision` 未返回 escalation 键，`dec.escalation` 会被覆盖。用 `dec.escalation` 自身做默认值规避（`result.get("escalation", dec.escalation)`）

---

## 步骤 2：B. TCA Implementation Shortfall 集成

### 目标
在 `execute_decision()` 中接入 TCA 双轨：执行前用 `PreTradeEstimator` 预筛（高成本订单升级人工），执行后用 `TCAManager.analyze()` 归因（写入审计）。Feature Flag 控制 + 异常隔离。

### 改动文件
- [ai_decision/execution_bridge.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/execution_bridge.py) — 插入预筛 hook（Step B 后）+ 事后归因 hook（执行结果产出后）
- [v8.3_institutional/config/feature_flags.yaml](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/config/feature_flags.yaml) — 新增 `USE_AI_DECISION_TCA_PRE_TRADE` / `USE_AI_DECISION_TCA_POST_TRADE` 两个独立 flag
- [tests/unit/test_execution_bridge.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/tests/unit/test_execution_bridge.py) — TCA 集成测试

### 关键设计

**集成点：两者都做（预筛 + 事后归因）**
- 预筛：Step B（L2 风控）之后、Step C（按模式分派）之前 → 下单前拦截高成本订单
- 事后归因：仅 `auto`/`paper` 模式真实下单成功后 → 写入审计

**API 签名扩展：**
```python
def execute_decision(
    decision, portfolio_value, price, market_state,
    order_router, broker, force_mode, risk_context,
    # 步骤 2 新增
    tca_pre_trade_estimator: Optional[Any] = None,    # 注入 PreTradeEstimator
    tca_post_trade_manager: Optional[Any] = None,     # 注入 TCAManager
    market_data_for_tca: Optional[Dict[str, Any]] = None,  # {adv, volatility, market_cap, vwap, decision_price}
) -> Dict[str, Any]:
```

返回 dict 新增：`tca_pre_estimate` / `tca_post_report` / `tca_error`

**Feature Flag 透传（fail-safe）：**
```python
def _ai_decision_tca_pre_trade_enabled() -> bool:
    try:
        from utils.infra.feature_flags import is_enabled
        return bool(is_enabled("USE_AI_DECISION_TCA_PRE_TRADE"))
    except Exception:
        return False  # 默认关闭
```

**TCA 预筛否决是软阈值（escalation 而非 veto）：**
- TCA 阈值（30bps）是经验值，硬 veto 会阻断所有小盘股交易
- 预筛否决时 `escalation=True`，`escalation_reason="TCA 预筛否决: {reason}"`，但 `executed` 仍可为 True
- 由人工决定是否覆盖

**异常隔离（不阻断主路径）：**
```python
try:
    estimate = estimator.estimate(tca_order, market_data_for_tca)
    result_dict["tca_pre_estimate"] = estimate.to_dict()
    if not estimate.approved:
        result_dict["escalation"] = True
        result_dict["escalation_reason"] = f"TCA 预筛否决: {estimate.rejection_reason}"
except Exception as exc:
    logger.error("[ExecutionBridge] TCA 预筛异常 (降级为不预估): %s", exc)
    result_dict["tca_error"] = str(exc)
    # 主路径继续
```

**PreTradeEstimator 调用所需的 order_dict 适配：**
```python
tca_order = {
    "symbol": execution_plan["symbol"],
    "side": execution_plan["side"],
    "shares": execution_plan["qty"],
    "price": execution_plan["limit_price"],
    "notional": execution_plan["notional"],
    "market_cap": market_data_for_tca.get("market_cap") if market_data_for_tca else None,
}
```

### 复用现有
- `utils.tca_pre_trade_estimator.PreTradeEstimator.estimate()` ([line 191-328](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/tca_pre_trade_estimator.py#L191))
- `utils.tca_engine.TCAManager.analyze()` ([line 142](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/tca_engine.py#L142))
- `utils.tca_engine.FillRecord` / `BenchmarkPrices` / `TCAReport`
- `utils.infra.feature_flags.is_enabled()`（已就绪）
- 参考实现：`utils.execution_router._tca_pre_trade_enabled()` 模式

### 验收标准
1. Flag 关闭（默认）：行为与步骤 1 完全一致，`tca_pre_estimate=None`
2. Flag 开启 + 预筛通过：`tca_pre_estimate` 非空且 `approved=True`
3. Flag 开启 + 预筛否决：`escalation=True`，`escalation_reason` 含"TCA 预筛否决"，但 `executed` 仍可为 True
4. Flag 开启 + TCA 异常：主路径不阻断，`tca_error` 非空
5. 事后归因：`mode=paper` 模拟成交后 `tca_post_report` 含 `is_cost_bps`、`quality_grade`
6. 持久化：`reports/tca/estimate_*.jsonl` 由 PreTradeEstimator 自动写入

### 风险
- `market_data_for_tca` 在 shadow 模式可能缺失（无真实行情）→ 降级：缺失时跳过 TCA 预筛，仅记日志
- `TCAManager.analyze()` 要求 `fills` 非空 → 通过 `execution_result.success` 条件规避（shadow 模式不调用）

---

## 步骤 3：阶段一 — 模型健康检查 + 熔断器

### 目标
新建 `ai_decision/health.py`，封装模型 liveness probe + 熔断器，复用 `v8.3_institutional/src/ai/model_router.py` 的 `CircuitBreaker`。失败自动降级 MockProvider。

### 改动文件
- `ai_decision/health.py` — **新建**，含 `ModelHealthMonitor` 类 + `HealthStatus` dataclass
- [ai_decision/orchestrator.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/orchestrator.py) — `run_decision()` 开头调用 `health_monitor.check(role)` 探测
- [ai_decision/providers.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/providers.py) — `get_active_provider()` 增加 `health_monitor` 参数，熔断时强制返回 MockProvider
- `tests/unit/test_ai_decision_health.py` — **新建**

### 关键设计

**ModelHealthMonitor 接口：**
```python
class ModelHealthMonitor:
    def __init__(self, config_path: Optional[str] = None):
        self._router = ModelRouter(config_path)  # 复用其 circuit_breakers + get_stats()

    def check(self, role: str, timeout: float = 2.0) -> HealthStatus:
        """单次 liveness probe: 调用 provider.generate() 一次轻量 prompt"""

    def is_circuit_open(self, role: str) -> bool:
        """检查 role 对应 provider 的熔断器是否开启"""

    def get_provider_with_fallback(self, role: str) -> BaseProvider:
        """熔断开启时返回 MockProvider, 否则返回 get_active_provider(role)"""

    def get_stats(self) -> Dict[str, Any]:
        """透传 ModelRouter.get_stats() — 供步骤 4 看板消费"""
```

**熔断器参数（沿用 CircuitBreaker 默认）：**
- `max_failures=3`（连续失败 3 次熔断）
- `cooldown_seconds=300`（5 分钟冷却后尝试恢复）

**集成点：** `orchestrator.run_decision()` 开头探测 bull/bear/judge 三个角色

### 复用现有
- `v8.3_institutional.src.ai.model_router.CircuitBreaker`
- `v8.3_institutional.src.ai.model_router.ModelRouter._is_circuit_open()` / `get_stats()`
- `ai_decision.providers.MockProvider` / `get_active_provider()`

### 验收标准
1. 健康检查通过：`health_monitor.check("bull")` 返回 `healthy=True`、`latency_ms<2000`
2. 熔断触发：连续 3 次失败后 `is_circuit_open("bull")` 返回 True
3. 自动降级：熔断时 `get_provider_with_fallback("bull")` 返回 MockProvider
4. 冷却恢复：`cooldown_seconds` 后 `try_reset()` 返回 True
5. 端到端：`run_decision(symbol, health_monitor=mon)` 不因模型故障崩溃

### 风险
- liveness probe 消耗真实 API 调用（成本）→ 配置 `probe_interval_seconds=60`，避免每决策都探测

---

## 步骤 4：阶段一 — 延迟/成本基准看板

### 目标
基于步骤 3 的 `ModelHealthMonitor.get_stats()` + 步骤 2 的 TCA 记录，输出每日延迟/成本基准报告（Markdown + JSON），超预算自动降级 Mock。

### 改动文件
- `ai_decision/dashboard.py` — **新建**，含 `generate_daily_dashboard()` / `check_budget_alert()` / `maybe_degrade_on_budget()`
- `scripts/run_ai_decision_dashboard.py` — **新建**，CLI 入口（每日 EOD 触发）
- [v8.3_institutional/config/ai_decision.yaml](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/config/ai_decision.yaml) — 新增 `budget` 段

### 关键设计

**看板数据源：**
1. `ModelHealthMonitor.get_stats()` → 模型延迟/成功率/熔断状态
2. `reports/tca/estimate_*.jsonl` → TCA 预估成本历史
3. `reports/ai_decision/audit_*.jsonl` → 决策模式分布、辩论触发率
4. `reports/ai_decision/execution/exec_*.jsonl` → 执行结果、escalation 分布

**预算熔断阈值（ai_decision.yaml.budget）：**
- `monthly_token_limit: 5_000_000`
- `per_call_latency_p99_ms: 5000`
- `daily_cost_limit_usd: 10.0`

### 复用现有
- `ai_decision.health.ModelHealthMonitor.get_stats()`（步骤 3 产物）
- `utils.tca_pre_trade_estimator.PreTradeEstimator.get_history()`

### 验收标准
1. `generate_daily_dashboard("2026-07-27")` 返回完整 dict，无 KeyError
2. Markdown 报告含 5 章节（模型/TCA/决策/执行/告警）
3. 预算超限时 `maybe_degrade_on_budget()` 返回对应 role 降级标记
4. 落盘：`reports/ai_decision/dashboard_{date}.md` + `.json`

### 风险
- 审计 jsonl 格式因步骤 1 增加 escalation 字段而变化，看板解析需向后兼容

---

## 步骤 5：D. 灰度自动推进（3天/1周）— 暂缓

### 暂缓理由
**硬约束：** 项目记忆明确"Shadow 账户需运行满 14 天最小周期，当前仅运行 1 天"。提前推进违反路线图总目标第 3 条"任何上线必须走影子→模拟→自动灰度三阶段"。

### 待 Shadow 满 14 天后的设计

**推进条件矩阵：**

| 当前阶段 | 推进条件 | 下一阶段 |
|---------|---------|---------|
| shadow | 跑满 14 天 + 日均决策数 ≥ 10 | paper |
| paper | 跑满 3 天 + 模拟成交率 ≥ 95% + 无 escalation 风险 | auto_10 |
| auto_10 | 跑满 3 天 + 累计 PnL > 0 + 无回滚 | auto_50 |
| auto_50 | 跑满 7 天 + 累计 PnL > 0 + 无回滚 | auto_100 |

**14 天硬约束检查：**
```python
def advance_grayscale(daily_pnl: float) -> Dict[str, Any]:
    gs = GrayscaleState.load()
    if gs.stage == "shadow":
        shadow_days = _calc_shadow_days(gs)
        if shadow_days < 14:
            return {"current_stage": "shadow", "days_to_advance": 14 - shadow_days}
    # 原逻辑...
```

### 验收标准（待实施）
1. Shadow < 14 天时不推进
2. Shadow = 14 天 + 决策数达标时推进到 paper
3. 回滚触发时 `do_rollback()` 退回上一阶段

---

## 步骤 6：阶段三 — 历史回放 + 基线对比

### 目标
用历史行情/新闻/研报快照回放 `run_decision()` 全链路，对比三条基线（AI 辩论 / 五 Agent 投票 / 纯规则），验证 AI 增量 IC 与边际夏普。

### 改动文件
- `ai_decision/backtest_replay.py` — **新建**，含 `replay_history()` / `compare_baselines()`
- `scripts/run_ai_decision_backtest.py` — **新建**，CLI 入口
- `tests/integration/test_ai_decision_replay.py` — **新建**

### 关键设计

**三条基线：**
| 基线 | 实现方式 |
|------|---------|
| `ai_debate` | 完整 `run_decision()`（含辩论+聚合+风控） |
| `five_agents` | 跳过辩论，仅用 `FinanceAgentOrchestrator` 共识 |
| `rule_only` | 跳过 AI，仅用规则兜底分支 |

**偏差防控（严守项目记忆规则）：**
- 用实际财报披露日而非报告期截止日
- 逐日成分股快照防幸存者偏差
- 停牌冻结不可交易、涨跌停不可成交
- 调仓日信号至少滞后一期

**验收门槛（roadmap line 46）：**
- 后半段 CAGR ≥ 前半段 60%
- 每月 IC 显著为正
- 辩论增量可量化（边际夏普 > 0.05 才值得上线）

### 复用现有
- `ai_decision.orchestrator.run_decision()`
- `ai_decision.orchestrator._run_five_agents()`（规则兜底）
- `v8.3_institutional.src.backtest.walk_forward`（参考回测框架）

### 验收标准
1. 回放 1 年数据无前视偏差（财报披露日校验通过）
2. 三条基线均产出完整决策序列
3. 输出报告含 IC/夏普/命中率/辩论触发率
4. 边际夏普 > 0.05 才建议上线 auto 模式

### 风险
- 历史 RAG 数据（新闻/研报）快照完整性不足会引入前视偏差
- 回放期间真实 API 调用成本高 → 优先用 MockProvider 跑

---

## 步骤 7：阶段五 — 审计日志自动复盘

### 目标
每日 EOD 自动解析 `reports/ai_decision/*.jsonl`，生成结构化复盘摘要（命中率、辩论有效性、escalation 分布、TCA 表现），落盘 Markdown + 推送告警。

### 改动文件
- `ai_decision/eod_review.py` — **新建**，含 `generate_eod_review()` / `_aggregate_audit_logs()`
- `scripts/run_ai_decision_eod.py` — **新建**，CLI 入口（每日收盘后触发）
- [v8.3_institutional/config/ai_decision.yaml](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/config/ai_decision.yaml) — 新增 `alerts` 段

### 关键设计

**复盘 5 维度：**
1. 决策分布：action / verdict_type / mode 分布
2. 辩论有效性：触发率、辩论后置信度提升、False Positive 率
3. 风控拦截：veto 原因 Top 5、escalation 原因 Top 5
4. 执行质量：成功率、下单延迟分布、TCA 评级分布
5. 异常检测：模型连续失败、Brier 跌破阈值、auto 异常放量

**告警规则（roadmap line 63）：**
| 规则 | 阈值 | 严重级别 |
|------|------|---------|
| 模型连续失败 | ≥ 3 次 | WARNING |
| Brier 跌破阈值 | < 0.25 | CRITICAL |
| auto 模式异常放量 | 单日 > 50 笔 | CRITICAL |
| 硬风控拦截突增 | 同比 > 200% | WARNING |
| TCA 评级 D/F 占比 | > 20% | WARNING |

**与 realtime_monitor 集成（5 秒触达 On-Call）：** 复用现有 `realtime_monitor/` 告警通道，try-except 隔离

### 复用现有
- `ai_decision.health.ModelHealthMonitor.get_stats()`（步骤 3）
- `utils.tca_pre_trade_estimator.PreTradeEstimator.get_history()`
- `realtime_monitor/`（告警通道）

### 验收标准
1. `generate_eod_review("2026-07-27")` 输出 Markdown + JSON 双格式
2. 复盘报告含 5 维度
3. 告警触发：构造 `Brier < 0.25` 场景，断言告警列表含 CRITICAL 项
4. 落盘：`reports/ai_decision/eod_review_{date}.md` 存在且非空

### 风险
- 审计 jsonl 行数过多时聚合性能 → 限制单次复盘日期范围 ≤ 7 天
- `realtime_monitor` 接口变化 → 告警推送 try-except 隔离

---

## 关键架构决策汇总

1. **escalation 是软阈值，veto 是硬阈值** — TCA 预筛否决、下单失败等用 escalation（人工确认），仅 L1/L2 硬风控用 veto（直接拦截）。避免 TCA 误判阻断所有小盘股交易。

2. **Feature Flag 双轨独立** — `USE_AI_DECISION_TCA_PRE_TRADE` 独立于 `USE_TCA_PRE_TRADE_ESTIMATE`，避免 `ai_decision` 包与 `utils/execution_router` 耦合，可独立灰度。

3. **TCA 异常 fail-safe** — 所有 TCA 调用包裹 try-except，异常仅记日志 + `tca_error` 字段，主路径不阻断（参考 `execution_router.route_with_tca` 模式）。

4. **熔断器复用不重写** — 直接 import `v8.3_institutional.src.ai.model_router.CircuitBreaker`，避免代码重复。`ModelHealthMonitor` 仅做轻量封装。

5. **Shadow 14 天硬约束** — 步骤 5 暂缓，等待 Shadow 跑满 14 天。项目记忆硬约束，不可绕过。

6. **cli.py veto 字段 bug 修复** — 步骤 1 顺带修复 `dec.veto = execution_result.get("veto", False)` 覆盖 `apply_mode` 已设 veto 的 bug，改为 `dec.veto = result.get("veto", dec.veto)`。

---

## 推荐执行节奏

| 周次 | 任务 | 产出 |
|------|------|------|
| W1 | 步骤 1 | escalation 字段全链路打通，5 个失败分支测试覆盖 |
| W2 | 步骤 2 + 步骤 3（并行） | TCA 双轨集成 + 模型熔断器 |
| W3 | 步骤 4 + 步骤 7 | 看板 + EOD 复盘 |
| W4 | 步骤 6 | 历史回放验证 |
| W5+ | 步骤 5（待 Shadow 14 天） | 灰度自动推进 |

---

## 验证方案

### 单步验证（每步完成后）
- 运行该步骤新增的所有单测：`python -m pytest tests/unit/test_execution_bridge.py -v`
- 运行该步骤的集成测试：`python -m pytest tests/integration/ -v`
- 静态分析：`python -m mypy ai_decision/ --follow-imports=skip` + `python -m pylint ai_decision/`

### 端到端验证（步骤 1/2/3 完成后）
```bash
# 1. Shadow 模式全链路 (无 Key)
python -m ai_decision.cli --symbol 600519 --mode shadow --mock-force --execute

# 2. Paper 模式 + TCA 预筛 (需开启 Flag)
python -m ai_decision.cli --symbol 600519 --mode paper --execute

# 3. Auto 模式 + 熔断降级 (模拟 API 失败)
python -m ai_decision.cli --symbol 600519 --mode auto --execute --blacklist 600519
```

### 回归验证（每步完成后）
```bash
# 全量 execution_bridge 测试
python -m pytest tests/unit/test_execution_bridge.py tests/integration/test_execution_bridge_integration.py -v

# Phase 3-B 静态分析验证
python scripts/_verify_phase3b_static_analysis.py
```

### 审计验证
- 检查 `reports/ai_decision/execution/exec_*.jsonl` 含 `escalation` 字段（步骤 1）
- 检查 `reports/tca/estimate_*.jsonl` 由 PreTradeEstimator 自动写入（步骤 2）
- 检查 `reports/ai_decision/dashboard_*.md` 含 5 章节（步骤 4）
- 检查 `reports/ai_decision/eod_review_*.md` 含 5 维度（步骤 7）

---

## 关键文件清单（按步骤 1-3 优先）

| 文件 | 涉及步骤 | 说明 |
|------|---------|------|
| [ai_decision/execution_bridge.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/execution_bridge.py) | 1, 2 | 5 个失败分支 + TCA 集成点 |
| [ai_decision/cli.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/cli.py) | 1 | 字段同步 + veto bug 修复 |
| [ai_decision/models.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/models.py) | 1 | TradingDecision（escalation 字段已就绪，无需改） |
| [utils/tca_pre_trade_estimator.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/tca_pre_trade_estimator.py) | 2 | 复用 `PreTradeEstimator.estimate()` |
| [utils/tca_engine.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/tca_engine.py) | 2 | 复用 `TCAManager.analyze()` |
| [v8.3_institutional/src/ai/model_router.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/src/ai/model_router.py) | 3 | 复用 `CircuitBreaker` |
| [ai_decision/providers.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/providers.py) | 3 | `get_active_provider()` 熔断降级 |
| [ai_decision/orchestrator.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/orchestrator.py) | 3 | `run_decision()` 开头探测 |
