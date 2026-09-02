# S13 Selection Alpha 路径 A 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or subagent-driven-development) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用系统因子库重算 2021-2026 月频 point-in-time 股票截面综合分，聚合为 ETF 信号，构建 S13 核心卫星组合（S12 底仓 70% + Top-3 等权卫星仓 30%）并跑三件套诚实验证。

**Architecture:** 三阶段管线——① `scripts/build_s13_signals.py` 离线重建历史月频信号（成分股快照 + factor_scorer 截面打分 + 流通市值加权聚合 → `data/etf_option_backtest/s13_signals.parquet`）；② `run_s13_core_satellite()` 加入 `run_etf_option_backtest.py` 策略注册表；③ `scripts/run_s13_validation.py` 跑 S12 vs S13 对照 + ablation + 三件套。

**Tech Stack:** pandas / numpy / 既有组件（factor_scorer, SurvivorshipBiasFreeUniverse, MarketDataProvider, run_p23_validation 的 DSR/CPCV/Noise）

**设计真相源:** `docs/S13_selection_alpha_注入设计_20260902.md`

---

## 关键既有接口（已核实签名）

```python
# utils/universe/factor_scorer.py
batch_compute_factors(symbols: list[str], klines_loader, config: ScoringConfig | None = None,
                      progress_callback=None) -> tuple[pd.DataFrame, dict[str, list[str]]]
#   factor_df: index=symbol, columns=[factor_id...]; klines_loader(symbol) -> DataFrame
#   K线 columns: open/high/low/close/volume/amount

cross_sectional_score(factor_df: pd.DataFrame, theme_factors: dict[str, list[str]],
                      config: ScoringConfig) -> pd.DataFrame
#   返回含 composite_score 与 rank 列

# utils/universe/survivorship_free_universe.py
SurvivorshipBiasFreeUniverse.get_universe_at_date(date: str, pool: str = "hs300_zz500")
#   -> DataFrame[columns=code, name, index]; 快照目录 data/universe/snapshots/

# utils/data_provider.py
MarketDataProvider().get_historical_data(symbol, period="3y")  # OHLCV DataFrame

# data/etf_option_backtest/run_etf_option_backtest.py  (L960-1015)
S12_UNIVERSE = ("518880", "511260", "512890"); S12_REBAL_FREQ = 21
_inverse_vol_weights(prices, codes, i) -> dict[str, float]
run_s12_defensive_rp(prices, tw) -> (equity_list, total_cost)
TRANSACTION_COST / SLIPPAGE / INITIAL_CAPITAL 常量; main() L1053 策略注册表 L1072-1093

# scripts/run_p23_validation.py
run_validation(eq_lists: ..., n_trials_dsr: int, quick: bool) -> list[StrategyValidation]
compute_metrics_from_eq(eq, bench_eq) -> dict
```

---

## Task 1: 信号重建脚本 `scripts/build_s13_signals.py`

**Files:**
- Create: `scripts/build_s13_signals.py`

- [ ] **Step 1: 写脚本骨架与 ETF→指数映射**

```python
"""S13 路径 A: 重建 2021-2026 月频 ETF 聚合信号 (point-in-time).

管线: 月末截面 → 历史成分股快照 → factor_scorer 截面综合分
      → 成分权重加权聚合为 ETF 信号 → parquet 落盘
输出: data/etf_option_backtest/s13_signals.parquet
      index=date(月末交易日), columns=[etf_code...], 值=composite 聚合分
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(_PROJECT_ROOT))

from utils.universe.factor_scorer import (  # noqa: E402
    ScoringConfig,
    batch_compute_factors,
    cross_sectional_score,
    select_factor_ids,
)
from utils.universe.survivorship_free_universe import (  # noqa: E402
    SurvivorshipBiasFreeUniverse,
)

logger = logging.getLogger("s13_signals")

OUTPUT_PATH = _PROJECT_ROOT / "data" / "etf_option_backtest" / "s13_signals.parquet"

# ETF -> 跟踪指数 (csindex 代码) 映射: 14 主池候选 ETF + 底仓红利低波排除
# 卫星候选 = 14 ETF 主池 (scripts/fetch_etf_phase2_data.py L43-145)
ETF_INDEX_MAP = {
    "510300": "000300",  # 沪深300
    "510500": "000905",  # 中证500
    "512100": "000852",  # 中证1000
    # 行业/主题 ETF 无对应 csindex 宽基指数 → 统一回退 hs300_zz500 池 + 行业近似
    # (见 Step 2 _resolve_constituents 的回退策略)
}
SATELLITE_ETFS = [
    "510300", "510500", "512100", "512880", "512010", "512690",
    "515000", "512480", "512800", "516160", "512760",
]  # 14 主池排除底仓三资产 (518880/511260/512890) 与无成分数据标的

START_DATE = "2021-01-01"
END_DATE = "2026-08-31"
```

- [ ] **Step 2: 写成分股解析与月频时间轴**

```python
def _month_end_trading_days(prices: pd.DataFrame) -> list[pd.Timestamp]:
    """从 ETF 面板取每月最后一个交易日 (信号计算时点)."""
    s = pd.Series(prices.index, index=prices.index)
    return list(s.groupby([prices.index.year, prices.index.month]).last())


def _resolve_constituents(
    sfu: SurvivorshipBiasFreeUniverse, date: str
) -> pd.DataFrame:
    """月末 date 的成分股: hs300+zz500 并集 (宽基 ETF 与行业 ETF 共用股票池).

    行业 ETF 无法按跟踪指数还原历史成分, 统一用全市场核心池近似的
    '市场综合分' + 行业 ETF 用其成分近似——本路径 A 的简化假设, 已知偏差:
    行业 ETF 信号 = 全池信号在行业内股票上的投影. 后续若引入 akshare
    行业成分接口可替换此函数 (接口签名不变).
    """
    df = sfu.get_universe_at_date(date, pool="hs300_zz500")
    return df[df["code"].notna()].copy()
```

- [ ] **Step 3: 写个股 K 线加载器（point-in-time 截尾）**

```python
def _make_klines_loader(cutoff: pd.Timestamp):
    """klines_loader(symbol) -> DataFrame, K线仅截至 cutoff (防前视).

    优先读 data/cache/klines/{symbol}.parquet 缓存, 缺失时
    MarketDataProvider.get_historical_data 拉取并落缓存.
    """
    from utils.data_provider import MarketDataProvider

    cache_dir = _PROJECT_ROOT / "data" / "cache" / "klines"
    cache_dir.mkdir(parents=True, exist_ok=True)
    provider = MarketDataProvider()

    def loader(symbol: str) -> pd.DataFrame:
        fp = cache_dir / f"{symbol}.parquet"
        df: pd.DataFrame | None = None
        if fp.exists():
            try:
                df = pd.read_parquet(fp)
            except OSError:
                df = None
        if df is None or df.empty:
            try:
                df = provider.get_historical_data(symbol, period="5y")
            except Exception:  # noqa: BLE001 - 数据源 fail-safe
                return pd.DataFrame()
            if df is not None and not df.empty:
                df.to_parquet(fp)
        if df is None or df.empty:
            return pd.DataFrame()
        # point-in-time 截尾: 只保留 cutoff 及以前的行
        if "date" in df.columns:
            df = df[pd.to_datetime(df["date"]) <= cutoff]
        return df

    return loader
```

- [ ] **Step 4: 写月频截面计算主循环**

```python
def build_signals(data_file: Path | None = None) -> pd.DataFrame:
    prices = _load_etf_panel(data_file)
    month_ends = _month_end_trading_days(prices)
    sfu = SurvivorshipBiasFreeUniverse()
    config = ScoringConfig()

    # 因子清单只选一次 (adapter 确定性, 不随时间变)
    from utils.vibe_trading_adapter import get_vibe_adapter

    theme_factors = select_factor_ids(get_vibe_adapter(), config)

    records: dict[str, dict[str, float]] = {}
    for me in month_ends:
        date_str = me.strftime("%Y-%m-%d")
        constituents = _resolve_constituents(sfu, date_str)
        if constituents.empty:
            logger.warning("%s 成分为空, 跳过", date_str)
            continue
        symbols = list(constituents["code"].astype(str))
        loader = _make_klines_loader(me)
        factor_df, _ = batch_compute_factors(symbols, loader, config)
        if factor_df.empty:
            logger.warning("%s 因子计算为空, 跳过", date_str)
            continue
        scored = cross_sectional_score(factor_df, theme_factors, config)
        # scored: index=symbol, 含 composite_score
        records[date_str] = scored["composite_score"].to_dict()
        logger.info("%s: %d 只股票打分完成", date_str, len(scored))

    sig = pd.DataFrame(records).T  # index=date, columns=symbol
    return sig.sort_index()
```

- [ ] **Step 5: 写 ETF 聚合与 main**

```python
def _load_etf_panel(data_file: Path | None) -> pd.DataFrame:
    fp = data_file or (_PROJECT_ROOT / "data" / "etf_option_backtest" / "all_etf_daily.parquet")
    df = pd.read_parquet(fp)
    wide = df.pivot_table(index="date", columns="code", values="close").sort_index()
    return wide


def aggregate_to_etf(stock_scores: pd.DataFrame) -> pd.DataFrame:
    """个股综合分 → ETF 层信号: 该月截面全部股票按 composite_score 排名
    分十档 (deciles), ETF 信号 = 其近似成分股所在档位的市值加权均分.

    路径 A 简化: 无逐 ETF 精确成分 → 统一用全池月度截面分位聚合, 即
    ETF 信号 = cross-section 综合分的月度市场宽度代理 (可解释为
    '该月市场选股 alpha 可得性'), 卫星仓在其上做 Top-3 选择.
    """
    out = {}
    for date, row in stock_scores.iterrows():
        s = row.dropna()
        if s.empty:
            continue
        out[date] = {"market_score": float(s.mean()), "breadth": float((s > 0).mean())}
    return pd.DataFrame(out).T.sort_index()


def main() -> None:
    parser = argparse.ArgumentParser(description="S13 路径 A 信号重建")
    parser.add_argument("--data-file", default=None)
    parser.add_argument("--dry-run", action="store_true", help="只打信号摘要不落盘")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stock_scores = build_signals(Path(args.data_file) if args.data_file else None)
    etf_sig = aggregate_to_etf(stock_scores)
    logger.info("信号矩阵: %d 月份 × %d 股票; 聚合: %d 月份", len(stock_scores), stock_scores.shape[1], len(etf_sig))
    if args.dry_run:
        print(etf_sig.tail(6))
        return
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    stock_scores.to_parquet(OUTPUT_PATH)
    logger.info("落盘 %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 干跑验证（1 个月截面冒烟）**

Run: `python scripts/build_s13_signals.py --dry-run`
Expected: 打印最近 6 个月 market_score/breadth 摘要；首月因子计算日志正常（gtja191 因子 zoo 加载、每主题因子数）；不落盘。
若单月耗时 > 10 分钟，改用 `ScoringConfig(max_factors_per_theme=10)` 缩减因子面（记录该折衷）。

- [ ] **Step 7: 全量跑 2021-2026 月频信号并落盘**

Run: `python scripts/build_s13_signals.py`
Expected: ~68 个月截面全部打分，`s13_signals.parquet` 生成。记下总耗时与降级月份（快照缺失回退）数。

## Task 2: S13 策略函数 + 注册

**Files:**
- Modify: `data/etf_option_backtest/run_etf_option_backtest.py`（S12 之后 L1015 处插入，注册表 L1072-1093 加两行）

- [ ] **Step 1: 写 run_s13_core_satellite**

```python
# ============================================================
# S13: 核心-卫星 (S12 底仓 70% + 因子信号 Top-3 等权卫星 30%) (2026-09-02)
# 路径 A: 信号为全池月频截面综合分 (data/etf_option_backtest/s13_signals.parquet).
# 超参先验固定, 不进调优: 卫星上限 30% / Top-K=3 / 月频 21 交易日.
# ============================================================
S13_SIGNAL_FILE = Path(__file__).parent / "s13_signals.parquet"
S13_SATELLITE_CAP = 0.30
S13_TOP_K = 3
S13_SATELLITE_UNIVERSE = tuple(SATELLITE_ETFS)  # 与信号端一致


def _load_s13_signal_file() -> pd.DataFrame:
    if not S13_SIGNAL_FILE.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(S13_SIGNAL_FILE)
    except OSError:
        return pd.DataFrame()


def run_s13_core_satellite(
    prices: pd.DataFrame, tw: dict[str, float]
) -> tuple[list[float], float]:
    """S13: S12 底仓 70% + 聚合信号 Top-3 ETF 等权卫星仓 30% (月频).

    无前视: 信号矩阵每行是月末用 ≤当日数据算的截面分, 下月生效.
    """
    sig = _load_s13_signal_file()
    codes = [c for c in S12_UNIVERSE if c in prices.columns]
    ret_df = prices.pct_change().fillna(0.0)
    unit_cost = TRANSACTION_COST + SLIPPAGE
    n_c = len(codes)
    w = {c: 1.0 / n_c for c in codes} if n_c else {}
    sat_codes: list[str] = []
    dates = prices.index

    n = len(prices)
    out = [float(INITIAL_CAPITAL)]
    tc_total = 0.0
    last_month = -1
    for i in range(1, n):
        d = dates[i]
        turnover = 0.0
        # 月频换仓 (含卫星): 与 S12 的 21 交易日错开没关系, 按自然月更直观
        if d.month != last_month:
            last_month = d.month
            # 底仓逆波动率 (复用 S12 逻辑, i-1 数据)
            new_w = _inverse_vol_weights(prices, codes, i)
            base_w = {c: new_w[c] * (1.0 - S13_SATELLITE_CAP) for c in codes}
            # 卫星: 上月末截面分 Top-3 (用 ≤i-1 的最近信号行)
            if not sig.empty:
                prior = sig[sig.index < d]
                if not prior.empty:
                    row = prior.iloc[-1]
                    # 按各 ETF 当期可得信号排序 — 路径 A 简化: 信号矩阵
                    # 为市场宽度代理时退化为动量代理, 见 aggregate_to_etf;
                    # Top-3 取信号行中值最大的 3 个候选 ETF
                    cand = {
                        c: float(row[c]) for c in S13_SATELLITE_UNIVERSE
                        if c in row and not pd.isna(row[c]) and c in prices.columns
                    }
                    top = sorted(cand, key=cand.get, reverse=True)[: S13_TOP_K]
                    sat_codes = top
            sat_w = {c: S13_SATELLITE_CAP / max(len(sat_codes), 1) for c in sat_codes}
            new_total = dict(base_w)
            for c, ww in sat_w.items():
                new_total[c] = new_total.get(c, 0.0) + ww
            turnover = 0.5 * sum(
                abs(new_total.get(c, 0.0) - w.get(c, 0.0)) for c in set(new_total) | set(w)
            )
            w = new_total

        ret_day = sum(w.get(c, 0.0) * ret_df[c].iloc[i] for c in w)
        daily = ret_day - turnover * unit_cost
        tc_total += turnover * unit_cost * out[i - 1]
        out.append(out[i - 1] * (1.0 + daily))

    return out, tc_total
```

- [ ] **Step 2: 注册到策略表与 smap**

`all_strategies` 追加 `("S13 核心-卫星(因子信号)", run_s13_core_satellite),`；`smap` 加 `"s13": 12`。

- [ ] **Step 3: 单测（新建 `tests/unit/test_s13_strategy_unit.py`）**

```python
"""S13 核心-卫星策略单测: 权重结构 / 无前视 / 成本扣除."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _make_prices(days: int = 130, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-04", periods=days)
    codes = ["518880", "511260", "512890", "510300", "512100", "515000"]
    df = pd.DataFrame(
        {c: 100 * np.cumprod(1 + rng.normal(0.0003, 0.012, days)) for c in codes},
        index=idx,
    )
    return df


class TestS13CoreSatellite:
    def test_runs_and_returns_equity_list(self, monkeypatch, tmp_path):
        from data.etf_option_backtest import run_etf_option_backtest as bt

        # 无信号文件时卫星仓为空 → 退化为 70% 底仓 (信号缺失降级)
        monkeypatch.setattr(bt, "S13_SIGNAL_FILE", tmp_path / "nonexistent.parquet")
        eq, tc = bt.run_s13_core_satellite(_make_prices(), {})
        assert len(eq) == 131
        assert np.isfinite(eq).all()
        assert tc >= 0.0

    def test_signal_file_drives_topk(self, monkeypatch, tmp_path):
        from data.etf_option_backtest import run_etf_option_backtest as bt

        prices = _make_prices(130)
        sig = pd.DataFrame(
            {"510300": [1.0, 1.0, 1.0], "512100": [0.5, 0.5, 0.5], "515000": [0.2, 0.2, 0.2]},
            index=pd.to_datetime(["2021-01-29", "2021-02-26", "2021-03-31"]),
        )
        fp = tmp_path / "sig.parquet"
        sig.to_parquet(fp)
        monkeypatch.setattr(bt, "S13_SIGNAL_FILE", fp)
        eq_with, _ = bt.run_s13_core_satellite(prices, {})
        # 无信号版本对照: 卫星仓缺失 → 曲线应不同
        monkeypatch.setattr(bt, "S13_SIGNAL_FILE", tmp_path / "no.parquet")
        eq_without, _ = bt.run_s13_core_satellite(prices, {})
        assert not np.allclose(eq_with, eq_without)

    def test_monthly_turnover_cost_positive_when_rebalancing(self, monkeypatch, tmp_path):
        from data.etf_option_backtest import run_etf_option_backtest as bt

        monkeypatch.setattr(bt, "S13_SIGNAL_FILE", tmp_path / "no.parquet")
        eq, tc = bt.run_s13_core_satellite(_make_prices(130), {})
        assert tc > 0.0  # 月频再平衡有换手
```

Run: `python -m pytest tests/unit/test_s13_strategy_unit.py -v`
Expected: 3 passed。注意 import 路径——run_etf_option_backtest.py 在 `data/etf_option_backtest/` 下非包，需 `sys.path` 或 importlib 方式加载（参照 tests 中 S12 既有测试的加载方式，若已有直接抄其 fixture）。

- [ ] **Step 4: 回测注册冒烟**

Run: `python data/etf_option_backtest/run_etf_option_backtest.py --strategies s13`
Expected: 输出 S13 一行指标（信号缺失时 = 70% 底仓降级版）。

## Task 3: 验证脚本 `scripts/run_s13_validation.py`

**Files:**
- Create: `scripts/run_s13_validation.py`

- [ ] **Step 1: 写验证脚本（S12 vs S13 对照 + ablation + 三件套）**

```python
"""S13 路径 A 诚实验证: S12 vs S13 对照 + ablation + DSR/CPCV/Noise 三件套.

多重检验计数: S1-S12 家族已 14 次, S13 计入后 n_trials=15.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "data" / "etf_option_backtest"))
sys.path.insert(0, str(_PROJECT_ROOT))

import run_etf_option_backtest as bt  # noqa: E402

logger = logging.getLogger("s13_validation")


def main() -> None:
    parser = argparse.ArgumentParser(description="S13 三件套验证")
    parser.add_argument("--n-trials", type=int, default=15, help="DSR 多重检验次数")
    parser.add_argument("--quick", action="store_true", help="仅 DSR")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    prices = bt.load_etf_prices(None)
    cfg = bt.load_config()
    tw = bt.extract_target_weights(cfg)
    bench_eq = bt.run_benchmark(prices)

    runs = {
        "S12 底仓(对照)": bt.run_s12_defensive_rp,
        "S13 核心-卫星(因子信号)": bt.run_s13_core_satellite,
    }
    equities = {}
    for name, fn in runs.items():
        eq, tc = fn(prices, tw)
        equities[name] = eq
        m = bt.compute_metrics(eq, bench_eq)  # 若无此函数用本地实现 (见 Step 2)
        logger.info("%s: 年化 %.2f%% 回撤 %.2f%% Sharpe %.3f",
                    name, m["annual_return"] * 100, m["max_drawdown"] * 100, m["sharpe"])

    # ablation: S13 - S12 超额
    # 三件套: 复用 scripts/run_p23_validation.py 的 run_validation
    ...
```

- [ ] **Step 2: 补齐指标与三件套调用（照抄 run_p23_validation 既有结构）**

- [ ] **Step 3: 跑全量验证并产出报告**

Run: `python scripts/run_s13_validation.py`
Expected: 控制台报告 + `data/etf_option_backtest/s13_validation_<ts>.md` 落盘（格式仿 p23_honest_validation_*.md）。判定规则按设计文档 §六验收标准。

## Task 4: 结论沉淀

**Files:**
- Modify: `docs/S13_selection_alpha_注入设计_20260902.md`（追加执行结果）
- Modify: `cairn/LOG.md`、`cairn/etf-option-hedge-model.md`

- [ ] **Step 1:** 按验收标准六项打分（年化/回撤/DSR/CPCV/ablation），写明结论：S13 是否通过、DSR 相对 S12 的 0.50 是否抬升
- [ ] **Step 2:** LOG.md 顶部追加条目（≤20 行，摘要+指针）；etf-option-hedge-model.md 补 S13 小节
- [ ] **Step 3:** 决策分支：通过 → S13 进 shadow 第二并行策略；不通过 → 沉淀结论关闭方向，Phase 3 按 09-06 跑纯 S12

---

## Self-Review 结论

- **覆盖检查**：设计文档 §二组合结构（Task 2）、§三信号管线（Task 1）、§四诚实验证（Task 3 三件套+ablation+多重检验 n_trials=15）、§六验收标准（Task 4 Step 1 打分）——全覆盖
- **已知简化**（路径 A 的诚实折衷，已在设计文档 §三声明）：行业 ETF 无历史成分 → 信号退化为市场宽度代理；Task 2 的 Top-3 选择在宽度代理信号下实际驱动因素需在报告中如实呈现，若 ablation 显示超额≈0 则结论就是"路径 A 信号太弱，需路径 B LGB 重训"
- **占位符扫描**：Task 3 Step 2 无代码——因为必须先读 run_p23_validation.py 全文才能照抄其结构，执行时读后再写，避免计划里凭记忆写错接口
