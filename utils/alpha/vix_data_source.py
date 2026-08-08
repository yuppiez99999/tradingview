# -*- coding: utf-8 -*-
"""
VIX 数据源 (A股 iVIX 替代方案)
================================

背景:
    中国波指 iVIX 已于 2018 年停用 (见 utils/gamma_engine.py:133 注释).
    原始 _fetch_vix 中的 wind_get_quote("VIX") 和 ak.stock_zh_index_vix() 均不可用.

方案 (用户确认: 510050 期权 IV + RV 备选):
    由于 tools/wind_mcp_fetcher.py 中 wind_get_option_iv 函数实际不存在,
    本模块采用以下降级链作为 VIX 替代:
        1. 主: 从 shadow_state.json 计算 20 日 realized_vol, 转换为 VIX 量纲
           VIX_proxy = realized_vol * 100 (realized_vol 已年化, 如 0.25 → 25)
        2. 备: Wind MCP 获取 510050.SH 近 30 日 K 线, 计算日收益波动率年化后 * 100
        3. 缓存兜底: reports/volatility/vix_cache.json (TTL 5 分钟)

设计原则:
    - fail-safe: 任何数据源失败都返回 None, 由 VolRegimeWeighter 降级到 neutral
    - 缓存保护: 盘中高频调用 (30s 周期) 使用 5 分钟缓存, 避免配额耗尽
    - EOD 强制刷新: use_cache=False 时跳过缓存读取, 但仍写入新缓存
    - 不阻塞主循环: 所有异常 catch, 返回 None

用法:
    from utils.alpha.vix_data_source import VixDataSource
    vix = VixDataSource().fetch_vix(use_cache=True)  # 盘中
    vix = VixDataSource().fetch_vix(use_cache=False)  # EOD 强制刷新
"""
from __future__ import annotations

import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ============================================================
# 路径常量
# ============================================================
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CACHE_DIR = _PROJECT_ROOT / "reports" / "volatility"
_CACHE_PATH = _CACHE_DIR / "vix_cache.json"
_SHADOW_STATE_PATH = _PROJECT_ROOT / "output" / "shadow_account" / "shadow_state.json"

# 缓存 TTL (秒) - 盘中 30s 周期 × 10 = 5 分钟, 平衡时效性与配额保护
_CACHE_TTL_SECONDS = 300

# 510050 期权标的 Wind 代码 (50ETF)
_WIND_UNDERLYING_CODE = "510050.SH"

# RV 计算窗口
_RV_LOOKBACK_DAYS = 20
_ANNUALIZATION_FACTOR = math.sqrt(252)


class VixDataSource:
    """VIX 替代数据源 (RV from shadow_state + 510050 K线 + 缓存).

    属性:
        CACHE_PATH: 缓存文件路径
        CACHE_TTL_SECONDS: 缓存有效期 (秒)
    """

    CACHE_PATH = _CACHE_PATH
    CACHE_TTL_SECONDS = _CACHE_TTL_SECONDS

    def __init__(self, cache_path: Optional[Path] = None) -> None:
        """初始化, 确保缓存目录存在.

        Args:
            cache_path: 自定义缓存路径 (测试用), None 时用默认路径
        """
        if cache_path is not None:
            self.CACHE_PATH = cache_path
        # 确保缓存目录存在
        Path(self.CACHE_PATH).parent.mkdir(parents=True, exist_ok=True)

    def fetch_vix(self, use_cache: bool = True) -> Optional[float]:
        """获取 VIX 替代值 (降级链: shadow_state RV → 510050 K线 → 缓存 → None).

        Args:
            use_cache: 是否使用缓存 (盘中 True, EOD False 强制刷新)

        Returns:
            VIX 数值 (如 25.3) 或 None (全失败时)
        """
        # 1. 主数据源: 从 shadow_state.json 计算 RV
        vix = self._fetch_from_shadow_state_rv()
        if vix is not None:
            self._save_cache(vix, source="shadow_state_rv")
            logger.debug("VIX 获取成功 (shadow_state_rv): %.2f", vix)
            return vix

        # 2. 备选: Wind MCP 510050 K 线计算波动率
        vix = self._fetch_from_wind_kline()
        if vix is not None:
            self._save_cache(vix, source="wind_kline_vol")
            logger.debug("VIX 获取成功 (wind_kline_vol): %.2f", vix)
            return vix

        # 3. 缓存兜底
        if use_cache:
            cached = self._load_cache()
            if cached is not None:
                logger.warning(
                    "VIX 数据源全失败, 使用缓存值: %.2f (source=%s, 缓存时间=%s)",
                    cached.get("vix", 0),
                    cached.get("source", "unknown"),
                    cached.get("timestamp", "unknown"),
                )
                return float(cached["vix"])

        logger.warning("VIX 数据源全失败且无缓存, 返回 None")
        return None

    # ============================================================
    # 主数据源: shadow_state.json → realized_vol → VIX proxy
    # ============================================================
    def _fetch_from_shadow_state_rv(self) -> Optional[float]:
        """从 shadow_state.json 的 daily_nav 计算 20 日已实现波动率.

        VIX_proxy = realized_vol * 100 (realized_vol 已年化, 如 0.25 → 25)

        Returns:
            VIX 替代值 或 None
        """
        try:
            if not _SHADOW_STATE_PATH.exists():
                logger.debug("shadow_state.json 不存在: %s", _SHADOW_STATE_PATH)
                return None

            with _SHADOW_STATE_PATH.open("r", encoding="utf-8") as f:
                state = json.load(f)

            daily_nav = state.get("daily_nav", [])
            if not daily_nav or len(daily_nav) < 2:
                logger.debug("daily_nav 数据不足 (len=%d)", len(daily_nav))
                return None

            # 提取 daily_return 序列 (取最近 _RV_LOOKBACK_DAYS 天)
            returns: list[float] = []
            for record in daily_nav:
                ret = record.get("daily_return")
                if ret is not None:
                    try:
                        returns.append(float(ret))
                    except (TypeError, ValueError):
                        continue

            if len(returns) < 2:
                logger.debug("daily_return 有效数据不足 (len=%d)", len(returns))
                return None

            # 取最近 _RV_LOOKBACK_DAYS 天 (不足时用全部)
            recent_returns = returns[-_RV_LOOKBACK_DAYS:] if len(returns) >= _RV_LOOKBACK_DAYS else returns

            # 计算日波动率 (标准差)
            n = len(recent_returns)
            mean_ret = sum(recent_returns) / n
            variance = sum((r - mean_ret) ** 2 for r in recent_returns) / max(n - 1, 1)
            daily_vol = math.sqrt(variance)

            # 年化并转换为 VIX 量纲 (0.25 → 25)
            annual_vol = daily_vol * _ANNUALIZATION_FACTOR
            vix_proxy = annual_vol * 100

            # 合理性校验 (VIX 通常在 10~80 之间)
            if vix_proxy < 5 or vix_proxy > 150:
                logger.warning("VIX 替代值异常: %.2f (年化波动率=%.4f), 跳过", vix_proxy, annual_vol)
                return None

            logger.debug(
                "shadow_state RV: 日波动率=%.4f%%, 年化=%.2f%%, VIX_proxy=%.2f",
                daily_vol * 100, annual_vol * 100, vix_proxy,
            )
            return float(vix_proxy)

        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("从 shadow_state 计算 RV 失败: %s", e)
            return None

    # ============================================================
    # 备选数据源: Wind MCP 510050 K 线
    # ============================================================
    def _fetch_from_wind_kline(self) -> Optional[float]:
        """从 Wind MCP 获取 510050 近 30 日 K 线, 计算波动率作为 VIX 替代.

        Returns:
            VIX 替代值 或 None
        """
        try:
            # 将 tools/ 目录加入 sys.path
            tools_dir = str(_PROJECT_ROOT / "tools")
            if tools_dir not in sys.path:
                sys.path.insert(0, tools_dir)

            from wind_mcp_fetcher import wind_get_kline  # type: ignore[import-not-found]

            kline_data = wind_get_kline(_WIND_UNDERLYING_CODE, days=30)
            if not kline_data or len(kline_data) < 5:
                logger.debug("Wind K 线数据不足: %d 条", len(kline_data) if kline_data else 0)
                return None

            # 提取收盘价, 计算日收益率
            closes: list[float] = []
            for record in kline_data:
                close = record.get("close") or record.get("CLOSE")
                if close is not None:
                    try:
                        closes.append(float(close))
                    except (TypeError, ValueError):
                        continue

            if len(closes) < 5:
                logger.debug("收盘价有效数据不足: %d 条", len(closes))
                return None

            # 计算日收益率
            returns: list[float] = []
            for i in range(1, len(closes)):
                if closes[i - 1] > 0:
                    returns.append((closes[i] - closes[i - 1]) / closes[i - 1])

            if len(returns) < 2:
                return None

            # 计算日波动率 (标准差)
            n = len(returns)
            mean_ret = sum(returns) / n
            variance = sum((r - mean_ret) ** 2 for r in returns) / max(n - 1, 1)
            daily_vol = math.sqrt(variance)

            # 年化并转换为 VIX 量纲
            annual_vol = daily_vol * _ANNUALIZATION_FACTOR
            vix_proxy = annual_vol * 100

            if vix_proxy < 5 or vix_proxy > 150:
                logger.warning("Wind K 线 VIX 替代值异常: %.2f, 跳过", vix_proxy)
                return None

            logger.debug(
                "Wind K线 RV: 日波动率=%.4f%%, 年化=%.2f%%, VIX_proxy=%.2f",
                daily_vol * 100, annual_vol * 100, vix_proxy,
            )
            return float(vix_proxy)

        except ImportError as e:
            logger.debug("wind_mcp_fetcher 导入失败: %s", e)
            return None
        except (OSError, ValueError, TypeError, RuntimeError, TimeoutError, ConnectionError) as e:
            logger.debug("Wind K 线获取失败: %s", e)
            return None

    # ============================================================
    # 缓存读写
    # ============================================================
    def _save_cache(self, vix: float, source: str) -> None:
        """写入 VIX 缓存.

        Args:
            vix: VIX 数值
            source: 数据源标识 (shadow_state_rv / wind_kline_vol)
        """
        try:
            cache_data = {
                "vix": float(vix),
                "source": source,
                "timestamp": datetime.now().isoformat(),
                "ttl": _CACHE_TTL_SECONDS,
            }
            cache_path = Path(self.CACHE_PATH)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with cache_path.open("w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except (OSError, ValueError, TypeError) as e:
            logger.warning("写入 VIX 缓存失败: %s", e)

    def _load_cache(self) -> Optional[dict[str, Any]]:
        """读取 VIX 缓存 (检查 TTL).

        Returns:
            缓存字典 {"vix": float, "source": str, "timestamp": str} 或 None
        """
        try:
            cache_path = Path(self.CACHE_PATH)
            if not cache_path.exists():
                return None

            with cache_path.open("r", encoding="utf-8") as f:
                cache_data = json.load(f)

            # 检查 TTL
            timestamp_str = cache_data.get("timestamp")
            if not timestamp_str:
                return None

            cached_time = datetime.fromisoformat(timestamp_str)
            age = (datetime.now() - cached_time).total_seconds()

            if age > _CACHE_TTL_SECONDS:
                logger.debug("VIX 缓存已过期 (age=%.0fs, ttl=%ds)", age, _CACHE_TTL_SECONDS)
                return None

            vix = cache_data.get("vix")
            if vix is None or not isinstance(vix, (int, float)):
                return None

            return cache_data

        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            logger.debug("读取 VIX 缓存失败: %s", e)
            return None


# ============================================================
# 便捷函数
# ============================================================
def fetch_vix(use_cache: bool = True) -> Optional[float]:
    """便捷函数: 获取 VIX 替代值.

    Args:
        use_cache: 是否使用缓存

    Returns:
        VIX 数值 或 None

    Usage:
        >>> from utils.alpha.vix_data_source import fetch_vix
        >>> vix = fetch_vix(use_cache=True)
    """
    return VixDataSource().fetch_vix(use_cache=use_cache)


__all__ = ["VixDataSource", "fetch_vix"]
