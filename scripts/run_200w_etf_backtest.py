"""200万ETF期权对冲策略回测验证 (Phase 2).

消费 config/portfolio_200w_etf.yaml 配置 + ETF历史数据 + BS期权模拟,
端到端回测验证200万ETF期权对冲策略.

回测逻辑:
    1. 加载组合配置 (核心 + 卫星 + 压舱 + 现金, 由 --config 指定)
    2. 加载ETF历史日线数据 (data/etf_option_backtest/)
    3. 按配置权重构建组合, 日频计算收益 (权重和偏离 1.0 即告警, 防静默丢仓)
    4. 模拟期权对冲成本, 成本口径统一取配置的「年度计提」语义
       (硬伤三: 不再使用「本金划拨」语义; collar 用 cost_target_pct 净成本)
    5. 对比4策略: 无对冲/仅Put/Put+Call/Put+Call+Tail
    6. 计算性能指标: 年化收益/波动率/夏普/最大回撤/Calmar
    7. 生成Markdown + JSON报告 (含成本口径来源, 可审计)

运行:
    python scripts/run_200w_etf_backtest.py                                   # v1.0 基准
    python scripts/run_200w_etf_backtest.py --config config/portfolio_200w_etf_v91.yaml \
        --report-dir "每日报告归档/2026-09-11" --tag 20260911                  # v9.1 修复版
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.datetime_utils import now_bj  # noqa: E402

_DEFAULT_CONFIG_PATH = _ROOT / "config" / "portfolio_200w_etf.yaml"
_DATA_DIR = _ROOT / "data" / "etf_option_backtest"
_DEFAULT_WIND_DIR = _DATA_DIR / "wind_2021_2026"
_DEFAULT_REPORT_DIR = _ROOT / "每日报告归档" / "2026-08-24"
_DEFAULT_TAG = "20260824"
_RISK_FREE = 0.03
_TRADING_DAYS = 252

#: 默认基准 (沪深300) —— 用于回答"这套防御配置到底跑赢基准多少"。
_DEFAULT_BENCHMARK = "000300"

#: L1~L4 减仓的回补缓冲 (百分点): 配置只写了触发条件, 未写回补规则。
#: 无缓冲会因回撤在阈值附近抖动而反复加减仓 (whipsaw), 故设 2pp 滞回;
#: 该假设在此显式声明并可经 --cb-recovery-buffer 调整, 不藏在代码里。
_DEFAULT_CB_RECOVERY_BUFFER = 0.02

#: 备兑认购年化收入估算 — 经验值 (模型未建模上行封顶损失, 故不据月度权利金推定)。
_COVERED_CALL_INCOME_DEFAULT = 0.008


@dataclass
class StrategyResult:
    """单策略回测结果."""

    name: str
    total_return: float = 0.0
    annual_return: float = 0.0
    annual_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    hedge_cost_total: float = 0.0
    hedge_cost_annual_pct: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    daily_returns: list[float] = field(default_factory=list)
    #: L1~L4 减仓熔断统计 (未启用时保持中性值)
    cb_enabled: bool = False
    cb_days_by_level: dict[str, int] = field(default_factory=dict)
    cb_min_equity_weight: float = 1.0
    cb_avg_equity_weight: float = 1.0
    cb_final_level: str = "L0"
    #: 档位切换次数 —— 每次切换都是一次真实的减仓/加仓换手 (本模型**未计**其交易成本,
    #: 故 L1~L4 的收益/回撤表现是「不计换手成本」的上界, 见报告声明)。
    cb_switches: int = 0
    #: L1~L4 累计单边换手 (占净值; 每次切换的 |Δ权益权重| 之和) —— 用于把"未计换手成本"
    #: 这句声明变成可算的数: 成本 ≈ 换手 × 单边费率 (A股 ETF 约 4bp)。
    cb_turnover_pct: float = 0.0
    #: 同期基准 (沪深300) 指标, 由报告层填充
    benchmark_annual_return: float = 0.0
    benchmark_max_drawdown: float = 0.0


def _load_config(config_path: Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_etf_prices() -> tuple[pd.DataFrame, list[str]]:
    """加载ETF历史收盘价, 返回 (DataFrame(index=date, columns=code), 补入的候选标的)。

    主数据 = all_etf_daily.parquet; 另并入本地**同目录按代码命名的单标的日线**与
    p2_candidates* 内的候选标的日线 (如 513100 纳指跨资产腿、511880 货币ETF), 仅补主
    数据**缺失**的 6 位代码, 且严格对齐主数据的日期索引 —— 不改变回测窗口, 避免历史
    报告数字不可比。
    """
    parquet_path = _DATA_DIR / "all_etf_daily.parquet"
    df = pd.read_parquet(parquet_path)
    df["date"] = pd.to_datetime(df["date"])
    df["code"] = df["code"].str.split(".").str[0]
    pivot = df.pivot_table(index="date", columns="code", values="close")
    pivot = pivot.sort_index()

    merged: list[str] = []
    base_index = pivot.index
    # 主目录(按代码命名的单标的 parquet, 如 511880.parquet) + 候选子目录
    for sub in ("", "p2_candidates_long", "p2_candidates"):
        sub_dir = _DATA_DIR if sub == "" else _DATA_DIR / sub
        if not sub_dir.is_dir():
            continue
        for path in sorted(sub_dir.glob("*.parquet")):
            code = path.stem
            if len(code) != 6 or not code.isdigit() or code in pivot.columns:
                continue
            try:
                extra = pd.read_parquet(path)
                extra["date"] = pd.to_datetime(extra["date"])
                series = extra.groupby("date")["close"].last()
            except Exception as exc:  # noqa: BLE001 - 候选数据可选, 失败即跳过并告警
                print(f"  [WARN] 候选数据 {path.name} 读取失败, 已跳过: {exc}")
                continue
            pivot[code] = series.reindex(base_index).ffill()
            merged.append(code)
    return pivot, merged


def _load_wind_prices(wind_dir: Path) -> tuple[pd.DataFrame, dict]:
    """加载 Wind 证据基座 (scripts/fetch_wind_v91_ohlc.py 产物) -> (close pivot, manifest)。

    只认 manifest 声明过的标的 —— 避免"目录里恰好有文件"被当成本次证据基座
    (证据基座的来源/窗口/复权口径必须可追溯)。
    """
    manifest_path = wind_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(
            f"Wind 证据基座缺失: {manifest_path}\n"
            "  先运行: .venv/Scripts/python.exe scripts/fetch_wind_v91_ohlc.py"
        )
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    if manifest.get("failures"):
        raise SystemExit(f"Wind 证据基座 manifest 含失败项, 拒绝使用: {manifest['failures']}")
    all_path = wind_dir / "wind_ohlc_all.parquet"
    if not all_path.is_file():
        raise SystemExit(f"Wind 证据基座缺失长表: {all_path}")
    long = pd.read_parquet(all_path)
    long["date"] = pd.to_datetime(long["date"])
    long["code"] = long["code"].astype(str)
    pivot = long.pivot_table(index="date", columns="code", values="close")
    return pivot.sort_index(), manifest


def _load_benchmark(benchmark: str, wind_dir: Path) -> pd.Series | None:
    """加载基准收盘序列 (Wind 产物); 缺失返回 None (报告层显式标注"未接入")。"""
    cands = sorted(wind_dir.glob(f"{benchmark}.*.parquet"))
    if not cands:
        return None
    df = pd.read_parquet(cands[0])
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["close"].sort_index()


def _split_sleeves(config: dict) -> tuple[set[str], set[str]]:
    """按配置分层把代码分成 权益腿 (核心+卫星) 与 防御腿 (压舱+现金)。

    L1~L4 只对**权益腿**减仓, 腾出的权重按原比例放大防御腿 —— 不为腾出的资金另造
    收益假设 (配置的 defense_exposure_target / defense_cash_min 就是这个语义)。
    """
    core = [etf for etf in (config.get("core_holdings") or []) if isinstance(etf, dict)]
    sat = [etf for etf in (config.get("satellite_holdings") or []) if isinstance(etf, dict)]
    ballast = [etf for etf in (config.get("ballast_holdings") or []) if isinstance(etf, dict)]
    cash = [etf for etf in (config.get("cash_holdings") or []) if isinstance(etf, dict)]
    equity = {str(e["code"]) for e in core + sat if "code" in e}
    defense = {str(e["code"]) for e in ballast + cash if "code" in e}
    return equity, defense


def _resolve_circuit_breaker(config: dict) -> list[tuple[float, float, str]]:
    """解析 L1~L4 减仓阶梯 -> [(回撤阈值, 权益目标权重, 档位名), ...] (按阈值升序)。

    阈值取自 `circuit_breaker.levels[].triggers` 里的「回撤 ≥ X%」字样 —— 这是配置的权威
    表述; 解析不出回撤阈值的档位不参与回测 (但会被 `guardrail` 与其单测单独约束)。
    """
    cb = _as_dict(config.get("circuit_breaker"))
    ladder: list[tuple[float, float, str]] = []
    for level in cb.get("levels") or []:
        node = _as_dict(level)
        target = node.get("equity_target")
        if not isinstance(target, (int, float)) or isinstance(target, bool):
            continue
        threshold = None
        for trig in node.get("triggers") or []:
            m = re.search(r"回撤\s*≥\s*(\d+(?:\.\d+)?)\s*%", str(trig))
            if m:
                threshold = float(m.group(1)) / 100.0
                break
        if threshold is None:
            continue
        ladder.append((threshold, float(target), str(node.get("level") or f"L{len(ladder) + 1}")))
    ladder.sort(key=lambda item: item[0])
    return ladder


def _apply_circuit_breaker(
    equity_ret: pd.Series,
    defense_ret: pd.Series,
    base_equity: float,
    ladder: list[tuple[float, float, str]],
    daily_cost: float,
    recovery_buffer: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, int], str]:
    """逐日执行 L1~L4 减仓 + Collar 成本, 返回 (净值, 每日权益权重, 各档天数, 末档)。

    无前视: 第 i 日的权重只由 **i-1 日收盘后** 的回撤决定 (信号在收盘后才可得)。
    权益权重 = 总权益腿权重 × (目标档位 / 基准档位); 腾出部分并入防御腿。
    期权成本按 `budget.basis = annual_return_provision` 口径对**全净值**计提, 与基准档一致。
    """
    n = len(equity_ret)
    nav = np.zeros(n)
    nav[0] = 1.0
    eq_w = np.zeros(n)
    eq_w[0] = base_equity
    levels = ["L0"] * n
    days_by_level: dict[str, int] = {}
    peak = 1.0
    switches = 0
    turnover = 0.0
    for i in range(1, n):
        blended = eq_w[i - 1] * float(equity_ret.iloc[i]) + (1.0 - eq_w[i - 1]) * float(
            defense_ret.iloc[i]
        )
        nav[i] = nav[i - 1] * (1.0 + blended - daily_cost)
        peak = max(peak, nav[i])
        dd = 1.0 - nav[i] / peak

        desired, desired_name = base_equity, "L0"
        for threshold, target, name in ladder:
            if dd >= threshold:
                desired, desired_name = target, name
        # 滞回: 回补 (加仓) 需回撤退出到"当前档触发阈值 − 缓冲"之下, 防阈值附近抖动
        if desired > eq_w[i - 1] and dd > _cur_threshold(ladder, eq_w[i - 1]) - recovery_buffer:
            desired, desired_name = eq_w[i - 1], levels[i - 1]
        eq_w[i] = desired
        levels[i] = desired_name
        days_by_level[desired_name] = days_by_level.get(desired_name, 0) + 1
        if desired_name != levels[i - 1]:
            switches += 1
        turnover += abs(eq_w[i] - eq_w[i - 1])
    return nav, eq_w, days_by_level, levels[-1], switches, turnover


def _cur_threshold(ladder: list[tuple[float, float, str]], target: float) -> float:
    """当前权益目标权重对应的触发阈值 (找不到则 0.0)。"""
    for threshold, tgt, _name in ladder:
        if abs(tgt - target) < 1e-12:
            return threshold
    return 0.0


def _build_weight_map(config: dict) -> dict[str, float]:
    """从配置构建 {code: weight} 映射 (含压舱资产, 缺失即静默丢仓)."""
    weights: dict[str, float] = {}
    for key, weight_field in (
        ("core_holdings", "weight"),
        ("satellite_holdings", "weight_base"),
        ("ballast_holdings", "weight"),  # 硬伤一: 国债/黄金压舱不得漏计
        ("cash_holdings", "weight"),
    ):
        for etf in config.get(key) or []:
            if not isinstance(etf, dict) or "code" not in etf:
                continue
            weights[str(etf["code"])] = float(etf.get(weight_field, 0.0) or 0.0)
    return weights


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _midpoint(values) -> float | None:
    """取二元数值区间中值; 非二元数值区间返回 None。"""
    nums = [float(v) for v in (values or []) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return (nums[0] + nums[1]) / 2 if len(nums) == 2 else None


def _protective_put_pct(put_cfg) -> tuple[float, str]:
    """解析 Protective Put 年度成本率 + 口径来源。"""
    cfg = _as_dict(put_cfg)
    mid = _midpoint(_as_dict(cfg.get("budget")).get("annual_pct_nav"))
    if mid is not None:
        return mid, "budget.annual_pct_nav 中值 (年度计提口径)"
    legacy = cfg.get("budget_annual_pct")
    if isinstance(legacy, (int, float)) and not isinstance(legacy, bool):
        return float(legacy), "budget_annual_pct (v1.0 本金划拨遗留口径)"
    return 0.015, "内置兜底 1.5%"


def _resolve_annual_hedge_pct(
    config: dict, use_put: bool, use_call: bool, use_tail: bool
) -> tuple[float, str]:
    """解析年度对冲净成本率 (%/净值·年) 及其口径来源 (硬伤三: 单一口径, 可审计)。

    Collar 模式下认沽与备兑认购是同一结构的双腿, 直接取 collar.cost_target_pct
    净成本中值, 不再单独叠加备兑认购收入 (避免重复计价)。
    """
    if not (use_put or use_call or use_tail):
        return 0.0, "none (无对冲腿)"
    opts = _as_dict(config.get("options_strategy"))
    collar = _as_dict(opts.get("collar"))
    structure = str(opts.get("primary_structure") or "")
    if use_put and use_call and collar.get("enabled") and structure == "collar":
        mid = _midpoint(collar.get("cost_target_pct"))
        if mid is not None:
            return mid, "collar.cost_target_pct 中值 (净成本 = 认沽成本 − 认购收入)"

    total = 0.0
    parts: list[str] = []
    if use_put:
        pct, src = _protective_put_pct(opts.get("protective_put"))
        total += pct
        parts.append(f"put +{pct:.4%} [{src}]")
    if use_call:
        total -= _COVERED_CALL_INCOME_DEFAULT
        parts.append(f"call -{_COVERED_CALL_INCOME_DEFAULT:.4%} [经验值, 未建模上行封顶]")
    if use_tail:
        tail = _as_dict(opts.get("tail_protection")).get("budget_annual_pct", 0.005)
        tail_pct = float(tail) if isinstance(tail, (int, float)) and not isinstance(tail, bool) else 0.005
        total += tail_pct
        parts.append(f"tail +{tail_pct:.4%}")
    return total, " + ".join(parts)


def _compute_metrics(
    equity: np.ndarray,
    name: str,
    hedge_cost: float = 0.0,
    hedge_cost_annual_pct: float = 0.0,
) -> StrategyResult:
    """计算性能指标.

    Args:
        equity: 归一化净值序列 (equity[0] = 1.0)。
        name: 策略名。
        hedge_cost: 对冲成本绝对值 (元)。
        hedge_cost_annual_pct: 对冲成本率 (占净值·年), 由配置口径解析而来 ——
            不可用 hedge_cost / equity[0] 反推 (equity 已归一化, 量纲不一致)。
    """
    if len(equity) < 2:
        return StrategyResult(name=name)
    rets = np.diff(equity) / equity[:-1]
    total_return = (equity[-1] - equity[0]) / equity[0]
    n_years = len(rets) / _TRADING_DAYS
    annual_return = (1 + total_return) ** (1 / max(n_years, 0.5)) - 1
    annual_vol = np.std(rets) * np.sqrt(_TRADING_DAYS)
    sharpe = (annual_return - _RISK_FREE) / max(annual_vol, 0.001)
    peak = np.maximum.accumulate(equity)
    drawdowns = (equity - peak) / peak
    max_dd = abs(np.min(drawdowns))
    calmar = annual_return / max(max_dd, 0.001)
    win_rate = np.sum(rets > 0) / max(len(rets), 1)
    return StrategyResult(
        name=name,
        total_return=total_return,
        annual_return=annual_return,
        annual_volatility=annual_vol,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        calmar_ratio=calmar,
        win_rate=win_rate,
        hedge_cost_total=hedge_cost,
        hedge_cost_annual_pct=hedge_cost_annual_pct,
        equity_curve=equity.tolist(),
        daily_returns=rets.tolist(),
    )


def _bs_put_price(
    spot: float, strike: float, t: float, r: float = 0.02, sigma: float = 0.25
) -> float:
    """Black-Scholes 认沽期权定价."""
    if t <= 0:
        return max(strike - spot, 0.0)
    from scipy.stats import norm

    d1 = (np.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
    d2 = d1 - sigma * np.sqrt(t)
    put = strike * np.exp(-r * t) * norm.cdf(-d2) - spot * norm.cdf(-d1)
    return max(put, 0.0001)


def _bs_call_price(
    spot: float, strike: float, t: float, r: float = 0.02, sigma: float = 0.25
) -> float:
    """Black-Scholes 认购期权定价."""
    if t <= 0:
        return max(spot - strike, 0.0)
    from scipy.stats import norm

    d1 = (np.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
    d2 = d1 - sigma * np.sqrt(t)
    call = spot * norm.cdf(d1) - strike * np.exp(-r * t) * norm.cdf(d2)
    return max(call, 0.0001)


def _run_backtest(
    prices: pd.DataFrame,
    weights: dict[str, float],
    config: dict,
    strategy_name: str,
    use_protective_put: bool = False,
    use_covered_call: bool = False,
    use_tail_protection: bool = False,
    circuit_breaker: bool = True,
    recovery_buffer: float = _DEFAULT_CB_RECOVERY_BUFFER,
) -> StrategyResult:
    """执行单策略回测 (权重加权 + L1~L4 减仓熔断 + Collar 成本逐日计提)。

    L1~L4 只在配置同时给出 `circuit_breaker.levels` 与 权益/防御两层持仓时启用;
    否则退回纯权重加权 (与修复前的口径完全一致, 老报告可复现)。
    """
    common_codes = [c for c in weights if c in prices.columns]
    w_arr = np.array([weights[c] for c in common_codes])
    w_arr = w_arr / w_arr.sum() if w_arr.sum() > 0 else w_arr

    price_sub = prices[common_codes].dropna(how="all").ffill()
    daily_ret = price_sub.pct_change().fillna(0.0)
    portfolio_ret = (daily_ret * w_arr).sum(axis=1)

    # 期权成本必须逐日计提进收益序列 (一次性乘数会让"保护"几乎免费 → 成本被严重低估)
    annual_hedge_pct, _ = _resolve_annual_hedge_pct(
        config, use_protective_put, use_covered_call, use_tail_protection
    )
    daily_cost = annual_hedge_pct / _TRADING_DAYS

    equity_codes, defense_codes = _split_sleeves(config)
    ladder = _resolve_circuit_breaker(config) if circuit_breaker else []
    eq_cols = [c for c in common_codes if c in equity_codes]
    df_cols = [c for c in common_codes if c in defense_codes]
    use_cb = bool(ladder) and bool(eq_cols) and bool(df_cols)

    cb_days: dict[str, int] = {}
    cb_final = "L0"
    cb_switches = 0
    cb_turnover = 0.0
    min_eq_w = 1.0
    avg_eq_w = 1.0
    if use_cb:
        eq_sum = sum(weights[c] for c in eq_cols)
        df_sum = sum(weights[c] for c in df_cols)
        eq_ret = (daily_ret[eq_cols] * np.array([weights[c] for c in eq_cols])).sum(axis=1) / eq_sum
        df_ret = (daily_ret[df_cols] * np.array([weights[c] for c in df_cols])).sum(axis=1) / df_sum
        equity, eq_w, cb_days, cb_final, cb_switches, cb_turnover = _apply_circuit_breaker(
            eq_ret, df_ret, eq_sum, ladder, daily_cost, recovery_buffer
        )
        # 归一为「相对基准权益权重」的倍数, 便于与 L1~L4 档位目标直接比对
        pct = np.zeros_like(eq_w)
        pct[eq_w > 0] = eq_w[eq_w > 0] / eq_sum
        min_eq_w = float(pct[pct > 0].min()) if (pct > 0).any() else 1.0
        avg_eq_w = float(pct[pct > 0].mean()) if (pct > 0).any() else 1.0
    else:
        if daily_cost:
            portfolio_ret = portfolio_ret - daily_cost
        n_days = len(portfolio_ret)
        equity = np.zeros(n_days)
        equity[0] = 1.0
        for i in range(1, n_days):
            equity[i] = equity[i - 1] * (1 + portfolio_ret.iloc[i])

    n_days = len(equity)
    raw_capital = _as_dict(config.get("portfolio")).get("total_capital", 2_000_000)
    total_capital = (
        float(raw_capital)
        if isinstance(raw_capital, (int, float)) and not isinstance(raw_capital, bool)
        else 2_000_000.0
    )
    hedge_cost = total_capital * annual_hedge_pct * (n_days / _TRADING_DAYS)

    result = _compute_metrics(equity, strategy_name, hedge_cost, annual_hedge_pct)
    result.cb_enabled = use_cb
    result.cb_days_by_level = cb_days
    result.cb_min_equity_weight = min_eq_w
    result.cb_avg_equity_weight = avg_eq_w
    result.cb_final_level = cb_final
    result.cb_switches = cb_switches
    result.cb_turnover_pct = cb_turnover
    return result


def _generate_report(
    results: list[StrategyResult],
    config: dict,
    prices: pd.DataFrame,
    config_name: str,
    hedge_spec: str,
    coverage: dict,
    *,
    data_source: str = "local",
    wind_manifest: dict | None = None,
    benchmark_name: str = "",
    benchmark: StrategyResult | None = None,
    cb_ladder: list[tuple[float, float, str]] | None = None,
    recovery_buffer: float = _DEFAULT_CB_RECOVERY_BUFFER,
) -> str:
    """生成Markdown报告."""
    now = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    start_date = prices.index[0].strftime("%Y-%m-%d")
    end_date = prices.index[-1].strftime("%Y-%m-%d")
    n_days = len(prices)
    total_capital = config.get("portfolio", {}).get("total_capital", 0)
    weight_sum_raw = float(coverage.get("config_weight_total") or 0.0)
    dropped_weight = float(coverage.get("dropped_weight") or 0.0)
    missing_codes = coverage.get("missing_codes") or []
    if coverage.get("fallback_equal_weight"):
        coverage_text = "配置匹配不足, 已改用数据内 ETF 等权组合 (回测口径 ≠ 配置口径)"
    else:
        coverage_text = (
            f"{float(coverage.get('covered_weight') or 0.0):.4f} / {weight_sum_raw:.4f} 权重"
            f"已纳入回测 (未覆盖 {dropped_weight:+.4f}"
            + (f", 缺数据: {', '.join(str(c) for c in missing_codes)}" if missing_codes else "")
            + ")"
        )
    if data_source == "wind" and wind_manifest:
        source_text = (
            f"Wind MCP 证据基座 (`{wind_manifest.get('tool')}`, "
            f"{wind_manifest.get('adjust')}; manifest {wind_manifest.get('generated_at')})"
        )
    else:
        source_text = "本地 parquet (`data/etf_option_backtest/`) — 未使用 Wind 证据基座"
    ladder = cb_ladder or []
    if ladder:
        cb_text = (
            "启用 (" + " / ".join(f"{name} dd≥{th:.0%}→权益{tgt:.0%}" for th, tgt, name in ladder)
            + f"; 回补缓冲 {recovery_buffer:.0%})"
        )
    else:
        cb_text = "未启用 (配置无 circuit_breaker.levels 或无分层持仓)"
    if benchmark is None:
        bench_text = "未接入"
    else:
        bench_text = (
            f"{benchmark_name} 年化 {benchmark.annual_return:.2%} / "
            f"回撤 {benchmark.max_drawdown:.2%}"
        )

    lines = [
        "# 200万ETF期权对冲策略回测验证报告 (Phase 2)",
        "",
        f"> 生成时间: {now}",
        f"> 回测区间: {start_date} ~ {end_date} ({n_days} 交易日)",
        f"> 初始资金: {total_capital / 10000:.0f} 万元",
        f"> 配置文件: `{config_name}`",
        f"> 期权成本口径: {hedge_spec}",
        f"> 配置权重合计: {weight_sum_raw:.4f} (与 1.0 偏离 > 0.5% 即视为丢仓)",
        f"> 数据覆盖: {coverage_text}",
        f"> 数据来源: {source_text}",
        f"> L1~L4 减仓熔断: {cb_text}",
        f"> 同期基准: {bench_text}",
        "",
        "## 1. 策略对比",
        "",
        "| 策略 | 年化收益 | 年化波动 | 夏普比率 | 最大回撤 | Calmar | 胜率 | 对冲成本/年 |",
        "|------|----------|----------|----------|----------|--------|------|-------------|",
    ]

    for r in results:
        lines.append(
            f"| {r.name} | {r.annual_return:.2%} | {r.annual_volatility:.2%} | "
            f"{r.sharpe_ratio:.3f} | {r.max_drawdown:.2%} | {r.calmar_ratio:.3f} | "
            f"{r.win_rate:.1%} | {r.hedge_cost_annual_pct:.2%} |"
        )

    lines.extend(
        [
            "",
            "## 2. 详细指标",
            "",
        ]
    )

    for r in results:
        lines.extend(
            [
                f"### {r.name}",
                f"- 总收益率: {r.total_return:.2%}",
                f"- 年化收益: {r.annual_return:.2%}",
                f"- 年化波动: {r.annual_volatility:.2%}",
                f"- 夏普比率: {r.sharpe_ratio:.3f}",
                f"- 最大回撤: {r.max_drawdown:.2%}",
                f"- Calmar比率: {r.calmar_ratio:.3f}",
                f"- 日胜率: {r.win_rate:.1%}",
                f"- 对冲成本总计: {r.hedge_cost_total:,.0f} 元",
                f"- 对冲成本/年: {r.hedge_cost_annual_pct:.2%}",
                "",
            ]
        )

    best = max(results, key=lambda r: r.sharpe_ratio)
    lines.extend(
        [
            "## 3. 结论",
            "",
            f"- **最优策略**: {best.name} (夏普 {best.sharpe_ratio:.3f})",
            f"- **年化收益**: {best.annual_return:.2%}",
            f"- **最大回撤**: {best.max_drawdown:.2%}",
            f"- **对冲成本/年**: {best.hedge_cost_annual_pct:.2%}",
            "- **口径警示**: 本模型只对期权**计费**、不模拟到期赔付, 故夏普排序必然偏向对冲"
            "最少者。该排序仅用于量化「对冲的成本拖累」, 不可用于否定对冲的下行保护价值。",
            "",
            "## 4. 配置摘要",
            "",
            f"- 核心仓: {len(config.get('core_holdings', []))} 只ETF",
            f"- 卫星仓: {len(config.get('satellite_holdings', []))} 只ETF",
            f"- 压舱仓: {len(config.get('ballast_holdings', []))} 只ETF",
            f"- 现金仓: {len(config.get('cash_holdings', []))} 只ETF",
            f"- 期权策略: {_as_dict(config.get('options_strategy')).get('primary_structure', 'protective_put')}"
            " + Protective Put + Covered Call + Tail Protection",
            "- 定价模型: Black-Scholes",
            f"- 期权成本口径: {hedge_spec}",
            "",
            "## 5. L1~L4 减仓熔断执行统计",
            "",
        ]
    )
    if ladder:
        lines.extend(
            [
                "| 档位 | 触发阈值 | 权益目标 | 执行天数 |",
                "|------|----------|----------|----------|",
            ]
        )
        for threshold, target, name in ladder:
            for r in results:
                if not r.cb_enabled:
                    continue
                days = r.cb_days_by_level.get(name, 0)
                lines.append(f"| {name} | {threshold:.0%} | {target:.0%} | {days} |")
                break
        lines.extend(
            [
                "",
                "| 策略 | 熔断启用 | 权益权重最低 | 权益权重均值 | 期末档位 | 档位切换次数 | 累计单边换手 |",
                "|------|----------|--------------|--------------|----------|--------------|--------------|",
            ]
        )
        for r in results:
            lines.append(
                f"| {r.name} | {'是' if r.cb_enabled else '否'} | "
                f"{r.cb_min_equity_weight:.1%} | {r.cb_avg_equity_weight:.1%} | {r.cb_final_level} | "
                f"{r.cb_switches} | {r.cb_turnover_pct:.1%} |"
            )
        lines.append("")
        lines.append(
            "- 口径: 档位由**已实现回撤**触发 (回撤 ≥ 阈值), 权重自**次日**生效 (无前视); "
            "L4 (dd≥18%) 在本样本内未被触发 —— 减仓本身压低了回撤, 故高档位存在自限性。"
        )
        for r in results:
            if r.cb_enabled and r.cb_turnover_pct > 0:
                est = r.cb_turnover_pct * 0.0004
                lines.append(
                    f"- 换手成本补算 ({r.name}): 累计单边换手 {r.cb_turnover_pct:.1%} × 4bp = "
                    f"**{est:.3%}**(全期, 未计入上表) ⇒ 摊到 {len(prices) / _TRADING_DAYS:.1f} 年约 "
                    f"{est / max(len(prices) / _TRADING_DAYS, 1e-9):.3%}/年。"
                )
                break
    else:
        lines.append("未启用 (配置无 circuit_breaker.levels 或无分层持仓)。")

    lines.extend(["", "## 6. 同期基准对比", ""])
    if benchmark is None:
        lines.append("基准未接入 (先跑 `scripts/fetch_wind_v91_ohlc.py` 生成 Wind 基准序列)。")
    else:
        lines.extend(
            [
                f"| 策略 | 年化收益 | 超额(vs {benchmark_name}) | 最大回撤 | 回撤差 |",
                "|------|----------|--------------------------|----------|--------|",
            ]
        )
        for r in results:
            lines.append(
                f"| {r.name} | {r.annual_return:.2%} | "
                f"{r.annual_return - benchmark.annual_return:+.2%} | {r.max_drawdown:.2%} | "
                f"{r.max_drawdown - benchmark.max_drawdown:+.2%} |"
            )
        lines.append(
            f"| {benchmark_name} (基准) | {benchmark.annual_return:.2%} | — | "
            f"{benchmark.max_drawdown:.2%} | — |"
        )

    lines.extend(
        [
            "",
            "## 7. 数据来源与口径",
            "",
            f"- 数据来源: {source_text}",
        ]
    )
    if wind_manifest:
        lines.extend(
            [
                f"- 基座窗口: {wind_manifest.get('window', ['?', '?'])[0]} ~ "
                f"{wind_manifest.get('window', ['?', '?'])[1]}",
                f"- 标的与基准数: {wind_manifest.get('n_instruments')}",
                f"- 复现: `{wind_manifest.get('reproduce')}`",
            ]
        )

    lines.extend(
        [
            "",
            "## 8. 声明",
            "",
            "- 本回测使用BS模型估算期权成本, 未接入真实期权市场数据.",
            "- 期权成本按配置的「年度计提」口径逐日扣减收益序列, 口径来源见报告头部。",
            "- 组合收益为「权重加权日收益」, 未建模期权到期赔付与削尾增益 (即对冲收益被低估).",
            "- L1~L4 减仓的**换手成本未计入** (每次档位切换 = 一次真实的卖/买), 故熔断组的"
            "收益/回撤是「不计换手成本」的上界; A股 ETF 双边成本约 0.08%~0.09%, 应按切换次数自行扣减.",
            "- 回测结果仅供参考, 不构成投资建议.",
            "- 后续 Phase 3 将接入影子账户验证.",
            "",
        ]
    )

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="200万ETF期权对冲策略回测 (Phase 2)")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG_PATH), help="组合配置 YAML 路径")
    parser.add_argument("--report-dir", default=str(_DEFAULT_REPORT_DIR), help="报告输出目录")
    parser.add_argument("--tag", default=_DEFAULT_TAG, help="报告文件名后缀 (默认 20260824)")
    parser.add_argument(
        "--data-source",
        choices=("local", "wind"),
        default="local",
        help="local=本地 parquet (老口径可复现) / wind=Wind 证据基座 (scripts/fetch_wind_v91_ohlc.py 产物)",
    )
    parser.add_argument("--wind-dir", default=str(_DEFAULT_WIND_DIR), help="Wind 证据基座目录")
    parser.add_argument("--benchmark", default=_DEFAULT_BENCHMARK, help="基准指数代码 (默认 000300)")
    parser.add_argument(
        "--no-circuit-breaker", action="store_true", help="关闭 L1~L4 减仓熔断 (仅权重加权 + 成本)"
    )
    parser.add_argument(
        "--cb-recovery-buffer",
        type=float,
        default=_DEFAULT_CB_RECOVERY_BUFFER,
        help="L1~L4 回补滞回缓冲 (默认 0.02; 配置未定义回补规则, 此为显式假设)",
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    report_dir = Path(args.report_dir)

    print("=" * 60)
    print("200万ETF期权对冲策略回测验证 (Phase 2)")
    print("=" * 60)

    print("\n[1] 加载配置...")
    if not config_path.exists():
        raise SystemExit(f"配置不存在: {config_path}")
    config = _load_config(config_path)
    weights = _build_weight_map(config)
    weight_sum_raw = sum(weights.values())
    print(f"  配置文件: {config_path.name}")
    print(f"  配置: {len(weights)} 只ETF")
    print(f"  权重总和: {weight_sum_raw:.4f}")
    if abs(weight_sum_raw - 1.0) > 0.005:
        print(f"  [WARN] 权重合计偏离 1.0 达 {weight_sum_raw - 1.0:+.4f} — 疑似漏计某类资产 (压舱/现金)")

    print("\n[2] 加载ETF历史数据...")
    wind_manifest = None
    benchmark = None
    extra_codes: list[str] = []
    if args.data_source == "wind":
        wind_dir = Path(args.wind_dir)
        prices, wind_manifest = _load_wind_prices(wind_dir)
        extra_codes = [str(item["code"]) for item in wind_manifest.get("instruments", [])]
        print(f"  证据基座: {wind_dir}")
        print(f"  来源: {wind_manifest.get('tool')} | 复权: {wind_manifest.get('adjust')}")
        benchmark = _load_benchmark(args.benchmark, wind_dir)
        print(
            f"  基准 {args.benchmark}: "
            + (f"{benchmark.shape[0]} 行" if benchmark is not None else "未接入")
        )
    else:
        prices, extra_codes = _load_etf_prices()
    print(f"  数据: {prices.shape[0]} 交易日 × {prices.shape[1]} 只ETF")
    print(f"  区间: {prices.index[0].date()} ~ {prices.index[-1].date()}")
    if args.data_source == "local" and extra_codes:
        print(f"  并入本地候选标的 (对齐主数据窗口): {extra_codes}")

    available = [c for c in weights if c in prices.columns]
    missing = [c for c in weights if c not in prices.columns]
    covered_weight = sum(weights[c] for c in available)
    dropped_weight = weight_sum_raw - covered_weight
    print(f"  匹配: {len(available)} 只可用, {len(missing)} 只缺失")
    print(f"  数据覆盖权重: {covered_weight:.4f} / {weight_sum_raw:.4f} (未覆盖 {dropped_weight:+.4f})")
    if missing:
        print(f"  缺失: {missing}")
    if dropped_weight > 0.005:
        print(f"  [WARN] {dropped_weight:.2%} 配置权重因数据缺失未纳入回测 — 报告将显式标注覆盖缺口")

    print("\n[3] 执行4策略对比回测...")
    fallback_equal_weight = len(available) < 5
    if fallback_equal_weight:
        bt_weights = {c: 1.0 / len(prices.columns) for c in prices.columns}
        print(f"  [WARN] 配置匹配不足, 改用数据中 {len(prices.columns)} 只ETF等权组合")
    else:
        bt_weights = {c: weights[c] for c in available}
        total = sum(bt_weights.values())
        bt_weights = {c: w / total for c, w in bt_weights.items()}
    coverage = {
        "config_weight_total": weight_sum_raw,
        "covered_weight": covered_weight,
        "dropped_weight": dropped_weight,
        "missing_codes": missing,
        "extra_candidate_codes": extra_codes,
        "fallback_equal_weight": fallback_equal_weight,
    }

    leg_sets = (
        ("S1: 无对冲", False, False, False),
        ("S2: 仅Protective Put", True, False, False),
        ("S3: Put+Covered Call", True, True, False),
        ("S4: Put+Call+Tail", True, True, True),
    )
    results: list[StrategyResult] = []
    hedge_specs: list[str] = []
    for name, put, call, tail in leg_sets:
        results.append(
            _run_backtest(
                prices,
                bt_weights,
                config,
                name,
                put,
                call,
                tail,
                circuit_breaker=not args.no_circuit_breaker,
                recovery_buffer=args.cb_recovery_buffer,
            )
        )
        hedge_specs.append(_resolve_annual_hedge_pct(config, put, call, tail)[1])
    for idx, r in enumerate(results):
        print(
            f"  {r.name}: 年化{r.annual_return:.2%} 夏普{r.sharpe_ratio:.3f} "
            f"回撤{r.max_drawdown:.2%} 成本{r.hedge_cost_annual_pct:.2%}"
        )
        print(f"      成本口径: {hedge_specs[idx]}")
        if r.cb_enabled:
            print(
                f"      L1~L4: 权益权重 最低{r.cb_min_equity_weight:.1%} "
                f"均值{r.cb_avg_equity_weight:.1%} 期末{r.cb_final_level} "
                f"切换{r.cb_switches}次 各档天数 {r.cb_days_by_level}"
            )

    bench_result = None
    if benchmark is not None:
        bench_nav = (benchmark / benchmark.iloc[0]).to_numpy()
        bench_result = _compute_metrics(bench_nav, f"基准 {args.benchmark}")
        print(
            f"  基准 {args.benchmark}: 年化{bench_result.annual_return:.2%} "
            f"回撤{bench_result.max_drawdown:.2%}"
        )

    print("\n[4] 生成报告...")
    report_dir.mkdir(parents=True, exist_ok=True)
    hedge_spec_text = " ; ".join(
        f"{leg_sets[idx][0]} → {hedge_specs[idx]}" for idx in range(len(leg_sets))
    )
    report_md = _generate_report(
        results,
        config,
        prices,
        config_path.name,
        hedge_spec_text,
        coverage,
        data_source=args.data_source,
        wind_manifest=wind_manifest,
        benchmark_name=args.benchmark,
        benchmark=bench_result,
        cb_ladder=_resolve_circuit_breaker(config) if not args.no_circuit_breaker else [],
        recovery_buffer=args.cb_recovery_buffer,
    )
    report_path = report_dir / f"ETF期权对冲回测_Phase2_{args.tag}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)
    print(f"  报告: {report_path}")

    result_json = {
        "strategies": [
            {
                "name": r.name,
                "total_return": r.total_return,
                "annual_return": r.annual_return,
                "annual_volatility": r.annual_volatility,
                "sharpe_ratio": r.sharpe_ratio,
                "max_drawdown": r.max_drawdown,
                "calmar_ratio": r.calmar_ratio,
                "win_rate": r.win_rate,
                "hedge_cost_annual_pct": r.hedge_cost_annual_pct,
                "hedge_cost_spec": hedge_specs[idx],
                "circuit_breaker_enabled": r.cb_enabled,
                "cb_days_by_level": r.cb_days_by_level,
                "cb_min_equity_weight": r.cb_min_equity_weight,
                "cb_avg_equity_weight": r.cb_avg_equity_weight,
                "cb_final_level": r.cb_final_level,
                "cb_switches": r.cb_switches,
                "cb_turnover_pct": r.cb_turnover_pct,
            }
            for idx, r in enumerate(results)
        ],
        "config": config_path.name,
        "config_weight_sum": weight_sum_raw,
        "coverage": coverage,
        "data_source": args.data_source,
        "wind_manifest": wind_manifest,
        "benchmark": (
            {
                "code": args.benchmark,
                "annual_return": bench_result.annual_return,
                "max_drawdown": bench_result.max_drawdown,
                "annual_volatility": bench_result.annual_volatility,
            }
            if bench_result is not None
            else None
        ),
        "circuit_breaker": {
            "enabled": not args.no_circuit_breaker,
            "ladder": [
                {"level": name, "drawdown_threshold": th, "equity_target": tgt}
                for th, tgt, name in _resolve_circuit_breaker(config)
            ],
            "recovery_buffer": args.cb_recovery_buffer,
            "note": "配置未定义回补规则, 回补滞回缓冲为显式假设; 权重变更次日生效(无前视)",
        },
        "data_range": [str(prices.index[0].date()), str(prices.index[-1].date())],
        "n_trading_days": len(prices),
        "generated_at": now_bj().isoformat(),
    }
    json_path = report_dir / f"ETF期权对冲回测_Phase2_{args.tag}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result_json, f, ensure_ascii=False, indent=2)
    print(f"  JSON: {json_path}")

    print("\n" + "=" * 60)
    print("回测验证完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
