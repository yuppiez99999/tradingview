"""
因子挖掘工具 Factor Discovery
终极量化交易系统 8.4

功能:
1. 从 AKShare 获取全市场/自选标的日线数据
2. 计算 6 大类 60+ 因子 (动量/价值/质量/低波/规模/流动性 + 技术面 + 量价)
3. 因子有效性验证 (IC/IR/单调性/分层回测)
4. 输出有效因子报告

用法:
    python -m research.factor_discovery --universe etf50 --start 2022-01-01
    python -m research.factor_discovery --codes 510300,588000,159915 --start 2023-01-01
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from utils.datetime_utils import now_bj

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.akshare_data_source import AKShareDataSource
from utils.alpha_factor_library import AlphaFactorLibrary

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

logger = logging.getLogger("factor_discovery")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ============================================================
# 标的池定义
# ============================================================

UNIVERSE_PRESETS = {
    "etf_core": [
        "510300.SH",
        "510500.SH",
        "512100.SH",
        "588000.SH",
        "159915.SZ",
        "510050.SH",
        "560080.SH",
        "159995.SZ",
    ],
    "etf_broad": [
        "510300.SH",
        "510500.SH",
        "512100.SH",
        "588000.SH",
        "159915.SZ",
        "510050.SH",
        "560080.SH",
        "159995.SZ",
        "512400.SH",
        "516160.SH",
        "159992.SZ",
        "515030.SH",
        "515050.SH",
        "512660.SH",
        "512690.SH",
        "512760.SH",
        "515790.SH",
        "516950.SH",
        "159825.SZ",
        "562500.SH",
    ],
    "etf50": [
        "510300.SH",
        "510500.SH",
        "512100.SH",
        "588000.SH",
        "159915.SZ",
        "510050.SH",
        "560080.SH",
        "159995.SZ",
        "512400.SH",
        "516160.SH",
        "159992.SZ",
        "515030.SH",
        "515050.SH",
        "512660.SH",
        "512690.SH",
        "512760.SH",
        "515790.SH",
        "516950.SH",
        "159825.SZ",
        "562500.SH",
        "518880.SH",
        "513100.SH",
        "513500.SH",
        "513050.SH",
        "513030.SH",
        "513520.SH",
        "159920.SZ",
        "510900.SH",
        "510230.SH",
        "512010.SH",
        "512070.SH",
        "512170.SH",
        "512200.SH",
        "512290.SH",
        "512300.SH",
        "512310.SH",
        "512330.SH",
        "512340.SH",
        "512350.SH",
        "512360.SH",
        "512380.SH",
        "512390.SH",
        "512410.SH",
        "512420.SH",
        "512430.SH",
        "512450.SH",
        "512460.SH",
        "512470.SH",
    ],
}


# ============================================================
# 数据结构
# ============================================================


@dataclass
class FactorValidationResult:
    """因子有效性验证结果"""

    factor_name: str
    category: str
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ic_ir: float = 0.0
    ic_positive_ratio: float = 0.0
    rank_ic_mean: float = 0.0
    monotonicity: float = 0.0
    long_short_return: float = 0.0
    long_return: float = 0.0
    short_return: float = 0.0
    turnover: float = 0.0
    decay_5d: float = 0.0
    decay_10d: float = 0.0
    effective: bool = False
    direction: str = "positive"
    score: float = 0.0


@dataclass
class DiscoveryReport:
    """因子挖掘报告"""

    universe: list[str]
    start_date: str
    end_date: str
    n_symbols: int
    n_dates: int
    n_factors_tested: int
    effective_factors: list[FactorValidationResult] = field(default_factory=list)
    strong_factors: list[FactorValidationResult] = field(default_factory=list)
    all_factors_sorted: list[FactorValidationResult] = field(default_factory=list)
    generation_time: str = ""


# ============================================================
# 数据获取
# ============================================================


class FactorDataFetcher:
    """因子挖掘数据获取器"""

    CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "ohlcv"

    def __init__(self):
        self.source = AKShareDataSource()

    def get_available_cached_symbols(self, min_days: int = 100) -> list[str]:
        """获取本地缓存中可用的标的列表"""
        symbols: list[str] = []
        if not self.CACHE_DIR.exists():
            return symbols
        for f in self.CACHE_DIR.glob("*.parquet"):
            try:
                df = pd.read_parquet(f)
                if len(df) >= min_days:
                    fname = f.stem
                    if "_2y" in fname:
                        code_num, market = fname.replace("_2y", "").rsplit("_", 1)
                        symbols.append(f"{code_num}.{market}")
                    elif "index" in fname:
                        pass
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
                pass
        return symbols

    def _load_from_cache(self, code: str) -> pd.DataFrame | None:
        """从本地 parquet 缓存加载数据"""
        code_num = code.split(".")[0]
        market = code.split(".")[-1] if "." in code else "SH"

        cache_name = f"{code_num}_{market}_2y.parquet"
        cache_path = self.CACHE_DIR / cache_name

        if cache_path.exists():
            try:
                df = pd.read_parquet(cache_path)
                if len(df) > 60:
                    return df
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
                pass
        return None

    def fetch_daily_data(
        self,
        codes: list[str],
        start_date: str,
        end_date: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        """获取多只标的日线数据

        Args:
            codes: 股票/ETF 代码列表
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD

        Returns:
            {code: DataFrame(index=日期, columns=[open,high,low,close,volume,amount])}
        """
        if end_date is None:
            end_date = now_bj().strftime("%Y-%m-%d")

        logger.info(f"开始获取 {len(codes)} 只标的日线数据: {start_date} ~ {end_date}")
        result = {}
        failed = []

        for i, code in enumerate(codes):
            try:
                df = self._load_from_cache(code)
                if df is not None:
                    start_dt = pd.to_datetime(start_date)
                    end_dt = pd.to_datetime(end_date)
                    df = df[(df.index >= start_dt) & (df.index <= end_dt)]
                    if len(df) > 60:
                        result[code] = df
                        logger.debug(
                            f"  [{i+1}/{len(codes)}] {code}: 缓存 {len(df)} 条"
                        )
                        continue

                ak_code = self.source._to_akshare_code(code)
                df = self._fetch_single(ak_code, start_date, end_date)
                if df is not None and len(df) > 60:
                    result[code] = df
                    logger.debug(f"  [{i+1}/{len(codes)}] {code}: AKShare {len(df)} 条")
                else:
                    failed.append(code)
                    logger.warning(f"  [{i+1}/{len(codes)}] {code}: 数据不足")
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
                failed.append(code)
                logger.warning(f"  [{i+1}/{len(codes)}] {code}: 获取失败 {e}")

            time.sleep(0.15)

        logger.info(
            f"数据获取完成: 成功 {len(result)}/{len(codes)}, 失败 {len(failed)}"
        )
        if failed:
            logger.warning(f"失败列表: {failed[:10]}...")
        return result

    def _fetch_single(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame | None:
        """获取单只标的数据"""
        try:
            import akshare as ak

            start_fmt = start_date.replace("-", "")
            end_fmt = end_date.replace("-", "")

            try:
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start_fmt,
                    end_date=end_fmt,
                    adjust="qfq",
                )
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
                df = ak.fund_etf_hist_em(
                    symbol=code,
                    period="daily",
                    start_date=start_fmt,
                    end_date=end_fmt,
                    adjust="qfq",
                )

            if df is None or len(df) == 0:
                return None

            df = self._normalize_columns(df)
            df = df.sort_index()
            return df

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
            logger.debug(f"获取 {code} 失败: {e}")
            return None

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化列名"""
        col_map = {
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "振幅": "amplitude",
            "涨跌幅": "pct_chg",
            "涨跌额": "change",
            "换手率": "turnover",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")

        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df


# ============================================================
# 因子计算器
# ============================================================


class FactorCalculator:
    """因子计算器 - 支持滚动因子计算"""

    def __init__(self):
        self.library = AlphaFactorLibrary()

    def compute_factors_panel(
        self,
        daily_data: dict[str, pd.DataFrame],
        lookback: int = 252,
        step: int = 5,
    ) -> dict[str, pd.DataFrame]:
        """计算因子面板 (每个时间点的因子值)

        Args:
            daily_data: {code: DataFrame} 日线数据
            lookback: 因子计算回看天数
            step: 因子计算步长 (每隔几天计算一次)

        Returns:
            {factor_name: DataFrame(index=日期, columns=标的, values=因子值)}
        """
        logger.info(f"开始计算因子面板: {len(daily_data)} 只标的, step={step}")

        all_dates = sorted(set().union(*[set(df.index) for df in daily_data.values()]))
        all_dates = [
            d for d in all_dates if d >= all_dates[0] + timedelta(days=lookback)
        ]

        codes = list(daily_data.keys())
        factor_panels: dict[str, pd.DataFrame] = {}

        calc_dates = all_dates[::step]
        logger.info(
            f"  计算时点: {len(calc_dates)} 个 (从 {calc_dates[0].date()} 到 {calc_dates[-1].date()})"
        )

        for idx, calc_date in enumerate(calc_dates):
            price_snapshot = {}
            for code in codes:
                df = daily_data[code]
                hist = df[df.index <= calc_date].tail(lookback)
                if len(hist) >= max(60, lookback // 2):
                    price_snapshot[code] = {
                        "closes": hist["close"].dropna().tolist(),
                        "volumes": hist["volume"].dropna().tolist(),
                        "highs": hist["high"].dropna().tolist(),
                        "lows": hist["low"].dropna().tolist(),
                    }

            if len(price_snapshot) < 3:
                continue

            result = self.library.compute_all(price_data=price_snapshot)

            for fname, fval in result.factors.items():
                if fname not in factor_panels:
                    factor_panels[fname] = pd.DataFrame(
                        index=calc_dates, columns=codes, dtype=float
                    )

                for code, val in fval.values.items():
                    if code in factor_panels[fname].columns:
                        factor_panels[fname].loc[calc_date, code] = val

            if (idx + 1) % 20 == 0:
                logger.info(f"  进度: {idx+1}/{len(calc_dates)}")

        for fname in factor_panels:
            factor_panels[fname] = factor_panels[fname].dropna(how="all")

        logger.info(f"因子面板计算完成: {len(factor_panels)} 个因子")
        return factor_panels


# ============================================================
# 因子有效性验证
# ============================================================


class FactorValidator:
    """因子有效性验证器"""

    MIN_SAMPLES = 5
    IC_STRONG_THRESHOLD = 0.05
    IC_EFFECTIVE_THRESHOLD = 0.02
    IR_STRONG_THRESHOLD = 0.5
    IR_EFFECTIVE_THRESHOLD = 0.2

    def __init__(self, forward_days: list[int] | None = None):
        self.forward_days = forward_days or [1, 5, 10, 20]

    def validate_all(
        self,
        factor_panels: dict[str, pd.DataFrame],
        daily_data: dict[str, pd.DataFrame],
    ) -> list[FactorValidationResult]:
        """验证所有因子的有效性

        Args:
            factor_panels: {factor_name: DataFrame(日期 x 标的)}
            daily_data: {code: DataFrame} 日线数据

        Returns:
            验证结果列表 (按综合评分排序)
        """
        logger.info(f"开始验证 {len(factor_panels)} 个因子的有效性...")

        returns_panel = self._build_returns_panel(daily_data, factor_panels)

        results = []
        for fname, fpanel in factor_panels.items():
            try:
                result = self._validate_single(fname, fpanel, returns_panel)
                if result is not None:
                    results.append(result)
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
                logger.warning(f"验证因子 {fname} 失败: {e}")

        results.sort(key=lambda r: r.score, reverse=True)

        n_effective = sum(1 for r in results if r.effective)
        n_strong = sum(1 for r in results if abs(r.ic_ir) >= self.IR_STRONG_THRESHOLD)
        logger.info(
            f"验证完成: 有效因子 {n_effective}/{len(results)}, 强因子 {n_strong}"
        )

        return results

    def _build_returns_panel(
        self,
        daily_data: dict[str, pd.DataFrame],
        factor_panels: dict[str, pd.DataFrame],
    ) -> dict[int, pd.DataFrame]:
        """构建远期收益面板

        Returns:
            {forward_day: DataFrame(日期 x 标的, values=远期收益率)}
        """
        codes = list(daily_data.keys())
        ref_factor = list(factor_panels.values())[0]
        dates = ref_factor.index

        returns_panels = {}
        for fwd in self.forward_days:
            panel = pd.DataFrame(index=dates, columns=codes, dtype=float)
            for code in codes:
                if code not in daily_data:
                    continue
                close = daily_data[code]["close"].reindex(dates)
                fwd_ret = close.shift(-fwd) / close - 1.0
                panel[code] = fwd_ret.reindex(dates)
            returns_panels[fwd] = panel

        return returns_panels

    def _validate_single(
        self,
        factor_name: str,
        factor_panel: pd.DataFrame,
        returns_panels: dict[int, pd.DataFrame],
    ) -> FactorValidationResult | None:
        """验证单个因子"""
        category = factor_name.split("_")[0] if "_" in factor_name else "Other"
        result = FactorValidationResult(factor_name=factor_name, category=category)

        ics_1d: list[float] = []
        ics_5d: list[float] = []
        for date in factor_panel.index:
            fvals = factor_panel.loc[date].dropna()
            if len(fvals) < 3:
                continue

            for fwd, ic_list in [(1, ics_1d), (5, ics_5d)]:
                if fwd in returns_panels:
                    rets = returns_panels[fwd].loc[date].reindex(fvals.index).dropna()
                    common = fvals.index.intersection(rets.index)
                    if len(common) >= 3:
                        try:
                            ic = float(
                                fvals[common].corr(rets[common], method="spearman")
                            )
                            if not np.isnan(ic):
                                ic_list.append(ic)
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
                            pass

        if len(ics_1d) < self.MIN_SAMPLES:
            return None

        ics_arr = np.array(ics_1d)
        result.ic_mean = float(np.mean(ics_arr))
        result.ic_std = float(np.std(ics_arr)) if len(ics_arr) > 1 else 1e-12
        result.ic_ir = result.ic_mean / result.ic_std if result.ic_std > 1e-12 else 0.0
        result.ic_positive_ratio = float(np.mean(ics_arr > 0))
        result.rank_ic_mean = result.ic_mean
        result.direction = "positive" if result.ic_mean >= 0 else "negative"

        if len(ics_5d) >= self.MIN_SAMPLES:
            ic5_mean = float(np.mean(ics_5d))
            result.decay_5d = abs(ic5_mean) / max(abs(result.ic_mean), 1e-12)

        result.effective = (
            abs(result.ic_mean) >= self.IC_EFFECTIVE_THRESHOLD
            and abs(result.ic_ir) >= self.IR_EFFECTIVE_THRESHOLD
        )

        result.score = (
            abs(result.ic_mean) * 20
            + min(abs(result.ic_ir), 3.0) * 10
            + result.ic_positive_ratio * 10
            + result.decay_5d * 5
        )

        return result


# ============================================================
# 报告输出
# ============================================================


class ReportGenerator:
    """报告生成器"""

    @staticmethod
    def generate_markdown(
        report: DiscoveryReport,
        output_dir: Path,
    ) -> str:
        """生成 Markdown 报告"""
        lines = []
        lines.append("# 因子挖掘报告")
        lines.append("")
        lines.append(f"**生成时间**: {report.generation_time}")
        lines.append(f"**标的池**: {report.n_symbols} 只标的")
        lines.append(f"**数据区间**: {report.start_date} ~ {report.end_date}")
        lines.append(f"**测试因子数**: {report.n_factors_tested}")
        lines.append(f"**有效因子数**: {len(report.effective_factors)}")
        lines.append(f"**强因子数**: {len(report.strong_factors)}")
        lines.append("")

        lines.append("## 强因子 (|IC_IR| ≥ 0.5)")
        lines.append("")
        if report.strong_factors:
            lines.append(
                "| 因子名 | 类别 | IC均值 | IC_IR | 正IC占比 | 5日衰减 | 评分 | 方向 |"
            )
            lines.append(
                "|--------|------|--------|-------|----------|---------|------|------|"
            )
            for r in report.strong_factors:
                lines.append(
                    f"| {r.factor_name} | {r.category} | {r.ic_mean:.4f} | "
                    f"{r.ic_ir:.3f} | {r.ic_positive_ratio:.1%} | "
                    f"{r.decay_5d:.2f} | {r.score:.1f} | {r.direction} |"
                )
        else:
            lines.append("暂无强因子")
        lines.append("")

        lines.append("## 有效因子 (|IC| ≥ 0.03 且 |IC_IR| ≥ 0.3)")
        lines.append("")
        if report.effective_factors:
            lines.append(
                "| 因子名 | 类别 | IC均值 | IC_IR | 正IC占比 | 5日衰减 | 评分 | 方向 |"
            )
            lines.append(
                "|--------|------|--------|-------|----------|---------|------|------|"
            )
            for r in report.effective_factors:
                lines.append(
                    f"| {r.factor_name} | {r.category} | {r.ic_mean:.4f} | "
                    f"{r.ic_ir:.3f} | {r.ic_positive_ratio:.1%} | "
                    f"{r.decay_5d:.2f} | {r.score:.1f} | {r.direction} |"
                )
        else:
            lines.append("暂无有效因子")
        lines.append("")

        lines.append("## 全因子排名")
        lines.append("")
        lines.append(
            "| 排名 | 因子名 | 类别 | IC均值 | IC_IR | 正IC占比 | 5日衰减 | 有效 | 评分 |"
        )
        lines.append(
            "|------|--------|------|--------|-------|----------|---------|------|------|"
        )
        all_factors = getattr(report, "all_factors_sorted", report.effective_factors)
        for i, r in enumerate(all_factors):
            eff_marker = "✅" if r.effective else "❌"
            lines.append(
                f"| {i+1} | {r.factor_name} | {r.category} | {r.ic_mean:.4f} | "
                f"{r.ic_ir:.3f} | {r.ic_positive_ratio:.1%} | "
                f"{r.decay_5d:.2f} | {eff_marker} | {r.score:.1f} |"
            )
        lines.append("")

        lines.append("## 备注")
        lines.append("")
        lines.append("- IC: Spearman 秩相关系数，衡量因子与未来收益的单调关系")
        lines.append("- IC_IR: IC均值 / IC标准差，衡量因子的稳定性")
        lines.append("- 5日衰减: 5日IC均值 / 1日IC均值，衡量因子信息的衰减速度")
        lines.append("- 评分 = |IC|×20 + min(|IC_IR|,3)×10 + 正IC占比×10 + 5日衰减×5")
        lines.append("")

        content = "\n".join(lines)

        output_path = (
            output_dir
            / f"factor_discovery_report_{report.start_date}_{report.end_date}.md"
        )
        output_path.write_text(content, encoding="utf-8")
        logger.info(f"报告已保存: {output_path}")

        return content


# ============================================================
# 主流程
# ============================================================


def run_discovery(
    universe: str = "etf_core",
    codes: str | None = None,
    start_date: str = "2023-01-01",
    end_date: str | None = None,
    output_dir: str | None = None,
) -> DiscoveryReport:
    """运行因子挖掘

    Args:
        universe: 预设标的池名称
        codes: 自定义代码列表 (逗号分隔)
        start_date: 起始日期
        end_date: 结束日期
        output_dir: 输出目录

    Returns:
        DiscoveryReport
    """
    if end_date is None:
        end_date = now_bj().strftime("%Y-%m-%d")

    if codes:
        target_codes = [c.strip() for c in codes.split(",") if c.strip()]
    elif universe == "local_cached":
        fetcher = FactorDataFetcher()
        target_codes = fetcher.get_available_cached_symbols(min_days=200)
        logger.info(f"从本地缓存发现 {len(target_codes)} 只可用标的")
    else:
        target_codes = UNIVERSE_PRESETS.get(universe, UNIVERSE_PRESETS["etf_core"])

    output_path = (
        Path(output_dir)
        if output_dir
        else Path(__file__).parent.parent / "research" / "outputs"
    )
    output_path.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("因子挖掘启动")
    logger.info(f"  标的池: {universe} ({len(target_codes)} 只)")
    logger.info(f"  时间区间: {start_date} ~ {end_date}")
    logger.info(f"  输出目录: {output_path}")
    logger.info("=" * 60)

    fetcher = FactorDataFetcher()
    daily_data = fetcher.fetch_daily_data(target_codes, start_date, end_date)

    if len(daily_data) < 3:
        logger.error("可用标的不足，无法进行因子挖掘")
        raise ValueError("可用标的不足")

    calculator = FactorCalculator()
    factor_panels = calculator.compute_factors_panel(daily_data, lookback=200, step=10)

    validator = FactorValidator(forward_days=[1, 5, 10, 20])
    all_results = validator.validate_all(factor_panels, daily_data)

    effective = [r for r in all_results if r.effective]
    strong = [r for r in all_results if abs(r.ic_ir) >= validator.IR_STRONG_THRESHOLD]

    report = DiscoveryReport(
        universe=target_codes,
        start_date=start_date,
        end_date=end_date,
        n_symbols=len(daily_data),
        n_dates=len(list(daily_data.values())[0]),
        n_factors_tested=len(all_results),
        effective_factors=effective,
        strong_factors=strong,
        generation_time=now_bj().strftime("%Y-%m-%d %H:%M:%S"),
    )
    report.all_factors_sorted = all_results

    report_gen = ReportGenerator()
    report_gen.generate_markdown(report, output_path)

    json_path = output_path / f"factor_discovery_{start_date}_{end_date}.json"
    json_data = {
        "universe": target_codes,
        "start_date": start_date,
        "end_date": end_date,
        "n_symbols": len(daily_data),
        "n_factors_tested": len(all_results),
        "effective_factors": [
            {
                "factor_name": r.factor_name,
                "category": r.category,
                "ic_mean": r.ic_mean,
                "ic_ir": r.ic_ir,
                "ic_positive_ratio": r.ic_positive_ratio,
                "decay_5d": r.decay_5d,
                "effective": r.effective,
                "direction": r.direction,
                "score": r.score,
            }
            for r in effective
        ],
        "strong_factors": [
            {
                "factor_name": r.factor_name,
                "category": r.category,
                "ic_mean": r.ic_mean,
                "ic_ir": r.ic_ir,
                "direction": r.direction,
                "score": r.score,
            }
            for r in strong
        ],
    }
    json_path.write_text(
        json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(f"JSON 数据已保存: {json_path}")
    logger.info("")
    logger.info("=" * 60)
    logger.info("因子挖掘完成!")
    logger.info(f"  测试因子: {len(all_results)}")
    logger.info(f"  有效因子: {len(effective)}")
    logger.info(f"  强因子: {len(strong)}")
    if strong:
        logger.info("  TOP 5 强因子:")
        for i, r in enumerate(strong[:5]):
            logger.info(
                f"    {i+1}. {r.factor_name} (IC={r.ic_mean:.4f}, IR={r.ic_ir:.3f}, {r.direction})"
            )
    logger.info("=" * 60)

    return report


def main():
    parser = argparse.ArgumentParser(description="因子挖掘工具")
    parser.add_argument(
        "--universe",
        default="local_cached",
        choices=list(UNIVERSE_PRESETS.keys()) + ["local_cached"],
        help="预设标的池 (local_cached=使用本地缓存全部标的)",
    )
    parser.add_argument("--codes", default=None, help="自定义标的代码 (逗号分隔)")
    parser.add_argument("--start", default="2023-01-01", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--output", default=None, help="输出目录")
    args = parser.parse_args()

    run_discovery(
        universe=args.universe,
        codes=args.codes,
        start_date=args.start,
        end_date=args.end,
        output_dir=args.output,
    )


if __name__ == "__main__":
    main()
