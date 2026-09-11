"""
utils.risk_thresholds — 风控阈值单一事实源加载器 (Issue #13 / S-1 / S-2 / P1-3)
==============================================================================

背景
----
Issue #13 巡检发现同一风控语义在仓库内存在多套互不相干的口径:

- 止损: ``daily_trade_executor`` 硬编码 8%/15%, 而 ``config/trade_execution.yaml``
  的熔断线是 3%/5% —— 两套口径无任何关联审计;
- L2 执行层: ``gate.max_single_pct`` 默认 2% (AI 子系统内置默认), 与真实
  200 万组合不匹配, 导致几乎所有有意义的仓位在 L2 被 veto;
- 因子有效性: ``FactorValidator`` 的常量散落在类属性中, 无显式来源。

本模块把上述阈值收敛到 ``config/risk_thresholds.yaml`` 唯一事实源, 并提供:
  - 类型化访问器 (``get_stop_loss_config`` / ``get_l2_config`` / ...);
  - 段级安全默认 (文件缺失/损坏 → 默认值 + WARNING, **fail-open 不阻断主流程**);
  - ``ThresholdSource`` 记录实际来源 (file / default), 供审计与测试断言。

设计原则
--------
1. 唯一事实源: 数值只在 ``config/risk_thresholds.yaml`` 维护;
2. fail-open: 配置不可用时用模块内默认继续运行 (风控参数缺失不应静默改变行为,
   但也不应使交易链路整体瘫痪 —— 调用方按需自行 fail-closed);
3. 可审计: ``resolve_*_config()`` 返回 ``(config, source)``, 调用方可在审计中记录来源;
4. 不引入新依赖: 复用 ``utils.config_manager`` (PyYAML 已在依赖内)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from utils.config_manager import get_config

logger = logging.getLogger(__name__)

_CONFIG_NAME = "risk_thresholds"


@dataclass(frozen=True)
class ThresholdSource:
    """阈值解析来源 (审计用)。

    Attributes:
        section: 配置段名 (如 ``stop_loss``)
        from_file: 是否来自 ``config/risk_thresholds.yaml``
        missing_keys: 文件中缺失、已用默认值补齐的键
    """

    section: str
    from_file: bool
    missing_keys: tuple[str, ...] = ()

    def describe(self) -> str:
        """生成人类可读描述 (用于日志/审计记录)。"""
        origin = "config/risk_thresholds.yaml" if self.from_file else "模块内默认值"
        detail = f", 缺键补齐={list(self.missing_keys)}" if self.missing_keys else ""
        return f"{self.section} <- {origin}{detail}"


# ============================================================
# 段级安全默认 (与 config/risk_thresholds.yaml 保持同值)
# ============================================================

DEFAULT_STOP_LOSS: dict[str, Any] = {
    "stop_loss_pct": 0.08,
    "take_profit_pct": 0.15,
    "require_manual_confirm": True,
    "block_on_trigger": True,
}

DEFAULT_PORTFOLIO_PROTECTION: dict[str, Any] = {
    "initial_drawdown_stop_pct": 0.05,
    "initial_min_samples": 5,
}

DEFAULT_L2_EXECUTION: dict[str, Any] = {
    "max_single_pct": 0.05,
    "max_daily_pct": 0.20,
    "default_portfolio_value": 2_000_000.0,
    "fill_queue_price_from_plan": True,
}

DEFAULT_FACTOR_VALIDATION: dict[str, Any] = {
    "min_samples": 60,
    "ic_effective_threshold": 0.03,
    "ir_effective_threshold": 0.5,
    "ic_strong_threshold": 0.05,
    "ir_strong_threshold": 0.5,
    "score_mode": "signed",
    "shadow_legacy": True,
}

_DEFAULTS_BY_SECTION: dict[str, dict[str, Any]] = {
    "stop_loss": DEFAULT_STOP_LOSS,
    "portfolio_protection": DEFAULT_PORTFOLIO_PROTECTION,
    "l2_execution": DEFAULT_L2_EXECUTION,
    "factor_validation": DEFAULT_FACTOR_VALIDATION,
}


def _coerce(defaults: dict[str, Any], raw: Any) -> dict[str, Any]:
    """用文件值覆盖默认值 (逐键类型对齐, 缺失键记录但不阻断)。"""
    merged = dict(defaults)
    if not isinstance(raw, dict):
        return merged
    for key, default_value in defaults.items():
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(default_value, bool):
            merged[key] = bool(value)
        elif isinstance(default_value, int) and not isinstance(default_value, bool):
            try:
                # 整数型阈值为"样本数/天数"类计数, 非价格 —— quant_review_lint 的
                # Q3 (价格截断) 提示在此为误报, 显式标注避免误改
                merged[key] = int(value)  # noqa: Q3 (计数型阈值, 非价格)
            except (TypeError, ValueError):
                logger.warning(
                    "[risk_thresholds] %s 值非整数 (%r), 沿用默认 %r",
                    key,
                    value,
                    default_value,
                )
        elif isinstance(default_value, float):
            try:
                merged[key] = float(value)
            except (TypeError, ValueError):
                logger.warning(
                    "[risk_thresholds] %s 值非数值 (%r), 沿用默认 %r",
                    key,
                    value,
                    default_value,
                )
        else:
            merged[key] = value
    return merged


def resolve_config(section: str) -> tuple[dict[str, Any], ThresholdSource]:
    """解析指定段的阈值配置。

    Args:
        section: 段名 (``stop_loss`` / ``portfolio_protection`` /
            ``l2_execution`` / ``factor_validation``)

    Returns:
        ``(config, source)`` — config 已用默认值补齐; source 记录来源与缺键。

    Raises:
        KeyError: section 不在已知段列表内 (编程错误, 应显式失败)。
    """
    defaults = _DEFAULTS_BY_SECTION.get(section)
    if defaults is None:
        raise KeyError(
            f"未知 risk_thresholds 段 {section!r}; "
            f"可用: {sorted(_DEFAULTS_BY_SECTION)}"
        )

    raw_all = get_config(_CONFIG_NAME, default={}, strict=False)
    raw_section = raw_all.get(section) if isinstance(raw_all, dict) else None
    from_file = isinstance(raw_section, dict) and bool(raw_section)

    if not from_file:
        logger.warning(
            "[risk_thresholds] %s 段不可用 (config/risk_thresholds.yaml 缺失或该段为空), "
            "使用模块内安全默认值 — 请确认是否为预期",
            section,
        )

    merged = _coerce(defaults, raw_section)
    missing = tuple(
        k for k in defaults if not (isinstance(raw_section, dict) and k in raw_section)
    )
    return merged, ThresholdSource(
        section=section, from_file=from_file, missing_keys=missing
    )


# ============================================================
# 类型化访问器
# ============================================================


def get_stop_loss_config() -> dict[str, Any]:
    """获取止损/止盈阈值 (S-1)。"""
    return resolve_config("stop_loss")[0]


def get_portfolio_protection_config() -> dict[str, Any]:
    """获取组合级保护阈值 (灰度阶段初期绝对回撤)。"""
    return resolve_config("portfolio_protection")[0]


def get_l2_config() -> dict[str, Any]:
    """获取 L2 执行层阈值 (S-2)。"""
    return resolve_config("l2_execution")[0]


def get_factor_validation_config() -> dict[str, Any]:
    """获取因子有效性判定口径 (P1-3)。"""
    return resolve_config("factor_validation")[0]


def get_max_single_pct() -> float:
    """L2/决策层单笔名义金额上限占净值比例。"""
    return float(get_l2_config()["max_single_pct"])


def get_default_portfolio_value() -> float:
    """组合净值默认值 (替代散落的 1_000_000 硬编码)。"""
    return float(get_l2_config()["default_portfolio_value"])


def get_stop_loss_pct() -> float:
    """单标的止损线。"""
    return float(get_stop_loss_config()["stop_loss_pct"])


def get_take_profit_pct() -> float:
    """单标的止盈线。"""
    return float(get_stop_loss_config()["take_profit_pct"])


def describe_sources() -> dict[str, str]:
    """返回各段阈值来源描述 (审计/自检用)。"""
    return {
        section: resolve_config(section)[1].describe()
        for section in _DEFAULTS_BY_SECTION
    }
