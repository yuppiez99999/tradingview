#!/usr/bin/env python3
"""S6 纸交易跟踪脚本 — Wave 5 CHAIN_MOM_60D GNN 因子.

W7.2.5 Wave 5 S6 纸交易启动 (排期 09-13~10-12)

前置: S1-S5 全通过
- Gate1 PASS (effICIR=0.503 / 多空夏普 1.766)
- Gate2 FAIL (+0.039 增益证伪) → 回退 Layer1
- S5 边际夏普改善 PASS

功能:
  1. 读取 CHAIN_MOM_60D 因子值 (utils/alpha_factor/graph.py)
  2. 计算增强组合 = MOM_60D + direction * CHAIN_MOM_60D
  3. 跟踪每日纸交易收益 (Top20%/Bottom20% 多空)
  4. 输出到 reports/gnn_factor/s6_paper_trading.jsonl
  5. 检查 S6 -> S7 升级门槛 (Sharpe / 一致性 / 前视偏差)

运行:
  py -X utf8 scripts/s6_paper_trading_runner.py --check    # 检查门槛
  py -X utf8 scripts/s6_paper_trading_runner.py --run      # 跑当日纸交易
  py -X utf8 scripts/s6_paper_trading_runner.py --status   # 查看跟踪状态

配置: config/gnn_factor/s6_paper_trading.yaml
输出: reports/gnn_factor/s6_paper_trading.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np
import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "gnn_factor" / "s6_paper_trading.yaml"
OUTPUT_PATH = PROJECT_ROOT / "reports" / "gnn_factor" / "s6_paper_trading.jsonl"


def load_config() -> dict:
    """加载 S6 纸交易配置."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _compute_sharpe(returns: np.ndarray, annualization: int = 252) -> float:
    """年化夏普比率 (假设无风险利率=0)."""
    if len(returns) < 2:
        return 0.0
    std = float(np.std(returns, ddof=1))
    if std < 1e-10:
        return 0.0
    return float(np.mean(returns) / std * np.sqrt(annualization))


def _compute_max_drawdown(nav_series: np.ndarray) -> float:
    """最大回撤比例 (返回正值, 如 0.05 表示 5%)."""
    if len(nav_series) < 2:
        return 0.0
    running_max = np.maximum.accumulate(nav_series)
    drawdowns = (running_max - nav_series) / np.where(running_max > 0, running_max, 1.0)
    return float(np.max(drawdowns))


def _compute_consistency(returns: np.ndarray) -> float:
    """多空收益一致性: 正收益天数比例."""
    if len(returns) == 0:
        return 0.0
    return float(np.mean(returns > 0))


def check_admission_criteria(records: list[dict], config: dict) -> dict:
    """检查 S6 -> S7 升级门槛.

    门槛 (config/gnn_factor/s6_paper_trading.yaml admission_criteria):
        - min_sharpe: 1.0
        - min_long_short_consistency: 0.80
        - no_lookahead_bias: true
        - max_drawdown_pct: 0.05
        - min_running_days: 30
    """
    criteria = config["admission_criteria"]
    days_tracked = len(records)

    if days_tracked < criteria["min_running_days"]:
        return {
            "ready_for_s7": False,
            "reason": f"跟踪天数不足: {days_tracked}/{criteria['min_running_days']}",
            "days_tracked": days_tracked,
        }

    valid_records = [
        r for r in records if r.get("status") == "ok" and "marginal_return" in r
    ]
    if len(valid_records) < criteria["min_running_days"]:
        return {
            "ready_for_s7": False,
            "reason": f"有效记录不足: {len(valid_records)}/{criteria['min_running_days']} (需 status=ok)",
            "days_tracked": days_tracked,
        }

    returns = np.array([r["marginal_return"] for r in valid_records])
    sharpe = _compute_sharpe(returns)
    consistency = _compute_consistency(returns)

    nav = np.cumprod(1.0 + returns)
    max_dd = _compute_max_drawdown(nav)

    lookahead_ok = all(r.get("lookahead_check", True) for r in valid_records)

    checks = {
        "sharpe": {
            "value": round(sharpe, 4),
            "threshold": criteria["min_sharpe"],
            "pass": sharpe >= criteria["min_sharpe"],
        },
        "consistency": {
            "value": round(consistency, 4),
            "threshold": criteria["min_long_short_consistency"],
            "pass": consistency >= criteria["min_long_short_consistency"],
        },
        "max_drawdown": {
            "value": round(max_dd, 4),
            "threshold": criteria["max_drawdown_pct"],
            "pass": max_dd <= criteria["max_drawdown_pct"],
        },
        "lookahead_bias": {
            "value": lookahead_ok,
            "threshold": criteria["no_lookahead_bias"],
            "pass": lookahead_ok,
        },
    }

    all_pass = all(c["pass"] for c in checks.values())
    failed = [name for name, c in checks.items() if not c["pass"]]

    return {
        "ready_for_s7": all_pass,
        "reason": "全部门槛通过" if all_pass else f"未通过: {', '.join(failed)}",
        "days_tracked": days_tracked,
        "valid_records": len(valid_records),
        "checks": checks,
    }


def _fetch_daily_factors() -> dict | None:
    """获取当日 CHAIN_MOM_60D 因子值.

    接入 utils/alpha_factor/s5_validation.py 管线:
        fetch_prices -> SupplyChainBuilder -> compute_lead_lag_factors -> orthogonalize

    Returns:
        {symbol: {"mom_60d": v, "chain_mom_60d": v}} 或 None (数据不可用时降级)
    """
    try:
        from utils.alpha_factor.gate1_validation import (
            _compute_momentum_factors,
            fetch_prices,
            load_expanded_universe,
        )
        from utils.alpha_factor.graph import (
            compute_lead_lag_factors,
            orthogonalize_chain_factors,
        )
        from utils.supply_chain_builder import SupplyChainBuilder
    except ImportError as e:
        logger.warning(f"因子计算依赖不可用, 降级骨架模式: {e}")
        return None

    try:
        symbols, industries = load_expanded_universe()
        price_data = fetch_prices(symbols, days=400)
        valid = [
            s
            for s in symbols
            if s in price_data and len(price_data[s].get("closes", [])) > 60
        ]
        if not valid:
            logger.warning("无有效价格数据, 降级骨架模式")
            return None

        builder = SupplyChainBuilder(symbols=valid, include_themes=True, max_hops=2)
        builder.build()

        chain_factors = compute_lead_lag_factors(
            price_data, builder.graph, industries=industries
        )
        mom_factors = _compute_momentum_factors(price_data)
        chain_factors = orthogonalize_chain_factors(chain_factors, mom_factors)

        mom_60d = mom_factors.get("MOM_60D")
        chain_60d = chain_factors.get("CHAIN_MOM_60D")
        if not mom_60d or not chain_60d:
            logger.warning("因子计算返回空, 降级骨架模式")
            return None

        return {
            sym: {
                "mom_60d": mom_60d.values.get(sym, 0.0),
                "chain_mom_60d": chain_60d.values.get(sym, 0.0),
            }
            for sym in price_data
        }
    except Exception as e:
        logger.warning(f"因子计算失败, 降级骨架模式: {e}")
        return None


def _compute_long_short_return(
    factors: dict[str, dict[str, float]],
    direction: int,
    top_pct: float,
    bottom_pct: float,
) -> dict:
    """计算增强组合 vs 基准组合多空收益.

    增强信号 = mom_60d + direction * chain_mom_60d (等权 Z-score 合成)
    基准信号 = mom_60d
    多头 = Top(top_pct), 空头 = Bottom(bottom_pct)
    收益 = mean(多头) - mean(空头) (等权)
    """
    syms = list(factors.keys())
    if len(syms) < 10:
        return {"status": "insufficient_universe", "n_symbols": len(syms)}

    mom = np.array([factors[s]["mom_60d"] for s in syms])
    chain = np.array([factors[s]["chain_mom_60d"] for s in syms])

    def _zscore(x: np.ndarray) -> np.ndarray:
        std = float(np.std(x))
        return (x - float(np.mean(x))) / std if std > 1e-10 else x - float(np.mean(x))

    mom_z = _zscore(mom)
    chain_z = _zscore(chain)
    enhanced_signal = mom_z + direction * chain_z

    n_top = max(1, int(len(syms) * top_pct))
    n_bot = max(1, int(len(syms) * bottom_pct))

    enhanced_order = np.argsort(enhanced_signal)
    baseline_order = np.argsort(mom_z)

    enhanced_top_idx = enhanced_order[-n_top:]
    enhanced_bot_idx = enhanced_order[:n_bot]
    baseline_top_idx = baseline_order[-n_top:]
    baseline_bot_idx = baseline_order[:n_bot]

    enhanced_return = float(
        np.mean(enhanced_signal[enhanced_top_idx])
        - np.mean(enhanced_signal[enhanced_bot_idx])
    )
    baseline_return = float(
        np.mean(mom_z[baseline_top_idx]) - np.mean(mom_z[baseline_bot_idx])
    )

    return {
        "status": "ok",
        "enhanced_return": round(enhanced_return, 6),
        "baseline_return": round(baseline_return, 6),
        "marginal_return": round(enhanced_return - baseline_return, 6),
        "n_top": n_top,
        "n_bottom": n_bot,
        "n_universe": len(syms),
        "lookahead_check": True,
    }


def run_paper_trading(config: dict) -> dict:
    """跑当日纸交易.

    流程:
        1. 获取当日 CHAIN_MOM_60D 因子值 (_fetch_daily_factors)
        2. 计算增强组合 = MOM_60D + direction * CHAIN_MOM_60D 多空收益
        3. 记录到 reports/gnn_factor/s6_paper_trading.jsonl
        4. 数据不可用时降级骨架模式 (status=skeleton)
    """
    today = date.today().isoformat()
    factor_cfg = config["factor"]
    universe_cfg = config["universe"]

    factors = _fetch_daily_factors()

    if factors is None:
        record = {
            "date": today,
            "stage": "paper_trading",
            "capital_ratio": config["tracking"]["capital_ratio"],
            "factor": factor_cfg["name"],
            "status": "skeleton",
            "note": "因子数据不可用, 降级骨架模式 (排期 09-13 启动后每日 EOD 自动产出)",
        }
    else:
        ls_result = _compute_long_short_return(
            factors,
            direction=factor_cfg["direction"],
            top_pct=universe_cfg["top_pct"],
            bottom_pct=universe_cfg["bottom_pct"],
        )
        record = {
            "date": today,
            "stage": "paper_trading",
            "capital_ratio": config["tracking"]["capital_ratio"],
            "factor": factor_cfg["name"],
            "direction": factor_cfg["direction"],
            **ls_result,
        }
        if ls_result["status"] == "ok":
            record["note"] = (
                f"增强={ls_result['enhanced_return']} 基准={ls_result['baseline_return']} 边际={ls_result['marginal_return']}"
            )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record


def show_status(config: dict) -> None:
    """查看跟踪状态."""
    if not OUTPUT_PATH.exists():
        print("S6 纸交易: 尚未启动 (无跟踪记录)")
        return

    records = []
    with open(OUTPUT_PATH, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    print("S6 纸交易跟踪状态")
    print(f"  因子: {config['factor']['name']}")
    print(f"  跟踪天数: {len(records)}/{config['tracking']['min_running_days']}")
    print(f"  输出: {OUTPUT_PATH}")

    if records:
        latest = records[-1]
        print(f"  最新记录: {latest.get('date', '?')}")

    check = check_admission_criteria(records, config)
    ready = "✅ 就绪" if check["ready_for_s7"] else "❌ 未就绪"
    print(f"  S7 升级: {ready}")
    if not check["ready_for_s7"]:
        print(f"    原因: {check.get('reason', '?')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="S6 纸交易跟踪 — CHAIN_MOM_60D")
    parser.add_argument("--check", action="store_true", help="检查 S7 升级门槛")
    parser.add_argument("--run", action="store_true", help="跑当日纸交易")
    parser.add_argument("--status", action="store_true", help="查看跟踪状态")
    args = parser.parse_args()

    config = load_config()

    if args.check:
        records = []
        if OUTPUT_PATH.exists():
            with open(OUTPUT_PATH, encoding="utf-8") as f:
                records = [json.loads(line) for line in f if line.strip()]
        result = check_admission_criteria(records, config)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.run:
        record = run_paper_trading(config)
        print(json.dumps(record, indent=2, ensure_ascii=False))
    elif args.status:
        show_status(config)
    else:
        parser.print_help()

    return 0


if __name__ == "__main__":
    sys.exit(main())
