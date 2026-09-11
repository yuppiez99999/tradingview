# 风控阈值单一事实源与三项遗留修复（Issue #13）

**创建**: 2026-09-11
**关联**: Issue #13 · PR #16（P0/P1 前置修复）· `cairn/LOG.md` 2026-09-11 条目
**状态**: 已实现（沙箱验证通过，待生产机观测）

---

## 1. 问题：同一风控语义存在多套互不相干的口径

巡检（Issue #13）暴露的共性问题——**阈值散落在类属性、函数默认值与硬编码之间**：

| 语义 | 修复前 | 位置 |
|---|---|---|
| 单标的止损/止盈 | `0.08 / 0.15` 硬编码 | `daily_trade_executor.init_wt_modules` |
| 单日亏损/组合回撤熔断 | `0.03 / 0.05` | `config/trade_execution.yaml` |
| 单笔名义上限 | `0.02`（AI 子系统默认） | `ai_decision.config.DEFAULT_CONFIG` |
| 组合净值默认 | `1_000_000`（**与真实 200 万不符**） | `execution_bridge` / `execution_risk` / `cli` |
| 因子有效性 | 类属性字面量 | `factor_discovery.FactorValidator` |

后果：止损 8% 与熔断 3% 是两套无法协同审计的数字；L2 的 2% 上限在 200 万组合下等于"单笔 4 万"，实测 1700 元 × 200 股（34 万）被直接 veto —— 说明桥接层参数从未用真实组合跑通。

## 2. 方案：`config/risk_thresholds.yaml` 单一事实源

```
config/risk_thresholds.yaml        # 数值唯一维护点（入版本库, .gitignore 放行）
        │
        └── utils/risk_thresholds.py   # 类型化加载器
                ├── resolve_config(section) -> (cfg, ThresholdSource)
                ├── get_stop_loss_config()       # S-1
                ├── get_portfolio_protection_config()  # 灰度阶段初期保护
                ├── get_l2_config()              # S-2
                └── get_factor_validation_config()     # P1-3
```

**不变量**：
1. **fail-open**：配置不可用 → 模块内安全默认 + WARNING，不阻断交易链路（缺失不应让系统瘫痪，也不应静默改变行为）；
2. **可审计**：`ThresholdSource.describe()` 记录"来自文件 / 来自默认 + 缺失键"，调用方可断言来源；
3. **类型强制**：YAML 的 `"0.07"` 等字符串按目标类型强制，不可解析则保留默认并告警。

回归锁：`tests/unit/test_risk_thresholds_unit.py`（13 例，含缺失/部分/非法类型/未知段的矩阵）。

## 3. S-1 止损执行归属 → 阻断性告警 + 可重试状态机

**性质判断**：原项标注"需要产品决策（自动平仓 vs 阻断性告警）"。**选阻断性告警**——自动平仓涉及自动下单权限，须与当前灰度阶段（auto_10）的风险预算一并评估，不宜在 bug 修复 PR 内顺手引入。

**状态机**（`utils/wt_risk_control.StopLossManager`）：

```
active ──触发──> pending_stop_loss ──acknowledge()──> triggered_stop_loss (终态)
  ▲                    │
  └──── 价格回到安全区间 (自动重新武装, rearm_count++) ────┘
```

关键点（均为原实现的缺陷修正）：
- **可重试**：原实现触发即终态，后续调用恒返回 `none` → "错过一条 WARNING = 止损永久消失"。现在待确认态**每日重复告警**并累计 `alert_retries`；
- **`set_stop_loss` 不再覆盖状态**：原实现无条件重建为 `active`，会让待确认标的下一次检查回到 active，造成重复触发且丢失确认轨迹；现仅在同成本时更新数量、成本变化（加仓）时重算触发价并重新武装；
- **返回快照**：原返回内部 dict 引用，后续重试会把调用方已持有的告警信息一并改写，使"第 N 次告警"无法审计复现；
- **阻断挂载点**：`daily_trade_executor` 触发即返回 `{"status": "blocked", "blocked_reason": "stop_loss_triggered (S-1)", "stop_loss_alerts": [...]}`；
- **降级标记必须排除**：DTE-3 的 `__manager_unavailable` 表示"止损模块不可用"，语义上不是"标的触发止损"，若一并阻断会把整条盘后执行链误伤（已在 `test_c1_c2_critical_fixes` 与新增用例双覆盖）。

**副作用修正**：`_run_stop_loss_check` 的日志格式 `%+.1%%` 经 printf 解析会残留裸 `%`，触发时抛 `ValueError: unsupported format character`（"止损触发时日志本身会炸"）→ 改用 `%+.1f%%`。

## 4. S-2 涨跌停接入主链 + L2 口径 + 队列限价

### 4.1 涨跌停保护此前形同虚设

`decision_gate.run_hard_risk` 的涨停/跌停分支早已实现，但**主链从不喂数据**：`RiskContext.is_limit_up` 默认 False，CLI 与 `run_decision` 均不设置（沙箱实测：默认 rc 的 `limit_up` 检查恒 False）。

修复引入 `utils/price_limit_refresh.py`：
- 优先级：**实时快照的 `limit_up`/`limit_down`**（东财 `f168`/`f170`）→ `price_limit_calculator` 按板块规则 + 前收盘价回退（主板 ±10% / 创业板·科创板 ±20% / 北交所 ±30% / ST ±5%）；
- 落盘 `reports/operations/price_limit_status.json`，含 `as_of` 与 `as_of_date`；
- **过期语义 fail-closed**：`RiskContext.price_limit_stale=True` 时，即使状态为 `normal` 也保守拒绝买入 —— 因为涨停可能连续多日，把"昨日 normal"当"今日未涨停"会放行；反之旧状态残留的 `limit_up` 会持续 veto。

### 4.2 L2 口径

| 项 | 修复前 | 修复后 | 依据 |
|---|---|---|---|
| `max_single_pct` | 0.02 | **0.05** | 200 万组合下 ≈10 万/笔，与 `trade_execution.yaml` `daily_fixed_budget` 20 万/日同量级 |
| `default_portfolio_value` | 1_000_000 | **2_000_000** | `system_config.json` `stock_etf_capital` |
| 灰度阶段初期保护 | 无（2σ 分支需 ≥5 样本） | **绝对回撤 5%** | 补 `auto_10` 前 5 个交易日无自动回滚保护的空窗 |

### 4.3 队列消费限价（实测缺陷）

模拟/回测路径下切片价缺失时，原实现直接回退持仓参考价 → **执行计划价 1700 与参考价（成本/昨收）脱钩，静默产生与决策不一致的成交价**；且 `isinstance(price, (int,float))` 会把 `Decimal` 判为非法而错误回退。现顺序：**切片价 → 执行计划限价 → 参考价（兜底 + WARNING）**，`Decimal` 统一经 `_to_positive_float` 归一化（0/负数/None 仍视为无有效限价，保留 BUG-E4 的"拒绝 0 价成交"语义）。

## 5. P1-3 因子判定口径 → 显式新口径 + 影子双跑

**性质判断**：原项标注"涉及因子库重评基线，改动会连锁影响已入库因子判定"。故**不静默改基线**，改为：
1. 新口径为 **primary**：`MIN_SAMPLES 5→60`、`|IC| 0.02→0.03`、`|IR| 0.2→0.5`；
2. 评分 `abs`→**带符号**：修正"稳定反向因子与稳定正向同分"（沙箱实测旧口径 +49.71 vs 反向 +49.59；新口径 +59.71 vs −29.71）；方向一致性改用 `ic_positive_ratio` 的"与因子自身方向同号率"；
3. **`shadow_legacy`**：旧口径同跑写入 `legacy_effective`/`legacy_score`/`legacy_n_samples`，**仅报告不判定**；
4. 报告新增"口径影子对照"章节，直接产出「旧口径判有效 / 新口径判无效」的**淘汰候选清单**，供人工确认后再切口径。

**为何不直接重判**：口径切换不是 bug 修复，是研究基线重置事件；且当前测试锁的是旧行为。正确顺序 = 影子重评 → 人工确认淘汰名单 → 切口径并同步更新断言。


---

## 6. 影子名单完备性修正（同日增量 · 用户确认后可执行切口径）

### 6.1 "影子报告可直接出淘汰名单"的成立条件

用户回复要求"影子报告可直接出淘汰名单"。复核初版实现后确认：**不完全成立**，存在一个系统性漏人维度。

**根因**：`_validate_single` 在样本不足时 `return None`，该因子**不进入** `validate_all` 的结果列表，因此**不出现在报告的任何章节**（全因子排名、有效因子、强因子、影子对照均无）。而旧口径的样本门槛是 `n >= 5`，所以：

| 因子样本数 n | 旧口径 | 新口径 | 初版名单是否覆盖 |
|---|---|---|---|
| n ≥ 60 | 可判定 | 可判定 | ✅ A 类表覆盖 |
| **5 ≤ n < 60** | **可判定（可能判"有效"）** | **不可判定（无任何输出）** | ❌ **漏掉** |
| n < 5 | 不可判定 | 不可判定 | 与口径切换无关 |

即：初版的"淘汰名单"只覆盖了"两口径都有判定结果"的因子，**遗漏了全部样本量在 [5, 60) 区间的因子** —— 这批因子在当前系统里恰好是最可能存在的（历史不足的新因子、数据源覆盖不足的标的池、分批上线的因子族）。

**实跑证据（沙箱）**：用 20 日面板 + 60 日/120 日面板构成混合因子集跑 `validate_all`：

```
results:      [('STRONG_120D', ...), ('NEW_WEAK_120D', ...)]   # 20 日因子不在内
insufficient: [('SHORT_20D', 20, True), ('TINY_3D', 3, False)]  # 初版实现根本不产出这个清单
影子对照章节: "旧口径判有效 / 新口径判无效的因子数: 0"            # 名单看起来完整
```

### 6.2 修正：淘汰名单按两个维度输出

新增 `InsufficientSamplesRecord`（仅承载名称/类别/样本数/旧口径可判性，**不计算 IC** —— 样本不足时 IC 统计本身不具解释力）并在报告中单列：

- **A 类 · 旧有效 / 新无效** —— 两口径均可判定，**直接淘汰候选**
- **B 类 · 新口径不可判但旧口径可判** —— 必须单列，处置为**补足样本后重评**（性质与 A 类不同，不应随 A 类一起淘汰）
- 总览给出"口径切换冲击面 = A + B"，并注明"两口径均不可判"的第三类与切换无关

同步落地 `scripts/factor_criteria_shadow_report.py`（离线可跑，读 JSON 产物出名单；`--criteria-only` 只打印口径），产出 `reports/operations/factor_criteria_shadow_<date>.md|json`。

### 6.3 口径切换执行前的三件事

1. A 类名单**逐因子经济含义复核**（不只看统计量，防误杀）
2. B 类确认处置 = 补样本，而非淘汰
3. 切口径时**同步**更新 `tests/unit/test_factor_discovery_unit.py` 的断言（当前断言锁的是新口径 primary + 旧口径影子并存的行为）

回归锁：`tests/unit/test_factor_discovery_unit.py::TestShadowEliminationList`（5 例，含"样本不足不得静默丢弃"与"B 类必须出现在名单中"）+ `tests/unit/test_factor_criteria_shadow_report_unit.py`（6 例，含"旧产物缺影子块必须显式报不可用，而非静默出空名单"）。

---

## 7. 止损自动平仓 —— 未实施，待授权口径

用户提及"若要走自动平仓，需要授权自动下单范围（建议与 auto_10 风险预算一起定）"。**本 PR 不引入任何自动下单路径**，维持 §3 的阻断性告警。

若后续启用，需要先定的三件事（缺一不可）：

1. **授权范围**：哪些标的/哪类触发条件允许自动平仓（建议先限定为"仅 STOP_LOSS、仅已持仓标的、仅减仓不反手"）
2. **与 auto_10 风险预算的关系**：自动平仓属于**降风险**动作，是否豁免于 auto_10 的日度下单额度/单笔上限？若不豁免，极端行情下可能因额度耗尽而无法平仓
3. **失败处置**：平仓单被拒/部分成交时的重试上限与升级路径（当前状态机有 `alert_retries`，自动平仓需另设 `exec_retries`）

在 1~3 明确前，止损保持"阻断 + 每日重复告警 + 待人工确认"语义。


---

## 8. P0-4 复核增量：熔断链「接上但无数据」+ 主链未消费熔断

复核 P0-4 三处修复（`cairn/LOG.md` 09-11 条目 · PR #17）后，确认**修复本身正确但未闭环**，另发现主链缺口。以下均为【R】沙箱可复现。

### 8.1 阈值确实是死的（修复未触及，原样保留）

`DAILY_LOSS_STOP_PCT` / `PORTFOLIO_DRAWDOWN_STOP_PCT` 全仓库消费点只有两类：

1. `executor/premarket.py` 的报告文案与 PASS/FAIL 判定（L668-669 / L938 / L980-981）
2. `daily_trade_executor.init_wt_modules` 组装 `RiskControl` 配置

```python
        wt_modules["risk_control"] = RiskControl({...})   # ← 构造后没有任何地方持有或使用它
```

即：**阈值接了，风控器建了，但执行链从不为它喂数**。

### 8.2 缺口 A：`RiskControl` 的熔断输入接口无人调用

`utils/wt_risk_control.RiskControl.check_circuit_breaker()` 依赖内部状态，而全仓库（排除测试）**没有任何调用者**：

| 方法 | 调用者 | 状态 |
|---|---|---|
| `update_equity(equity)` | 无 | `max_equity` / `current_equity` 恒为 0 |
| `record_trade_result`（唯一给 `daily_loss` 赋值处） | 无 | `daily_loss` 恒为 0 |
| `check_circuit_breaker()` | **无** | 从未被执行 |
| `check_position_concentration(code, pv, equity)` | **无** | 从未被执行 |

后果：`max_equity == 0` → 回撤分支短路；`daily_loss == 0` → 当日亏损分支短路。**即使有人调用 `check_circuit_breaker()`，它也恒返回 `(True, "")` —— 只改配置不动喂数，这条链无法生效。**

### 8.3 缺口 B：主链 `_check_execution_preconditions` 不消费熔断

`daily_trade_executor.py:627` 只检查 `daily_limit`：

```python
    if not risk_checks.get("daily_limit", {}).get("passed", True):
        return [], {"status": "blocked", "reason": "单日金额上限未通过"}
```

`circuit_breaker` 与 `manual_confirm` **均不消费**。实测（P0-4 修复后的 `premarket` 语义：数据缺失 → `daily_loss_pct=None` / `passed=False` / 报告标 UNKNOWN）：

```
sentinel = {"daily_limit":{"passed":True},
            "circuit_breaker":{"daily_loss_pct":None,"passed":False,"source":"数据不可用"},
            "manual_confirm":{"passed":False}}
_check_execution_preconditions(sentinel)
→ confirmed=[{...}], error=None     # 未阻断
```

即 **P0-4 把 `passed` 从硬编码 True 改成"缺数据即 UNKNOWN"是正确方向，但下游没人看这个字段** —— 报告上从"假 PASS"变成"UNKNOWN"，执行上则从"假放行"**原样保留为放行**。这是缺陷在链路上的一次位移，不是闭环。

**建议（供独立排期，未在本 PR 动）**：在 `_check_execution_preconditions` 补熔断分支，并对 `passed is False 且 daily_loss_pct is None`（UNKNOWN）走 **fail-closed**，与 `daily_limit` 同等对待。
