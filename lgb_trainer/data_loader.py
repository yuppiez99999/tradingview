# -*- coding: utf-8 -*-
"""真实 OHLCV 数据加载 (B3.5: 从 lgb_enhanced_trainer.py 抽取)

本模块集中以下职责:
  - load_real_ohlcv: 单标的拉取真实 OHLCV (free-stockdb → Wind MCP → iFinD → 新浪)
  - fetch_all_real_ohlcv: 批量拉取持仓标的 OHLCV

数据源优先级 (阶段 1: 研究/训练专用, 不影响实盘):
    free-stockdb 本地引擎 (极速) → Wind MCP → iFinD MCP → 新浪 HTTP → 兜底

路径/缓存目录由主模块通过 configure_paths 注入。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger("lgb_enhanced")


# ============================================================
# 路径 (由主模块注入)
# ============================================================
BASE_DIR: Path = Path(__file__).resolve().parent.parent
CACHE_DIR: Path = BASE_DIR / "cache" / "ohlcv"


def configure_paths(base_dir: Path, cache_dir: Path) -> None:
    """由主模块注入路径。

    Args:
        base_dir: 项目根目录
        cache_dir: OHLCV 缓存目录
    """
    global BASE_DIR, CACHE_DIR
    BASE_DIR = base_dir
    CACHE_DIR = cache_dir


# ============================================================
# 真实 OHLCV 数据加载
# ============================================================
def load_real_ohlcv(symbol: str, period: str = "2y") -> Optional[pd.DataFrame]:
    """拉取真实 OHLCV (free-stockdb 本地优先, 自动回退 MarketDataProvider)

    数据源优先级 (阶段 1: 研究/训练专用, 不影响实盘):
        free-stockdb 本地引擎 (极速) → Wind MCP → iFinD MCP → 新浪 HTTP → 兜底

    Args:
        symbol: 标的代码 (如 "688041.SH" 或 "688041")
        period: 周期 (1y/2y/3y/5y)

    Returns:
        DataFrame[open, high, low, close, volume] 或 None
    """
    # data_provider.get_historical_data 仅接受纯 6 位代码, 不接受 .SH/.SZ 后缀
    # 剥离后缀, 统一为纯代码
    raw_symbol = symbol
    for sfx in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
        if symbol.endswith(sfx):
            symbol = symbol[: -len(sfx)]
            break

    # 缓存检查 (用原始带后缀名命名, 避免冲突)
    cache_file = CACHE_DIR / f"{raw_symbol.replace('.', '_')}_{period}.parquet"
    if cache_file.exists():
        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if (datetime.now() - mtime).total_seconds() < 12 * 3600:  # 12 小时缓存
            # C11 修复: 缓存读取失败时删除损坏文件, 避免后续训练持续命中损坏缓存
            try:
                df = pd.read_parquet(cache_file)
                if not df.empty and len(df) >= 100:
                    return df
            except Exception as e:
                logger.warning(f"  {raw_symbol}: 缓存读取失败, 删除损坏文件: {e}")
                try:
                    cache_file.unlink(missing_ok=True)
                except Exception as e:
                    logger.warning(
                        f"Unexpected error in load_real_ohlcv cache cleanup",
                        exc_info=True,
                    )

    try:
        # 阶段 1: free-stockdb 本地优先 (研究/训练专用), 自动回退到 MarketDataProvider
        from utils.free_stockdb_adapter import get_historical_data

        df = get_historical_data(symbol, period)
        if df is None or df.empty:
            logger.warning(f"  {raw_symbol}: 真实 OHLCV 拉取失败 (纯代码 {symbol})")
            return None

        # 标准化列名和索引
        df = df.copy()
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.index.name = "date"

        # 保存缓存 (C11 修复: 写入失败时记录警告, 避免静默丢失缓存)
        try:
            df.to_parquet(cache_file)
        except Exception as e:
            logger.warning(f"  {symbol}: 缓存写入失败 (磁盘满/权限?): {e}")

        return df
    except Exception as e:
        logger.error(f"  {symbol}: 拉取真实 OHLCV 异常: {e}")
        return None


def fetch_all_real_ohlcv(
    symbols: List[Tuple],
    period: str = "2y",
) -> Dict[str, pd.DataFrame]:
    """批量拉取真实 OHLCV

    Args:
        symbols: [(code, suffix, server_type, name, style), ...]
        period: 周期

    Returns:
        {code: DataFrame}
    """
    ohlcv_dict: Dict[str, pd.DataFrame] = {}
    logger.info(f"拉取真实 OHLCV 数据 ({period})...")
    for code, suffix, _, name, _ in symbols:
        symbol = f"{code}{suffix}"
        df = load_real_ohlcv(symbol, period)
        if df is not None and not df.empty:
            ohlcv_dict[code] = df
            logger.info(f"  {code} ({name}): {len(df)} 日真实数据")
        else:
            logger.warning(f"  {code} ({name}): 拉取失败, 跳过")
    return ohlcv_dict
