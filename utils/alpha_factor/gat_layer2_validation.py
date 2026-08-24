"""GAT Layer 2 无偏验证 (B1-B5 全套修复后)

[研究资产 — 不在生产路径] 2026-08-03 Gate2 FAIL 回退后保留为研究资产。
本脚本为 GAT 增益证伪的可复现验证脚本, 不被任何生产模块调用。
未来重启 GAT 时需先运行本脚本确认增益是否可复现。

验证「GAT 学习注意力 > 静态权重」命题, 复现/证伪此前 +0.039 增益结论。

结论: +0.039 增益被证伪 (两次验证 +0.0017/+0.0064, t 值 0.15/0.76 不显著), Gate2 FAIL。
详见 cairn/gnn-supply-chain-factor.md §踩坑记录「GAT 增益证伪」。

背景:
  cairn 文档 (§5.1h, wave5-review) 记录 "GAT 多时间点样本外增益 +0.039@80只 /
  +0.0088@200只", 但代码库中无任何调用 GATFactorTorch 的验证脚本 — 结论不可复现。
  本脚本从头实现无偏版 GAT Layer 2 验证, 套用 B1-B5 全套修复框架。

无偏框架 (B1-B5):
  - B1/B2 前视偏差: 每时间点 T 用 closes[:T+1] 就地重算特征, 标签 = [T, T+horizon]
    未来收益; 训练时间点严格 < 测试时点; 因子与收益窗口零重叠。
  - B5 GAT 掩码: 复用 gat_factor_torch.py (已修 masked_fill(-inf) + nan_to_num)。
  - B3 方向修正: direction 按 ICIR 符号 (eff_icir=|icir|), 反向因子不被误杀。
  - B4 多空夏普: 多期非重叠滚动多空序列 → 年化夏普 = mean/std × sqrt(252/horizon)。

对比设计 (公平性):
  - GAT 因子  = Σ_j alpha_ij  * feat[j,0]   (学习权重 alpha, softmax 归一化)
  - 静态因子  = Σ_j strength_ij * feat[j,0] / Σ_j strength_ij  (手工权重, 归一化)
  两者都聚合「邻居 20 日动量」(feat[0]), 区别仅权重来源 → 增益可归因于「学习」。

用法:
    python -m utils.alpha_factor.gat_layer2_validation
    python -m utils.alpha_factor.gat_layer2_validation --days 400 --horizon 20
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np

# 路径处理 (兼容直接运行 / -m 运行)
_DIR = Path(__file__).resolve().parent
_UTILS = _DIR.parent
_PROJ = _UTILS.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

from utils.alpha_factor.gat_factor_torch import GATFactorTorch, build_adjacency  # noqa: E402
from utils.alpha_factor.gate1_validation import (  # noqa: E402
    calc_icir,
    fetch_prices,
    load_expanded_universe,
)
from utils.supply_chain_builder import SupplyChainBuilder  # noqa: E402

# Windows 控制台 UTF-8 输出 (幂等)
for _name in ("stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if _stream is not None and getattr(_stream, "encoding", "").lower() != "utf-8":
        _buffer = getattr(_stream, "buffer", None)
        if _buffer is not None:
            try:
                setattr(sys, _name, io.TextIOWrapper(_buffer, encoding="utf-8", errors="replace"))
            except (ValueError, TypeError, KeyError, AttributeError, OSError):
                pass

logger = logging.getLogger(__name__)


def compute_features_at_time(
    price_data: dict[str, dict[str, list[float]]],
    symbols: list[str],
    T: int,
    horizon: int,
) -> tuple[np.ndarray, np.ndarray]:
    """在时间点 T 用 closes[:T+1] 就地重算特征 + 未来收益 (B1/B2 无前视).

    Args:
        price_data: {sym: {closes: [...]}}
        symbols: 股票列表 (决定行顺序, 与邻接矩阵对齐)
        T: 因子计算时点 (用 closes[:T+1], 含 closes[T])
        horizon: 未来收益窗口

    Returns:
        features [n, 2]: feat[0]=自身20日动量, feat[1]=自身60日动量
        future_ret [n]: closes[T+horizon]/closes[T]-1 (无数据的股票为 nan)
    """
    n = len(symbols)
    features = np.full((n, 2), np.nan)
    future_ret = np.full(n, np.nan)
    for i, sym in enumerate(symbols):
        closes = price_data.get(sym, {}).get("closes", [])
        # 需要 T 之前 60 天 (算 60 日动量) + T+horizon 之后 (未来收益)
        if len(closes) <= T + horizon:
            continue
        if T < 60 or closes[T] <= 0 or closes[T - 20] <= 0 or closes[T - 60] <= 0:
            continue
        # B1/B2 无前视: 特征只用 closes[:T+1], 未来收益用 [T, T+horizon]
        m20 = closes[T] / closes[T - 20] - 1
        m60 = closes[T] / closes[T - 60] - 1
        features[i] = [m20, m60]
        future_ret[i] = closes[T + horizon] / closes[T] - 1
    return features, future_ret


def compute_static_factor(features: np.ndarray, adj: np.ndarray) -> np.ndarray:
    """静态基线: strength 归一化加权邻居 20 日动量.

    static[i] = Σ_j adj[i,j]*feat[j,0] / Σ_j adj[i,j]
    (与 GAT 因子 Σ_j alpha_ij*feat[j,0] 可比, 区别仅权重来源)
    """
    feat0 = features[:, 0]
    # nan 填 0 避免传播 (无数据节点不参与聚合)
    feat0_safe = np.where(np.isnan(feat0), 0.0, feat0)
    row_sum = adj.sum(axis=1)
    row_sum_safe = np.where(row_sum == 0, 1.0, row_sum)
    static = (adj @ feat0_safe) / row_sum_safe
    # 无邻居或无数据的节点置 nan
    static[(row_sum == 0) | np.isnan(feat0)] = np.nan
    return static


def build_block_adjacency(adj_single: np.ndarray, n_blocks: int) -> np.ndarray:
    """构建多时间点训练邻接矩阵 (block diagonal, 跨时间点无边).

    GAT 注意力只在同一时间点的股票间传播 (同时间点有供应链边, 跨时间点无边)。
    [NM, NM] = block_diag(M 个 [N,N])
    """
    n = adj_single.shape[0]
    nm = n * n_blocks
    adj_block = np.zeros((nm, nm))
    for i in range(n_blocks):
        s = i * n
        adj_block[s : s + n, s : s + n] = adj_single
    return adj_block


def calc_ic(factor: np.ndarray, future_ret: np.ndarray) -> float:
    """Spearman IC (因子与未来收益的秩相关)."""
    try:
        from scipy.stats import spearmanr
    except ImportError:
        return 0.0
    valid = ~np.isnan(factor) & ~np.isnan(future_ret)
    if valid.sum() < 5:
        return 0.0
    if np.var(factor[valid]) < 1e-12 or np.var(future_ret[valid]) < 1e-12:
        return 0.0
    corr, _ = spearmanr(factor[valid], future_ret[valid])
    return float(corr) if not np.isnan(corr) else 0.0


def calc_long_short_sharpe(
    factor_series: list[np.ndarray],
    ret_series: list[np.ndarray],
    horizon: int,
    direction: int,
) -> float:
    """多期非重叠滚动多空 → 年化夏普 (B4).

    每个测试时点按因子排序 Top20%-Bottom20%, 得多空收益序列, 年化夏普。
    """
    ls_returns: list[float] = []
    for factor, ret in zip(factor_series, ret_series, strict=True):
        valid = ~np.isnan(factor) & ~np.isnan(ret)
        if valid.sum() < 10:
            continue
        rows = sorted(zip(factor[valid], ret[valid], strict=True), reverse=True)
        n = len(rows)
        top_n = max(2, n // 5)
        long_ret = float(np.mean([r for _, r in rows[:top_n]]))
        short_ret = float(np.mean([r for _, r in rows[-top_n:]]))
        ls_returns.append(long_ret - short_ret)
    if len(ls_returns) < 3:
        return 0.0
    arr = np.array(ls_returns)
    std = float(arr.std())
    if std < 1e-12:
        return 0.0
    sharpe = float(arr.mean() / std * (252.0 / horizon) ** 0.5)
    return direction * sharpe  # 方向修正


def run_gat_layer2_validation(
    days: int = 500,
    horizon: int = 20,
    n_train_tp: int = 12,
    n_test_tp: int = 5,
    epochs: int = 300,
    n_hidden: int = 16,
    n_heads: int = 4,
    lr: float = 0.005,
    use_cache: bool = True,
) -> dict[str, Any]:
    """执行 GAT Layer 2 无偏验证.

    Args:
        days: 历史K线天数 (实际受缓存数据长度限制)
        horizon: 未来收益窗口 (天)
        n_train_tp: 训练时间点数
        n_test_tp: 测试时间点数
        epochs: GAT 训练轮数
        n_hidden: GAT 隐层维度
        n_heads: GAT 注意力头数
        lr: 学习率
        use_cache: True 读价格缓存, False 强制重拉 (拉长历史时用)

    Returns:
        {gat_ics, static_ics, gain, eff_ic, eff_icir, long_short_sharpe, ...}
    """
    logger.info("[1/6] 构建 universe + 拉取价格...")
    universe, industries = load_expanded_universe()
    price_data = fetch_prices(universe, days, use_cache=use_cache)
    logger.info(f"     成功拉取 {len(price_data)} 只")
    if len(price_data) < 30:
        return {"error": f"数据不足: 仅 {len(price_data)} 只"}

    logger.info("[2/6] 构建供应链图 + 邻接矩阵...")
    builder = SupplyChainBuilder(symbols=list(price_data.keys()), include_themes=True, max_hops=2)
    graph_info = builder.build()
    logger.info(f"     图: {graph_info['node_count']} 节点 / {graph_info['edge_count']} 边")

    # 筛选有效股票: 在 price_data 中且有供应链邻居
    symbols = list(price_data.keys())
    adj, _ = build_adjacency(builder.graph, symbols)  # [n, n]
    # 只保留有邻居的股票 (无邻居的节点 GAT/静态因子都无意义)
    has_neighbor = (adj.sum(axis=1) > 0)
    valid_idx = np.where(has_neighbor)[0]
    symbols = [symbols[i] for i in valid_idx]
    adj = adj[valid_idx][:, valid_idx]
    logger.info(f"     有效股票 (有邻居): {len(symbols)} 只")

    # 计算共同覆盖长度 (低分位数, 排除次新股)
    closes_lens = [len(price_data[s].get("closes", [])) for s in symbols]
    el = np.array(sorted(closes_lens))
    max_len = int(el[max(0, int(len(el) * 0.2) - 1)])
    logger.info(f"     共同覆盖长度 (低分位): {max_len} 天")

    # 自适应时间点数 (根据数据长度调整)
    # 需要: 最旧训练时点 T_old >= 60 (算60日动量) + 所有时点+horizon <= max_len-1
    # 总跨度 = (n_train_tp + n_test_tp) * horizon
    total_span = (n_train_tp + n_test_tp) * horizon
    if max_len - 60 < total_span + horizon:
        # 数据不足, 减少时间点
        max_tp = (max_len - 60 - horizon) // horizon
        n_test_tp = max(3, min(n_test_tp, max_tp // 3))
        n_train_tp = max(6, min(n_train_tp, max_tp - n_test_tp))
        logger.info(f"     数据不足, 自适应: 训练 {n_train_tp} 点 / 测试 {n_test_tp} 点")
    # 筛选覆盖 max_len 的股票 (所有时间点都有数据)
    full_syms = [s for s in symbols if len(price_data[s].get("closes", [])) >= max_len]
    full_idx = [symbols.index(s) for s in full_syms]
    symbols = full_syms
    adj = adj[full_idx][:, full_idx]
    logger.info(f"     全覆盖股票 (len>=max_len): {len(symbols)} 只")

    # 时间点切分 (从最新往旧, 非重叠, 严格时序: 训练 < 测试)
    # 测试时点: T_test_k = max_len-1-horizon - k*horizon (留 horizon 余量算未来收益)
    # 训练时点: T_train_m = 最早测试时点 - m*horizon (往更旧)
    test_times = [max_len - 1 - horizon - k * horizon for k in range(n_test_tp)]
    earliest_test = min(test_times)
    train_times = [earliest_test - (m + 1) * horizon for m in range(n_train_tp)]
    train_times = [t for t in train_times if t >= 60]  # 需 60 天历史算动量
    logger.info(f"     测试时点 (最新→旧): {test_times}")
    logger.info(f"     训练时点数: {len(train_times)} (严格 < 测试时点 {earliest_test})")

    logger.info("[3/6] 多时间点训练样本构建 (B1/B2 无前视)...")
    features_list: list[np.ndarray] = []
    labels_list: list[np.ndarray] = []
    for T in train_times:
        feat, ret = compute_features_at_time(price_data, symbols, T, horizon)
        features_list.append(feat)
        labels_list.append(ret)
    features_block = np.vstack(features_list)  # [NM, 2]
    labels_block = np.concatenate(labels_list)  # [NM]
    # nan 填 0 (无数据节点), 训练 loss 只在 valid 样本算 (GATFactorTorch.train 已处理)
    valid_mask = ~np.isnan(features_block).any(axis=1) & ~np.isnan(labels_block)
    features_block = np.nan_to_num(features_block, nan=0.0)
    labels_block = np.nan_to_num(labels_block, nan=0.0)
    # 标准化特征 (Z-score, 防特征尺度差异影响 GAT 训练)
    feat_mean = features_block[valid_mask].mean(axis=0)
    feat_std = features_block[valid_mask].std(axis=0)
    feat_std = np.where(feat_std < 1e-8, 1.0, feat_std)
    features_block = (features_block - feat_mean) / feat_std
    # block 邻接矩阵 (跨时间点无边)
    adj_block = build_block_adjacency(adj, len(train_times))  # [NM, NM]
    logger.info(
        f"     训练样本: {features_block.shape[0]} ({len(train_times)} 时点 × {len(symbols)} 股), "
        f"有效 {valid_mask.sum()}"
    )

    logger.info("[4/6] GAT 训练 (B5 掩码已修, MSE + Adam)...")
    model = GATFactorTorch(n_hidden=n_hidden, n_heads=n_heads, lr=lr, weight_decay=1e-4)
    losses = model.train(features_block, adj_block, labels_block, epochs=epochs, verbose=False)
    logger.info(f"     loss: {losses[0]:.6f} → {losses[-1]:.6f} ({epochs} epochs)")

    logger.info("[5/6] 测试时点评估 (GAT vs 静态)...")
    gat_ics: list[float] = []
    static_ics: list[float] = []
    gat_factor_series: list[np.ndarray] = []
    static_factor_series: list[np.ndarray] = []
    ret_series: list[np.ndarray] = []
    for T in test_times:
        feat, ret = compute_features_at_time(price_data, symbols, T, horizon)
        # 标准化 (用训练集统计量)
        feat_norm = (feat - feat_mean) / feat_std
        feat_norm = np.nan_to_num(feat_norm, nan=0.0)
        # GAT 因子 (学习权重)
        gat_f = model.compute(feat_norm, adj)
        # 静态因子 (手工 strength 权重, 用原始 feat[:,0] 非标准化)
        static_f = compute_static_factor(feat, adj)
        gat_ic = calc_ic(gat_f, ret)
        static_ic = calc_ic(static_f, ret)
        gat_ics.append(gat_ic)
        static_ics.append(static_ic)
        gat_factor_series.append(gat_f)
        static_factor_series.append(static_f)
        ret_series.append(ret)
        logger.info(f"     T={T}: GAT IC={gat_ic:+.4f}, 静态 IC={static_ic:+.4f}, "
                    f"增益={gat_ic - static_ic:+.4f}")

    logger.info("[6/6] 评估 (B3 方向修正 + B4 多空夏普)...")
    gat_mean_ic = float(np.mean(gat_ics))
    static_mean_ic = float(np.mean(static_ics))
    gain = gat_mean_ic - static_mean_ic  # 原始增益 (对比 +0.039)

    # B3 方向修正 (按 ICIR 符号)
    gat_direction = -1 if calc_icir(gat_ics) < 0 else 1
    static_direction = -1 if calc_icir(static_ics) < 0 else 1
    gat_eff_ic = gat_direction * gat_mean_ic
    gat_eff_icir = gat_direction * calc_icir(gat_ics)
    static_eff_ic = static_direction * static_mean_ic
    static_eff_icir = static_direction * calc_icir(static_ics)

    # B4 多空夏普 (方向修正后)
    gat_ls_sharpe = calc_long_short_sharpe(gat_factor_series, ret_series, horizon, gat_direction)
    static_ls_sharpe = calc_long_short_sharpe(
        static_factor_series, ret_series, horizon, static_direction
    )

    # Gate 2 判定: GAT effIC > 静态 effIC 且 GAT effICIR > 静态 effICIR
    gate2_pass = (gat_eff_ic > static_eff_ic) and (gat_eff_icir > static_eff_icir)

    return {
        "universe_size": len(universe),
        "price_coverage": len(price_data),
        "valid_symbols": len(symbols),
        "graph": graph_info,
        "max_len": max_len,
        "horizon": horizon,
        "n_train_tp": len(train_times),
        "n_test_tp": n_test_tp,
        "train_loss_start": float(losses[0]),
        "train_loss_end": float(losses[-1]),
        "gat_ics": gat_ics,
        "static_ics": static_ics,
        "gat_mean_ic": gat_mean_ic,
        "static_mean_ic": static_mean_ic,
        "gain": gain,  # 原始增益 (对比 +0.039)
        "gat_direction": gat_direction,
        "static_direction": static_direction,
        "gat_eff_ic": gat_eff_ic,
        "gat_eff_icir": gat_eff_icir,
        "static_eff_ic": static_eff_ic,
        "static_eff_icir": static_eff_icir,
        "gat_long_short_sharpe": gat_ls_sharpe,
        "static_long_short_sharpe": static_ls_sharpe,
        "gate2_verdict": "PASS" if gate2_pass else "FAIL",
        "gate2_threshold": "GAT effIC > 静态 effIC 且 GAT effICIR > 静态 effICIR",
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="GAT Layer 2 无偏验证 (B1-B5)")
    parser.add_argument("--days", type=int, default=500, help="历史K线天数")
    parser.add_argument("--horizon", type=int, default=20, help="未来收益窗口")
    parser.add_argument("--train-tp", type=int, default=12, help="训练时间点数")
    parser.add_argument("--test-tp", type=int, default=5, help="测试时间点数")
    parser.add_argument("--epochs", type=int, default=300, help="GAT 训练轮数")
    parser.add_argument("--refresh-cache", action="store_true", help="强制重拉价格 (拉长历史)")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    result = run_gat_layer2_validation(
        days=args.days,
        horizon=args.horizon,
        n_train_tp=args.train_tp,
        n_test_tp=args.test_tp,
        epochs=args.epochs,
        use_cache=not args.refresh_cache,
    )

    if args.json:
        import json
        logger.info(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    if "error" in result:
        logger.error(f"GAT Layer 2 验证失败: {result['error']}")
        return 1

    logger.info("=" * 80)
    logger.info("GAT Layer 2 无偏验证结果 (B1-B5 全套修复后)")
    logger.info("=" * 80)
    logger.info(f"Universe: {result['universe_size']} 只 | 有效股票(有邻居): {result['valid_symbols']} 只")
    logger.info(f"图: {result['graph']['node_count']} 节点 / {result['graph']['edge_count']} 边")
    logger.info(f"共同覆盖: {result['max_len']} 天 | horizon={result['horizon']} | "
          f"训练时点={result['n_train_tp']} | 测试时点={result['n_test_tp']}")
    logger.info(f"GAT 训练 loss: {result['train_loss_start']:.6f} → {result['train_loss_end']:.6f}")
    logger.info("-" * 80)
    logger.info(f"{'测试时点':>10}{'GAT IC':>12}{'静态 IC':>12}{'增益':>12}")
    logger.info("-" * 80)
    for i, (g, s) in enumerate(zip(result["gat_ics"], result["static_ics"], strict=True)):
        logger.info(f"{'T'+str(i+1):>10}{g:>12.4f}{s:>12.4f}{g - s:>12.4f}")
    logger.info("-" * 80)
    logger.info(f"{'均值':>10}{result['gat_mean_ic']:>12.4f}{result['static_mean_ic']:>12.4f}"
          f"{result['gain']:>12.4f}")
    logger.info("-" * 80)
    logger.info(f"方向修正 (B3): GAT direction={result['gat_direction']}, "
          f"静态 direction={result['static_direction']}")
    logger.info(f"  GAT  effIC={result['gat_eff_ic']:+.4f}, effICIR={result['gat_eff_icir']:+.4f}, "
          f"多空夏普={result['gat_long_short_sharpe']:+.3f}")
    logger.info(f"  静态 effIC={result['static_eff_ic']:+.4f}, effICIR={result['static_eff_icir']:+.4f}, "
          f"多空夏普={result['static_long_short_sharpe']:+.3f}")
    logger.info("-" * 80)
    logger.info(f"原始增益 (对比 +0.039): {result['gain']:+.4f}")
    logger.info(f"Gate 2 判定: {result['gate2_verdict']} (阈值: {result['gate2_threshold']})")
    logger.info("=" * 80)
    return 0 if result["gate2_verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
