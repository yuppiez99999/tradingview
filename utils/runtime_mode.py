"""统一运行模式开关 (P1-1, 2026-09-01)

背景: "跑不通"诊断 P1-1 — 运行模式散落各处且互不知晓:
- QUANT_OFFLINE (external_data_source 外网短路, P0-2)
- CLI --dry-run (各入口脚本独立参数, P0-1)
- sim_mode (v8.3 institutional 构造参数 / KILL_SWITCH_SIM_MODE 场景模拟)
- QUANT_RESEARCH_MODE / AI_DECISION_MODE (模块私有)

本模块提供单一真相源, 三态语义互不蕴含 (可独立组合):

┌──────────────┬──────────────────────────────────┬──────────────────┐
│ 开关          │ 环境变量                          │ 语义             │
├──────────────┼──────────────────────────────────┼──────────────────┤
│ is_offline() │ QUANT_OFFLINE=1                  │ 零外网请求       │
│ is_dry_run() │ QUANT_DRY_RUN=1                  │ 不落盘/不撮合/   │
│              │                                  │ 不写报告         │
│ is_sandbox() │ QUANT_SANDBOX=1                  │ 模拟盘执行       │
└──────────────┴──────────────────────────────────┴──────────────────┘

优先级: 编程覆盖 (set_mode / CLI 解析后调用) > 环境变量 > 默认 False。

兼容性: 既有 QUANT_OFFLINE 语义不变 (external_data_source 已委托本模块);
QUANT_DRY_RUN / QUANT_SANDBOX 是新的全局入口, 不影响既有 CLI 参数
(它们作为 env 预设值, CLI 显式传参仍可覆盖)。

使用:
    from utils.runtime_mode import is_dry_run, set_mode

    # CLI 解析后 (让深层模块也感知):
    parser.add_argument("--dry-run", action="store_true",
                        default=env_flag("QUANT_DRY_RUN"))
    args = parser.parse_args()
    set_mode(dry_run=args.dry_run)   # 显式 True 时全局生效

    # 任意深层模块:
    if is_dry_run():
        ...跳过落盘...
"""

from __future__ import annotations

import os

_TRUE_VALUES = {"1", "true", "yes", "on"}

# 编程覆盖 (CLI 解析后 set_mode 写入), 优先于环境变量
_overrides: dict[str, bool] = {}


def _flag(env_var: str) -> bool:
    """读取布尔环境变量 (每次调用读取 — 支持运行中/conftest 动态设置)"""
    return os.environ.get(env_var, "").strip().lower() in _TRUE_VALUES


def env_flag(env_var: str) -> bool:
    """给 argparse default 用的 env 预设读取 (公开版)"""
    return _flag(env_var)


def set_mode(*, offline: bool | None = None, dry_run: bool | None = None,
             sandbox: bool | None = None) -> None:
    """编程覆盖运行模式 (仅显式传入的项生效, None 表示不改)

    CLI 解析后调用, 使显式参数对深层模块全局可见。
    """
    if offline is not None:
        _overrides["offline"] = bool(offline)
    if dry_run is not None:
        _overrides["dry_run"] = bool(dry_run)
    if sandbox is not None:
        _overrides["sandbox"] = bool(sandbox)


def reset_mode() -> None:
    """清除全部编程覆盖 (测试用)"""
    _overrides.clear()


def is_offline() -> bool:
    """OFFLINE: 零外网请求 (QUANT_OFFLINE, P0-2 引入)"""
    return _overrides.get("offline", _flag("QUANT_OFFLINE"))


def is_dry_run() -> bool:
    """DRY_RUN: 只计算不落盘/不撮合/不写报告 (QUANT_DRY_RUN, P1-1 引入)"""
    return _overrides.get("dry_run", _flag("QUANT_DRY_RUN"))


def is_sandbox() -> bool:
    """SANDBOX: 模拟盘执行 (QUANT_SANDBOX, P1-1 引入; 对应 v8.3 sim_mode)"""
    return _overrides.get("sandbox", _flag("QUANT_SANDBOX"))


def current_mode() -> dict[str, bool]:
    """当前三态快照 (诊断/报告用)"""
    return {
        "offline": is_offline(),
        "dry_run": is_dry_run(),
        "sandbox": is_sandbox(),
        "source": {
            "offline": "override" if "offline" in _overrides else "env",
            "dry_run": "override" if "dry_run" in _overrides else "env",
            "sandbox": "override" if "sandbox" in _overrides else "env",
        },
    }
