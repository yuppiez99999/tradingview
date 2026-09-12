#!/usr/bin/env python3
"""超参数自适应搜索 (N4) — Self-Evolution 框架 Day 3.

根据漂移信号强度动态调整 LGB 超参数配置.

设计原则:
  - 纯函数: 不修改原 config, 返回新的配置副本
  - Feature Flag: USE_ADAPTIVE_OPTIMIZE (默认关闭, 需显式启用)
  - 降级安全: 任何参数异常时返回原配置, 不抛出异常
  - 与 Day 1 对齐: 漂移信号格式与 ModelDriftDetector.generate_report() 输出兼容

使用方式:
    from scripts.adaptive_optimize import adaptive_optimize
    new_config = adaptive_optimize("688041.SH", LGB_ENHANCED_CONFIG, drift_signal=report)
    # 然后用 new_config 替换原 config 进行训练
"""

from __future__ import annotations

import copy
import logging
import os
from datetime import datetime
from typing import Any

logger = logging.getLogger("adaptive_optimize")


# ========== 自适应优化配置 ==========
ADAPTIVE_OPTIMIZE_CONFIG: dict[str, Any] = {
    "feature_flag_name": "USE_ADAPTIVE_OPTIMIZE",
    # 漂移强度阈值 (0-1)
    "drift_severity_threshold_light": 0.25,  # 超过此值: 轻度调整
    "drift_severity_threshold_medium": 0.50,  # 超过此值: 中度调整
    "drift_severity_threshold_heavy": 0.75,  # 超过此值: 重度调整
    # 参数调整幅度 (相对于基准值的乘法因子)
    # 学习率: 漂移越严重, lr 越小 (保守学习)
    "lr_factor_light": 0.75,
    "lr_factor_medium": 0.50,
    "lr_factor_heavy": 0.25,
    "lr_min": 0.0005,
    "lr_max": 0.05,
    # 估计器数量: 漂移越严重, 越多估计器 (配合小 lr)
    "n_est_factor_light": 1.2,
    "n_est_factor_medium": 1.5,
    "n_est_factor_heavy": 2.0,
    "n_est_min": 500,
    "n_est_max": 10000,
    # 树深度: 漂移越严重, 越小深度 (防止过拟合旧模式)
    "max_depth_factor_light": 0.9,
    "max_depth_factor_medium": 0.8,
    "max_depth_factor_heavy": 0.6,
    "max_depth_min": 2,
    "max_depth_max": 12,
    # 正则化: 漂移越严重, 越强正则
    "reg_factor_light": 1.5,
    "reg_factor_medium": 2.5,
    "reg_factor_heavy": 5.0,
    "reg_max": 10.0,
    # 早停轮数: 漂移越严重, 越宽松 (允许更多迭代)
    "early_stop_factor_light": 1.2,
    "early_stop_factor_medium": 1.5,
    "early_stop_factor_heavy": 2.0,
    "early_stop_min": 50,
    "early_stop_max": 500,
}


def _check_feature_flag(flag_name: str) -> bool:
    """Feature Flag 检查 (与 N1/N2 保持一致)."""
    try:
        val = os.getenv(flag_name, "False")
        return val.lower() in ("true", "1", "yes", "on")
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return False


def _compute_drift_severity(drift_signal: dict[str, Any] | None) -> float:
    """从漂移检测器输出中计算综合漂移强度 (0-1).

    与 Day 1 ModelDriftDetector.generate_report() 输出格式对齐:
        {
            "ic_stats": {"mean_ic": float, "ic_10d": float, ...},
            "alerts": [{"severity": "critical"|"warning"|"info", ...}, ...],
            "ks_detected": bool,
            "psi_value": float,
            "adwin_drift": bool,
            "should_retrain": bool,
            ...
        }

    Returns:
        漂移强度 [0, 1], 值越高表示漂移越严重
    """
    if drift_signal is None:
        return 0.0

    severity = 0.0
    components = 0

    # 1. IC 衰减: 最近 10 日 IC 与历史均值的偏差
    ic_stats = drift_signal.get("ic_stats", {})
    recent_ic = float(ic_stats.get("ic_10d") or ic_stats.get("rolling_ic") or 0)
    historic_ic = float(ic_stats.get("mean_ic") or 0)
    if historic_ic > 0:
        # IC 下降比例 (归一化到 0-1)
        ic_drop = max(
            0.0, min(1.0, (historic_ic - recent_ic) / max(historic_ic, 0.001))
        )
        severity += ic_drop * 0.35  # IC 衰减权重 35%
        components += 1

    # 2. 告警数量与严重度
    alerts = drift_signal.get("alerts", [])
    if alerts:
        crit_count = sum(
            1 for a in alerts if str(a.get("severity", "")).lower() == "critical"
        )
        warn_count = sum(
            1 for a in alerts if str(a.get("severity", "")).lower() == "warning"
        )
        # 最多 5 条 critical + 10 条 warning 触发满值
        alert_score = min(1.0, crit_count * 0.25 + warn_count * 0.08)
        severity += alert_score * 0.25
        components += 1

    # 3. KS 检测
    if drift_signal.get("ks_detected", False):
        severity += 0.15
        components += 1

    # 4. PSI 值
    psi = float(drift_signal.get("psi_value", 0) or 0)
    if psi > 0:
        # PSI > 0.25 视为严重漂移
        psi_score = min(1.0, psi / 0.5)
        severity += psi_score * 0.15
        components += 1

    # 5. ADWIN 漂移 + should_retrain
    if drift_signal.get("adwin_drift", False):
        severity += 0.10
        components += 1
    if drift_signal.get("should_retrain", False):
        severity += 0.10
        components += 1

    # 归一化 (至少有一个分量时)
    if components > 0 and severity > 1.0:
        severity = min(1.0, severity)

    return severity


def _apply_factor(base: float, factor: float, min_val: float, max_val: float) -> float:
    """对参数应用调整因子, 并限制在合法范围."""
    result = base * factor
    result = max(min_val, min(max_val, result))
    # 保留合理精度
    if result >= 1:
        return round(result)
    return round(result, 6)


def adaptive_optimize(
    symbol: str,
    config: dict[str, Any],
    drift_signal: dict[str, Any] | None = None,
    cv_results: list[dict[str, Any]] | None = None,
    override_enabled: bool | None = None,
) -> dict[str, Any]:
    """根据漂移信号自适应优化 LGB 配置.

    Args:
        symbol: 标的代码 (如 "688041.SH")
        config: 基准训练配置 (格式与 LGB_ENHANCED_CONFIG 一致)
        drift_signal: 漂移检测器输出 (来自 Day 1 ModelDriftDetector.generate_report())
        cv_results: 历史 CV 结果 (可选, 用于趋势判断)
        override_enabled: 覆盖 Feature Flag (True=强制启用, False=强制禁用, None=用 Flag)

    Returns:
        新的配置字典 (深拷贝, 不修改原 config):
        {
            "lgb_params": {...},
            "early_stopping_rounds": int,
            "top_n_features": int,           # 可能调整
            "_adaptive_meta": {                # 调试信息
                "severity": float,
                "level": "none"|"light"|"medium"|"heavy",
                "adjustments": {...},
                "enabled": bool,
                "symbol": str,
                "timestamp": str,
            }
        }
    """
    # Feature Flag 检查
    enabled = (
        override_enabled
        if override_enabled is not None
        else _check_feature_flag(ADAPTIVE_OPTIMIZE_CONFIG["feature_flag_name"])
    )

    # 深拷贝原配置 (遵循不可变性原则)
    result = copy.deepcopy(config)

    # 添加元信息字段 (总会添加, 便于下游判断是否被调整过)
    result["_adaptive_meta"] = {
        "enabled": enabled,
        "severity": 0.0,
        "level": "none",
        "adjustments": {},
        "symbol": symbol,
        "timestamp": now_bj().isoformat(),
    }

    if not enabled:
        logger.debug(f"[adaptive_optimize] {symbol}: Feature Flag 关闭, 返回原配置")
        return result

    try:
        # Step 1: 计算漂移强度
        severity = _compute_drift_severity(drift_signal)
        result["_adaptive_meta"]["severity"] = round(severity, 4)

        # Step 2: 判断调整级别
        cfg = ADAPTIVE_OPTIMIZE_CONFIG
        if severity >= cfg["drift_severity_threshold_heavy"]:
            level = "heavy"
        elif severity >= cfg["drift_severity_threshold_medium"]:
            level = "medium"
        elif severity >= cfg["drift_severity_threshold_light"]:
            level = "light"
        else:
            level = "none"

        result["_adaptive_meta"]["level"] = level

        if level == "none":
            logger.debug(
                f"[adaptive_optimize] {symbol}: 漂移强度 {severity:.3f} < 阈值, 无需调整"
            )
            return result

        # Step 3: 应用参数调整
        lgb_params = result.get("lgb_params", {})
        adjustments: dict[str, dict[str, float]] = {}

        # 3.1 学习率
        base_lr = float(lgb_params.get("learning_rate", 0.005))
        lr_factor = cfg[f"lr_factor_{level}"]
        new_lr = _apply_factor(base_lr, lr_factor, cfg["lr_min"], cfg["lr_max"])
        if new_lr != base_lr:
            lgb_params["learning_rate"] = new_lr
            adjustments["learning_rate"] = {
                "from": base_lr,
                "to": new_lr,
                "factor": lr_factor,
            }

        # 3.2 估计器数量
        base_n_est = int(lgb_params.get("n_estimators", 2000))
        n_est_factor = cfg[f"n_est_factor_{level}"]
        new_n_est = _apply_factor(
            base_n_est, n_est_factor, cfg["n_est_min"], cfg["n_est_max"]
        )
        if new_n_est != base_n_est:
            lgb_params["n_estimators"] = int(new_n_est)
            adjustments["n_estimators"] = {
                "from": base_n_est,
                "to": int(new_n_est),
                "factor": n_est_factor,
            }

        # 3.3 树深度
        base_depth = int(lgb_params.get("max_depth", 6))
        depth_factor = cfg[f"max_depth_factor_{level}"]
        new_depth = _apply_factor(
            base_depth, depth_factor, cfg["max_depth_min"], cfg["max_depth_max"]
        )
        if new_depth != base_depth:
            lgb_params["max_depth"] = int(new_depth)
            # 同步调整 num_leaves (通常 <= 2^max_depth)
            base_leaves = int(lgb_params.get("num_leaves", 31))
            new_leaves = min(base_leaves, int(2**new_depth - 1))
            if new_leaves != base_leaves:
                lgb_params["num_leaves"] = new_leaves
                adjustments["num_leaves"] = {"from": base_leaves, "to": new_leaves}
            adjustments["max_depth"] = {
                "from": base_depth,
                "to": int(new_depth),
                "factor": depth_factor,
            }

        # 3.4 正则化
        base_reg_alpha = float(lgb_params.get("reg_alpha", 0.1))
        base_reg_lambda = float(lgb_params.get("reg_lambda", 0.5))
        reg_factor = cfg[f"reg_factor_{level}"]
        new_reg_alpha = _apply_factor(base_reg_alpha, reg_factor, 0.0, cfg["reg_max"])
        new_reg_lambda = _apply_factor(base_reg_lambda, reg_factor, 0.0, cfg["reg_max"])
        if new_reg_alpha != base_reg_alpha:
            lgb_params["reg_alpha"] = new_reg_alpha
            adjustments["reg_alpha"] = {
                "from": base_reg_alpha,
                "to": new_reg_alpha,
                "factor": reg_factor,
            }
        if new_reg_lambda != base_reg_lambda:
            lgb_params["reg_lambda"] = new_reg_lambda
            adjustments["reg_lambda"] = {
                "from": base_reg_lambda,
                "to": new_reg_lambda,
                "factor": reg_factor,
            }

        # 3.5 早停轮数
        base_early = int(result.get("early_stopping_rounds", 200))
        early_factor = cfg[f"early_stop_factor_{level}"]
        new_early = _apply_factor(
            base_early, early_factor, cfg["early_stop_min"], cfg["early_stop_max"]
        )
        if new_early != base_early:
            result["early_stopping_rounds"] = int(new_early)
            adjustments["early_stopping_rounds"] = {
                "from": base_early,
                "to": int(new_early),
                "factor": early_factor,
            }

        # 保存调整记录
        result["_adaptive_meta"]["adjustments"] = adjustments

        # 日志
        if adjustments:
            adj_summary = ", ".join(
                f"{k}: {v['from']}→{v['to']}" for k, v in adjustments.items()
            )
            logger.info(
                f"[adaptive_optimize] {symbol}: 级别={level}, 强度={severity:.3f}, 调整: {adj_summary}"
            )
        else:
            logger.debug(f"[adaptive_optimize] {symbol}: 级别={level} 但无实际参数变化")

    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:

        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.warning(f"[adaptive_optimize] {symbol}: 自适应优化失败, 返回原配置: {e}")
        result["_adaptive_meta"]["error"] = str(e)

    return result


# ========== 便捷函数 ==========
def quick_optimize_for_drift(
    symbol: str,
    config: dict[str, Any],
    severity_hint: str = "light",  # "light" | "medium" | "heavy"
) -> dict[str, Any]:
    """快速构造一个模拟漂移信号并调用 adaptive_optimize.

    用于调试或手动触发特定级别的参数调整.
    """
    mock_signal = {
        "ic_stats": {"mean_ic": 0.05, "ic_10d": 0.05},
        "alerts": [],
        "ks_detected": False,
        "psi_value": 0.0,
        "adwin_drift": False,
        "should_retrain": False,
    }
    # 根据级别调整各分量以触发对应阈值
    # 目标: light → [0.25, 0.50), medium → [0.50, 0.75), heavy → [0.75, 1.0]
    if severity_hint == "light":
        mock_signal["alerts"] = [{"severity": "critical"}, {"severity": "critical"}]
        mock_signal["ic_stats"]["ic_10d"] = 0.025  # IC 下降 50%
        # 预期: 0.35*0.5 + 0.25*0.5 = 0.175 + 0.125 = 0.30
    elif severity_hint == "medium":
        mock_signal["alerts"] = [{"severity": "critical"}, {"severity": "critical"}]
        mock_signal["ic_stats"]["ic_10d"] = 0.02  # IC 下降 60%
        mock_signal["ks_detected"] = True
        mock_signal["psi_value"] = 0.15
        # 预期: 0.35*0.6 + 0.25*0.5 + 0.15*1 + 0.15*0.3 = 0.21 + 0.125 + 0.15 + 0.045 = 0.53
    elif severity_hint == "heavy":
        mock_signal["alerts"] = [{"severity": "critical"} for _ in range(4)]
        mock_signal["ic_stats"]["ic_10d"] = -0.01  # IC 转负
        mock_signal["ks_detected"] = True
        mock_signal["psi_value"] = 0.35
        mock_signal["adwin_drift"] = True
        mock_signal["should_retrain"] = True
        # 预期: > 0.75

    return adaptive_optimize(
        symbol, config, drift_signal=mock_signal, override_enabled=True
    )


if __name__ == "__main__":
    # 快速自检: 测试所有 4 个级别
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    base_config = {
        "lgb_params": {
            "learning_rate": 0.005,
            "n_estimators": 2000,
            "max_depth": 6,
            "num_leaves": 31,
            "reg_alpha": 0.1,
            "reg_lambda": 0.5,
        },
        "early_stopping_rounds": 200,
    }

    logger.info("\n" + "=" * 70)
    logger.info("  N4 adaptive_optimize 自检 (4 个级别)")
    logger.info("=" * 70)
    logger.info("\n基准配置:")
    print(
        f"  lr={base_config['lgb_params']['learning_rate']}, "
        f"n_est={base_config['lgb_params']['n_estimators']}, "
        f"depth={base_config['lgb_params']['max_depth']}, "
        f"early_stop={base_config['early_stopping_rounds']}"
    )

    for level in ["none", "light", "medium", "heavy"]:
        logger.info(f"\n--- 级别: {level} ---")
        if level == "none":
            result = adaptive_optimize("TEST", base_config, override_enabled=True)
        else:
            result = quick_optimize_for_drift("TEST", base_config, severity_hint=level)

        meta = result.get("_adaptive_meta", {})
        adj = meta.get("adjustments", {})
        logger.info(f"  漂移强度: {meta.get('severity', 0):.4f}")
        logger.info(f"  实际级别: {meta.get('level', 'none')}")
        if adj:
            for param, change in adj.items():
                logger.info(f"  {param}: {change['from']} → {change['to']}")
        else:
            logger.info("  (无调整)")

    logger.info("\n" + "=" * 70)
    logger.info("  ✅ N4 自检完成")
    logger.info("=" * 70)
