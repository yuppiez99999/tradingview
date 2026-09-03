# R10 Batch4 复核 — 宽 except 精确化（2026-09-03）

> 上下文：R10（`utils/scripts/quant_modules` 裸宽捕获精确化）每周 30 处配额。
> AUTO-1 云端 batch2/3 已完成；本文件记录 09-03 晚间本地复核（面向下一批配额，避免重复踩坑）。

## 判读口径（4 类结论规则）

对 `scripts/audit_bare_except_sites.py --json` 输出的带 `suggest` 候选逐条按 except 体语义分类：

| 类别 | 语义 | 处置 | 典型站点（本次判读） |
|---|---|---|---|
| ① fail-open 初始化/条件依赖导入 | yaml/配置损坏、可选第三方（import 失败）必须旁路，ImportError 非 OSError/ValueError 子类 | **保持宽**，不可收窄 | `value_discipline_layer.py` L70/L220、`timesfm_predictor.py` L58/L256、`portfolio_builder.py` L470、`signal_fusion.py` L1608、`fault_injector.py` L144/L169/L190/L231、`pipeline_report_mixin.py` L155 |
| ② 逐文件/逐条容错继续 | 批量摄入/回填循环，坏单条跳过不影响整体 | **保持宽** | `experience_rag.py` L421（Tier M 摄入）、`event_tracker.py` L260/L279、`backfill_*.py`、`ashare_data.py` L196/L205（网络层） |
| ③ cleanup+re-raise / guard-return | `except BaseException: 清理; raise`（原子写语义），或输入非法防御返回 None | **保持宽**；`BaseException` 场景是正确用法不应被 lint 建议误导 | `concurrency.py` L93（原子写清理）、`financial_rigor.py` L316（`exact_calc` 防御返回 None，收窄会漏 ZeroDivisionError/SyntaxError） |
| ④ best-effort 文件读取+解析 | 外层包 open/循环，**内层已有行级具体异常**（如 `except json.JSONDecodeError: continue`） | **可安全收窄** `(OSError, ValueError)`：IO 与解析仍是主要失败域，逻辑 bug 不再被吞 | 本次落地 3 处见下 |

## 本次落地（3 处，均已过 ruff + py_compile + 相关单测）

1. `utils/tier_safety.py` `recent_audit` 外层：`except Exception as exc` → `except (OSError, ValueError) as exc`（读取审计日志 jsonl，内层 L277 已有 JSONDecodeError 行级捕获）。
2. `utils/shadow_30day_evaluator.py` `_load_jsonl` 外层：→ `(OSError, ValueError)`（内层 L211 已有 JSONDecodeError 捕获）。
3. `utils/shadow_30day_evaluator.py` fail_fast 状态读取：`except Exception: pass` → `except (OSError, ValueError): pass`（best-effort 状态 JSON 读取）。

验证：`ruff check` 两文件 All checks passed；`py_compile` OK；
`pytest tests/unit/test_shadow_30day_unit.py tests/unit/test_shadow_30day_evaluator.py` → **36 passed**。

## 遗留站点（判读保留宽，后续批次勿重复提）

- ① 类全部（类别①表格所列）——ImportError/条件依赖是设计语义。
- `mirror_test.py` L54 / `value_discipline_layer.py` L220 / `live_order_executor.py` L435（LLM/券商 RPC 网络 + JSON + 业务异常混合，宽捕获合理）。
- `pipeline_signal_mixin.py` L211/L232/L278（逐 symbol 行情拉取容错）。
- `stock_screener.py` L78（`subprocess.run` 收窄会漏 `TimeoutExpired`，`TimeoutExpired` 非 `OSError` 子类）。
- `eval_flywheel.py` L227（LLM 响应包装）、L404（一次性 flywheel 工具，价值低）。
- `utils/` 之外：`scripts/` 站点多为一次性工具（`_check_coverage_trend`、`_select_tests_by_diff`、`_verify_*`、`evolution_dry_run_0824` 等）与门禁脚本自身 fail-safe 语义——按 R10 价值排序应放 utils/ 之后。

## 判读教训

审计工具的 `suggest` 是**语法级**建议，不区分语义；逐条精确化前必须先读 except 体确认：
- re-raise 型（concurrency L93）收窄会破坏原子写清理；
- ImportError 捕获（可选依赖）收窄后 import 失败将直接崩；
- `subprocess.TimeoutExpired` / `yaml.YAMLError` 不在建议元组内，收窄需显式确认不会改变 fallback 语义。
