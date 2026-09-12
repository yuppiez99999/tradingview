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
- 因子有效性: ``FactorValidator`` 的常量散落在类属性中, 无显式来源;
- 资金口径: 5M 总资本散落 11 处硬编码 (再平衡/对冲/期权预算/风控预算),
  实际证券账本 ≈274 万 → 再平衡目标高估 ~82% (P1-2, 数值切换待用户拍板)。

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

# S-1 止损自动平仓授权默认值 (Issue #13, 2026-09-12)
# ⚠ 默认 enabled=False —— 与启用前行为逐字节一致 (仅走阻断性告警)。
# 三个授权口径见 config/risk_thresholds.yaml `stop_loss.auto_liquidate` 段注释。
DEFAULT_STOP_LOSS_AUTO_LIQUIDATE: dict[str, Any] = {
    # 口径 1 · 授权范围
    "enabled": False,
    "scope": "stop_loss_only",
    "allow_take_profit": False,
    "held_positions_only": True,
    "reduce_only": True,
    "max_liquidations_per_symbol_per_day": 1,
    # 口径 2 · 与 auto_10 风险预算的关系
    "exempt_from_daily_quota": True,
    "exempt_from_single_trade_limit": True,
    # 口径 3 · 失败处置
    "max_exec_retries": 2,  # 总尝试次数上限 (含首次)
    "escalate_on_failure": True,
}

DEFAULT_STOP_LOSS: dict[str, Any] = {
    "stop_loss_pct": 0.08,
    "take_profit_pct": 0.15,
    "require_manual_confirm": True,
    "block_on_trigger": True,
    "auto_liquidate": DEFAULT_STOP_LOSS_AUTO_LIQUIDATE,
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

DEFAULT_CAPITAL_BASE: dict[str, Any] = {
    # 口径拍板 2026-09-11 (Issue #13): 权威总口径 = 300 万 (证券 200w + 对冲 100w),
    # 依据 = p9_200w_preset 灰度路线 / kill_switch.yaml total_margin /
    # system_config.json / ROADMAP performance_targets 目标结构 300 万 = 200 + 100。
    # 原 5M (证券 300w + 期货 200w) 系 2026-07 建仓计划口径, 已被取代;
    # 审查报告 §P1-2 量化其导致再平衡目标高估 ~82%、对冲手数 ~1.82×。
    "total_capital": 3_000_000.0,
    "stock_etf_capital": 2_000_000.0,
    "hedge_capital": 1_000_000.0,
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
    "capital_base": DEFAULT_CAPITAL_BASE,
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
        elif isinstance(default_value, dict):
            # 一层嵌套段 (如 stop_loss.auto_liquidate): 递归逐键对齐,
            # 未知键丢弃并告警 —— 防止配置漂移时静默引入未消费的开关。
            if not isinstance(value, dict):
                logger.warning(
                    "[risk_thresholds] %s 应为映射, 实际 %r, 沿用默认", key, type(value)
                )
                continue
            nested = dict(default_value)
            unknown = [k for k in value if k not in default_value]
            if unknown:
                logger.warning(
                    "[risk_thresholds] %s 含未消费键 %s, 已忽略 (防配置漂移)",
                    key,
                    unknown,
                )
            nested = _coerce(nested, {k: v for k, v in value.items() if k in nested})
            merged[key] = nested
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


def get_stop_loss_auto_liquidate_config() -> dict[str, Any]:
    """获取止损自动平仓授权配置 (S-1 三个授权口径)。

    ⚠ ``enabled`` 默认 ``False``: 未显式授权时**不产生任何自动下单路径**,
    调用方必须先判 enabled 再决定是否走自动平仓。
    """
    cfg = get_stop_loss_config()
    auto = cfg.get("auto_liquidate")
    if not isinstance(auto, dict):
        return dict(DEFAULT_STOP_LOSS_AUTO_LIQUIDATE)
    return auto


def get_portfolio_protection_config() -> dict[str, Any]:
    """获取组合级保护阈值 (灰度阶段初期绝对回撤)。"""
    return resolve_config("portfolio_protection")[0]


def get_l2_config() -> dict[str, Any]:
    """获取 L2 执行层阈值 (S-2)。"""
    return resolve_config("l2_execution")[0]


def get_factor_validation_config() -> dict[str, Any]:
    """获取因子有效性判定口径 (P1-3)。"""
    return resolve_config("factor_validation")[0]


def get_capital_base_config() -> dict[str, Any]:
    """获取资金口径静态基准 (P1-2 单一事实源).

    返回 ``{"total_capital": float, "stock_etf_capital": float,
    "hedge_capital": float}`` — 三者语义**不可互换**:

    - ``total_capital``     总口径 (300 万 = 证券 200w + 对冲 100w) →
      风控预算 / kill_switch / institutional pipeline / 组合级绩效分母。
      **不得**用作再平衡单腿目标基数。
    - ``stock_etf_capital`` 证券/ETF 腿 (200 万) → **再平衡链基数**。
    - ``hedge_capital``     对冲腿 (100 万) → 对冲链预算基数。

    口径拍板 2026-09-11 (Issue #13): 权威总口径由 5M 改为 3M, 依据见
    ``config/risk_thresholds.yaml`` capital_base 段注释。本函数返回**静态
    基准**; 运行时真实权益请用 :func:`resolve_effective_capital` (positions
    meta 优先)。
    """
    return resolve_config("capital_base")[0]


def get_total_capital() -> float:
    """总资本权威口径 (风控预算 / institutional pipeline / 组合级绩效分母).

    注: 证券/ETF 腿的再平衡目标基数应用 :func:`get_stock_etf_capital`,
    不可直接用本值 (腿口径混用 = 审查报告 §P1-2 的根因)。
    """
    return float(get_capital_base_config()["total_capital"])


def get_stock_etf_capital() -> float:
    """证券/ETF 腿资本 (再平衡链基数; 构成口径, 供需要拆分的消费方取用)。"""
    return float(get_capital_base_config()["stock_etf_capital"])


def get_hedge_capital() -> float:
    """对冲腿资本 (构成口径; 消耗方仍可被 positions meta 等显式值覆盖)。"""
    return float(get_capital_base_config()["hedge_capital"])


def resolve_effective_capital(
    leg: str = "total",
    *,
    runtime_value: float | None = None,
) -> tuple[float, str]:
    """解析某条腿的**有效资本** (运行时优先序 + 来源审计).

    优先序: ``runtime_value`` (positions meta / 权益派生) > 静态
    ``capital_base`` 段 > 模块内默认。返回 ``(value, source_desc)``。

    Args:
        leg: ``"total"`` / ``"stock_etf"`` / ``"hedge"``。
        runtime_value: 运行时真实值 (如 positions.json meta 或实时权益);
            ``None`` 或非正数视为不可用 → 回退静态基准。

    Returns:
        ``(value, source)`` — source 供审计记录, 形如
        ``"runtime(positions meta)"`` / ``"config/risk_thresholds.yaml"``。

    设计意图: 实际账本 (≈274 万) 会随建仓/行情漂移, 不应硬编码进配置;
    但静态基准也不得静默冒充真实权益 —— 故本函数把"取哪一层、来自哪里"
    显式返回, 由调用方决定是否告警/阻断。
    """
    accessors = {
        "total": ("total_capital", get_total_capital),
        "stock_etf": ("stock_etf_capital", get_stock_etf_capital),
        "hedge": ("hedge_capital", get_hedge_capital),
    }
    if leg not in accessors:
        raise KeyError(
            f"未知资金腿 {leg!r}; 可用: {sorted(accessors)}"
        )
    key, accessor = accessors[leg]

    if runtime_value is not None:
        try:
            value = float(runtime_value)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value, "runtime(positions meta / 权益)"

    _cfg, source = resolve_config("capital_base")
    origin = "config/risk_thresholds.yaml" if source.from_file else "模块内默认值"
    return float(accessor()), f"{origin}.capital_base.{key}"


def get_effective_total_capital(runtime_value: float | None = None) -> float:
    """总口径有效资本 (便捷包装; 来源见 :func:`resolve_effective_capital`)。"""
    return resolve_effective_capital("total", runtime_value=runtime_value)[0]


def get_effective_stock_etf_capital(runtime_value: float | None = None) -> float:
    """证券/ETF 腿有效资本 (再平衡链应使用此口径, 非 total)。"""
    return resolve_effective_capital("stock_etf", runtime_value=runtime_value)[0]


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
