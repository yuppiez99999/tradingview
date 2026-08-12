"""GNN 供应链因子 — Layer 1 Gate 1 门禁验证

从真实数据源拉取持仓 + 供应链邻居的历史价格, 构建关系图, 计算 Lead-Lag 因子,
并执行 Gate 1 门禁验证: 同一因子同时满足 effIC≥0.01 且 effICIR>0.3 且 多空夏普>1.0
(反向因子按 ICIR 符号方向修正后评估, eff_icir=|icir|, 不被误杀)。

数据源 (已验证可用):
- 腾讯历史 K 线 (web.ifzq.gtimg.cn) — 历史价格 (前复权日线)
- 东财 push2 slist/get spt=3 (graph_data_source) — 行业/概念/题材边

用法:
    python -m utils.alpha_factor.gate1_validation
    python -m utils.alpha_factor.gate1_validation --days 250 --min-neighbors 1
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# 路径处理 (兼容直接运行 / -m 运行)
_DIR = Path(__file__).resolve().parent  # utils/alpha_factor
_UTILS = _DIR.parent  # utils
_PROJ = _UTILS.parent  # 28-终极量化交易系统8.4
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

from utils.supply_chain_builder import SupplyChainBuilder  # noqa: E402
from utils.alpha_factor.graph import (  # noqa: E402
    compute_lead_lag_factors,
    orthogonalize_chain_factors,
)
from utils.alpha_factor.base import (  # noqa: E402
    FactorValue,
    winsorize,
    standardize,
)

# Windows 控制台 UTF-8 输出 (幂等)
for name in ("stdout", "stderr"):
    stream = getattr(sys, name, None)
    if stream is not None and getattr(stream, "encoding", "").lower() != "utf-8":
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            try:
                setattr(sys, name, io.TextIOWrapper(buffer, encoding="utf-8", errors="replace"))
            except (ValueError, TypeError, KeyError, AttributeError, OSError):
                pass

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA}
_TX_INTERVAL = 0.3  # 腾讯接口请求间隔 (防限流)


def to_tx_code(code: str) -> str:
    """6 位代码 → 腾讯代码 (sh/sz 前缀)."""
    c = str(code).strip().split(".")[0]
    if c.startswith(("6", "5", "9")):
        return f"sh{c}"
    return f"sz{c}"


def fetch_tx_kline(code: str, days: int = 250) -> Optional[List[List[str]]]:
    """腾讯历史 K 线 (前复权日线).

    Returns:
        [[date, open, close, high, low, volume, ...], ...] 或 None
    """
    tx_code = to_tx_code(code)
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {
        "param": f"{tx_code},day,,,{days},qfq",
        "_var": "kline_dayqfq",
    }
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        r.encoding = "utf-8"
        txt = r.text
        js = txt.split("=", 1)[1].strip() if "=" in txt else txt
        d = json.loads(js)
        data = d.get("data") or {}
        for _key, val in data.items():
            kline = val.get("qfqday") or val.get("day") or []
            if kline:
                return kline
        return None
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as exc:
        logger.warning(f"腾讯K线失败 ({code}): {exc}")
        return None


# 价格缓存文件 (避免每次验证重复拉取 247 只腾讯K线)
_PRICE_CACHE_FILE = _PROJ / "cache" / "gate1_price.json"
_PRICE_CACHE_TTL = 3600  # 1h


def fetch_prices(symbols: List[str], days: int = 250, use_cache: bool = True) -> Dict[str, dict[str, List[float]]]:
    """批量拉取腾讯 K 线, 转为因子库 price_data 格式 (带本地缓存).

    Returns:
        {sym: {"closes": [...], "volumes": [...], "highs": [...], "lows": [...], "dates": [...]}}
    """
    cache = {}
    if use_cache and _PRICE_CACHE_FILE.exists():
        if time.time() - _PRICE_CACHE_FILE.stat().st_mtime < _PRICE_CACHE_TTL:
            try:
                cache = json.loads(_PRICE_CACHE_FILE.read_text(encoding="utf-8"))
            except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
                cache = {}

    price_data: Dict[str, dict[str, List[float]]] = {}
    missing: List[str] = []
    for sym in symbols:
        if sym in cache and cache[sym]:
            price_data[sym] = cache[sym]
        else:
            missing.append(sym)

    for idx, sym in enumerate(missing):
        klines = fetch_tx_kline(sym, days)
        if not klines:
            logger.warning(f"跳过 {sym}: 无 K 线数据")
            continue
        closes = [float(k[2]) for k in klines]   # close
        opens = [float(k[1]) for k in klines]    # open
        highs = [float(k[3]) for k in klines]    # high
        lows = [float(k[4]) for k in klines]     # low
        volumes = [float(k[5]) for k in klines]  # volume
        dates = [k[0] for k in klines]
        price_data[sym] = {
            "closes": closes,
            "volumes": volumes,
            "highs": highs,
            "lows": lows,
            "opens": opens,
            "dates": dates,
        }
        if (idx + 1) % 10 == 0:
            logger.info(f"已拉取 {idx + 1}/{len(missing)} 只(新增)")
        time.sleep(_TX_INTERVAL)

    # 合并缓存并回写
    cache.update(price_data)
    if use_cache:
        try:
            _PRICE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PRICE_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as exc:
            logger.debug(f"价格缓存写入失败: {exc}")
    return price_data


def load_universe() -> List[str]:
    """构建 universe: positions 持仓 + 供应链 2 阶邻居 (基准版本)."""
    from utils.supply_chain_builder import load_positions_symbols
    core = load_positions_symbols()
    builder = SupplyChainBuilder(symbols=core, include_themes=True, max_hops=2)
    builder.build()
    return list(builder.graph.all_nodes)


# 核心产业板块 (用于扩大 universe, 覆盖算力/半导体/医药/煤炭等主线)
CORE_BOARDS = [
    ("BK1036", "半导体"), ("BK0448", "通信设备"), ("BK0465", "化学制药"),
    ("BK0437", "煤炭"), ("BK0428", "电力"), ("BK0921", "算力概念"),
]


def load_expanded_universe(per_board: int = 40) -> Tuple[List[str], Dict[str, str]]:
    """构建扩展 universe: 核心板块成分股 + 持仓 (扩大截面样本).

    东财 push2 实测对快速连续请求限流 (RemoteDisconnected), 故板块请求间隔拉长.
    首次成功后 graph_data_source 会写本地缓存, 后续验证读缓存不请求东财.

    Returns:
        (symbols, industries) — industries 用于行业中性化
    """
    from utils.supply_chain_builder import load_positions_symbols
    from utils.graph_data_source import get_graph_data_source

    ds = get_graph_data_source()
    symbols: List[str] = []
    industries: Dict[str, str] = {}

    # 1) 板块成分股 (东财, 带行业标签), 间隔 1.5s 防限流
    for board_code, board_name in CORE_BOARDS:
        rows = ds.fetch_board_stocks(board_code, limit=per_board)
        for r in rows:
            code = r["code"]
            if code not in symbols:
                symbols.append(code)
            if r.get("industry"):
                industries[code] = r["industry"]
        time.sleep(1.5)  # 东财限流规避

    # 2) 持仓 (兜底核心标的)
    core = load_positions_symbols()
    for c in core:
        base = c.split(".")[0]
        if base not in symbols:
            symbols.append(base)

    return symbols, industries


def calc_ic_series(
    price_data: Dict[str, dict[str, List[float]]],
    graph,
    factor_name: str,
    industries: Optional[Dict[str, str]] = None,
    horizon: int = 5,
    windows: int = 12,
    window_len: int = 30,
    min_neighbors: int = 1,
) -> List[float]:
    """跨时间窗 IC 序列 (滚动重算因子, 消除前视偏差).

    在每个滚动窗口的 end_idx 时点, 用 closes[:end_idx+1] 重新计算因子值
    (含正交化), 再与 [end_idx, end_idx+horizon] 未来收益做 Spearman IC.
    得到 len≤windows 的 IC 序列, 用于 ICIR (mean/std) 评估稳定性.

    修正 B1: 原实现把「最新时点 T 的常数因子」用于所有历史窗口, 用今天的因子
    "预测"历史窗口的未来收益, 构成前视偏差, ICIR 不可信。现改为每个窗口在
    end_idx 时点就地重算因子 (含正交化锚因子), 因子与收益严格时点对齐。

    索引约定: 所有股票统一用「从末尾往前」的绝对时间轴。last_idx 为低分位数
    长度-1 预留 horizon。窗口从 last_idx 往旧滚动。
    """
    import numpy as np
    try:
        from scipy.stats import spearmanr
    except ImportError:
        return []

    closes_map = {sym: data.get("closes", []) for sym, data in price_data.items()}
    # 仅考虑能支撑完整窗口的股票 (排除次新股等短序列)
    eligible = [len(c) for c in closes_map.values() if len(c) > horizon + window_len]
    if len(eligible) < 5:
        return []
    # 用低分位数长度 (默认 20%) 作为共同覆盖上限, 排除极端次新股
    el = np.array(sorted(eligible))
    max_len = int(el[max(0, int(len(el) * 0.2) - 1)])
    last_idx = max_len - horizon - 1

    ic_series = []
    step = max(2, window_len // 3)  # 滚动步进
    for w in range(windows):
        end_idx = last_idx - w * step
        start_idx = end_idx - window_len + 1
        if start_idx < 0 or end_idx > last_idx:
            break
        # 在 end_idx 时点重算因子: 截断 closes 到 end_idx+1, 消除前视偏差
        truncated = {
            sym: {"closes": c[: end_idx + 1]}
            for sym, c in closes_map.items()
            if len(c) > end_idx
        }
        chain_t = compute_lead_lag_factors(
            truncated, graph, industries=industries, min_neighbors=min_neighbors
        )
        mom_t = _compute_momentum_factors(truncated)
        chain_t = orthogonalize_chain_factors(chain_t, mom_t)
        fv_t = chain_t.get(factor_name)
        if not fv_t or not fv_t.values:
            continue
        fs, rets = [], []
        for sym, fv in fv_t.values.items():
            closes = closes_map.get(sym, [])
            if len(closes) <= end_idx + horizon:
                continue
            if closes[end_idx] <= 0:
                continue
            fut = closes[end_idx + horizon] / closes[end_idx] - 1
            fs.append(fv)
            rets.append(fut)
        # 需足够样本 + 收益有区分度 (避免 spearmanr 返回 nan)
        if len(fs) >= 5 and np.var(rets) > 1e-12:
            corr, _ = spearmanr(fs, rets)
            if not np.isnan(corr):
                ic_series.append(float(corr))
    return ic_series


def calc_icir(ic_series: List[float]) -> float:
    """ICIR = mean(IC)/std(IC)."""
    if len(ic_series) < 2:
        return 0.0
    import numpy as np
    arr = np.array(ic_series)
    std = arr.std()
    return float(arr.mean() / std) if std > 0 else 0.0


def run_long_short_ic(
    price_data: Dict[str, dict[str, List[float]]],
    graph,
    factor_name: str,
    industries: Optional[Dict[str, str]] = None,
    horizon: int = 20,
    min_neighbors: int = 1,
    windows: int = 6,
) -> float:
    """多空收益模拟: 多期滚动多空 → 年化夏普 (名实相符).

    在多个非重叠滚动时点 entry_idx=T-horizon 重算期初因子, 按 Top20%/Bottom20%
    分档计算该期 [entry, T] 多空收益, 得到多空收益序列后计算年化夏普:
        Sharpe = mean(ls_returns) / std(ls_returns) * sqrt(252/horizon)

    修正 B2: 期初因子预测整期收益, 因子与收益窗口零重叠 (无前视).
    修正 B4: 原实现返回单期年化多空收益却以"夏普>1.0"为阈值, 名实不符; 且单期
    收益噪声大。现改为多期序列算真正的年化夏普, 阈值"多空夏普>1.0"名实相符.

    Returns:
        年化夏普比率 (原始因子方向; 负 IC 因子由调用方按 direction 取反).
    """
    import numpy as np

    closes_map = {sym: data.get("closes", []) for sym, data in price_data.items()}
    eligible = [len(c) for c in closes_map.values() if len(c) > horizon + 25]
    if len(eligible) < 10:
        return 0.0
    el = np.array(sorted(eligible))
    max_len = int(el[max(0, int(len(el) * 0.2) - 1)])
    step = horizon  # 非重叠窗口, 减少多空收益序列自相关

    ls_returns: List[float] = []
    for w in range(windows):
        T = max_len - 1 - w * step
        entry_idx = T - horizon  # 期初: 因子计算时点
        if entry_idx < 25 or T >= max_len:
            continue
        # 在 entry_idx 时点重算因子 (期初因子, 消除前视)
        truncated = {
            sym: {"closes": c[: entry_idx + 1]}
            for sym, c in closes_map.items()
            if len(c) > entry_idx
        }
        chain_t = compute_lead_lag_factors(
            truncated, graph, industries=industries, min_neighbors=min_neighbors
        )
        mom_t = _compute_momentum_factors(truncated)
        chain_t = orthogonalize_chain_factors(chain_t, mom_t)
        fv_t = chain_t.get(factor_name)
        if not fv_t or not fv_t.values:
            continue
        rows = []
        for sym, fv in fv_t.values.items():
            closes = closes_map.get(sym, [])
            if len(closes) <= T:
                continue
            if closes[entry_idx] <= 0:
                continue
            ret = closes[T] / closes[entry_idx] - 1  # [entry, T] 整期收益
            rows.append((fv, ret))
        if len(rows) < 10:
            continue
        rows.sort(key=lambda x: x[0], reverse=True)
        n = len(rows)
        top_n = max(2, int(n * 0.2))
        long_ret = float(np.mean([r[1] for r in rows[:top_n]]))
        short_ret = float(np.mean([r[1] for r in rows[-top_n:]]))
        ls_returns.append(long_ret - short_ret)

    if len(ls_returns) < 3:
        return 0.0
    arr = np.array(ls_returns)
    std = float(arr.std())
    if std < 1e-12:
        return 0.0
    # 年化夏普 = 周期均值/标准差 × sqrt(年内期数)
    return float(arr.mean() / std * (252.0 / horizon) ** 0.5)


def run_gate1_validation(days: int = 250, min_neighbors: int = 1, expanded: bool = True) -> Dict[str, Any]:
    """执行 Gate 1 门禁验证.

    Args:
        days: 历史K线天数
        min_neighbors: 因子有效所需最少邻居数
        expanded: True 用扩展 universe (板块成分股), False 用基准 (持仓+邻居)

    Returns:
        {factors, ic, icir, long_short, verdict, universe_size, ...}
    """
    logger.info("[1/5] 构建 universe ...")
    if expanded:
        universe, industries = load_expanded_universe()
        logger.info(f"     扩展 universe: {len(universe)} 只 (板块成分股+持仓), 行业标签 {len(industries)} 个")
    else:
        universe = load_universe()
        industries = None
        logger.info(f"     基准 universe: {len(universe)} 只 (持仓+供应链邻居)")

    logger.info("[2/5] 拉取腾讯历史 K 线...")
    price_data = fetch_prices(universe, days)
    logger.info(f"     成功拉取 {len(price_data)} 只")
    if len(price_data) < 10:
        return {"error": f"数据不足: 仅 {len(price_data)} 只", "universe_size": len(universe)}

    logger.info("[3/5] 构建供应链关系图...")
    # 用有数据的股票重建图 (剔除无数据节点)
    builder = SupplyChainBuilder(symbols=list(price_data.keys()), include_themes=True, max_hops=2)
    graph_info = builder.build()
    logger.info(f"     图: {graph_info['node_count']} 节点 / {graph_info['edge_count']} 边")

    logger.info("[4/5] 计算 Lead-Lag 因子 + 正交化 + 行业中性化...")
    # industries 仅取 price_data 覆盖的标的
    ind_used = {k: v for k, v in (industries or {}).items() if k in price_data}
    chain_factors = compute_lead_lag_factors(
        price_data, builder.graph, industries=ind_used or None, min_neighbors=min_neighbors
    )
    # 对已有动量因子正交化, 验证「邻居信息」增量价值
    mom_factors = _compute_momentum_factors(price_data)
    chain_factors = orthogonalize_chain_factors(chain_factors, mom_factors)
    logger.info(f"     生成 {len(chain_factors)} 个 Lead-Lag 因子")

    logger.info("[5/5] Gate 1 门禁验证 (增量 IC + ICIR + 多空夏普)...")
    results = {}
    gate_results = []
    for name, fv in chain_factors.items():
        if not fv.values:
            continue
        # 跨时间窗 IC 序列 → IC 均值 + ICIR (稳定性)
        # 修正 B1: 滚动重算因子 (每窗口 end_idx 时点就地重算), 消除前视偏差
        ic_series = calc_ic_series(
            price_data, builder.graph, name,
            industries=ind_used or None, horizon=5, windows=10,
            min_neighbors=min_neighbors,
        )
        ic_mean = float(sum(ic_series) / len(ic_series)) if ic_series else 0.0
        icir = calc_icir(ic_series)
        # 因子方向修正 (修正 B3): 用 ICIR 符号判定方向比 IC 更稳健 (IC 单期噪声大,
        # ICIR 综合均值与稳定性). 反向因子 ICIR<0 → direction=-1, 反向后
        # eff_ic / eff_icir / eff_ls 全部转为正方向评估, 反向因子不再被误杀.
        direction = -1 if icir < 0 else 1
        eff_ic = direction * ic_mean
        eff_icir = direction * icir  # = abs(icir)
        # 多空收益 (按修正方向)
        # 修正 B2: 期初因子预测整期收益, 消除因子与收益窗口重叠的前视偏差
        # 修正 B4: 多期滚动多空 → 年化夏普 (名实相符, 阈值"夏普>1.0"成立)
        ls_raw = run_long_short_ic(
            price_data, builder.graph, name,
            industries=ind_used or None, horizon=20,
            min_neighbors=min_neighbors,
        )
        eff_ls = direction * ls_raw  # 按方向修正后的多空年化夏普
        results[name] = {
            "ic_mean": ic_mean,
            "icir": icir,
            "eff_ic": eff_ic,
            "eff_icir": eff_icir,
            "direction": direction,
            "long_short_sharpe": eff_ls,  # 名实相符: 多空年化夏普
            "coverage": len(fv.values),
            "n_windows": len(ic_series),
        }
        gate_results.append((name, eff_ic, eff_icir, eff_ls))

    # Gate 1 判定 (修正 B3): 同一因子同时满足三条件, 不再分两个不相交集合
    #   eff_ic >= 0.01 AND eff_icir > 0.3 AND eff_ls > 1.0
    # 原实现 strong∩ls_pass 可能为空却仍 PASS (两个集合不相交), 现改为单因子交集.
    # 反向因子经 direction 修正后 eff_icir=abs(icir), 不再被 icir>0.3 误杀.
    passers = [(n, ic, ir, ls) for n, ic, ir, ls in gate_results
               if ic >= 0.01 and ir > 0.3 and ls > 1.0]
    verdict = "PASS" if passers else "FAIL"

    return {
        "universe_type": "expanded" if expanded else "baseline",
        "universe_size": len(universe),
        "price_coverage": len(price_data),
        "industry_coverage": len(ind_used),
        "graph": graph_info,
        "factors": results,
        "gate1_verdict": verdict,
        "gate1_passers": [n for n, _, _, _ in passers],  # 通过门禁的因子 (审计可见)
        "gate1_threshold": "同一因子同时满足: effIC≥0.01 且 effICIR>0.3 且 多空夏普>1.0",
    }


def _compute_momentum_factors(price_data: Dict[str, dict[str, List[float]]]) -> Dict[str, FactorValue]:
    """计算基准动量因子 (作正交化对照).

    必须补全 MOM_20D / MOM_60D / MOM_REVERSAL_5D 三个锚因子, 与
    orthogonalize_chain_factors 的 mapping 对齐. 原实现仅返回 MOM_20D,
    导致 CHAIN_MOM_60D / CHAIN_REVERSAL_5D 跳过正交化, 保留动量暴露,
    验证的不是「邻居信息增量」而是「动量本身」.
    """
    mom20: Dict[str, float] = {}
    mom60: Dict[str, float] = {}
    rev5: Dict[str, float] = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 21 and closes[-21] > 0:
            mom20[sym] = closes[-1] / closes[-21] - 1
        if len(closes) > 61 and closes[-61] > 0:
            mom60[sym] = closes[-1] / closes[-61] - 1
        if len(closes) > 6 and closes[-6] > 0:
            rev5[sym] = -(closes[-1] / closes[-6] - 1)  # 反转: 负动量
    return {
        "MOM_20D": FactorValue(name="MOM_20D", category="Momentum", values=mom20),
        "MOM_60D": FactorValue(name="MOM_60D", category="Momentum", values=mom60),
        "MOM_REVERSAL_5D": FactorValue(name="MOM_REVERSAL_5D", category="Momentum", values=rev5),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Gate 1 门禁验证")
    parser.add_argument("--days", type=int, default=250, help="历史K线天数")
    parser.add_argument("--min-neighbors", type=int, default=1)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--baseline", action="store_true", help="用基准 universe (持仓+邻居), 默认扩展")
    args = parser.parse_args()

    result = run_gate1_validation(days=args.days, min_neighbors=args.min_neighbors,
                                  expanded=not args.baseline)
    if args.json:
        logger.info(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    if "error" in result:
        logger.error(f"Gate 1 验证失败: {result['error']}")
        return 1

    logger.info("=" * 78)
    logger.info("Gate 1 门禁验证结果 — GNN Lead-Lag 因子")
    logger.info("=" * 78)
    logger.info(f"Universe: {result['universe_size']} 只 ({result['universe_type']}) | 有数据: {result['price_coverage']} 只 | 行业标签: {result.get('industry_coverage', 0)}")
    logger.info(f"图: {result['graph']['node_count']} 节点 / {result['graph']['edge_count']} 边")
    logger.info("-" * 78)
    # 表头: IC均值(原始) / effIC(方向修正) / ICIR(原始) / effICIR(方向修正) / 方向 / 多空夏普 / 覆盖
    logger.info(f"{'因子':<22}{'IC均值':>8}{'effIC':>8}{'ICIR':>8}{'effICIR':>9}{'方向':>5}{'多空夏普':>10}{'覆盖':>6}")
    logger.info("-" * 78)
    for name, meta in sorted(result["factors"].items()):
        logger.info(f"{name:<22}{meta['ic_mean']:>8.4f}{meta['eff_ic']:>8.4f}"
              f"{meta['icir']:>8.3f}{meta['eff_icir']:>9.3f}"
              f"{meta['direction']:>5}"
              f"{meta['long_short_sharpe']:>10.3f}{meta['coverage']:>6}")
    logger.info("-" * 78)
    passers = result.get("gate1_passers", [])
    if passers:
        logger.info(f"通过因子: {', '.join(passers)}")
    else:
        logger.info("通过因子: (无) — 无因子同时满足 effIC≥0.01 且 effICIR>0.3 且 多空夏普>1.0")
    logger.info(f"Gate 1 判定: {result['gate1_verdict']} (阈值: {result['gate1_threshold']})")
    logger.info("=" * 78)
    return 0 if result["gate1_verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())