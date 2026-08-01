# -*- coding: utf-8 -*-
"""风险参数统一访问层 (B1.3)
================================
所有模块应通过此模块获取风险阈值, 而非硬编码。

历史问题:
    MAX_DRAWDOWN_LIMIT 在 6 处硬编码 0.15 + 1 处 0.08 (中性策略专属),
    散落难维护, 修改时容易遗漏。

解决方案:
    config/risk_params.yaml (唯一事实源)
        ↓
    utils.config_manager.get_risk_params_config() (加载 + 缓存)
        ↓
    utils.risk_params.get_max_drawdown_limit() / get_quant_neutral_max_drawdown()
    (本模块, fail-safe 兜底)

fail-safe 策略:
    ConfigManager 加载失败 (yaml 缺失/格式错误) 时, 返回模块内默认常量,
    确保生产环境永不因配置缺失而崩溃。

用法:
    from utils.risk_params import get_max_drawdown_limit, get_quant_neutral_max_drawdown
    max_dd = get_max_drawdown_limit()  # 0.15
    qn_dd = get_quant_neutral_max_drawdown()  # 0.08
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# === 模块内 fail-safe 兜底常量 (ConfigManager 不可用时回退) ===
# 与 config/risk_params.yaml 保持一致, 修改时需同步
_FALLBACK_MAX_DRAWDOWN_LIMIT = 0.15
_FALLBACK_QUANT_NEUTRAL_MAX_DRAWDOWN = 0.08
_FALLBACK_DAILY_AMOUNT_LIMIT = 200000
_FALLBACK_PRICE_PROTECTION_PCT = 0.03
_FALLBACK_DAILY_LOSS_STOP_PCT = 0.03
_FALLBACK_PORTFOLIO_DRAWDOWN_STOP_PCT = 0.05


def _get_risk_cfg() -> dict:
    """加载 risk_params 配置 (fail-safe, 失败返回空 dict)"""
    try:
        from utils.config_manager import get_risk_params_config
        cfg = get_risk_params_config()
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:
        logger.warning("[risk_params] 加载 risk_params.yaml 失败, 使用兜底常量: %s", exc)
        return {}


def get_max_drawdown_limit() -> float:
    """获取组合整体最大回撤上限 (生产标准: 0.15)

    用于回测验收、组合风控、alpha 对冲引擎、年化收益预测。
    """
    cfg = _get_risk_cfg()
    try:
        val = float(cfg.get("max_drawdown_limit", _FALLBACK_MAX_DRAWDOWN_LIMIT))
        # 合理性校验: 应在 [0.01, 0.50] 区间
        if not 0.01 <= val <= 0.50:
            logger.warning("[risk_params] max_drawdown_limit=%s 越界, 回退到 %s",
                           val, _FALLBACK_MAX_DRAWDOWN_LIMIT)
            return _FALLBACK_MAX_DRAWDOWN_LIMIT
        return val
    except (TypeError, ValueError) as exc:
        logger.warning("[risk_params] max_drawdown_limit 解析失败, 回退到 %s: %s",
                       _FALLBACK_MAX_DRAWDOWN_LIMIT, exc)
        return _FALLBACK_MAX_DRAWDOWN_LIMIT


def get_quant_neutral_max_drawdown() -> float:
    """获取量化中性策略专属最大回撤上限 (策略文档: 0.08)

    中性策略账户 (70万资金, 140万名义敞口) 风控更严格。
    """
    cfg = _get_risk_cfg()
    try:
        val = float(cfg.get("quant_neutral_max_drawdown", _FALLBACK_QUANT_NEUTRAL_MAX_DRAWDOWN))
        if not 0.01 <= val <= 0.50:
            logger.warning("[risk_params] quant_neutral_max_drawdown=%s 越界, 回退到 %s",
                           val, _FALLBACK_QUANT_NEUTRAL_MAX_DRAWDOWN)
            return _FALLBACK_QUANT_NEUTRAL_MAX_DRAWDOWN
        return val
    except (TypeError, ValueError) as exc:
        logger.warning("[risk_params] quant_neutral_max_drawdown 解析失败, 回退到 %s: %s",
                       _FALLBACK_QUANT_NEUTRAL_MAX_DRAWDOWN, exc)
        return _FALLBACK_QUANT_NEUTRAL_MAX_DRAWDOWN


def get_daily_amount_limit() -> int:
    """获取单日金额上限 (默认 200000)"""
    cfg = _get_risk_cfg()
    try:
        return int(cfg.get("daily_amount_limit", _FALLBACK_DAILY_AMOUNT_LIMIT))
    except (TypeError, ValueError):
        return _FALLBACK_DAILY_AMOUNT_LIMIT


def get_price_protection_pct() -> float:
    """获取价格保护带百分比 (默认 0.03 = ±3%)"""
    cfg = _get_risk_cfg()
    try:
        return float(cfg.get("price_protection_pct", _FALLBACK_PRICE_PROTECTION_PCT))
    except (TypeError, ValueError):
        return _FALLBACK_PRICE_PROTECTION_PCT


def get_daily_loss_stop_pct() -> float:
    """获取单日累计亏损熔断阈值 (默认 0.03 = -3%)"""
    cfg = _get_risk_cfg()
    try:
        return float(cfg.get("daily_loss_stop_pct", _FALLBACK_DAILY_LOSS_STOP_PCT))
    except (TypeError, ValueError):
        return _FALLBACK_DAILY_LOSS_STOP_PCT


def get_portfolio_drawdown_stop_pct() -> float:
    """获取组合回撤熔断阈值 (默认 0.05 = -5%)"""
    cfg = _get_risk_cfg()
    try:
        return float(cfg.get("portfolio_drawdown_stop_pct", _FALLBACK_PORTFOLIO_DRAWDOWN_STOP_PCT))
    except (TypeError, ValueError):
        return _FALLBACK_PORTFOLIO_DRAWDOWN_STOP_PCT


if __name__ == "__main__":
    # 自测
    logger.info(f"max_drawdown_limit (组合整体): {get_max_drawdown_limit()}")
    logger.info(f"quant_neutral_max_drawdown (中性策略): {get_quant_neutral_max_drawdown()}")
    logger.info(f"daily_amount_limit: {get_daily_amount_limit()}")
    logger.info(f"price_protection_pct: {get_price_protection_pct()}")
    logger.info(f"daily_loss_stop_pct: {get_daily_loss_stop_pct()}")
    logger.info(f"portfolio_drawdown_stop_pct: {get_portfolio_drawdown_stop_pct()}")
