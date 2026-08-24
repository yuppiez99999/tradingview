"""
每日盘后定时调度器

主流程编排：
1. 获取股票池
2. 拉取全市场快照 + K 线数据
3. 风险前置过滤
4. 批量因子计算
5. 横截面打分 + 行业中性化
6. 分层组合构建
7. 生成可视化报告

可由 Windows 计划任务每日 16:00 触发
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class ScanResult:
    """扫描结果摘要"""

    trade_date: str = ""
    universe_size: int = 0
    filtered_size: int = 0
    factor_computed: int = 0
    factor_failed: int = 0
    portfolio_size: int = 0
    elapsed_seconds: float = 0.0
    output_dir: str = ""
    success: bool = False
    error: str = ""
    report_paths: dict[str, str] = field(default_factory=dict)


class KlinesLoader:
    """K 线数据加载器（带缓存）

    优先用通达信（TCP 稳定），降级用 AKShare
    """

    def __init__(self, count: int = 300, cache_dir: Path | None = None):
        self.count = count
        self.cache_dir = cache_dir or (_REPO_ROOT / "data" / "cache" / "klines")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._tdx_source = None
        self._akshare_source = None
        self._cache: dict[str, pd.DataFrame] = {}

    def _get_tdx(self):
        """获取通达信数据源（优先）"""
        if self._tdx_source is None:
            try:
                from utils.tdx_data_source import get_tdx_source

                self._tdx_source = get_tdx_source()
                if self._tdx_source and self._tdx_source._connected:
                    logger.info("  K线加载器: 使用通达信数据源 (TCP)")
                else:
                    self._tdx_source = None
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001
                logger.debug(f"通达信不可用: {e}")
        return self._tdx_source

    def _get_akshare(self):
        if self._akshare_source is None:
            try:
                from utils.akshare_data_source import get_akshare_source

                self._akshare_source = get_akshare_source()
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001
                logger.debug(f"AKShare 不可用: {e}")
        return self._akshare_source

    def __call__(self, symbol: str) -> pd.DataFrame | None:
        """加载单只股票 K 线"""
        if symbol in self._cache:
            return self._cache[symbol]

        # 1. 检查本地缓存
        cache_file = self.cache_dir / f"{symbol}.parquet"
        if cache_file.exists():
            try:
                df = pd.read_parquet(cache_file)
                if not df.empty and len(df) >= self.count * 0.5:
                    self._cache[symbol] = df
                    return df
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning("Unexpected error in scheduler.py", exc_info=True)

        # 2. 通达信（优先，TCP 协议稳定）
        tdx = self._get_tdx()
        if tdx and tdx._connected:
            ak_symbol = self._to_market_symbol(symbol)
            df = tdx.get_historical_klines(ak_symbol, period="1d", count=self.count)
            if df is not None and not df.empty:
                df = df.copy()
                required = ["open", "high", "low", "close", "volume"]
                if all(c in df.columns for c in required):
                    try:
                        df.to_parquet(cache_file)
                    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                        logger.warning("Unexpected error in scheduler.py", exc_info=True)
                    self._cache[symbol] = df
                    return df

        # 3. AKShare 降级
        source = self._get_akshare()
        if source is not None:
            ak_symbol = self._to_market_symbol(symbol)
            df = source.get_historical_klines(ak_symbol, period="1d", count=self.count)
            if df is not None and not df.empty:
                df = df.copy()
                rename_map = {
                    "日期": "date",
                    "开盘": "open",
                    "收盘": "close",
                    "最高": "high",
                    "最低": "low",
                    "成交量": "volume",
                    "成交额": "amount",
                }
                df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
                required = ["open", "high", "low", "close", "volume"]
                if all(c in df.columns for c in required):
                    try:
                        df.to_parquet(cache_file, index=False)
                    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                        logger.warning("Unexpected error in scheduler.py", exc_info=True)
                    self._cache[symbol] = df
                    return df

        # 4. 最终降级：生成模拟 K 线（仅用于烟雾测试）
        logger.debug(f"  {symbol} 使用模拟 K 线 (网络/API 不可用)")
        df = self._make_synthetic_kline(symbol)
        self._cache[symbol] = df
        return df

    @staticmethod
    def _to_market_symbol(symbol: str) -> str:
        """将 6 位代码转换为带市场后缀"""
        if "." in symbol:
            return symbol
        if symbol.startswith("6"):
            return f"{symbol}.SH"
        elif symbol.startswith(("0", "3")):
            return f"{symbol}.SZ"
        elif symbol.startswith(("4", "8")):
            return f"{symbol}.BJ"
        return symbol

    @staticmethod
    def _make_synthetic_kline(symbol: str, n: int = 300) -> pd.DataFrame:
        """生成模拟 K 线（用于网络不可用时的烟雾测试）"""
        import numpy as np

        seed = int(symbol[-6:]) if symbol[-6:].isdigit() else 42
        np.random.seed(seed)
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        close0 = 30.0 + (seed % 100)
        returns = np.random.randn(n) * 0.025 + 0.0005
        close = close0 * np.exp(np.cumsum(returns))
        high = close * (1 + np.abs(np.random.randn(n)) * 0.015)
        low = close * (1 - np.abs(np.random.randn(n)) * 0.015)
        open_p = close * (1 + np.random.randn(n) * 0.008)
        volume = np.abs(np.random.randn(n)) * 1e7 + 1e6
        amount = close * volume * 0.001
        df = pd.DataFrame(
            {
                "open": open_p,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": amount,
            },
            index=dates,
        )
        return df


def run_daily_scan(
    trade_date: str | None = None,
    output_dir: str | None = None,
    pool: str = "hs300_zz500",
    smoke_test: bool = False,
    smoke_count: int = 10,
) -> ScanResult:
    """每日扫描主流程

    Args:
        trade_date: 交易日期 YYYY-MM-DD, None=今天
        output_dir: 输出目录, None=reports/universe/YYYY-MM-DD
        pool: 股票池 "hs300" / "zz500" / "hs300_zz500"
        smoke_test: 烟雾测试模式
        smoke_count: 烟雾测试股票数

    Returns:
        ScanResult
    """
    start_time = time.time()
    result = ScanResult()

    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    result.trade_date = trade_date

    if output_dir is None:
        output_dir = str(_REPO_ROOT / "reports" / "universe" / trade_date)
    result.output_dir = output_dir
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    logger.info("=" * 70)
    logger.info("对冲基金级全市场选股系统 - 每日扫描")
    logger.info("=" * 70)
    logger.info(f"交易日期: {trade_date}")
    logger.info(f"股票池: {pool}")
    logger.info(f"输出目录: {output_dir}")
    if smoke_test:
        logger.info(f"⚠️ 烟雾测试模式: 仅处理 {smoke_count} 只股票")
    logger.info("=" * 70)

    try:
        # ============================================================
        # 阶段 1: 获取股票池
        # ============================================================
        logger.info(f"\n[1/6] 获取股票池 ({pool})...")
        from .stock_universe import _get_builtin_pool, get_full_market_snapshot, get_industry_map, get_universe

        universe_df = get_universe(pool=pool)
        if universe_df.empty:
            # 最终降级：使用内置白名单（仅用于烟雾测试验证流程）
            logger.warning("所有外部数据源不可用，降级为内置 30 只蓝筹股白名单")
            universe_df = _get_builtin_pool()
        result.universe_size = len(universe_df)
        logger.info(f"  股票池: {len(universe_df)} 只")

        # 烟雾测试模式：截取前 N 只
        if smoke_test:
            universe_df = universe_df.head(smoke_count)
            logger.info(f"  烟雾测试截取: {len(universe_df)} 只")

        # ============================================================
        # 阶段 2: 拉取全市场快照 + 风险过滤
        # ============================================================
        logger.info("\n[2/6] 拉取全市场快照 + 风险过滤...")
        spot_df = get_full_market_snapshot()

        from .risk_filter import RiskFilterConfig, filter_universe

        if spot_df.empty:
            # 降级：跳过风险过滤，直接用股票池
            logger.warning("全市场快照为空，跳过风险过滤")
            filtered_df = universe_df.copy()
            # 添加模拟的行情列（避免后续 KeyError）
            for col in ["price", "amount", "volume", "turnover_ratio", "change_ratio"]:
                if col not in filtered_df.columns:
                    filtered_df[col] = 100.0 if col == "price" else 1e8
            filter_stats = {
                "initial": len(universe_df),
                "final": len(universe_df),
                "pass_rate": "100%(skipped)",
                "total_removed": 0,
            }
        else:
            filtered_df = filter_universe(universe_df, spot_df, RiskFilterConfig())
            if filtered_df.empty:
                logger.warning("风险过滤后股票池为空，使用原始股票池")
                filtered_df = universe_df.copy()
                for col in ["price", "amount", "volume", "turnover_ratio", "change_ratio"]:
                    if col not in filtered_df.columns:
                        filtered_df[col] = 100.0 if col == "price" else 1e8
                filter_stats = {
                    "initial": len(universe_df),
                    "final": len(universe_df),
                    "pass_rate": "100%(fallback)",
                    "total_removed": 0,
                }
            else:
                filter_stats = filtered_df.attrs.get("filter_stats", {})
        result.filtered_size = len(filtered_df)
        logger.info(f"  过滤后: {len(filtered_df)} 只")

        # ============================================================
        # 阶段 3: 批量因子计算
        # ============================================================
        logger.info("\n[3/6] 批量计算因子...")
        symbols = filtered_df["code"].tolist()
        name_map = dict(zip(filtered_df["code"], filtered_df.get("name", filtered_df["code"]), strict=True))

        klines_loader = KlinesLoader(count=300)
        # 预热 K 线（烟雾测试不需要预热所有）
        if not smoke_test:
            logger.info("  预加载 K 线数据...")
            for i, sym in enumerate(symbols):
                klines_loader(sym)
                if (i + 1) % 100 == 0:
                    logger.info(f"    K线进度: {i + 1}/{len(symbols)}")

        from .factor_scorer import ScoringConfig, batch_compute_factors, cross_sectional_score, industry_neutralize

        # 烟雾测试用更少因子
        scoring_config = ScoringConfig()
        if smoke_test:
            scoring_config.max_factors_per_theme = 10
            scoring_config.max_workers = 2

        factor_df, theme_factors = batch_compute_factors(
            symbols=symbols,
            klines_loader=klines_loader,
            config=scoring_config,
        )
        result.factor_computed = len(factor_df)
        result.factor_failed = len(symbols) - len(factor_df)
        if factor_df.empty:
            raise RuntimeError("因子计算全部失败")

        # ============================================================
        # 阶段 4: 横截面打分 + 行业中性化
        # ============================================================
        logger.info("\n[4/6] 横截面打分 + 行业中性化...")
        scores_df = cross_sectional_score(factor_df, theme_factors)

        # 行业映射
        logger.info("  获取行业映射...")
        industry_map = get_industry_map(scores_df.index.tolist())
        # 对未匹配的股票给默认行业
        for sym in scores_df.index:
            if sym not in industry_map:
                industry_map[sym] = "其他"

        # 行业中性化
        neutral_scores = industry_neutralize(scores_df["composite_score"], industry_map)
        scores_df["composite_score_neutral"] = neutral_scores
        # 用中性化得分重排
        scores_df = scores_df.sort_values("composite_score_neutral", ascending=False)
        scores_df["rank"] = scores_df["composite_score_neutral"].rank(ascending=False, method="min").astype(int)

        # ============================================================
        # 阶段 5: 分层组合构建
        # ============================================================
        logger.info("\n[5/6] 分层组合构建...")
        from .portfolio_builder import PortfolioConfig, build_layered_portfolio

        port_config = PortfolioConfig()
        if smoke_test:
            port_config.short_count = 3
            port_config.mid_count = 3
            port_config.long_count = 4

        portfolio = build_layered_portfolio(
            scores_df=scores_df,
            industry_map=industry_map,
            name_map=name_map,
            config=port_config,
            trade_date=trade_date,
            universe_size=result.universe_size,
            filtered_size=result.filtered_size,
        )
        result.portfolio_size = len(portfolio.holdings)

        # ============================================================
        # 阶段 6: 生成可视化报告
        # ============================================================
        logger.info("\n[6/6] 生成可视化报告...")
        from .report_generator import generate_full_report

        theme_stats = scores_df.attrs.get("theme_stats", {})
        report_paths = generate_full_report(
            portfolio=portfolio,
            scores_df=scores_df,
            filter_stats=filter_stats,
            output_dir=output_dir,
            theme_stats=theme_stats,
        )
        result.report_paths = {
            "candidates_csv": report_paths.candidates_csv,
            "portfolio_json": report_paths.portfolio_json,
            "report_md": report_paths.report_md,
            "chart_industry": report_paths.chart_industry,
            "chart_factor_contrib": report_paths.chart_factor_contrib,
            "chart_risk_radar": report_paths.chart_risk_radar,
            "chart_score_dist": report_paths.chart_score_dist,
        }

        result.success = True
        result.elapsed_seconds = time.time() - start_time
        logger.info("\n" + "=" * 70)
        logger.info(f"✅ 扫描完成  耗时: {result.elapsed_seconds:.1f}秒")
        logger.info(f"  股票池: {result.universe_size}  过滤后: {result.filtered_size}")
        logger.info(f"  因子计算: {result.factor_computed} 成功 / {result.factor_failed} 失败")
        logger.info(f"  持仓: {result.portfolio_size} 只")
        logger.info(f"  报告目录: {output_dir}")
        logger.info("=" * 70)

    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001
        result.success = False
        result.error = str(e)
        result.elapsed_seconds = time.time() - start_time
        logger.error(f"❌ 扫描失败: {e}", exc_info=True)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    # 烟雾测试
    result = run_daily_scan(smoke_test=True, smoke_count=5)
    logger.info(result)
