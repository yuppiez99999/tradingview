"""KillSwitch 级别枚举与解析 (审计 item 11, 2026-09-10 拆自 utils/risk_guard_integrator.py)。

拆解口径: 零行为变更 —— 枚举成员值、解析分支、告警文本与降级语义全部保持原样。
日志一律走 ``plan_context.logger`` (名称仍为 ``"risk_guard_integrator"``),
使既有 ``monkeypatch.setattr("...plan_context.logger", mock)`` 可继续拦截。
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from utils.risk.guards import plan_context

# ============================================================
# P1-Q6 修复 (2026-07-26): KillSwitch level 用 IntEnum 替代字符串解析
# 原始问题: 嵌套三元运算符 + 字符串 "L3" → 3 解析无类型保护
# 修复方案: 用 IntEnum + 专用解析函数, 提供类型安全与可读性
# ============================================================


class KillSwitchLevel(IntEnum):
    """KillSwitch 熔断级别枚举

    顶级对冲基金标准: 风控级别必须用 Enum, 禁止裸字符串/整数

    阈值口径 (2026-09-13 与 utils/kill_switch.py 主体对齐, 勿再漂移):
        保证金: L1 ≥ 0.50 / L2 ≥ 0.75 / L3 ≥ 0.95 (extreme_call)
        集中度: L1 ≥ 25%  / L2 ≥ 35%  / L3 ≥ 50%
        (历史文档曾写 L2=65%/L3=75% — 与主体实现不符, 已更正)
    """

    OK = 0  # 正常
    L1 = 1  # 一级警戒 (保证金 ≥ 50% 或 单票集中度 ≥ 25%)
    L2 = 2  # 二级熔断 (保证金 ≥ 75% 或 单票集中度 ≥ 35%) — 禁止开仓
    L3 = 3  # 三级互盲 (保证金 ≥ 95% 或 单票集中度 ≥ 50%) — 强制平仓


def parse_kill_switch_level(
    ks_level: Any,
    margin_usage: float = 0.0,
) -> KillSwitchLevel:
    """解析 KillSwitch level 为 KillSwitchLevel 枚举

    P1-Q6 修复: 替换原嵌套三元运算符
        ks_level_int = 3 if level_str == 'OK' and margin_usage >= 0.75 else (
            3 if level_str == '3' else
            2 if level_str == '2' else
            1 if level_str == '1' else 0
        )

    支持输入类型:
        - int (0/1/2/3): 直接转换为枚举
        - str ("L0"/"L1"/"L2"/"L3"/"OK"): 解析为枚举
        - KillSwitchLevel: 直接返回

    特殊语义:
        - "OK" 字符串 + margin_usage ≥ 0.75 → 视为 L3 (隐式升级)
          这是为了兼容历史 bug: 部分 KillSwitch 实现在保证金超阈值时
          仍返回 level="OK", 需在 Guard 层补强判断

    Args:
        ks_level: 原始 level 值 (int/str/KillSwitchLevel)
        margin_usage: 保证金占用率, 用于 "OK" 隐式升级判断

    Returns:
        KillSwitchLevel 枚举值
    """
    # 已是枚举, 直接返回
    if isinstance(ks_level, KillSwitchLevel):
        return ks_level

    # 整数: 直接转枚举
    if isinstance(ks_level, int):
        try:
            return KillSwitchLevel(ks_level)
        except ValueError:
            plan_context.logger.warning(f"无效 ks_level 整数: {ks_level}, 默认 OK")
            return KillSwitchLevel.OK

    # 字符串解析
    if isinstance(ks_level, str):
        level_str = ks_level.upper().replace("L", "").strip()
        if level_str == "OK":
            # 隐式升级: "OK" + 高保证金 = L3
            return KillSwitchLevel.L3 if margin_usage >= 0.75 else KillSwitchLevel.OK
        try:
            return KillSwitchLevel(int(level_str))
        except (ValueError, TypeError):
            plan_context.logger.warning(f"无法解析 ks_level 字符串: {ks_level}, 默认 OK")
            return KillSwitchLevel.OK

    # 其他类型: 保守返回 OK
    plan_context.logger.warning(
        f"未知 ks_level 类型: {type(ks_level).__name__}={ks_level}, 默认 OK"
    )
    return KillSwitchLevel.OK


__all__ = ["KillSwitchLevel", "parse_kill_switch_level"]
