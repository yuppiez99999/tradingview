"""
Kronos 因子有效性验证脚本 (阶段 1: 研究验证)
==================================================

功能:
1. 使用现有 AKShare 数据源获取持仓标的历史日线数据
2. 通过 Kronos 基础模型进行滚动预测，生成因子信号
3. 计算因子 IC / IC_IR / 正IC占比 / 5日衰减 等核心指标
4. 与现有因子库做对比，评估 Kronos 的增量价值
5. 输出结构化的验证报告

设计原则:
- 完全独立运行，不修改任何生产代码
- 失败安全: 任何环节失败都输出诊断信息，不崩溃
- 可复现: 所有参数可配置，结果自动保存

用法:
    # 快速验证 (仅持仓核心标的, 2024年至今)
    python research/kronos_factor_validation.py --fast

    # 完整验证 (ETF50 + 个股池, 2022年至今)
    python research/kronos_factor_validation.py

    # 指定标的和日期范围
    python research/kronos_factor_validation.py \
        --codes 510300.SH,588000.SH,600900.SH \
        --start 2023-01-01 --end 2026-07-28

输出:
    research/outputs/kronos_validation_YYYYMMDD/
    ├── kronos_factor_report.md      # 验证报告 (Markdown)
    ├── kronos_factor_report.json     # 结构化数据
    ├── kronos_factor_panel.csv       # 因子面板 (日期×标的)
    ├── kronos_ic_timeseries.csv      # IC 时间序列
    └── kronos_validation.log         # 运行日志
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from utils.datetime_utils import now_bj

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _proxy_key in [
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "ICUBE_PROXY_HOST",
    "ICUBE_PROXY_PORT",
]:
    os.environ[_proxy_key] = ""
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

try:
    import requests as _requests

    _requests.adapters.DEFAULT_RETRIES = 2
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

os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
try:
    import huggingface_hub

    huggingface_hub.constants.HF_HUB_DISABLE_PROGRESS_BARS = True
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

# ============================================================
# 日志配置
# ============================================================

LOGGER = logging.getLogger("kronos_validation")


def setup_logging(log_dir: Path) -> None:
    """配置日志输出到文件和控制台"""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "kronos_validation.log"

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)

    LOGGER.handlers.clear()
    LOGGER.addHandler(fh)
    LOGGER.addHandler(ch)
    LOGGER.setLevel(logging.DEBUG)


# ============================================================
# 数据结构
# ============================================================


@dataclass
class KronosFactorResult:
    """Kronos 因子验证结果"""

    factor_name: str
    model_size: str
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ic_ir: float = 0.0
    ic_positive_ratio: float = 0.0
    decay_5d: float = 0.0
    n_days: int = 0
    n_symbols: int = 0
    effective: bool = False
    score: float = 0.0
    direction: str = "neutral"
    prediction_latency_ms: float = 0.0
    error_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class KronosValidationReport:
    """完整验证报告"""

    generation_time: str
    model_sizes_tested: list[str]
    symbols: list[str]
    date_range: tuple[str, str]
    lookback: int
    pred_len: int
    results: list[KronosFactorResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["results"] = [r.to_dict() for r in self.results]
        return d


# ============================================================
# Kronos 模型封装 (延迟加载, 失败安全)
# ============================================================


class KronosModelWrapper:
    """Kronos 模型封装，支持延迟加载和失败安全

    设计:
    - 首次使用时才加载模型，避免导入即失败
    - 如果 Kronos 未安装，返回 mock 数据用于测试流程
    - 支持多种模型规模 (mini/small/base)
    """

    def __init__(self, model_size: str = "small", device: str = "auto"):
        self.model_size = model_size
        self.device = device
        self._tokenizer = None
        self._model = None
        self._predictor = None
        self._available = None
        self._error_msg = None
        self._init_time = 0.0

    @property
    def available(self) -> bool:
        """检查 Kronos 是否可用 (惰性检查)"""
        if self._available is None:
            self._try_init()
        return self._available

    @property
    def error_msg(self) -> str | None:
        return self._error_msg

    def _try_init(self) -> None:
        """尝试初始化 Kronos，失败则标记为不可用"""
        t0 = time.perf_counter()
        try:
            LOGGER.info(f"正在加载 Kronos-{self.model_size} 模型...")

            try:
                from model import Kronos, KronosPredictor, KronosTokenizer
            except ImportError:
                LOGGER.warning("未找到 Kronos 本地模块，尝试从 GitHub 克隆...")
                self._clone_and_install()
                from model import Kronos, KronosPredictor, KronosTokenizer

            model_name_map = {
                "mini": (
                    "NeoQuasar/Kronos-Tokenizer-2k",
                    "NeoQuasar/Kronos-mini",
                    2048,
                ),
                "small": (
                    "NeoQuasar/Kronos-Tokenizer-base",
                    "NeoQuasar/Kronos-small",
                    512,
                ),
                "base": (
                    "NeoQuasar/Kronos-Tokenizer-base",
                    "NeoQuasar/Kronos-base",
                    512,
                ),
            }
            tok_name, model_name, max_ctx = model_name_map.get(
                self.model_size, model_name_map["small"]
            )

            LOGGER.info(f"  Tokenizer: {tok_name}")
            LOGGER.info(f"  Model: {model_name}")
            LOGGER.info(f"  Max context: {max_ctx}")

            self._tokenizer = KronosTokenizer.from_pretrained(tok_name)
            self._model = Kronos.from_pretrained(model_name)

            import torch

            if self.device == "auto":
                dev = "cuda" if torch.cuda.is_available() else "cpu"
            else:
                dev = self.device
            self._model = self._model.to(dev)
            self._model.eval()

            self._predictor = KronosPredictor(
                self._model, self._tokenizer, max_context=max_ctx
            )
            self._available = True
            self._init_time = (time.perf_counter() - t0) * 1000
            LOGGER.info(
                f"Kronos-{self.model_size} 加载成功 "
                f"(耗时 {self._init_time:.0f}ms, 设备: {dev})"
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
        ) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            self._available = False
            self._error_msg = str(e)
            LOGGER.warning(f"Kronos-{self.model_size} 不可用: {e}")
            LOGGER.debug(traceback.format_exc())

    def _clone_and_install(self) -> None:
        """克隆 Kronos 仓库并安装到临时目录"""
        import subprocess
        import tempfile

        tmp_dir = Path(tempfile.mkdtemp(prefix="kronos_"))
        LOGGER.info(f"克隆 Kronos 到 {tmp_dir}")

        try:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "https://github.com/shiyu-coder/Kronos.git",
                    str(tmp_dir),
                ],
                check=True,
                timeout=120,
            )
            kronos_model_dir = tmp_dir
            sys.path.insert(0, str(kronos_model_dir))

            req_file = kronos_model_dir / "requirements.txt"
            if req_file.exists():
                LOGGER.info("安装 Kronos 依赖 (仅新增缺失包)...")
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-q", "-r", str(req_file)],
                    timeout=300,
                )
            LOGGER.info("Kronos 克隆安装完成")
        except subprocess.TimeoutExpired:
            raise RuntimeError("克隆 Kronos 超时 (120s)") from None
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"克隆 Kronos 失败: {e}") from e

    def predict(
        self,
        df: pd.DataFrame,
        pred_len: int = 20,
        temperature: float = 1.0,
        top_p: float = 0.9,
    ) -> pd.DataFrame | None:
        """对单只标的的历史数据进行预测

        Args:
            df: 历史K线 DataFrame (必须有 open/high/low/close/volume 列)
            pred_len: 预测天数
            temperature: 采样温度 (1.0 = 标准, <1.0 = 更确定)
            top_p: 核采样概率

        Returns:
            预测结果 DataFrame, 包含预测的 OHLCV 数据
            如果模型不可用或预测失败，返回 None
        """
        if not self.available:
            return None

        try:
            required_cols = ["open", "high", "low", "close", "volume"]
            if not all(c in df.columns for c in required_cols):
                LOGGER.warning(f"数据缺少必要列，实际列: {list(df.columns)}")
                return None

            input_df = df[required_cols].copy()
            if "amount" in df.columns:
                input_df["amount"] = df["amount"]
            else:
                input_df["amount"] = 0.0

            input_df = input_df.reset_index()
            if "date" in input_df.columns:
                x_timestamp = input_df["date"]
            elif "index" in input_df.columns and isinstance(
                input_df["index"].iloc[0], pd.Timestamp
            ):
                x_timestamp = input_df["index"]
            else:
                x_timestamp = pd.Series(
                    pd.date_range(
                        start=datetime(2020, 1, 1), periods=len(input_df), freq="D"
                    )
                )

            last_date = (
                x_timestamp.iloc[-1] if len(x_timestamp) > 0 else pd.Timestamp.now()
            )
            y_timestamp = pd.Series(
                pd.date_range(
                    start=last_date + pd.Timedelta(days=1),
                    periods=pred_len,
                    freq="D",
                )
            )

            input_df_for_predict = input_df.drop(
                columns=["date", "index"], errors="ignore"
            )

            pred_df = self._predictor.predict(
                df=input_df_for_predict,
                x_timestamp=x_timestamp,
                y_timestamp=y_timestamp,
                pred_len=pred_len,
                T=temperature,
                top_p=top_p,
                sample_count=1,
            )
            return pred_df

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
            LOGGER.warning(f"预测失败: {e}")
            LOGGER.debug(traceback.format_exc())
            return None


# ============================================================
# 因子信号提取
# ============================================================


def extract_kronos_factors(
    pred_df: pd.DataFrame,
    hist_df: pd.DataFrame,
) -> dict[str, float]:
    """从 Kronos 预测结果中提取因子信号

    提取的因子:
    - KRONOS_RET_{N}D: 未来 N 日预测收益率
    - KRONOS_VOL_{N}D: 未来 N 日预测波动率
    - KRONOS_DIR: 预测方向 (1=涨, -1=跌)
    - KRONOS_MOM: 预测动量 (预测收益/历史波动)
    - KRONOS_CONF: 预测置信度 (基于高低价范围)

    Args:
        pred_df: Kronos 预测结果
        hist_df: 历史数据 (用于计算基准)

    Returns:
        {因子名: 因子值} 字典
    """
    factors = {}
    try:
        if pred_df is None or pred_df.empty:
            return factors

        last_close = float(hist_df["close"].iloc[-1]) if len(hist_df) > 0 else 1.0
        if last_close <= 0:
            last_close = 1.0

        for horizon in [1, 5, 10, 20]:
            if len(pred_df) >= horizon:
                pred_close = float(pred_df["close"].iloc[horizon - 1])
                ret = (pred_close - last_close) / last_close
                factors[f"KRONOS_RET_{horizon}D"] = ret

                if horizon >= 5:
                    pred_returns = pred_df["close"].iloc[:horizon].pct_change().dropna()
                    if len(pred_returns) > 0:
                        vol = float(pred_returns.std())
                        factors[f"KRONOS_VOL_{horizon}D"] = vol

        if len(pred_df) >= 5:
            ret_5d = factors.get("KRONOS_RET_5D", 0.0)
            hist_returns = hist_df["close"].pct_change().dropna()
            hist_vol = (
                float(hist_returns.tail(20).std()) if len(hist_returns) >= 20 else 0.02
            )
            factors["KRONOS_MOM"] = ret_5d / max(hist_vol, 1e-6)

        factors["KRONOS_DIR"] = 1.0 if factors.get("KRONOS_RET_5D", 0.0) >= 0 else -1.0

        if len(pred_df) >= 5:
            pred_range = (
                pred_df["high"].iloc[:5].max() - pred_df["low"].iloc[:5].min()
            ) / last_close
            factors["KRONOS_CONF"] = 1.0 / max(pred_range, 1e-4)

        if len(pred_df) >= 5:
            pred_mean = float(pred_df["close"].iloc[:5].mean())
            factors["KRONOS_PREMIUM"] = (pred_mean - last_close) / last_close

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
        LOGGER.debug(f"提取因子失败: {e}")

    return factors


# ============================================================
# 主验证引擎
# ============================================================


class KronosValidationEngine:
    """Kronos 因子验证引擎"""

    IC_EFFECTIVE_THRESHOLD = 0.03
    IR_EFFECTIVE_THRESHOLD = 0.3
    MIN_SAMPLES = 10

    def __init__(
        self,
        output_dir: Path,
        model_sizes: list[str] = None,
        lookback: int = 400,
        pred_len: int = 20,
    ):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.model_sizes = model_sizes or ["small"]
        self.lookback = lookback
        self.pred_len = pred_len
        self._data_source = None
        self._models: dict[str, KronosModelWrapper] = {}
        self._factor_panels: dict[str, pd.DataFrame] = {}
        self._ic_series: dict[str, list[float]] = {}

    def _get_data_source(self):
        """获取数据源 (延迟初始化)"""
        if self._data_source is None:
            from utils.akshare_data_source import AKShareDataSource

            self._data_source = AKShareDataSource()
        return self._data_source

    def _get_model(self, size: str) -> KronosModelWrapper:
        """获取模型实例 (延迟初始化)"""
        if size not in self._models:
            self._models[size] = KronosModelWrapper(model_size=size)
        return self._models[size]

    def _fetch_from_local_cache(self, symbol: str) -> pd.DataFrame | None:
        """从本地数据缓存读取历史K线

        缓存文件命名: historical_{code}_5y_base.parquet
        code 为不带 .SH/.SZ 后缀的证券代码
        """
        cache_dir = Path(__file__).resolve().parent.parent / "data_cache"
        if not cache_dir.exists():
            return None

        code = symbol.split(".")[0]
        candidates = [
            cache_dir / f"historical_{code}_5y_base.parquet",
            cache_dir / f"historical_sh{code}_3m.parquet",
            cache_dir / f"historical_{code}_3y.parquet",
            cache_dir / f"historical_{code}_2y.parquet",
        ]
        for fpath in candidates:
            if fpath.exists():
                try:
                    df = pd.read_parquet(fpath)
                    if df is not None and not df.empty:
                        LOGGER.debug(
                            f"  {symbol}: 从本地缓存 {fpath.name} 读取 {len(df)} 条"
                        )
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
                    LOGGER.debug(f"  {symbol}: 读取本地缓存 {fpath.name} 失败: {e}")
        return None

    def fetch_data(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> dict[str, pd.DataFrame]:
        """获取所有标的的历史K线数据 (优先本地缓存, 兜底远程数据源)"""
        LOGGER.info(
            f"获取 {len(symbols)} 只标的的历史K线数据 ({start_date} ~ {end_date})"
        )
        ds = None

        all_data = {}
        success = 0
        failed = []

        for sym in symbols:
            df = None
            try:
                df = self._fetch_from_local_cache(sym)
                source = "本地缓存"
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
                df = None

            if df is None:
                try:
                    if ds is None:
                        ds = self._get_data_source()
                    df = ds.get_historical_klines(sym, period="1d", count=1000)
                    source = "远程数据源"
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
                    failed.append(f"{sym}: {e}")
                    continue

            if df is not None and not df.empty:
                df = df.sort_index()
                df = df.loc[start_date:end_date]
                if len(df) >= 60:
                    all_data[sym] = df
                    success += 1
                    LOGGER.debug(f"  {sym}: {len(df)} 条数据 ({source})")
                else:
                    failed.append(f"{sym}: 数据不足({len(df)}天)")
            else:
                failed.append(f"{sym}: 获取失败")

        LOGGER.info(f"数据获取完成: 成功 {success}/{len(symbols)}")
        if failed:
            LOGGER.warning(f"失败标的: {', '.join(failed[:10])}")

        return all_data

    def generate_factor_panel(
        self,
        all_data: dict[str, pd.DataFrame],
        model_size: str,
        rebalance_freq: str = "weekly",
    ) -> pd.DataFrame:
        """滚动生成因子面板

        Args:
            all_data: {symbol: DataFrame} 历史数据
            model_size: 模型规模
            rebalance_freq: 调仓频率 (daily/weekly)

        Returns:
            MultiIndex DataFrame (date × symbol) × factors
        """
        model = self._get_model(model_size)
        if not model.available:
            LOGGER.warning(f"Kronos-{model_size} 不可用，跳过因子生成")
            return pd.DataFrame()

        symbols = list(all_data.keys())
        LOGGER.info(
            f"[{model_size}] 滚动生成因子面板: "
            f"{len(symbols)} 只标的, lookback={self.lookback}, "
            f"pred_len={self.pred_len}, freq={rebalance_freq}"
        )

        all_factor_records = []
        latencies = []
        error_count = 0

        common_dates = None
        for _sym, df in all_data.items():
            if common_dates is None:
                common_dates = set(df.index)
            else:
                common_dates &= set(df.index)

        common_dates = sorted(common_dates)
        if rebalance_freq == "weekly":
            rebalance_dates = [
                d
                for i, d in enumerate(common_dates)
                if i == 0 or (d.weekday() == 0 and (d - common_dates[i - 1]).days >= 5)
            ]
        else:
            rebalance_dates = common_dates

        LOGGER.info(f"[{model_size}] 调仓日: {len(rebalance_dates)} 天")

        for date_idx, rebal_date in enumerate(rebalance_dates):
            if date_idx < self.lookback:
                continue

            LOGGER.info(
                f"[{model_size}] 处理日期 {rebal_date.date()} "
                f"({date_idx - self.lookback + 1}/{len(rebalance_dates) - self.lookback})"
            )

            for sym in symbols:
                df = all_data[sym]
                if rebal_date not in df.index:
                    continue

                pos = df.index.get_loc(rebal_date)
                if pos < self.lookback:
                    continue

                hist_slice = df.iloc[pos - self.lookback : pos].copy()

                t0 = time.perf_counter()
                try:
                    pred_df = model.predict(
                        hist_slice,
                        pred_len=self.pred_len,
                    )
                    latency_ms = (time.perf_counter() - t0) * 1000
                    latencies.append(latency_ms)

                    if pred_df is not None and not pred_df.empty:
                        factors = extract_kronos_factors(pred_df, hist_slice)
                        for fname, fval in factors.items():
                            if np.isfinite(fval):
                                all_factor_records.append(
                                    {
                                        "date": rebal_date,
                                        "symbol": sym,
                                        "factor": f"{fname}_{model_size.upper()}",
                                        "value": fval,
                                    }
                                )
                    else:
                        error_count += 1

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
                    error_count += 1
                    LOGGER.debug(f"  {sym}@{rebal_date.date()} 预测异常: {e}")

        if not all_factor_records:
            LOGGER.warning(f"[{model_size}] 未生成任何因子数据")
            return pd.DataFrame()

        raw_df = pd.DataFrame(all_factor_records)
        panel = raw_df.pivot_table(
            index=["date", "symbol"],
            columns="factor",
            values="value",
        )

        avg_latency = float(np.mean(latencies)) if latencies else 0.0
        LOGGER.info(
            f"[{model_size}] 因子面板生成完成: "
            f"{panel.shape[0]} 条记录, "
            f"平均延迟 {avg_latency:.1f}ms, "
            f"错误 {error_count} 次"
        )

        return panel

    def compute_forward_returns(
        self,
        all_data: dict[str, pd.DataFrame],
        horizons: list[int] = None,
    ) -> dict[int, pd.DataFrame]:
        """计算未来收益率面板 (用于IC计算)

        Args:
            all_data: {symbol: DataFrame} 历史数据
            horizons: 预测天数列表

        Returns:
            {horizon: DataFrame(date×symbol)} 未来收益率
        """
        horizons = horizons or [1, 5, 10, 20]
        returns_panels = {}

        for h in horizons:
            records = []
            for sym, df in all_data.items():
                closes = df["close"]
                fwd_rets = closes.shift(-h) / closes - 1.0
                for date, ret in fwd_rets.items():
                    if np.isfinite(ret):
                        records.append({"date": date, "symbol": sym, f"ret_{h}d": ret})
            if records:
                panel = pd.DataFrame(records).pivot(
                    index="date", columns="symbol", values=f"ret_{h}d"
                )
                returns_panels[h] = panel

        return returns_panels

    def validate_factor(
        self,
        factor_panel: pd.DataFrame,
        returns_panels: dict[int, pd.DataFrame],
        factor_name: str,
        model_size: str,
    ) -> KronosFactorResult | None:
        """验证单个因子的有效性

        Args:
            factor_panel: (date×symbol) × factor 的因子值面板
            returns_panels: {horizon: (date×symbol)} 未来收益率面板
            factor_name: 因子名称
            model_size: 模型规模

        Returns:
            KronosFactorResult 或 None (样本不足)
        """
        try:
            if factor_name not in factor_panel.columns:
                return None

            factor_series = factor_panel[factor_name].unstack(level="symbol")
            horizon = 1
            for h in [1, 5, 10, 20]:
                if f"_{h}D" in factor_name:
                    horizon = h
                    break

            if horizon not in returns_panels:
                return None

            rets = returns_panels[horizon]

            common_dates = factor_series.index.intersection(rets.index)
            if len(common_dates) < self.MIN_SAMPLES:
                return None

            ics = []
            ics_5d = []
            for date in common_dates:
                fvals = factor_series.loc[date].dropna()
                if len(fvals) < 3:
                    continue

                for fwd_h, ic_list in [(horizon, ics), (5, ics_5d)]:
                    if fwd_h in returns_panels:
                        r = (
                            returns_panels[fwd_h]
                            .loc[date]
                            .reindex(fvals.index)
                            .dropna()
                        )
                        common = fvals.index.intersection(r.index)
                        if len(common) >= 3:
                            try:
                                ic = float(
                                    fvals[common].corr(r[common], method="spearman")
                                )
                                if np.isfinite(ic):
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

            if len(ics) < self.MIN_SAMPLES:
                return None

            ics_arr = np.array(ics)
            ic_mean = float(np.mean(ics_arr))
            ic_std = float(np.std(ics_arr)) if len(ics_arr) > 1 else 1e-12
            ic_ir = ic_mean / ic_std if ic_std > 1e-12 else 0.0
            ic_pos = float(np.mean(ics_arr > 0))

            decay_5d = 0.0
            if len(ics_5d) >= self.MIN_SAMPLES:
                ic5_mean = float(np.mean(ics_5d))
                decay_5d = abs(ic5_mean) / max(abs(ic_mean), 1e-12)

            effective = (
                abs(ic_mean) >= self.IC_EFFECTIVE_THRESHOLD
                and abs(ic_ir) >= self.IR_EFFECTIVE_THRESHOLD
            )

            score = (
                abs(ic_mean) * 20
                + min(abs(ic_ir), 3.0) * 10
                + ic_pos * 10
                + min(decay_5d, 2.0) * 5
            )

            direction = "positive" if ic_mean >= 0 else "negative"

            n_syms = (
                int(factor_series.count(axis=1).mean()) if len(factor_series) > 0 else 0
            )

            return KronosFactorResult(
                factor_name=factor_name,
                model_size=model_size,
                ic_mean=ic_mean,
                ic_std=ic_std,
                ic_ir=ic_ir,
                ic_positive_ratio=ic_pos,
                decay_5d=decay_5d,
                n_days=len(ics),
                n_symbols=n_syms,
                effective=effective,
                score=score,
                direction=direction,
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
        ) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            LOGGER.debug(f"验证因子 {factor_name} 失败: {e}")
            return None

    def run(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> KronosValidationReport:
        """执行完整验证流程"""
        report = KronosValidationReport(
            generation_time=now_bj().strftime("%Y-%m-%d %H:%M:%S"),
            model_sizes_tested=self.model_sizes,
            symbols=symbols,
            date_range=(start_date, end_date),
            lookback=self.lookback,
            pred_len=self.pred_len,
        )

        LOGGER.info("=" * 60)
        LOGGER.info("Kronos 因子有效性验证启动")
        LOGGER.info(f"  标的数: {len(symbols)}")
        LOGGER.info(f"  日期范围: {start_date} ~ {end_date}")
        LOGGER.info(f"  模型: {', '.join(self.model_sizes)}")
        LOGGER.info("=" * 60)

        t_total = time.perf_counter()

        all_data = self.fetch_data(symbols, start_date, end_date)
        if not all_data:
            report.errors.append("未获取到任何有效数据")
            return report

        returns_panels = self.compute_forward_returns(all_data)
        LOGGER.info(f"计算了 {len(returns_panels)} 个未来收益面板")

        for model_size in self.model_sizes:
            LOGGER.info(f"\n--- 验证模型: Kronos-{model_size} ---")

            panel = self.generate_factor_panel(all_data, model_size)
            if panel.empty:
                report.notes.append(
                    f"Kronos-{model_size}: 未生成因子面板 (模型可能不可用)"
                )
                continue

            panel_file = self.output_dir / f"kronos_factor_panel_{model_size}.csv"
            panel.to_csv(panel_file, encoding="utf-8-sig")
            LOGGER.info(f"因子面板已保存: {panel_file}")

            factor_names = panel.columns.tolist()
            LOGGER.info(f"共 {len(factor_names)} 个因子待验证")

            for fname in factor_names:
                result = self.validate_factor(panel, returns_panels, fname, model_size)
                if result is not None:
                    report.results.append(result)
                    LOGGER.info(
                        f"  {fname}: IC={result.ic_mean:.4f}, "
                        f"IC_IR={result.ic_ir:.3f}, "
                        f"有效={'✅' if result.effective else '❌'}"
                    )

        report.results.sort(key=lambda r: r.score, reverse=True)

        total_time = time.perf_counter() - t_total
        report.notes.append(f"总耗时: {total_time:.1f}s")

        effective_count = sum(1 for r in report.results if r.effective)
        LOGGER.info(
            f"\n验证完成: {len(report.results)} 个因子, "
            f"有效 {effective_count} 个, 总耗时 {total_time:.1f}s"
        )

        return report

    def save_report(self, report: KronosValidationReport) -> None:
        """保存验证报告"""
        json_file = self.output_dir / "kronos_factor_report.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        LOGGER.info(f"JSON 报告已保存: {json_file}")

        md_file = self.output_dir / "kronos_factor_report.md"
        self._save_markdown_report(report, md_file)
        LOGGER.info(f"Markdown 报告已保存: {md_file}")

    def _save_markdown_report(self, report: KronosValidationReport, path: Path) -> None:
        """生成 Markdown 格式报告"""
        lines = []

        lines.append("# Kronos 因子有效性验证报告")
        lines.append("")
        lines.append(f"**生成时间**: {report.generation_time}")
        lines.append(f"**标的池**: {len(report.symbols)} 只标的")
        lines.append(f"**测试模型**: {', '.join(report.model_sizes_tested)}")
        lines.append(f"**日期范围**: {report.date_range[0]} ~ {report.date_range[1]}")
        lines.append(f"**回看窗口**: {report.lookback} 天")
        lines.append(f"**预测长度**: {report.pred_len} 天")
        lines.append("")

        lines.append("## 评估标准")
        lines.append("")
        lines.append("- **IC**: 因子值与未来收益的 Spearman 秩相关系数")
        lines.append("- **IC_IR**: IC均值 / IC标准差，衡量因子稳定性")
        lines.append("- **有效标准**: |IC| ≥ 0.03 且 |IC_IR| ≥ 0.3")
        lines.append(
            "- **评分**: |IC|×20 + min(|IC_IR|,3)×10 + 正IC占比×10 + 5日衰减×5"
        )
        lines.append("")

        effective = [r for r in report.results if r.effective]
        all_results = report.results

        lines.append("## 核心结论")
        lines.append("")
        lines.append(f"- **测试因子总数**: {len(all_results)}")
        lines.append(f"- **有效因子数**: {len(effective)}")
        if effective:
            best = effective[0]
            lines.append(
                f"- **最佳因子**: {best.factor_name} "
                f"(IC={best.ic_mean:.4f}, IC_IR={best.ic_ir:.3f})"
            )
        lines.append("")

        if effective:
            lines.append("## ✅ 有效因子列表 (|IC|≥0.03 且 |IC_IR|≥0.3)")
            lines.append("")
            lines.append(
                "| 排名 | 因子名 | 模型 | IC均值 | IC_IR | 正IC占比 | 5日衰减 | 样本天数 | 方向 | 评分 |"
            )
            lines.append(
                "|------|--------|------|--------|-------|----------|---------|----------|------|------|"
            )
            for i, r in enumerate(effective):
                lines.append(
                    f"| {i+1} | {r.factor_name} | {r.model_size} | "
                    f"{r.ic_mean:.4f} | {r.ic_ir:.3f} | "
                    f"{r.ic_positive_ratio:.1%} | {r.decay_5d:.2f} | "
                    f"{r.n_days} | {r.direction} | {r.score:.1f} |"
                )
            lines.append("")

        lines.append("## 📊 全部因子排名")
        lines.append("")
        lines.append(
            "| 排名 | 因子名 | 模型 | IC均值 | IC_IR | 正IC占比 | 有效 | 评分 |"
        )
        lines.append(
            "|------|--------|------|--------|-------|----------|------|------|"
        )
        for i, r in enumerate(all_results):
            eff_marker = "✅" if r.effective else "❌"
            lines.append(
                f"| {i+1} | {r.factor_name} | {r.model_size} | "
                f"{r.ic_mean:.4f} | {r.ic_ir:.3f} | "
                f"{r.ic_positive_ratio:.1%} | {eff_marker} | {r.score:.1f} |"
            )
        lines.append("")

        if report.notes:
            lines.append("## 📝 备注")
            lines.append("")
            for note in report.notes:
                lines.append(f"- {note}")
            lines.append("")

        if report.errors:
            lines.append("## ⚠️ 错误信息")
            lines.append("")
            for err in report.errors:
                lines.append(f"- {err}")
            lines.append("")

        lines.append("---")
        lines.append("*报告由 Kronos 因子验证脚本自动生成*")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


# ============================================================
# CLI 入口
# ============================================================


def get_default_symbols(fast: bool = False) -> list[str]:
    """获取默认标的池"""
    core_etfs = [
        "510300.SH",
        "510500.SH",
        "512100.SH",
        "588000.SH",
        "159915.SZ",
        "510050.SH",
        "518880.SH",
        "512170.SH",
        "512880.SH",
        "512800.SH",
        "515030.SH",
        "512760.SH",
    ]

    core_stocks = [
        "600900.SH",
        "601088.SH",
        "600276.SH",
        "688041.SH",
        "002371.SZ",
        "300308.SZ",
        "603019.SH",
        "300033.SZ",
        "300274.SZ",
        "688017.SH",
    ]

    if fast:
        return core_etfs[:6] + core_stocks[:4]
    return core_etfs + core_stocks


def main():
    parser = argparse.ArgumentParser(description="Kronos 因子有效性验证")
    parser.add_argument("--codes", type=str, default="", help="标的代码, 逗号分隔")
    parser.add_argument("--start", type=str, default="2024-01-01", help="开始日期")
    parser.add_argument("--end", type=str, default=None, help="结束日期 (默认今天)")
    parser.add_argument(
        "--models",
        type=str,
        default="small",
        help="模型规模, 逗号分隔 (mini/small/base)",
    )
    parser.add_argument("--lookback", type=int, default=400, help="回看窗口")
    parser.add_argument("--pred-len", type=int, default=20, help="预测长度")
    parser.add_argument("--fast", action="store_true", help="快速验证模式 (少标的)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")

    args = parser.parse_args()

    end_date = args.end or now_bj().strftime("%Y-%m-%d")
    start_date = args.start

    if args.codes:
        symbols = [s.strip() for s in args.codes.split(",") if s.strip()]
    else:
        symbols = get_default_symbols(fast=args.fast)

    model_sizes = [m.strip() for m in args.models.split(",") if m.strip()]

    timestamp = now_bj().strftime("%Y%m%d_%H%M%S")
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = (
            Path(__file__).resolve().parent
            / "outputs"
            / f"kronos_validation_{timestamp}"
        )

    setup_logging(output_dir)

    try:
        engine = KronosValidationEngine(
            output_dir=output_dir,
            model_sizes=model_sizes,
            lookback=args.lookback,
            pred_len=args.pred_len,
        )
        report = engine.run(symbols, start_date, end_date)
        engine.save_report(report)

        print("\n" + "=" * 60)
        print("验证完成!")
        print(f"输出目录: {output_dir}")
        print(f"报告文件: {output_dir / 'kronos_factor_report.md'}")
        print("=" * 60)

        effective = [r for r in report.results if r.effective]
        if effective:
            print(f"\n✅ 发现 {len(effective)} 个有效因子!")
            for r in effective[:5]:
                print(f"   - {r.factor_name}: IC={r.ic_mean:.4f}, IC_IR={r.ic_ir:.3f}")
        else:
            print("\n⚠️  未发现有效因子 (|IC|≥0.03 且 |IC_IR|≥0.3)")

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
        LOGGER.error(f"验证流程异常: {e}")
        LOGGER.error(traceback.format_exc())
        print(f"\n❌ 验证失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
