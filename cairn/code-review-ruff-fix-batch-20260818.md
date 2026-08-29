---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-18
updated: 2026-08-18
related:
  - cairn/code-review-newcode-bug-patterns-20260817.md
  - cairn/exception-handling-standards.md
  - cairn/code-quality-fix-batch-20260813.md
---

# 代码质量修复批次 2026-08-18：ruff 高危规则清零

> 触发：0818 系统代码质量审查发现 ruff F821/F811/B904 共 33 处，其中生产代码 10 处。
> 范围：6 个文件，28 处修复（F821 ×18 + F811 ×1 + B904 ×9），零行为变更，纯增量修复。
> 验证：ruff 三类规则全仓清零 + py_compile 全通过 + test_g7_coverage_boost 54 passed 无回归。

## 一、修复清单

| # | 文件 | 规则 | 处数 | 修复方式 | 风险 |
|---|---|---|---|---|---|
| 1 | `tests/unit/test_g7_coverage_boost.py` | F821 | 17 | 导入区补 `from typing import Any`（17 处注解引用 `Any` 未导入） | 无（测试文件，future-annotations 下运行无害，修复破坏的 get_type_hints） |
| 2 | `utils/risk_guard_integrator.py:204` | F821 | 1 | `from typing import Any, Optional, Type, cast` → 加 `List`（`self.log_entries: List[str]` 注解） | 无（生产代码，future-annotations 下运行无害，修复类型内省） |
| 3 | `lgb_enhanced_trainer.py:151` | F811 | 1 | 删除重复的 `POSITION_SYMBOLS` 导入（第 62 行已导入，第 151 行冗余） | 无（模块级导入，62 行已建立命名空间绑定） |
| 4 | `quant_modules/ai_hedge_fund/input.py:195,200` | B904 | 2 | `except ValueError:` → `except ValueError as exc:` + `raise ... from exc` | 无（异常链显式化，调试定位改善） |
| 5 | `utils/execution/qmt_rpc_server.py:185,205,228,239,250,269` | B904 | 6 | 6 处 `raise HTTPException(...)` 统一加 `from exc` | 无（FastAPI 异常链，6 处模式完全相同） |
| 6 | `utils/tdam_client.py:747` | B904 | 1 | `except ValueError:` → `except ValueError as exc:` + `raise TDAMAPIError(...) from exc` | 无（TDAM 业务错误异常链） |

**合计**：28 处修复，F821 18→0、B904 9→0 全仓清零；F811 6→5（剩余 5 处为参数遮蔽导入名，低风险债务，见第三节）。

## 二、根因分析

### 2.1 F821 undefined-name（18 处）

**模式**：类型注解引用未导入的符号。两类：
- **测试文件**（17 处）：`test_g7_coverage_boost.py` 大量使用 `Any` 作参数/返回注解，但导入区只有 `json/tempfile/Path/MagicMock/patch/pytest`，缺 `from typing import Any`。`from __future__ import annotations` 让注解不求值，运行时不崩，但破坏 `get_type_hints()` 内省且 ruff 静态报错。
- **生产代码**（1 处）：`risk_guard_integrator.py:204` `List[str]` 注解，typing 导入行漏了 `List`。同模式：future-annotations 掩盖了运行时风险。

**根因**：future-annotations 让"注解不求值"成为默认，开发者补类型注解时容易漏导入，IDE 若未配置 ruff 实时检查则无感知。这类缺陷**静态可检测、运行时潜伏**，ruff F821 是唯一可靠防线。

**规约**：新增类型注解时，ruff F821 必须零新增（已纳入 quality-gate.yml PR 增量门禁）。

### 2.2 F811 redefinition（1 处修复 + 5 处保留）

**修复**：`lgb_enhanced_trainer.py` 第 62 行导入 `POSITION_SYMBOLS`（注释标明 re-export 用途），第 151 行的导入块又包含 `POSITION_SYMBOLS` → 第 62 行的绑定被覆盖成为 unused。删除 151 行的 `POSITION_SYMBOLS`，保留 62 行的 re-export 导入。

**保留**（5 处低风险债务）：
| 文件 | 行 | 模式 | 处置理由 |
|---|---|---|---|
| `quant_modules/ai_hedge_fund/memory_reflection.py:476` | 函数参数 `field` 遮蔽 `from dataclasses import field` | 改参数名破坏 API 兼容；函数内未用 dataclass.field，运行无害 |
| `scripts/tdam/export_cairn_for_tdam.py:98` | `extract_title` 重定义 | 脚本文件，低频执行 |
| `tests/test_dqc_skeleton.py:321` | `timedelta` 重定义 | 测试文件 |
| `tests/unit/test_market_impact_model_unit.py:171` | `TestOptimalTrajectory` 重定义 | 测试文件 |
| `v8.3_institutional/daily_workflow.py:687` | `os` 重定义 | daily_workflow 拆分进行中，待拆分完成后统一清理 |

### 2.3 B904 raise-without-from（9 处）

**模式**：`except SomeError:` 块内 `raise AnotherError(...)` 未用 `raise ... from err`，丢失异常链。Python 3 默认隐式链 `__context__` 仍存在，但 `from` 显式化后：
- `traceback` 显示 "The above exception was the direct cause of..." 而非 "During handling of..."，语义更清晰；
- 调试时 `__cause__` 可直接访问而非推断 `__context__`。

**分布**：
- `input.py`（2 处）：日期格式校验，`ValueError` → `ValueError`。
- `qmt_rpc_server.py`（6 处）：FastAPI HTTP 异常，业务异常 → `HTTPException`。6 处模式完全一致（`except (ValueError, TypeError, ...) as exc: raise HTTPException(...) `），用 `replaceAll` 一次性修复。
- `tdam_client.py`（1 处）：TDAM 响应非 JSON，`ValueError` → `TDAMAPIError`。

**规约**：`except` 块内 `raise` 必须带 `from exc`（重新抛同型异常）或 `from None`（有意抑制链）。已纳入 ruff B904 门禁。

## 三、验证结果

| 验证项 | 命令 | 结果 |
|---|---|---|
| ruff F821/F811/B904（修复文件） | `ruff check <5文件> --select F821,F811,B904` | All checks passed ✅ |
| ruff F821/B904（全仓） | `ruff check . --select F821,B904 --statistics` | 0 条 ✅（全仓清零） |
| ruff F811（全仓） | `ruff check . --select F811 --statistics` | 5 条（均为低风险参数遮蔽，见 2.2） |
| py_compile（6 文件） | `python -m py_compile <6文件>` | ALL OK ✅ |
| 单元测试 | `pytest tests/unit/test_g7_coverage_boost.py` | 54 passed in 5.09s ✅ 无回归 |

## 四、与历史报告的关系

- **0816 报告** NB-3/NB-4（F821 生产代码 `cast`/`logging` 未导入）已于 0817 确认修复。本轮 F821 剩余 18 处为 0816 报告未覆盖的测试文件 + risk_guard_integrator `List`。
- **0816 报告** NB-5（build_plan_executor 缺 future-annotations）已确认修复（第 1 行已有 `from __future__ import annotations`）。
- **0816 报告** NB-6（drift_monitor 异常元组漏 RuntimeError）已确认修复（第 172 行元组已含 RuntimeError）。
- 本轮 B904 9 处为历史报告未记录的新发现，属异常链最佳实践债务。

## 五、剩余债务与后续建议

| 项 | 级别 | 建议 |
|---|---|---|
| F811 剩余 5 处（参数遮蔽导入名） | Low | memory_reflection.py 改参数名需评估 API 兼容；daily_workflow 待拆分完成后统一清理 |
| ruff 全仓 ~5400 条（W293/F401/I001/T201/ANN/BLE001 等） | Debt | 按 ROADMAP Wave 3 增量清零，`ruff fix` 可自动处理 W293/I001/F401 约 2000 条 |
| `tests/unit/test_hedge_execution_orders_unit.py:19` 无效 `# noqa` 指令 | Low | ruff 每次运行告警，改为合法代码列表格式 |
| cast(dict[str, Any]) 6 处 py38 兼容 | Low | 生产用 py3.11 不受影响；py38 解释器已损坏（NB-8），项目 requires-python>=3.10，建议弃用 py38 |

## 六、contains 标签

`contains: ruff-fix, F821-undefined-name, B904-raise-from, F811-redefinition, exception-chaining, type-annotation-missing-import, future-annotations-pitfall, zero-behavior-change`

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [独立代码审查 + 二次核验纠偏（2026-08-08）](code-review-independent-audit-20260808.md) (相似度 12%)
- [2026-08-08 代码审查修复批次经验沉淀](code-review-fix-batch-20260808.md) (相似度 12%)
- [异常处理规约 (Exception Handling Standards)](exception-handling-standards.md) (相似度 11%)
- [代码审查 + 修复批次 标准作业流程 (SOP)](code-review-sop.md) (相似度 10%)
- [测试健康度治理经验（2026-08-19）](test-health-20260819.md) (相似度 9%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
