---
type: project_topic
status: active
created: 2026-08-31
updated: 2026-08-31
contains: bug, fix, eod-pipeline, feature-flags, intraday-llm
---

# EOD 管道三 bug 修复（2026-08-31）

> 三个独立 bug 导致 EOD 审计不通过，同日全部修复，审计恢复 ✅ 通过。

## Bug 1: 盘中/LLM 决策 risk_rules None（13/13 全失败）

**现象**: 盘中 LLM 决策 13/13 全失败，错误 `'NoneType' object has no attribute 'get'`，EOD 审计不通过。

**根因**: `utils/glm5_decision_engine.py:605` `_build_decision_prompt` 对 `risk_rules.get('max_single_position', 0.10)` 调用，但 `make_decisions` 签名 `risk_rules: dict | None = None`，`v8.3_institutional/llm_intraday_decision_engine.py:269` 调用时未传 risk_rules → None.get() → AttributeError。

**修复**: `risk_rules.get(...)` → `(risk_rules or {}).get(...)`（None 安全防护）。

**教训**: 公开方法签名允许 `param: dict | None = None` 时，内部所有 `.get()` 调@须做 None 防护（`(param or {}).get()` 或前置 `param = param or {}`）。

## Bug 2: ECL bypass FeatureFlags.is_enabled 实例方法当类方法调用

**现象**: EOD phase4_95_ecl_bypass 失败，错误 `FeatureFlags.is_enabled() missing 1 required positional argument: 'name'`。

**根因**: `utils/infra/feature_flags.py:187` `def is_enabled(self, name: str)` 是**实例方法**，但 `utils/infra/ecl/bypass.py:140/152/160` 和 `utils/infra/ecl/sinks.py:132` 用 `FeatureFlags.is_enabled("FLAG_NAME")` 直接在类上调用 → 字符串传给 self，name 缺失。docstring 示例（feature_flags.py:201）也写错了，误导调用方。

**修复**: 改用模块级快捷函数 `from utils.infra.feature_flags import is_enabled`（feature_flags.py:378 定义，内部走 `FeatureFlags.get_instance().is_enabled(name)`）。

**教训**: 单例类的查询方法若未标 `@staticmethod`/`@classmethod`，不能直接 `ClassName.method()`。项目已提供模块级快捷函数时，业务代码应优先用快捷函数而非类方法。**docstring 示例必须与实际签名一致**，错误示例会误导所有调用方。

## Bug 3: shadow_fills_bridge --date 参数 vs 位置参数

**现象**: EOD phase4_5b1_shadow_fills_bridge success=false，但脚本单独运行成功。

**根因**: `scripts/run_shadow_fills_integrator.py` argparse 定义为位置参数 `[date]`（`usage: run_shadow_fills_integrator.py [-h] [date]`），但 `=EOD workflow `run_daily_eod_workflow.py:877` 传 `["--date", report_date]` → argparse 报 `unrecognized arguments: --date` → 退出码 2 → run_step 返回 False。

**修复**: `["--date", report_date]` → `[report_date]`（位置参数）。

**教训**: EOD workflow 用 `run_step2调用子脚本时，参数格式（`--flag` vs 位置参数A必须与子脚本 argparse 定义一致。子脚本参数变更时须同步更新所有调用方。

## 修复后状态

- EOD 审计: ✅ 通过（数据 HEALTHY 26/26 + 盘中决策 1/1 成功 + 计划可执行）
- Phase B: stable 7/7 ✅，samples 0/20（需交易日积累，真实达标日 09-19）
- ruff: All checks passed（三个修复文件）

## �"次要注意: LLM 原文为空

LLM 路由返回空 content，因 `.env` 中 `ZHIPUAI_API_KEY` + `VOLCENGINE_API_KEY` 均为空，DeepSeek 路由也返回空。这是环境配置问题（非代码 bug），用户充值后配置 API key 即可)恢复。