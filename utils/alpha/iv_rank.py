"""IV Rank 数据源 (RV-based proxy).

为 ETF 期权组合策略提供波动率分位数 (IV Rank): 当前已实现波动率在过去
N 日 (默认 252 交易日) 滚动 RV 序列中的百分位, 驱动策略参数自适应
(高波动加深保护 / 低波动多收租).

数据形态说明:
    Wind MCP 无期权隐波接口 (vix_data_source.py 已确认), 本模块用
    510050 K 线滚动 20 日年化 RV 作 proxy. 接口命名 IVRank, 真实期权
    IV 数据源接入时只需替换 provider 内部实现, 消费方 (iv_adaptive /
    tool_selector) 不变.

降级链 (fail-open, 全失败返回 None → 调用方回退静态参数):
    1. Wind MCP kline (lookback+rv_window+30 日) → 滚动 RV 序列 → percentile
    2. shadow_state.json daily_nav (只读, 不写) → 滚动 RV 序列
    3. 历史缓存 iv_rank_cache.json 的 history 段 (≥ min_history_days 条)

与 vix_data_source 的关系:
    vix_data_source 返回"当前值" (无历史序列), 本模块返回"分位数" (需
    历史序列做分母), 数据形态不同故独立文件; 降级链/缓存/数值校验模式
    复用 vix_data_source 的成熟范式. vix_data_source 被生产链消费, 属
    风险敏感区, 不扩展它以保持零回归面.
"""

from __future__ import annotations

import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path

from utils.datetime_utils import now_bj

logger = logging.getLogger(__name__)

# ============================================================
# 路径常量
# ============================================================
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CACHE_DIR = _PROJECT_ROOT / "reports" / "volatility"
_CACHE_PATH = _CACHE_DIR / "iv_rank_cache.json"
_SHADOW_STATE_PATH = _PROJECT_ROOT / "output" / "shadow_account" / "shadow_state.json"

# RV proxy 标的 (50ETF, 与 vix_data_source 一致)
_DEFAULT_UNDERLYING = "510050.SH"
_RV_WINDOW = 20
_ANNUALIZATION_FACTOR = math.sqrt(252)

# 历史缓存滚动上限 (条), 防止无限增长
_MAX_HISTORY = 400


class IVRankProvider:
    """IV Rank 数据提供者 (RV-based proxy, fail-open).

    属性:
        CACHE_PATH: 缓存文件路径 (类属性, 测试可注入替换)
    """

    CACHE_PATH = _CACHE_PATH

    def __init__(
        self,
        config: dict | None = None,
        cache_path: Path | None = None,
    ) -> None:
        """初始化.

        Args:
            config: 可选配置 {lookback_days=252, min_history_days=60,
                     method="percentile", cache_ttl_seconds=300,
                     underlying="510050.SH", rv_window=20}
            cache_path: 自定义缓存路径 (测试用), None 用默认
        """
        cfg = config or {}
        self.lookback_days = int(cfg.get("lookback_days", 252))
        self.min_history_days = int(cfg.get("min_history_days", 60))
        self.method = str(cfg.get("method", "percentile"))
        self.cache_ttl_seconds = float(cfg.get("cache_ttl_seconds", 300))
        self.underlying = str(cfg.get("underlying", _DEFAULT_UNDERLYING))
        self.rv_window = int(cfg.get("rv_window", _RV_WINDOW))

        if cache_path is not None:
            self.CACHE_PATH = cache_path
        try:
            Path(self.CACHE_PATH).parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:  # 只读文件系统等 — 缓存不可用不影响主链
            logger.warning("IV Rank 缓存目录创建失败: %s", e)

    # ============================================================
    # 公开接口
    # ============================================================
    def fetch_iv_rank(self, use_cache: bool = True) -> int | None:
        """获取当前 IV Rank (0-100).

        Args:
            use_cache: 是否在主备链全失败时用缓存兜底

        Returns:
            IV Rank (0-100) 或 None (全失败, 调用方回退静态参数)
        """
        # 1. 主链: Wind kline → 滚动 RV 序列 → rank
        rv_series = self._rv_series_from_wind_kline()
        if rv_series:
            rank = self._rank_from_series(rv_series)
            if rank is not None:
                self._append_history(rv_series)
                self._save_cache(rank, source="wind_kline_rv")
                return rank

        # 2. 备链: shadow_state.json daily_return (只读) → 滚动 RV
        rv_series = self._rv_series_from_shadow_state()
        if rv_series:
            rank = self._rank_from_series(rv_series)
            if rank is not None:
                self._append_history(rv_series)
                self._save_cache(rank, source="shadow_state_rv")
                return rank

        # 3. 三链: 历史缓存 history 段
        if use_cache:
            rank = self._rank_from_cache_history()
            if rank is not None:
                logger.warning(
                    "IV Rank 主备链全失败, 使用缓存 history 推导 rank=%d", rank
                )
                return rank

        logger.warning("IV Rank 全数据源失败, 返回 None (调用方回退静态参数)")
        return None

    def fetch_current_level(self, use_cache: bool = True) -> float | None:
        """获取当前波动率水平 (VIX 量纲, RV*100).

        供 tool_selector 统一 iv_level 口径 (P3 接入点).
        """
        rv_series = self._rv_series_from_wind_kline() or self._rv_series_from_shadow_state()
        if rv_series:
            current = rv_series[-1]["vol"]
            if 5 <= current <= 150:
                return float(current)
        cached = self._load_cache()
        if cached and use_cache:
            try:
                vol = cached.get("current_vol")
                return float(vol) if vol is not None else None
            except (TypeError, ValueError):
                return None
        return None

    def fetch_rv_history(self) -> list[dict]:
        """获取滚动 RV 历史序列 [{date, vol}] — 回测 integration 用."""
        series = self._rv_series_from_wind_kline()
        if series:
            return series
        return self._rv_series_from_shadow_state()

    # ============================================================
    # 纯函数: rank 计算
    # ============================================================
    @staticmethod
    def compute_rank(series: list[float], current: float, method: str = "percentile") -> int:
        """计算 current 在 series 中的分位 (0-100).

        Args:
            series: 历史 RV 序列 (VIX 量纲)
            current: 当前值
            method: "percentile" (小于当前值的占比) | "minmax"

        Raises:
            ValueError: 序列空 / 单点 / minmax 时 max==min
        """
        if not series or len(series) < 2:
            raise ValueError(f"序列不足 (len={len(series)})")
        if method == "minmax":
            lo, hi = min(series), max(series)
            if hi <= lo:
                raise ValueError("minmax 序列无区分度 (max<=min)")
            rank = (current - lo) / (hi - lo) * 100
        else:  # percentile
            rank = 100 * sum(1 for s in series if s < current) / len(series)
        return int(min(100, max(0, round(rank))))

    # ============================================================
    # 主链: Wind kline → 滚动 RV 序列
    # ============================================================
    def _rv_series_from_wind_kline(self) -> list[dict]:
        """Wind kline 拉取 lookback+rv_window+buffer 日, 计算滚动 RV 序列."""
        try:
            tools_dir = str(_PROJECT_ROOT / "tools")
            if tools_dir not in sys.path:
                sys.path.insert(0, tools_dir)

            from wind_mcp_fetcher import wind_get_kline  # type: ignore

            days = self.lookback_days + self.rv_window + 30
            kline_data = wind_get_kline(self.underlying, days=days)
            if not kline_data:
                return []

            closes: list[tuple[str, float]] = []
            for record in kline_data:
                close = record.get("close") or record.get("CLOSE")
                d = record.get("date") or record.get("DATE") or ""
                if close is None:
                    continue
                try:
                    closes.append((str(d)[:10], float(close)))
                except (TypeError, ValueError):
                    continue

            return self._rolling_rv(closes)
        except ImportError as e:
            logger.debug("wind_mcp_fetcher 不可用: %s", e)
            return []
        except (
            OSError, ValueError, TypeError, RuntimeError, TimeoutError, ConnectionError,
        ) as e:
            logger.debug("Wind kline 获取失败: %s", e)
            return []

    # ============================================================
    # 备链: shadow_state.json 只读
    # ============================================================
    def _rv_series_from_shadow_state(self) -> list[dict]:
        """从 shadow_state.json daily_nav 的 daily_return 计算滚动 RV.

        只读 — 绝不写 shadow_account 目录 (S12 shadow 隔离).
        """
        try:
            if not _SHADOW_STATE_PATH.exists():
                return []
            with _SHADOW_STATE_PATH.open("r", encoding="utf-8") as f:
                state = json.load(f)

            rows: list[tuple[str, float]] = []
            for record in state.get("daily_nav", []):
                ret = record.get("daily_return")
                if ret is None:
                    continue
                try:
                    rows.append((str(record.get("date", ""))[:10], float(ret)))
                except (TypeError, ValueError):
                    continue

            if len(rows) < self.rv_window + 2:
                return []

            # daily_return → 等价 close 序列 (复用 _rolling_rv 需转价格)
            closes: list[tuple[str, float]] = []
            price = 1.0
            for d, ret in rows:
                price *= 1.0 + ret
                closes.append((d, price))
            return self._rolling_rv(closes)
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            logger.debug("shadow_state RV 序列计算失败: %s", e)
            return []

    # ============================================================
    # 滚动 RV 计算 (共用)
    # ============================================================
    def _rolling_rv(self, closes: list[tuple[str, float]]) -> list[dict]:
        """滚动窗口 RV 序列 [{date, vol}], vol 为 VIX 量纲 (年化 RV*100)."""
        if len(closes) < self.rv_window + 2:
            return []
        returns: list[float] = []
        for i in range(1, len(closes)):
            prev = closes[i - 1][1]
            if prev > 0:
                returns.append((closes[i][1] - prev) / prev)

        series: list[dict] = []
        for i in range(self.rv_window, len(returns) + 1):
            window = returns[i - self.rv_window : i]
            n = len(window)
            mean_ret = sum(window) / n
            variance = sum((r - mean_ret) ** 2 for r in window) / max(n - 1, 1)
            vol = math.sqrt(variance) * _ANNUALIZATION_FACTOR * 100
            if 5 <= vol <= 150:  # 数值合法性 (与 vix_data_source 同口径)
                # 序列第 i 个 return 对应 closes 第 i+1 行的日期
                series.append({"date": closes[i][0], "vol": round(vol, 4)})
        # 只保留最近 lookback_days 条
        return series[-self.lookback_days :]

    def _rank_from_series(self, series: list[dict]) -> int | None:
        """从 RV 序列计算当前 rank, 历史不足 min_history_days → None."""
        if len(series) < self.min_history_days:
            logger.debug(
                "RV 序列长度 %d < min_history_days %d", len(series), self.min_history_days
            )
            return None
        try:
            return self.compute_rank(
                [s["vol"] for s in series[:-1]], series[-1]["vol"], self.method
            )
        except ValueError as e:
            logger.debug("rank 计算失败: %s", e)
            return None

    # ============================================================
    # 缓存读写 (history 段按 date 去重, 滚动上限)
    # ============================================================
    def _append_history(self, rv_series: list[dict]) -> None:
        """把新 RV 点追加进缓存 history (按 date 去重)."""
        try:
            cache = self._load_cache() or {"history": []}
            history: list[dict] = cache.get("history", [])
            known = {h.get("date") for h in history}
            for point in rv_series:
                if point["date"] not in known:
                    history.append(point)
            history.sort(key=lambda h: str(h.get("date", "")))
            cache["history"] = history[-_MAX_HISTORY:]
            cache["last_appended"] = now_bj().isoformat(timespec="seconds")
            self._write_cache(cache)
        except (OSError, ValueError, TypeError) as e:
            logger.debug("history 追加失败 (fail-open): %s", e)

    def _rank_from_cache_history(self) -> int | None:
        """从缓存 history 段推导 rank (三链兜底)."""
        cache = self._load_cache()
        if not cache:
            return None
        history = cache.get("history", [])
        if len(history) < self.min_history_days + 1:
            return None
        try:
            return self.compute_rank(
                [h["vol"] for h in history[:-1]], history[-1]["vol"], self.method
            )
        except (ValueError, KeyError, TypeError) as e:
            logger.debug("缓存 history rank 计算失败: %s", e)
            return None

    def _save_cache(self, rank: int, source: str) -> None:
        """写入当前 rank 快照 (含 current_vol 供 fetch_current_level 兜底)."""
        try:
            cache = self._load_cache() or {"history": []}
            rv_series = None  # current_vol 由调用方上下文不可得, 置 None 占位
            cache.update({
                "rank": rank,
                "source": source,
                "current_vol": rv_series,
                "timestamp": now_bj().isoformat(timespec="seconds"),
            })
            # current_vol 用最近一次 history 尾部值回填
            history = cache.get("history", [])
            if history:
                cache["current_vol"] = history[-1].get("vol")
            self._write_cache(cache)
        except (OSError, ValueError, TypeError) as e:
            logger.debug("rank 缓存写入失败 (fail-open): %s", e)

    def _load_cache(self) -> dict | None:
        """读缓存, TTL 过期返回 None."""
        try:
            if not Path(self.CACHE_PATH).exists():
                return None
            with open(self.CACHE_PATH, encoding="utf-8") as f:
                cache = json.load(f)
            if not isinstance(cache, dict):
                return None
            ts = cache.get("timestamp", "")
            if ts:
                age = (now_bj() - datetime.fromisoformat(ts)).total_seconds()
                if age > self.cache_ttl_seconds and not cache.get("history"):
                    return None
            return cache
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            logger.debug("缓存读取失败 (fail-open): %s", e)
            return None

    def _write_cache(self, cache: dict) -> None:
        Path(self.CACHE_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(self.CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)


def fetch_iv_rank(use_cache: bool = True) -> int | None:
    """模块级便捷函数 (同 vix_data_source 风格)."""
    return IVRankProvider().fetch_iv_rank(use_cache=use_cache)
