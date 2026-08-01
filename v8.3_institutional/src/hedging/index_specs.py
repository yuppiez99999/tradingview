# -*- coding: utf-8 -*-
"""
指数规格配置加载器 (B-4.6)
==========================

从 config/index_specs.yaml 加载指数成分股权重和合约规格。
读取失败时回退到硬编码默认值, 保证永不崩溃。

唯一事实源: v8.3_institutional/config/index_specs.yaml
历史来源: hedge_engine_v59.py L38-136 (2026-08-01 抽取)

提供常量:
  - INDEX_WEIGHTS_CSI300: 沪深300成分股权重 (简化版)
  - INDEX_WEIGHTS_CSI500: 中证500成分股权重 (简化版)
  - INDEX_FUTURES_SPECS: 股指期货合约规格 (IF/IC/IM/IH)
  - ETF_OPTIONS_SPECS: ETF期权合约规格 (510300/510050/000300)

使用方式:
  from hedging.index_specs import INDEX_FUTURES_SPECS
  spec = INDEX_FUTURES_SPECS["IF"]
  multiplier = spec["multiplier"]  # 300
"""

import logging
from typing import Dict, Any

logger = logging.getLogger("index_specs")


# ── 硬编码默认值 (yaml 读取失败时的回退, 保证永不崩溃) ──
# 与 config/index_specs.yaml 保持同步, 定期校验一致性

_DEFAULT_INDEX_WEIGHTS_CSI300: Dict[str, float] = {
    "300750": 0.042,
    "600519": 0.055,
    "000858": 0.038,
    "601318": 0.032,
    "600036": 0.028,
    "000333": 0.025,
    "002415": 0.022,
    "300059": 0.020,
    "600276": 0.018,
    "601166": 0.016,
    "600900": 0.015,
    "000651": 0.014,
    "002475": 0.013,
    "601899": 0.013,
    "603259": 0.012,
}

_DEFAULT_INDEX_WEIGHTS_CSI500: Dict[str, float] = {
    "688981": 0.008,
    "688041": 0.007,
    "002371": 0.006,
    "300308": 0.005,
    "000792": 0.004,
    "600219": 0.004,
    "002422": 0.004,
    "000425": 0.003,
    "600019": 0.003,
    "601088": 0.005,
}

_DEFAULT_INDEX_FUTURES_SPECS: Dict[str, Dict[str, Any]] = {
    "IF": {
        "name": "沪深300股指期货",
        "underlying": "CSI300",
        "multiplier": 300,
        "margin_pct": 0.12,
        "tick_size": 0.2,
        "contracts_per_month": 4,
        "dominant_contract_months": [3, 6, 9, 12],
        "sina_code": "nf_IF0",
    },
    "IC": {
        "name": "中证500股指期货",
        "underlying": "CSI500",
        "multiplier": 200,
        "margin_pct": 0.14,
        "tick_size": 0.2,
        "contracts_per_month": 4,
        "dominant_contract_months": [3, 6, 9, 12],
        "sina_code": "nf_IC0",
    },
    "IM": {
        "name": "中证1000股指期货",
        "underlying": "CSI1000",
        "multiplier": 200,
        "margin_pct": 0.15,
        "tick_size": 0.2,
        "contracts_per_month": 4,
        "dominant_contract_months": [3, 6, 9, 12],
        "sina_code": "nf_IM0",
    },
    "IH": {
        "name": "上证50股指期货",
        "underlying": "SSE50",
        "multiplier": 300,
        "margin_pct": 0.12,
        "tick_size": 0.2,
        "contracts_per_month": 4,
        "dominant_contract_months": [3, 6, 9, 12],
        "sina_code": "nf_IH0",
    },
}

_DEFAULT_ETF_OPTIONS_SPECS: Dict[str, Dict[str, Any]] = {
    "510300": {
        "name": "沪深300ETF期权",
        "underlying": "510300.SH",
        "multiplier": 10000,
        "strike_step": 0.1,
        "exchange": "SSE",
    },
    "510050": {
        "name": "上证50ETF期权",
        "underlying": "510050.SH",
        "multiplier": 10000,
        "strike_step": 0.05,
        "exchange": "SSE",
    },
    "000300": {
        "name": "沪深300指数期权",
        "underlying": "000300.SH",
        "multiplier": 100,
        "strike_step": 50,
        "exchange": "CFFEX",
    },
}


def _load_from_yaml() -> Dict[str, Any]:
    """从 config/index_specs.yaml 加载配置 (失败返回空 dict)"""
    try:
        from utils.config_manager import get_config
        cfg = get_config("index_specs")
        if cfg and isinstance(cfg, dict):
            logger.debug("[index_specs] yaml 配置加载成功")
            return cfg
    except Exception as e:  # pragma: no cover — 回退路径
        logger.warning("[index_specs] yaml 加载失败, 使用硬编码默认值: %s", e)
    return {}


# ── 模块级加载 (首次 import 时执行一次) ──
_cfg = _load_from_yaml()

INDEX_WEIGHTS_CSI300: Dict[str, float] = _cfg.get(
    "index_weights_csi300", _DEFAULT_INDEX_WEIGHTS_CSI300
)
INDEX_WEIGHTS_CSI500: Dict[str, float] = _cfg.get(
    "index_weights_csi500", _DEFAULT_INDEX_WEIGHTS_CSI500
)
INDEX_FUTURES_SPECS: Dict[str, Dict[str, Any]] = _cfg.get(
    "index_futures_specs", _DEFAULT_INDEX_FUTURES_SPECS
)
ETF_OPTIONS_SPECS: Dict[str, Dict[str, Any]] = _cfg.get(
    "etf_options_specs", _DEFAULT_ETF_OPTIONS_SPECS
)


def reload() -> None:
    """强制重新加载 yaml 配置 (供 ConfigManager hot reload 后调用)"""
    global INDEX_WEIGHTS_CSI300, INDEX_WEIGHTS_CSI500
    global INDEX_FUTURES_SPECS, ETF_OPTIONS_SPECS
    cfg = _load_from_yaml()
    if cfg:
        INDEX_WEIGHTS_CSI300 = cfg.get("index_weights_csi300", _DEFAULT_INDEX_WEIGHTS_CSI300)
        INDEX_WEIGHTS_CSI500 = cfg.get("index_weights_csi500", _DEFAULT_INDEX_WEIGHTS_CSI500)
        INDEX_FUTURES_SPECS = cfg.get("index_futures_specs", _DEFAULT_INDEX_FUTURES_SPECS)
        ETF_OPTIONS_SPECS = cfg.get("etf_options_specs", _DEFAULT_ETF_OPTIONS_SPECS)
        logger.debug("[index_specs] 配置已重新加载")


__all__ = [
    "INDEX_WEIGHTS_CSI300",
    "INDEX_WEIGHTS_CSI500",
    "INDEX_FUTURES_SPECS",
    "ETF_OPTIONS_SPECS",
    "reload",
]
