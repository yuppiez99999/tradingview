"""IV Rank 自适应参数解析层.

职责 (生产与回测共用, 纯函数核心):
    - resolve_adaptive_params: iv_rank + 分档配置 + 静态参数 → 生效参数
    - validate_iv_adaptive_config: fail-fast 配置校验 (orchestrator 装载时调用)
    - build_iv_rank_provider: 从配置构造 IVRankProvider (延迟 import, combo 包不硬依赖 alpha 包)

分档语义 (左闭右开, 按 max_rank 升序):
    tiers: [{name: "low", max_rank: 30, params: {put_otm_pct: 0.03, ...}},
            {name: "mid", max_rank: 70, params: {}},
            {name: "high", max_rank: 101, params: {...}}]
    rank=29 → low; rank=30 → mid; rank=70 → high; rank=100 → high (末档 max_rank 需 >100)

fail-open 约定 (所有异常路径回退静态参数, 绝不因自适应失败阻断建仓):
    - enabled != true / 配置缺失 → tier=None
    - iv_rank None / 非数值 / 越界 [0,100] → tier=None
    - 无命中档 (如末档 max_rank ≤ 100) → tier=None
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class IVRankSource(Protocol):
    """IV Rank 数据源最小协议 (结构化类型, 避免本包硬依赖 utils.alpha)."""

    def fetch_iv_rank(self) -> int | None: ...

# 可被 tier 覆盖的字段及其合法区间 (与 collar 引擎参数/期权链口径一致:
# call 链 otm_range 硬编码 (0.02, 0.08), 故 call_otm_pct 强制约束在内)
_PARAM_RANGES: dict[str, tuple[float, float]] = {
    "put_otm_pct": (0.02, 0.08),
    "call_otm_pct": (0.02, 0.08),
    "protection_band_min": (0.05, 0.30),
    "put_otm_max": (0.05, 0.15),
    "max_net_cost_pct": (0.0, 0.02),
    "dte_min": (10, 90),
    "dte_max": (10, 180),
    "preferred_dte": (10, 180),
}


def resolve_adaptive_params(
    iv_rank: int | float | None,
    iv_adaptive_cfg: dict | None,
    static_params: dict,
) -> dict:
    """解析 IV Rank 分档 → 生效参数 (纯函数, 生产与回测共用同一实现).

    Args:
        iv_rank: 当前 IV Rank (0-100), None=数据不可用
        iv_adaptive_cfg: iv_adaptive 配置段 {enabled, tiers: [...]}
        static_params: 引擎静态参数快照 (tier 未覆盖字段继承此值)

    Returns:
        {"params": 合并后参数, "tier": 档名 | None, "iv_rank": int | None}
        tier=None 表示走静态路径 (调用方不应覆盖任何参数)
    """
    result: dict = {"params": dict(static_params), "tier": None, "iv_rank": None}

    if iv_rank is None or isinstance(iv_rank, bool):
        return result
    if not isinstance(iv_rank, (int, float)) or not 0 <= iv_rank <= 100:
        logger.warning("iv_rank 非法值 %r, 回退静态参数", iv_rank)
        return result
    result["iv_rank"] = int(iv_rank)

    if not isinstance(iv_adaptive_cfg, dict) or not iv_adaptive_cfg.get("enabled", False):
        return result

    tiers = iv_adaptive_cfg.get("tiers") or []
    if not isinstance(tiers, list):
        logger.warning("iv_adaptive.tiers 非列表, 回退静态参数")
        return result

    # 按 max_rank 升序找第一个满足 rank < max_rank 的档 (左闭右开)
    for tier in sorted(tiers, key=lambda t: t.get("max_rank", 0) if isinstance(t.get("max_rank"), (int, float)) else 0):
        if not isinstance(tier, dict):
            continue
        max_rank = tier.get("max_rank")
        if not isinstance(max_rank, (int, float)) or isinstance(max_rank, bool):
            continue
        if iv_rank < max_rank:
            tier_params = tier.get("params") or {}
            if not isinstance(tier_params, dict):
                tier_params = {}
            merged = dict(static_params)
            merged.update(tier_params)  # 字段可选覆盖, 省略继承静态
            result["params"] = merged
            result["tier"] = str(tier.get("name", f"tier_{max_rank}"))
            return result

    logger.debug("iv_rank=%d 未命中任何分档, 回退静态参数", iv_rank)
    return result


def _validate_tier_params(tier_idx: int, params: dict) -> None:
    """校验单档 params: 字段合法 + 区间 + 档内交叉约束."""
    if not isinstance(params, dict):
        raise ValueError(f"iv_adaptive.tiers[{tier_idx}].params 必须为映射")
    for key, value in params.items():
        if key not in _PARAM_RANGES:
            raise ValueError(
                f"iv_adaptive.tiers[{tier_idx}].params 含未知字段: {key} "
                f"(合法: {sorted(_PARAM_RANGES)})"
            )
        lo, hi = _PARAM_RANGES[key]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not lo <= value <= hi
        ):
            raise ValueError(
                f"iv_adaptive.tiers[{tier_idx}].params.{key}={value!r} 越界 [{lo}, {hi}]"
            )
    # 交叉校验: put 目标须落在 put 链搜索范围内, dte_min ≤ dte_max
    if "put_otm_pct" in params and "put_otm_max" in params:
        if params["put_otm_pct"] > params["put_otm_max"]:
            raise ValueError(
                f"iv_adaptive.tiers[{tier_idx}].put_otm_pct={params['put_otm_pct']} "
                f"> put_otm_max={params['put_otm_max']}, 目标行权价不可达"
            )
    if "dte_min" in params and "dte_max" in params:
        if params["dte_min"] > params["dte_max"]:
            raise ValueError(
                f"iv_adaptive.tiers[{tier_idx}].dte_min={params['dte_min']} "
                f"> dte_max={params['dte_max']}"
            )


def validate_iv_adaptive_config(cfg: dict | None) -> None:
    """fail-fast 校验 iv_adaptive 配置段 (orchestrator 装载时调用).

    未启用 (enabled != true) 时直接返回 — 默认旁路交付, 不校验.

    Raises:
        ValueError: 配置非法 (结构错误 / max_rank 非严格递增 / 末档未覆盖
            rank=100 / 参数字段未知或越界 / 档内 dte/otm 交叉矛盾)
    """
    if not isinstance(cfg, dict) or not cfg.get("enabled", False):
        return

    tiers = cfg.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("iv_adaptive.enabled=true 但 tiers 缺失或为空列表")

    prev_max: float = -1.0
    for i, tier in enumerate(tiers):
        if not isinstance(tier, dict):
            raise ValueError(f"iv_adaptive.tiers[{i}] 必须为映射, 实际 {type(tier).__name__}")
        max_rank = tier.get("max_rank")
        if not isinstance(max_rank, (int, float)) or isinstance(max_rank, bool):
            raise ValueError(f"iv_adaptive.tiers[{i}].max_rank 非数值: {max_rank!r}")
        if max_rank <= prev_max:
            raise ValueError(
                f"iv_adaptive.tiers[{i}].max_rank={max_rank} 未严格递增 (前值 {prev_max})"
            )
        prev_max = float(max_rank)
        _validate_tier_params(i, tier.get("params") or {})

    if prev_max <= 100:
        raise ValueError(
            f"iv_adaptive.tiers 末档 max_rank={prev_max:g} 未覆盖 rank=100 (需 > 100)"
        )


def build_iv_rank_provider(cfg: dict | None) -> IVRankSource | None:
    """从 iv_adaptive 配置段构造 IVRankProvider (延迟 import).

    Args:
        cfg: iv_adaptive 配置段

    Returns:
        IVRankProvider 实例 (满足 IVRankSource 协议); 配置未启用 /
        导入失败返回 None (调用方走静态路径)
    """
    if not isinstance(cfg, dict) or not cfg.get("enabled", False):
        return None
    try:
        from utils.alpha.iv_rank import IVRankProvider
    except (ImportError, ModuleNotFoundError) as e:
        logger.warning("IVRankProvider 导入失败, iv_adaptive 旁路: %s", e)
        return None
    return IVRankProvider(config={
        "lookback_days": cfg.get("lookback_days", 252),
        "min_history_days": cfg.get("min_history_days", 60),
        "method": cfg.get("method", "percentile"),
        "underlying": cfg.get("underlying", "510050.SH"),
        "rv_window": cfg.get("rv_window", 20),
    })


def load_iv_adaptive_config(config_path: Path | str | None = None) -> dict:
    """从 config/etf_option_combo.yaml 只读加载 iv_adaptive 段 (回测对比用).

    与 ComboOrchestrator._load_config 同构: yaml 的 combo_strategies 段为
    运行时配置根, iv_adaptive 嵌套其下; 亦兼容 iv_adaptive 直接位于文件
    顶层的写法。文件缺失/不可读/语法错误/结构异常一律返回 {} 并告警 —
    供 run_comparison fail-open 回静态对比, 不抛异常阻断回测入口。

    Returns:
        iv_adaptive 配置段 (dict); 不可用时返回 {}
    """
    path = Path(config_path) if config_path else (
        Path(__file__).resolve().parents[2] / "config" / "etf_option_combo.yaml"
    )
    try:
        import yaml
    except (ImportError, ModuleNotFoundError) as e:
        logger.warning("PyYAML 不可用, iv_adaptive 配置加载失败: %s", e)
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("iv_adaptive 配置读取失败 (%s): %s", path, e)
        return {}
    if not isinstance(cfg, dict):
        logger.warning("配置文件 %s 顶层非映射, 忽略 iv_adaptive 段", path)
        return {}
    # 解包 combo_strategies (生产 yaml 结构); 无此段则按顶层处理
    section = cfg.get("combo_strategies")
    if isinstance(section, dict):
        cfg = section
    iv = cfg.get("iv_adaptive")
    return iv if isinstance(iv, dict) else {}
