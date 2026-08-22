"""
信号有效性监控 — 评估QLib模型信号的实际效果
支持v3/v3.5/v4/v5版本报告
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
REPORTS_DIR = PROJECT_ROOT / "reports"
QLIB_DATA_DIR = str(PROJECT_ROOT / "qlib_data" / "cn_data")


def get_latest_qlib_report() -> Path | None:
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


def _parse_portfolio_signals(report: dict[str, Any]) -> tuple[list[Any], dict[str, int], list[dict[str, Any]]]:
    """从 portfolio_signals 字段解析信号 (v5+ 报告格式)"""
    signals = []
    dist = {"long": 0, "short": 0, "neutral": 0}
    stock_signals = []

    for _code, info in report["portfolio_signals"].items():
        # 使用 .get() 防 KeyError: 不同报告格式可能缺少部分字段
        signal = info.get("signal")
        direction = info.get("direction")
        symbol = info.get("symbol", _code)
        name = info.get("name", "")
        if signal is not None and direction is not None:
            signals.append(signal)
            if direction == "看多":
                dist["long"] += 1
            elif direction == "看空":
                dist["short"] += 1
            else:
                dist["neutral"] += 1

            stock_signals.append(
                {
                    "code": symbol,
                    "name": name,
                    "latest_signal": signal,
                    "direction": direction,
                    "rank": None,
                    "total": report.get("n_stocks", 0),
                }
            )
    return signals, dist, stock_signals


def _parse_legacy_signals(report: dict[str, Any]) -> tuple[list[Any], dict[str, int], list[dict[str, Any]]]:
    """从 signals 字段解析信号 (v3/v4 报告格式)"""
    signals_dict = report["signals"]
    signals = list(signals_dict.values())
    dist = {"long": 0, "short": 0, "neutral": 0}
    stock_signals = []

    for symbol, signal in signals_dict.items():
        # 类型验证: 跳过非数值信号 (None/str/bool 等), 防止 TypeError
        if not isinstance(signal, (int, float)) or isinstance(signal, bool):
            continue
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
    return signals, dist, stock_signals


def _parse_stock_signals_fallback(report: dict[str, Any]) -> tuple[list[Any], dict[str, int], list[dict[str, Any]]]:
    """从 stock_signals 字段解析信号 (兜底格式)"""
    stock_signals = report.get("stock_signals", [])
    signals = [s.get("latest_signal", 0) for s in stock_signals if isinstance(s, dict)]
    dist = report.get("signal_distribution", {"long": 0, "short": 0, "neutral": 0})
    return signals, dist, stock_signals


def _collect_signals_from_report(report: dict[str, Any]) -> tuple[list[Any], dict[str, int], list[dict[str, Any]]]:
    """根据报告格式分发信号解析, 返回 (signals, dist, stock_signals)"""
    if "portfolio_signals" in report:
        return _parse_portfolio_signals(report)
    if "signals" in report:
        return _parse_legacy_signals(report)
    return _parse_stock_signals_fallback(report)


def _determine_quality_rating(ic_ir: float) -> tuple[str, str]:
    """根据 IC IR 判定信号质量评级, 返回 (quality, color)"""
    if ic_ir > 0.3:
        return "优秀", "🟢"
    if ic_ir > 0.2:
        return "良好", "🟡"
    if ic_ir > 0.1:
        return "一般", "🟠"
    return "较差", "🔴"


def analyze_signal_effectiveness() -> None:
    # Q-2 修复: Windows GBK 控制台对 ✓/✗/─/🟢 等符号抛 UnicodeEncodeError, 兜底切 UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # 重定向/管道场景无 reconfigure 或不支持
        pass

    report_path = get_latest_qlib_report()
    if not report_path:
        return

    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)


    signals, dist, stock_signals = _collect_signals_from_report(report)

    total = sum(dist.values())

    # 除零保护: total=0 时显示无有效信号
    if total > 0:
        pass
    else:
        pass

    # 过滤非数值信号, 防止 np.mean/np.std/max/min 抛 TypeError
    numeric_signals = [s for s in signals if isinstance(s, (int, float)) and not isinstance(s, bool)]
    if numeric_signals:
        pass
    else:
        pass

    metrics = report.get("metrics", report)

    ic_ir = metrics.get("ic_ir", 0)
    quality, color = _determine_quality_rating(ic_ir)

    long_stocks = [s for s in stock_signals if isinstance(s, dict) and s.get("direction") == "看多"]
    short_stocks = [s for s in stock_signals if isinstance(s, dict) and s.get("direction") == "看空"]

    # GitHub 集成钩子: OpenViking 记录信号评估结果到 Agent 长期记忆 (2026-08-21)
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from quant_modules.ai_hedge_fund.openviking_memory import (
            get_openviking_memory,
            is_openviking_available,
        )
        if is_openviking_available():
            get_openviking_memory().add_context(
                agent_id="signal_monitor",
                content=f"信号有效性评估: quality={quality}, ic_ir={ic_ir:.4f}, "
                        f"total={total}, long={len(long_stocks)}, short={len(short_stocks)}",
                metadata={"report": str(report_path), "type": "signal_evaluation"},
                memory_type="decision",
            )
    except (ImportError, RuntimeError, ValueError, OSError):
        pass

    if long_stocks and short_stocks:
        # 用 .get() 防 KeyError, 过滤非数值信号
        long_signals = [s.get("latest_signal") for s in long_stocks if isinstance(s.get("latest_signal"), (int, float))]
        short_signals = [s.get("latest_signal") for s in short_stocks if isinstance(s.get("latest_signal"), (int, float))]
        if long_signals and short_signals:
            long_avg_signal = np.mean(long_signals)
            short_avg_signal = np.mean(short_signals)
            long_avg_signal - short_avg_signal

    # 过滤无 latest_signal 的条目, 防 KeyError; 用 .get() 安全访问其他字段
    valid_signals = [s for s in stock_signals if isinstance(s, dict) and "latest_signal" in s]
    for _sig in sorted(valid_signals, key=lambda x: x["latest_signal"], reverse=True):
        pass

    if ic_ir > 0.2:
        pass
    else:
        pass

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
            "mean": float(np.mean(numeric_signals)) if numeric_signals else 0,
            "std": float(np.std(numeric_signals)) if numeric_signals else 0,
            "max": float(max(numeric_signals)) if numeric_signals else 0,
            "min": float(min(numeric_signals)) if numeric_signals else 0,
        },
        "latest_signals": stock_signals,
    }

    monitor_dir = PROJECT_ROOT / "monitor_reports"
    os.makedirs(monitor_dir, exist_ok=True)
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    monitor_path = monitor_dir / f"signal_monitor_{ts}.json"
    with open(monitor_path, "w", encoding="utf-8") as f:
        json.dump(monitor_report, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    analyze_signal_effectiveness()
