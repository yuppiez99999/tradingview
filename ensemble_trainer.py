#!/usr/bin/env python3
"""
两树 Ensemble 训练 CLI (T4, 2026-09-07)
========================================

对指定标的构造特征 → 两树 stacking 拟合 → 落盘 ensemble 元数据.
与 lgb_enhanced_trainer.py 平行的薄壳, 复用其数据准备链
(lgb_trainer.trainer._build_all_features: OHLCV + 技术/情绪因子).

⚠️ 依赖真实行情数据源 (Wind MCP / 新浪 HTTP), 无法离线端到端验证;
   核心算法 (Purged K-Fold + stacking) 已由 tests/unit/test_t4_ensemble_stacker.py 锁定.

使用方式:
    python ensemble_trainer.py --symbols 688041.SH,000333.SZ
    python ensemble_trainer.py --symbols 688041.SH --no-timesfm

输出:
    models/ensemble/{symbol}_ensemble_meta.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from utils.datetime_utils import now_bj

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

LABEL_HORIZON = 5  # 5 日前瞻收益 (与 embargo=5 对齐, 防泄漏)


def build_xy(df, horizon: int = LABEL_HORIZON):
    """从特征 DataFrame 构造 (X, y, feature_names, row_mask).

    y = 未来 horizon 日收益 (shift(-horizon)), 末尾 horizon 行因标签缺失丢弃 —
    严格防前视: 特征只用 t 及以前, 标签是 t+1..t+h.

    row_mask (2026-09-12 新增): 原始行的保留掩码 (与 df 行对齐), 供调用方把
    y 坐标映射回原始时间轴 (如 TimesFM 逐折滚动预测的 as-of 对齐)。
    """
    exclude = {"open", "high", "low", "close", "volume"}
    feature_cols = [c for c in df.columns if c not in exclude]
    close = df["close"].astype(float)
    y = close.pct_change(horizon).shift(-horizon).to_numpy()
    X = df[feature_cols].replace([np.inf, -np.inf], np.nan).to_numpy(dtype=float)
    mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
    return X[mask], y[mask], feature_cols, mask


def train_symbol_ensemble(symbol: str, use_news: bool = False, use_timesfm: bool = True) -> dict:
    """对单个标的训练 stacking ensemble, 返回结果 dict."""
    from lgb_trainer.trainer import _build_all_features, configure_paths

    configure_paths(PROJECT_ROOT, PROJECT_ROOT / "models" / "lgb_enhanced")

    try:
        from utils.config_manager import get_config

        config = get_config("lgb_enhanced", default={}) or {}
    except (ImportError, ValueError, OSError):
        config = {}

    symbol_code = symbol.split(".")[0]
    featured = _build_all_features([(symbol_code, symbol)], config=config, use_news=use_news)
    if not featured or symbol_code not in featured:
        return {"symbol": symbol, "success": False, "error": "no_feature_data"}

    X, y, feature_names, row_mask = build_xy(featured[symbol_code])
    if len(y) < 100:
        return {"symbol": symbol, "success": False, "error": f"insufficient_samples: {len(y)}"}

    # TimesFM meta 特征 (可选): 逐折滚动起源预测 (P0 修复, 2026-09-12)
    #
    # 原实现: 用**完整价格序列** (含所有训练样本标签期之后的数据) 对"今天"做
    # 一次预测, 再把这一个未来值广播给全部历史样本 (timesfm_col = full(len(y)))
    # — 每个历史样本都携带未来截面信息 (前视偏差), 且常数列被 stacker 拒绝。
    #
    # 现实现: 与 stacker 同一套 Purged K-Fold 折, 对每个验证折用**折前数据**
    # 做一次 point-in-time 预测, 填充该折的样本行 — 每个折的预测只使用该时点
    # 之前的价格, 与实盘推理一致。
    timesfm_col = None
    timesfm_folds_covered = 0
    if use_timesfm:
        try:
            from utils.alpha.ensemble_stacker import purged_kfold_indices
            from utils.timesfm_predictor import TimesFMPredictor

            predictor = TimesFMPredictor()
            if predictor.preflight_check():
                closes = featured[symbol_code]["close"].astype(float).to_numpy()
                kept_rows = np.flatnonzero(row_mask)  # y 坐标 → 原始行坐标
                n_timesfm = len(y)
                timesfm_col = np.full(n_timesfm, np.nan)
                folds = purged_kfold_indices(n_timesfm, n_splits=5, embargo=LABEL_HORIZON)
                for _train_idx, val_idx in folds:
                    asof_row = int(kept_rows[val_idx[0]])  # 折首样本的原始行
                    if asof_row < 30:  # TimesFM 最低历史长度
                        continue
                    hist = closes[:asof_row]
                    base = float(hist[-1])
                    try:
                        fc = predictor.forecast(hist, horizon=LABEL_HORIZON)
                        pred_ret = (float(fc.point_forecast[-1]) - base) / base
                    except (ImportError, ValueError, OSError, RuntimeError) as e:
                        print(f"[WARN] TimesFM 逐折预测失败 (折内填 NaN): {e}")
                        continue
                    timesfm_col[val_idx] = pred_ret
                    timesfm_folds_covered += 1
                covered = int(np.isfinite(timesfm_col).sum())
                if covered == 0:
                    timesfm_col = None  # 全部失败 → 不注入该列 (fail-open)
        except (ImportError, ValueError, OSError, RuntimeError) as e:
            print(f"[WARN] TimesFM meta 特征不可用, 跳过 (fail-open): {e}")
            timesfm_col = None

    from utils.alpha.ensemble_stacker import stacked_ensemble_fit

    fit = stacked_ensemble_fit(X, y, n_splits=5, embargo=LABEL_HORIZON, timesfm_col=timesfm_col)

    out_dir = PROJECT_ROOT / "models" / "ensemble"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "symbol": symbol,
        "generated_at": now_bj().isoformat(),
        "n_samples": int(fit["n_samples"]),
        "n_features": int(fit["n_features"]),
        "feature_names": feature_names,
        "n_splits": fit["n_splits"],
        "embargo": fit["embargo"],
        "label_horizon": LABEL_HORIZON,
        "oof_ic": fit["oof_ic"],
        "ridge_weights": fit["ridge_weights"],
        "single_model": fit["single_model"],
        "timesfm_used": fit["timesfm_used"],
        "timesfm_folds_covered": timesfm_folds_covered,
    }
    out_path = out_dir / f"{symbol}_ensemble_meta.json"
    out_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"symbol": symbol, "success": True, "meta_path": str(out_path), "oof_ic": fit["oof_ic"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="两树 Ensemble 训练 (T4)")
    parser.add_argument("--symbols", type=str, required=True, help="标的 (逗号分隔)")
    parser.add_argument("--no-news", action="store_true", help="跳过新闻因子")
    parser.add_argument("--no-timesfm", action="store_true", help="跳过 TimesFM meta 特征")
    args = parser.parse_args()

    results = []
    for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        print(f"[Ensemble] 训练 {symbol} ...")
        r = train_symbol_ensemble(symbol, use_news=not args.no_news, use_timesfm=not args.no_timesfm)
        results.append(r)
        print(f"  -> {'OK' if r['success'] else 'FAIL'} {r.get('oof_ic', r.get('error', ''))}")

    failed = sum(1 for r in results if not r["success"])
    sys.exit(1 if failed == len(results) and results else 0)


if __name__ == "__main__":
    main()
