"""
信号有效性监控 — 评估QLib模型信号的实际效果
支持v3/v3.5/v4/v5版本报告
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
REPORTS_DIR = PROJECT_ROOT / "reports"
QLIB_DATA_DIR = str(PROJECT_ROOT / "qlib_data" / "cn_data")


def get_latest_qlib_report():
    preferred = [
        "qlib_v7_train_*.json",
        "qlib_v9_train_*.json",
        "qlib_v8_train_*.json",
        "qlib_v6_train_*.json",
        "qlib_v5_train_*.json",
        "qlib_v4_train_*.json",
        "qlib_v3_5_fix_train_*.json",
        "qlib_v3_5_train_*.json",
        "qlib_v3_train_*.json",
    ]
    for pattern in preferred:
        reports = sorted(REPORTS_DIR.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)
        if reports:
            return reports[0]
    return None


def analyze_signal_effectiveness():
    report_path = get_latest_qlib_report()
    if not report_path:
        print("[错误] 未找到 QLib 训练报告")
        return

    print(f"[读取] {report_path}")
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    print(f"\n{'=' * 70}")
    print("信号有效性监控报告")
    print(f"{'=' * 70}")
    print(f"模型版本: {report.get('model', '未知')} (v{report.get('version', '?')})")
    print(f"报告时间: {report.get('timestamp', '')[:19]}")
    print(f"训练股票数: {report.get('n_stocks', report.get('training_pool_size', 0))}")
    print(f"测试样本: {report.get('metrics', report).get('test_samples', 0)}")
    print(f"{'=' * 70}\n")

    if "portfolio_signals" in report:
        signals = []
        dist = {"long": 0, "short": 0, "neutral": 0}
        stock_signals = []

        for _code, info in report["portfolio_signals"].items():
            if info["signal"] is not None:
                signals.append(info["signal"])
                if info["direction"] == "看多":
                    dist["long"] += 1
                elif info["direction"] == "看空":
                    dist["short"] += 1
                else:
                    dist["neutral"] += 1

                stock_signals.append(
                    {
                        "code": info["symbol"],
                        "name": info["name"],
                        "latest_signal": info["signal"],
                        "direction": info["direction"],
                        "rank": None,
                        "total": report.get("n_stocks", 0),
                    }
                )
    elif "signals" in report:
        signals_dict = report["signals"]
        signals = list(signals_dict.values())
        dist = {"long": 0, "short": 0, "neutral": 0}
        stock_signals = []

        for symbol, signal in signals_dict.items():
            direction = "看多" if signal > 0 else "看空"
            if signal > 0:
                dist["long"] += 1
            elif signal < 0:
                dist["short"] += 1
            else:
                dist["neutral"] += 1

            stock_signals.append(
                {
                    "code": symbol,
                    "name": "",
                    "latest_signal": signal,
                    "direction": direction,
                    "rank": None,
                    "total": len(signals_dict),
                }
            )
    else:
        stock_signals = report.get("stock_signals", [])
        signals = [s.get("latest_signal", 0) for s in stock_signals if isinstance(s, dict)]
        dist = report.get("signal_distribution", {"long": 0, "short": 0, "neutral": 0})

    total = sum(dist.values())

    print("--- 信号分布统计 ---")
    print(f"看多: {dist.get('long', 0)} ({dist.get('long', 0) / total * 100:.1f}%)")
    print(f"看空: {dist.get('short', 0)} ({dist.get('short', 0) / total * 100:.1f}%)")
    print(f"中性: {dist.get('neutral', 0)} ({dist.get('neutral', 0) / total * 100:.1f}%)")

    print("\n--- 信号强度统计 ---")
    if signals:
        print(f"信号均值: {np.mean(signals):.4f}")
        print(f"信号标准差: {np.std(signals):.4f}")
        print(f"信号最大值: {max(signals):.4f}")
        print(f"信号最小值: {min(signals):.4f}")

    print("\n--- IC指标分析 ---")
    metrics = report.get("metrics", report)
    print(f"整体 IC: {metrics.get('ic', metrics.get('ic_test', metrics.get('overall_ic', 0))):.4f}")
    print(f"日均 IC: {metrics.get('mean_daily_ic', metrics.get('avg_ic', 0)):.4f}")
    print(f"日均 Rank IC: {metrics.get('mean_daily_rank_ic', metrics.get('rank_ic_test', 0)):.4f}")
    print(f"IC IR: {metrics.get('ic_ir', 0):.4f}")
    print(f"IC > 0 占比: {metrics.get('ic_positive_ratio', 0):.2%}")

    print("\n--- 信号质量评估 ---")
    ic_ir = metrics.get("ic_ir", 0)
    if ic_ir > 0.3:
        quality = "优秀"
        color = "🟢"
    elif ic_ir > 0.2:
        quality = "良好"
        color = "🟡"
    elif ic_ir > 0.1:
        quality = "一般"
        color = "🟠"
    else:
        quality = "较差"
        color = "🔴"
    print(f"信号质量评级: {color} {quality}")

    print("\n--- 分位数收益分析 ---")
    long_stocks = [s for s in stock_signals if isinstance(s, dict) and s.get("direction") == "看多"]
    short_stocks = [s for s in stock_signals if isinstance(s, dict) and s.get("direction") == "看空"]

    if long_stocks and short_stocks:
        long_avg_signal = np.mean([s["latest_signal"] for s in long_stocks])
        short_avg_signal = np.mean([s["latest_signal"] for s in short_stocks])
        signal_spread = long_avg_signal - short_avg_signal
        print(f"多空信号差: {signal_spread:.4f}")
        print(f"多头平均信号: {long_avg_signal:.4f}")
        print(f"空头平均信号: {short_avg_signal:.4f}")

    print("\n--- 最新信号一览 ---")
    print(f"{'代码':<12} {'名称':<10} {'信号':>10} {'方向':>6}")
    print(f"{'─' * 45}")
    for sig in sorted(stock_signals, key=lambda x: x["latest_signal"], reverse=True):
        print(f"{sig['code']:<12} {sig['name']:<10} {sig['latest_signal']:>10.4f} {sig['direction']:>6}")

    print(f"\n{'=' * 70}")
    print("监控结论")
    print(f"{'=' * 70}")
    if ic_ir > 0.2:
        print(f"✓ 当前模型IC IR={ic_ir:.4f}，信号有效性良好")
        print("✓ 建议继续使用当前模型")
    else:
        print(f"✗ 当前模型IC IR={ic_ir:.4f}，信号有效性一般")
        print("✗ 建议考虑：增加训练样本、调整特征、优化模型参数")

    monitor_report = {
        "timestamp": pd.Timestamp.now().isoformat(),
        "model_version": f"{report.get('model', '未知')} (v{report.get('version', '?')})",
        "report_path": str(report_path),
        "quality_rating": quality,
        "metrics": {
            "ic_ir": metrics.get("ic_ir", 0),
            "mean_daily_ic": metrics.get("mean_daily_ic", metrics.get("avg_ic", 0)),
            "overall_ic": metrics.get("ic", metrics.get("ic_test", metrics.get("overall_ic", 0))),
            "ic_positive_ratio": metrics.get("ic_positive_ratio", 0),
        },
        "signal_distribution": dist,
        "signal_stats": {
            "mean": float(np.mean(signals)) if signals else 0,
            "std": float(np.std(signals)) if signals else 0,
            "max": float(max(signals)) if signals else 0,
            "min": float(min(signals)) if signals else 0,
        },
        "latest_signals": stock_signals,
    }

    monitor_dir = PROJECT_ROOT / "monitor_reports"
    os.makedirs(monitor_dir, exist_ok=True)
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    monitor_path = monitor_dir / f"signal_monitor_{ts}.json"
    with open(monitor_path, "w", encoding="utf-8") as f:
        json.dump(monitor_report, f, ensure_ascii=False, indent=2)
    print(f"\n监控报告已保存: {monitor_path}")


if __name__ == "__main__":
    analyze_signal_effectiveness()
