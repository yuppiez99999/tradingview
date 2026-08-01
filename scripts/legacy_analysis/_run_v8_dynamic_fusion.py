"""V8 动态权重融合重算

核心思路:
  当 LGB 近期 IC 表现差时, 自动回退到等权基线, 避免集中亏损
  final = α * lgb_weights + (1-α) * equal_weights
  α 由滚动 IC 和 regime 决定

重算流程:
  1. 加载 V7.1 记录 (含每月 weights/returns/market_regime)
  2. 计算每月实现 IC = corr(weights, returns)
  3. 计算滚动 3 月 IC 作为信任度依据
  4. 动态融合 LGB 权重与等权基线
  5. 重算组合收益, 验证 Sharpe CV / DSR
"""
import glob
import json
import os
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path("v8.3_institutional/src/validation").resolve()))


# ============================================================
# V8 核心函数
# ============================================================

def compute_realized_ic(weights: dict, returns: dict) -> float:
    """计算单月实现 IC: 权重与收益的 Spearman 相关

    IC > 0 表示 LGB 给高权重的股票确实涨得多 (LGB 正确)
    IC < 0 表示 LGB 给高权重的股票反而跌得多 (LGB 错误)
    """
    pairs = [(w, returns.get(s, 0.0)) for s, w in weights.items() if w > 0]
    if len(pairs) < 5:
        return 0.0
    w_series = pd.Series([p[0] for p in pairs])
    r_series = pd.Series([p[1] for p in pairs])
    if w_series.std() == 0 or r_series.std() == 0:
        return 0.0
    return float(w_series.corr(r_series, method="spearman"))


def compute_trust_alpha(rolling_ic: float, regime: str,
                        ic_high: float = 0.15, ic_mid: float = 0.05,
                        ic_low: float = -0.05, ic_vlow: float = -0.15,
                        regime_factor: float = 0.80) -> float:
    """根据滚动 IC 和 regime 计算信任度 α

    Args:
        rolling_ic: 前 N 月平均实现 IC
        regime: 当前市场状态 (bull/bear/choppy/rebound)
        ic_high/mid/low/vlow: IC 分档阈值
        regime_factor: bull regime 下 LGB 信任度额外折扣

    Returns:
        α ∈ [0.10, 0.90], 越高越信任 LGB
    """
    if rolling_ic > ic_high:
        alpha = 0.85
    elif rolling_ic > ic_mid:
        alpha = 0.65
    elif rolling_ic > ic_low:
        alpha = 0.50
    elif rolling_ic > ic_vlow:
        alpha = 0.35
    else:
        alpha = 0.15

    if regime == "bull":
        alpha *= regime_factor
    elif regime == "bear":
        alpha *= 0.90

    return max(0.10, min(0.90, alpha))


def fuse_weights(lgb_weights: dict, alpha: float) -> tuple:
    """融合 LGB 权重与等权基线

    final_i = α * lgb_i + (1-α) * equal_weight
    然后归一化保持总暴露度

    Returns: (fused_weights, equal_weight)
    """
    active = {s: w for s, w in lgb_weights.items() if w > 0}
    n_active = len(active)
    if n_active == 0:
        return dict(lgb_weights), 0.0

    total_exposure = sum(active.values())
    equal_w = total_exposure / n_active
    fused = {s: alpha * w + (1.0 - alpha) * equal_w for s, w in active.items()}

    # 归一化保持总暴露度
    total_new = sum(fused.values())
    if total_new > 0:
        scale = total_exposure / total_new
        fused = {s: w * scale for s, w in fused.items()}

    # 对零权重股票保持零
    for s in lgb_weights:
        if s not in fused:
            fused[s] = 0.0

    return fused, equal_w


def recompute_portfolio_return(weights: dict, returns: dict) -> float:
    """重算组合收益 = sum(weight_i * return_i)"""
    return float(sum(weights.get(s, 0.0) * returns.get(s, 0.0) for s in weights))


# ============================================================
# 主流程
# ============================================================

def compute_exposure_factor(rolling_ic: float,
                            ic_high: float = 0.15, ic_mid: float = 0.05,
                            ic_low: float = -0.05, ic_vlow: float = -0.15) -> float:
    """V8b: IC 驱动敞口缩放因子

    当 LGB 近期 IC 为负时, 降低总敞口 (持仓现金), 而非重分配给等权
    IC 越负, 敞口越低

    Returns:
        exposure_factor ∈ [0.3, 1.0]
    """
    if rolling_ic >= ic_high:
        return 1.0   # LGB 高置信, 全仓
    elif rolling_ic >= ic_mid:
        return 0.9   # LGB 中置信, 轻微减仓
    elif rolling_ic >= ic_low:
        return 0.7   # 中性, 适度减仓
    elif rolling_ic >= ic_vlow:
        return 0.5   # LGB 低置信, 半仓
    else:
        return 0.3   # LGB 不置信, 重仓减仓


def run_v8(ic_window: int = 3, regime_factor: float = 0.80,
           ic_high: float = 0.15, ic_mid: float = 0.05,
           ic_low: float = -0.05, ic_vlow: float = -0.15,
           label: str = "v8", mode: str = "exposure") -> dict:
    """运行 V8 动态权重融合重算

    Args:
        ic_window: 滚动 IC 窗口 (月数)
        regime_factor: bull regime LGB 信任度折扣 (仅 mode=blend)
        ic_high/mid/low/vlow: IC 分档阈值
        label: 输出文件标签
        mode: "blend" (等权融合) 或 "exposure" (敞口缩放)

    Returns:
        V8 结果字典
    """
    # === 加载 V7.1 结果 ===
    v71_files = glob.glob("output/validation_reports/lgb_backtest_v71_signal_penalty*.json")
    v71_files.sort(key=lambda x: os.path.getmtime(x))
    v71_file = Path(v71_files[-1])
    print(f"加载 V7.1 结果: {v71_file}")

    with open(v71_file, encoding="utf-8") as f:
        v71_data = json.load(f)

    records = v71_data.get("records", [])
    n = len(records)
    print(f"V7.1 记录数: {n} ({records[0]['date']} ~ {records[-1]['date']})")
    print(f"V8 参数: mode={mode}, ic_window={ic_window}, regime_factor={regime_factor}, "
          f"ic阈值={ic_high}/{ic_mid}/{ic_low}/{ic_vlow}")

    # === 逐月计算实现 IC ===
    monthly_ic = []
    for rec in records:
        ic = compute_realized_ic(rec.get("weights", {}), rec.get("returns", {}))
        monthly_ic.append(ic)

    print("\n每月实现 IC:")
    for i, rec in enumerate(records):
        print(f"  {rec['date']}: IC={monthly_ic[i]:+.4f} regime={rec.get('market_regime',{}).get('regime','?')}")

    # === 滚动 IC + 动态融合 ===
    ic_history = deque(maxlen=ic_window)
    v8_records = []
    v8_changes = []

    for i, rec in enumerate(records):
        regime = rec.get("market_regime", {}).get("regime", "unknown")
        old_weights = rec.get("weights", {})
        returns = rec.get("returns", {})
        old_return = rec.get("portfolio_return", 0.0)

        # 滚动 IC (用前 ic_window 月的 IC, 不含当月)
        if len(ic_history) > 0:
            rolling_ic = float(np.mean(ic_history))
        else:
            rolling_ic = 0.0  # 前几个月无历史, 中性信任

        # 计算信任度 α (blend 模式) 或 敞口因子 (exposure 模式)
        if mode == "blend":
            alpha = compute_trust_alpha(
                rolling_ic, regime,
                ic_high=ic_high, ic_mid=ic_mid, ic_low=ic_low, ic_vlow=ic_vlow,
                regime_factor=regime_factor,
            )
            # 融合权重
            fused_weights, equal_w = fuse_weights(old_weights, alpha)
            new_return = recompute_portfolio_return(fused_weights, returns)
        else:  # mode == "exposure"
            alpha = compute_exposure_factor(
                rolling_ic,
                ic_high=ic_high, ic_mid=ic_mid, ic_low=ic_low, ic_vlow=ic_vlow,
            )
            # 敞口缩放: 权重 × alpha, 剩余 (1-alpha) 为现金
            fused_weights = {s: w * alpha for s, w in old_weights.items()}
            equal_w = 0.0
            new_return = recompute_portfolio_return(fused_weights, returns)

        # 记录变更
        new_rec = dict(rec)
        new_rec["weights"] = fused_weights
        new_rec["portfolio_return"] = new_return
        new_rec["v8_alpha"] = alpha
        new_rec["v8_rolling_ic"] = rolling_ic
        new_rec["v8_realized_ic"] = monthly_ic[i]
        new_rec["v8_equal_weight"] = equal_w
        new_rec["v8_mode"] = mode
        v8_records.append(new_rec)

        if abs(new_return - old_return) > 0.001:  # 变更 > 0.1%
            v8_changes.append({
                "date": rec["date"],
                "regime": regime,
                "rolling_ic": rolling_ic,
                "alpha": alpha,
                "old_return": old_return,
                "new_return": new_return,
                "delta": new_return - old_return,
            })

        # 更新 IC 历史 (用当月实现 IC, 供下月使用)
        ic_history.append(monthly_ic[i])

    # === 打印变更摘要 ===
    print(f"\n{'='*70}")
    print(f"V8 变更摘要: {len(v8_changes)} 个月被修改 (变更>0.1%)")
    print(f"{'='*70}")
    for ch in v8_changes:
        print(f"{ch['date']}: regime={ch['regime']:6s} IC={ch['rolling_ic']:+.3f} α={ch['alpha']:.2f} | "
              f"ret {ch['old_return']*100:+.2f}% -> {ch['new_return']*100:+.2f}% (Δ{ch['delta']*100:+.2f}%)")
    print(f"  (mode={mode}, α 含义: {'信任度' if mode=='blend' else '敞口因子'})")

    # === 计算 V8 指标 ===
    returns_series = pd.Series([r["portfolio_return"] for r in v8_records])

    ann_ret = float((1 + returns_series.mean()) ** 12 - 1)
    ann_vol = float(returns_series.std() * np.sqrt(12))
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    equity = (1 + returns_series).cumprod()
    peak = equity.cummax()
    dd_series = (peak - equity) / peak
    max_dd = float(dd_series.max())
    win_rate = float((returns_series > 0).mean())
    skew = float(returns_series.skew())
    kurt = float(returns_series.kurt())

    print(f"\n{'='*70}")
    print(f"V8 动态权重融合结果 (mode={mode}, ic_window={ic_window}, regime_factor={regime_factor})")
    print(f"{'='*70}")
    print(f"回测期: {records[0]['date']} ~ {records[-1]['date']} ({n} 个月)")
    print(f"年化收益: {ann_ret:.4f} ({ann_ret*100:.2f}%)  [V7.2: 13.44%, V7.1: 14.23%, V6.2: 14.35%]")
    print(f"年化波动: {ann_vol:.4f} ({ann_vol*100:.2f}%)  [V7.2: 10.98%, V7.1: 12.83%, V6.2: 12.85%]")
    print(f"Sharpe:   {sharpe:.4f}  [V7.2: 1.224, V7.1: 1.110, V6.2: 1.092]")
    print(f"最大回撤: {max_dd:.4f} ({max_dd*100:.2f}%)  [V7.2: 8.34%, V7.1: 8.43%, V6.2: 7.90%]")
    print(f"胜率:     {win_rate:.4f} ({win_rate*100:.2f}%)  [V7.2: 55.56%, V7.1: 55.56%, V6.2: 57.78%]")
    print(f"偏度:     {skew:.4f}  [V7.2: 1.39, V7.1: 1.43, V6.2: 1.31]")
    print(f"峰度:     {kurt:.4f}  [V7.2: 3.96, V7.1: 4.82, V6.2: 6.86]")

    # === DSR ===
    print(f"\n{'-'*70}")
    print("Deflated Sharpe Ratio (DSR)")
    print(f"{'-'*70}")
    try:
        from deflated_sharpe import deflated_sharpe_ratio
        print(f"{'n_trials':>10} | {'DSR':>8} | {'p-value':>10} | {'E[max SR]':>12} | {'pass':>6}")
        print("-" * 60)
        dsr_results = []
        max_pass = 0
        for n_trials in [1, 3, 5, 8, 10, 20, 50, 100]:
            result = deflated_sharpe_ratio(returns_series, n_trials=n_trials)
            dsr_results.append({
                "n_trials": n_trials,
                "e_max_sr": float(result.e_max_sr),
                "dsr": float(result.deflated_sharpe_ratio),
                "p_value": float(result.p_value),
                "pass": bool(result.is_pass),
            })
            if result.is_pass:
                max_pass = n_trials
            status = "PASS" if result.is_pass else "FAIL"
            print(f"{n_trials:10d} | {result.deflated_sharpe_ratio:8.4f} | {result.p_value:10.6f} | {result.e_max_sr:12.6f} | {status:>6}")
        print(f"\nDSR 最大通过 n_trials: {max_pass} (目标 >=5, V7.2: 5, V7.1: 3, V6.2: 3)")
    except Exception as e:
        print(f"DSR 计算失败: {e}")
        dsr_results = []
        max_pass = 0

    # === Walk-Forward ===
    print(f"\n{'-'*70}")
    print("Walk-Forward 稳定性 (3个窗口)")
    print(f"{'-'*70}")
    window_size = n // 3
    windows = []
    for i in range(3):
        start = i * window_size
        end = (i + 1) * window_size if i < 2 else n
        win_returns = returns_series.iloc[start:end]
        win_equity = (1 + win_returns).cumprod()
        win_peak = win_equity.cummax()
        win_dd = float(((win_peak - win_equity) / win_peak).max())
        win_ann_ret = float((1 + win_returns.mean()) ** 12 - 1)
        win_ann_vol = float(win_returns.std() * np.sqrt(12))
        win_sharpe = win_ann_ret / win_ann_vol if win_ann_vol > 0 else 0
        win_pos = int((win_returns > 0).sum())
        windows.append({
            "window": i, "start": v8_records[start]["date"], "end": v8_records[end-1]["date"],
            "n": len(win_returns), "ann_ret": win_ann_ret, "ann_vol": win_ann_vol,
            "sharpe": win_sharpe, "max_dd": win_dd, "pos_months": win_pos,
        })
        print(f"窗口{i}: {v8_records[start]['date']} ~ {v8_records[end-1]['date']} "
              f"| 年化={win_ann_ret*100:6.2f}% 波动={win_ann_vol*100:5.2f}% "
              f"Sharpe={win_sharpe:5.3f} 回撤={win_dd*100:5.2f}% 胜月={win_pos}/{len(win_returns)}")

    sharpes = [w["sharpe"] for w in windows]
    sharpe_cv = float(np.std(sharpes) / (np.mean(sharpes) + 1e-9)) if np.mean(sharpes) > 0 else 0
    stable = sharpe_cv < 0.5
    print(f"\nSharpe CV: {sharpe_cv:.4f} (目标 <0.5, V7.2: 0.71, V7.1: 0.74, V6.2: 0.55, {'达标' if stable else '未达标'})")

    # === 2024-06 验证 ===
    print(f"\n{'-'*70}")
    print("V8 关键验证: 2024-06 月 (bull regime 崩盘月)")
    print(f"{'-'*70}")
    jun = [r for r in v8_records if r["date"].startswith("2024-06")][0]
    [r for r in records if r["date"].startswith("2024-06")][0]
    print(f"2024-06 组合收益: {jun['portfolio_return']*100:.2f}%  [V7.2: -5.26%, V7.1: -5.29%]")
    print(f"V8 α={jun['v8_alpha']:.2f} (rolling_ic={jun['v8_rolling_ic']:+.4f}, regime={jun['market_regime']['regime']})")
    if mode == "blend":
        print(f"等权基线收益: {jun['v8_equal_weight']*100:.2f}%/股")
    else:
        print(f"敞口因子: {jun['v8_alpha']:.2f} (总敞口缩放至 {jun['v8_alpha']*100:.0f}%)")

    # === 极端月份 ===
    print(f"\n{'-'*70}")
    print("极端月份依赖分析")
    print(f"{'-'*70}")
    sorted_rets = returns_series.sort_values(ascending=False)
    top2_idx = sorted_rets.iloc[:2].index
    top2_dates = [v8_records[i]["date"] for i in top2_idx]
    top2_returns = sorted_rets.iloc[:2].tolist()
    returns_without_top2 = returns_series.drop(top2_idx)
    ann_ret_without = float((1 + returns_without_top2.mean()) ** 12 - 1)
    print(f"最高2个月份: {top2_dates} = {[f'{r*100:.2f}%' for r in top2_returns]}")
    print(f"去除最高2月后年化: {ann_ret_without*100:.2f}% (目标 >=8%, V7.2: 7.22%, V7.1: 6.90%)")

    # === 保存 ===
    output_path = Path("output/validation_reports")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = {
        "timestamp": datetime.now().isoformat(),
        "method": "dynamic_weight_fusion_recompute",
        "data_source": str(v71_file),
        "v8_params": {
            "mode": mode,
            "ic_window": ic_window, "regime_factor": regime_factor,
            "ic_high": ic_high, "ic_mid": ic_mid, "ic_low": ic_low, "ic_vlow": ic_vlow,
        },
        "period": f"{records[0]['date']}~{records[-1]['date']}",
        "n_months": n,
        "annual_return": ann_ret, "annual_volatility": ann_vol,
        "max_drawdown": max_dd, "sharpe_ratio": sharpe,
        "win_rate": win_rate, "skewness": skew, "kurtosis": kurt,
        "dsr_results": dsr_results, "dsr_max_pass_n_trials": max_pass,
        "walk_forward": {"windows": windows, "sharpe_cv": sharpe_cv, "stable": stable},
        "extreme_months_removed": {
            "dates": top2_dates, "returns": top2_returns,
            "annual_return_without": ann_ret_without,
        },
        "v8_changes": v8_changes,
        "monthly_ic": monthly_ic,
        "comparison": {
            "V6.2": {"sharpe_cv": 0.55, "dsr_max_pass": 3, "ann_ret": 0.1435, "sharpe": 1.092, "max_dd": 0.079, "kurt": 6.86},
            "V7.1": {"sharpe_cv": 0.74, "dsr_max_pass": 3, "ann_ret": 0.1423, "sharpe": 1.110, "max_dd": 0.084, "kurt": 4.82},
            "V7.2": {"sharpe_cv": 0.71, "dsr_max_pass": 5, "ann_ret": 0.1344, "sharpe": 1.224, "max_dd": 0.083, "kurt": 3.96},
            "V8": {"sharpe_cv": sharpe_cv, "dsr_max_pass": max_pass, "ann_ret": ann_ret, "sharpe": sharpe, "max_dd": max_dd, "kurt": kurt},
        },
    }
    result_file = output_path / f"dsr_walkforward_{label}_{ts}.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {result_file}")

    return result


if __name__ == "__main__":
    # === 敏感性分析: 测试多组参数 ===
    configs = [
        # (label, mode, ic_window, regime_factor, ic_high, ic_mid, ic_low, ic_vlow)
        # V8b 原始 (激进)
        ("v8b_aggressive", "exposure", 3, 0.80, 0.15, 0.05, -0.05, -0.15),
        # V8c 温和敞口 (减少 upside 牺牲幅度)
        ("v8c_gentle", "exposure", 3, 0.80, 0.10, 0.03, -0.03, -0.10),
        # V8d 短窗口 (更快响应 IC 变化)
        ("v8d_short_window", "exposure", 2, 0.80, 0.15, 0.05, -0.05, -0.15),
        # V8e 极温和 (仅极端 IC 缩仓)
        ("v8e_minimal", "exposure", 3, 0.80, 0.20, 0.10, -0.10, -0.20),
    ]

    results_summary = []
    for label, mode, ic_w, rf, ih, im, il, iv in configs:
        print("\n" + "#" * 70)
        print(f"# 敏感性分析: {label}")
        print(f"# mode={mode}, ic_window={ic_w}, ic阈值={ih}/{im}/{il}/{iv}")
        print("#" * 70)
        result = run_v8(
            mode=mode, ic_window=ic_w, regime_factor=rf,
            ic_high=ih, ic_mid=im, ic_low=il, ic_vlow=iv,
            label=label,
        )
        results_summary.append({
            "label": label,
            "params": f"win={ic_w}, ic={ih}/{im}/{il}/{iv}",
            "ann_ret": result["annual_return"],
            "sharpe": result["sharpe_ratio"],
            "max_dd": result["max_drawdown"],
            "kurt": result["kurtosis"],
            "sharpe_cv": result["walk_forward"]["sharpe_cv"],
            "dsr_pass": result["dsr_max_pass_n_trials"],
            "w0_sharpe": result["walk_forward"]["windows"][0]["sharpe"],
            "w1_sharpe": result["walk_forward"]["windows"][1]["sharpe"],
            "w2_sharpe": result["walk_forward"]["windows"][2]["sharpe"],
        })

    # === 汇总对比 ===
    print("\n" + "=" * 90)
    print("V8 敏感性分析汇总")
    print("=" * 90)
    print(f"{'版本':<22} {'参数':<24} {'年化':>7} {'Sharpe':>7} {'CV':>6} {'DSR':>5} {'W0':>6} {'W1':>6} {'W2':>6} {'峰度':>6}")
    print("-" * 90)
    # 基线
    print(f"{'V6.2 (基线)':<22} {'':<24} {'14.35%':>7} {'1.092':>7} {'0.55':>6} {'3':>5} {'0.797':>6} {'0.238':>6} {'2.068':>6} {'6.86':>6}")
    print(f"{'V7.2 (5%上限)':<22} {'':<24} {'13.44%':>7} {'1.224':>7} {'0.71':>6} {'5':>5} {'1.031':>6} {'0.238':>6} {'2.303':>6} {'3.96':>6}")
    for s in results_summary:
        print(f"{s['label']:<22} {s['params']:<24} {s['ann_ret']*100:6.2f}% {s['sharpe']:7.3f} {s['sharpe_cv']:6.2f} {s['dsr_pass']:5d} {s['w0_sharpe']:6.3f} {s['w1_sharpe']:6.3f} {s['w2_sharpe']:6.3f} {s['kurt']:6.2f}")
    print("-" * 90)
    print("目标: Sharpe CV <0.5, DSR >=5")

