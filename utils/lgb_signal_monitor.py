"""
LGB 增强信号实盘监控 + 阈值优化分析
=====================================

功能:
    1. record_lgb_application: 每次交易执行后记录LGB信号应用详情
       - 写入 logs/lgb_signal_applications.jsonl (每行一个事件)
       - 字段: trade_date, timestamp, code, name, lgb_signal, lgb_multiplier,
              original_shares, final_shares, direction(boost/cut/neutral)
    2. analyze_lgb_history: 分析历史应用日志, 输出统计报告
       - 总执行次数, 总boost/cut/neutral数
       - 各标的的boost/cut分布
       - 各乘数档位的使用频率
       - 阈值优化建议
    3. CLI: python lgb_signal_monitor.py --analyze [days] 输出分析报告

阈值优化逻辑:
    - boost_ratio > 50%  → 建议提高boost阈值 (signal门槛 0.05→0.10)
    - cut_ratio > 40%    → 建议收紧cut阈值 (signal门槛 -0.05→-0.10)
    - neutral_ratio > 70% → 建议降低门槛让信号更敏感
    - 根据各档位频率分布给出具体调整建议
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ============================================================
# 路径配置
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
REPORTS_DIR = BASE_DIR / "reports" / "lgb_enhanced"

LOG_FILE = LOG_DIR / "lgb_signal_applications.jsonl"

for d in [LOG_DIR, REPORTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("lgb_monitor")


# ============================================================
# 1. 记录 LGB 信号应用
# ============================================================
def record_lgb_application(
    trade_date: str,
    orders: list[dict[str, Any]],
    lgb_signals: dict[str, dict[str, Any]],
    boost_count: int = 0,
    cut_count: int = 0,
) -> int:
    """记录一次LGB信号应用事件

    Args:
        trade_date: 交易日期 YYYY-MM-DD
        orders: 订单列表 (已应用LGB乘数后的订单)
        lgb_signals: LGB信号字典 {code: {"signal": float, "quality_flag": str, "name": str}}
        boost_count: 本次加仓订单数
        cut_count: 本次减仓订单数

    Returns:
        记录的事件数
    """
    timestamp = datetime.now().isoformat(timespec="seconds")
    events_written = 0

    # 汇总事件
    summary_event = {
        "type": "summary",
        "trade_date": trade_date,
        "timestamp": timestamp,
        "total_orders": len(orders),
        "boost_count": int(boost_count),
        "cut_count": int(cut_count),
        "neutral_count": len(orders) - int(boost_count) - int(cut_count),
        "lgb_signals_loaded": len(lgb_signals),
    }
    _append_jsonl(summary_event)
    events_written += 1

    # 每个订单的事件
    for order in orders:
        code = str(order.get("code", ""))
        lgb_info = lgb_signals.get(code, {}) if lgb_signals else {}
        lgb_signal = lgb_info.get("signal")
        lgb_mult = order.get("lgb_multiplier")

        if lgb_signal is None and lgb_mult is None:
            continue  # 未应用LGB信号的订单不记录

        direction = "neutral"
        if lgb_mult is not None:
            if float(lgb_mult) > 1.0:
                direction = "boost"
            elif float(lgb_mult) < 1.0:
                direction = "cut"

        event = {
            "type": "order",
            "trade_date": trade_date,
            "timestamp": timestamp,
            "code": code,
            "name": str(order.get("name", "")),
            "lgb_signal": (
                round(float(lgb_signal), 4) if lgb_signal is not None else None
            ),
            "lgb_multiplier": float(lgb_mult) if lgb_mult is not None else None,
            "quality_flag": lgb_info.get("quality_flag", "OK"),
            "direction": direction,
            "original_shares": int(
                order.get("original_shares", order.get("shares", 0))
            ),
            "final_shares": int(order.get("shares", 0)),
            "est_price": float(order.get("est_price", 0)),
            "est_amount": float(order.get("est_amount", 0)),
        }
        _append_jsonl(event)
        events_written += 1

    logger.info(
        "LGB监控记录完成: %s, %d个订单, boost=%d, cut=%d",
        trade_date,
        len(orders),
        boost_count,
        cut_count,
    )
    return events_written


def _append_jsonl(event: dict[str, Any]) -> None:
    """追加事件到JSONL文件"""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:  # P2 模块 fail-safe, 待后续精确化
        logger.error("写入LGB监控日志失败: %s", e)


# ============================================================
# 2. 分析历史日志
# ============================================================
def load_history(days: int = 30) -> list[dict[str, Any]]:
    """加载历史日志

    Args:
        days: 最近N天 (0=全部)

    Returns:
        事件列表
    """
    if not LOG_FILE.exists():
        return []

    events = []
    cutoff_date = None
    if days > 0:
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if cutoff_date and event.get("trade_date", "") < cutoff_date:
                        continue
                    events.append(event)
                except json.JSONDecodeError:
                    continue
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:  # P2 模块 fail-safe, 待后续精确化
        logger.error("读取LGB监控日志失败: %s", e)
        return []

    return events


def analyze_lgb_history(days: int = 30) -> dict[str, Any]:
    """分析历史日志, 输出统计报告

    Args:
        days: 分析最近N天 (0=全部)

    Returns:
        分析结果字典
    """
    events = load_history(days=days)
    if not events:
        return {
            "empty": True,
            "message": f"最近{days}天无LGB信号应用记录",
            "log_file": str(LOG_FILE),
        }

    # 分离 summary 和 order 事件
    summaries = [e for e in events if e.get("type") == "summary"]
    orders = [e for e in events if e.get("type") == "order"]

    # 基本统计
    trade_dates = sorted(set(e.get("trade_date", "") for e in summaries))
    total_executions = len(summaries)
    total_boost = sum(e.get("boost_count", 0) for e in summaries)
    total_cut = sum(e.get("cut_count", 0) for e in summaries)
    total_neutral = sum(e.get("neutral_count", 0) for e in summaries)
    total_orders = sum(e.get("total_orders", 0) for e in summaries)

    # 乘数分布
    multiplier_dist: Counter[Any] = Counter()
    for o in orders:
        mult = o.get("lgb_multiplier")
        if mult is not None:
            multiplier_dist[round(float(mult), 2)] += 1

    # 信号分布 (按档位)
    signal_buckets = {
        "strong_bull (>=0.3)": 0,
        "mid_bull (0.15~0.3)": 0,
        "weak_bull (0.05~0.15)": 0,
        "neutral (-0.05~0.05)": 0,
        "weak_bear (-0.15~-0.05)": 0,
        "strong_bear (<-0.15)": 0,
    }
    for o in orders:
        sig = o.get("lgb_signal")
        if sig is None:
            continue
        sig = float(sig)
        if sig >= 0.3:
            signal_buckets["strong_bull (>=0.3)"] += 1
        elif sig >= 0.15:
            signal_buckets["mid_bull (0.15~0.3)"] += 1
        elif sig >= 0.05:
            signal_buckets["weak_bull (0.05~0.15)"] += 1
        elif sig >= -0.05:
            signal_buckets["neutral (-0.05~0.05)"] += 1
        elif sig >= -0.15:
            signal_buckets["weak_bear (-0.15~-0.05)"] += 1
        else:
            signal_buckets["strong_bear (<-0.15)"] += 1

    # 按标的统计
    per_symbol_stats: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"boost": 0, "cut": 0, "neutral": 0, "total": 0}
    )
    for o in orders:
        code = o.get("code", "")
        direction = o.get("direction", "neutral")
        per_symbol_stats[code][direction] += 1
        per_symbol_stats[code]["total"] += 1

    # 按日期统计
    per_date_stats: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"boost": 0, "cut": 0, "neutral": 0, "total": 0}
    )
    for s in summaries:
        date = s.get("trade_date", "")
        per_date_stats[date]["boost"] = s.get("boost_count", 0)
        per_date_stats[date]["cut"] = s.get("cut_count", 0)
        per_date_stats[date]["neutral"] = s.get("neutral_count", 0)
        per_date_stats[date]["total"] = s.get("total_orders", 0)

    # 质量标志分布
    quality_dist = Counter(o.get("quality_flag", "OK") for o in orders)

    # 计算比例
    boost_ratio = total_boost / total_orders if total_orders > 0 else 0
    cut_ratio = total_cut / total_orders if total_orders > 0 else 0
    neutral_ratio = total_neutral / total_orders if total_orders > 0 else 0

    # 阈值优化建议
    suggestions = _generate_threshold_suggestions(
        boost_ratio=boost_ratio,
        cut_ratio=cut_ratio,
        neutral_ratio=neutral_ratio,
        signal_buckets=signal_buckets,
        multiplier_dist=multiplier_dist,
        total_orders=total_orders,
    )

    return {
        "empty": False,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "log_file": str(LOG_FILE),
        "days_analyzed": days,
        "trade_dates": trade_dates,
        "total_executions": total_executions,
        "total_orders": total_orders,
        "total_boost": total_boost,
        "total_cut": total_cut,
        "total_neutral": total_neutral,
        "boost_ratio": round(boost_ratio, 4),
        "cut_ratio": round(cut_ratio, 4),
        "neutral_ratio": round(neutral_ratio, 4),
        "signal_buckets": dict(signal_buckets),
        "multiplier_dist": dict(sorted(multiplier_dist.items())),
        "per_symbol_stats": dict(per_symbol_stats),
        "per_date_stats": dict(per_date_stats),
        "quality_dist": dict(quality_dist),
        "suggestions": suggestions,
    }


def _generate_threshold_suggestions(
    boost_ratio: float,
    cut_ratio: float,
    neutral_ratio: float,
    signal_buckets: dict[str, int],
    multiplier_dist: dict[float, int],
    total_orders: int,
) -> list[str]:
    """根据统计数据生成阈值优化建议

    当前阈值表 (lgb_enhanced_trainer.py 中的 _lgb_confidence_multiplier):
        >= 0.3  + OK  → 1.08 (强看涨)
        >= 0.15 + OK  → 1.04 (中看涨)
        >= 0.05 + OK  → 1.00 (弱看涨, 中性)
        >= -0.05      → 0.98 (中性)
        >= -0.15      → 0.92 (弱看跌)
        < -0.15       → 0.85 (强看跌)
    """
    suggestions = []

    # 1. boost 比例过高 → 信号过于乐观, 建议提高门槛
    if boost_ratio > 0.5:
        suggestions.append(
            f"⚠️ boost比例过高 ({boost_ratio:.1%} > 50%): "
            f"信号过于乐观, 建议将强看涨门槛从 0.05 提高到 0.10, "
            f"中看涨门槛从 0.15 提高到 0.20"
        )
    elif boost_ratio > 0.35:
        suggestions.append(
            f"✓ boost比例偏高 ({boost_ratio:.1%}): 可考虑微调强看涨门槛 0.05→0.08, 保持其他不变"
        )

    # 2. cut 比例过高 → 风控触发过于频繁
    if cut_ratio > 0.4:
        suggestions.append(
            f"⚠️ cut比例过高 ({cut_ratio:.1%} > 40%): "
            f"风控触发过于频繁, 建议将弱看跌门槛从 -0.05 收紧到 -0.10, "
            f"强看跌门槛从 -0.15 收紧到 -0.20"
        )
    elif cut_ratio > 0.25:
        suggestions.append(
            f"✓ cut比例偏高 ({cut_ratio:.1%}): 可考虑微调弱看跌门槛 -0.05→-0.08"
        )

    # 3. 中性比例过高 → 信号过于保守, 建议降低门槛
    if neutral_ratio > 0.7:
        suggestions.append(
            f"⚠️ 中性比例过高 ({neutral_ratio:.1%} > 70%): "
            f"信号过于保守, 建议扩大boost/cut区间: "
            f"将中性区间从 [-0.05, 0.05] 收窄到 [-0.03, 0.03], "
            f"让更多订单进入boost/cut档位"
        )

    # 4. 强看涨/强看跌为零 → 信号幅度不够
    strong_bull = signal_buckets.get("strong_bull (>=0.3)", 0)
    strong_bear = signal_buckets.get("strong_bear (<-0.15)", 0)
    if strong_bull == 0 and total_orders > 50:
        suggestions.append(
            "⚠️ 强看涨信号(>=0.3)出现次数为0: "
            "LGB信号幅度可能不够, 建议检查信号生成逻辑, "
            "或考虑将强看涨门槛从 0.3 降低到 0.25"
        )
    if strong_bear == 0 and total_orders > 50:
        suggestions.append(
            "⚠️ 强看跌信号(<-0.15)出现次数为0: LGB信号幅度可能不够, 建议将强看跌门槛从 -0.15 提高到 -0.10"
        )

    # 5. 单一乘数占比过高 → 阈值划分不合理
    if multiplier_dist:
        max_mult_count = max(multiplier_dist.values())
        max_mult_ratio = max_mult_count / sum(multiplier_dist.values())
        if max_mult_ratio > 0.6:
            max_mult = max(multiplier_dist, key=lambda k: multiplier_dist.get(k, 0))
            suggestions.append(
                f"⚠️ 单一乘数 {max_mult} 占比过高 ({max_mult_ratio:.1%} > 60%): 阈值划分过于集中, 建议重新分配乘数档位"
            )

    # 6. 样本数太少
    if total_orders < 30:
        suggestions.append(
            f"ℹ️ 样本数较少 ({total_orders} < 30): 建议累积更多交易日后再次分析, 当前建议仅供参考"
        )

    if not suggestions:
        suggestions.append(
            f"✓ 当前阈值配置合理: boost={boost_ratio:.1%}, cut={cut_ratio:.1%}, neutral={neutral_ratio:.1%}, 无优化建议"
        )

    return suggestions


# ============================================================
# 3. 生成 Markdown 报告
# ============================================================
def generate_analysis_report(analysis: dict[str, Any]) -> str:
    """生成 Markdown 分析报告"""
    if analysis.get("empty"):
        return f"# LGB信号实盘监控报告\n\n**分析时间**: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n{analysis.get('message', '无数据')}\n\n**日志文件**: `{analysis.get('log_file', LOG_FILE)}`\n"

    lines = [
        "# LGB信号实盘监控报告",
        "",
        f"**分析时间**: {analysis['analysis_date']}",
        f"**分析周期**: 最近 {analysis['days_analyzed']} 天 ({len(analysis['trade_dates'])} 个交易日)",
        f"**日志文件**: `{analysis['log_file']}`",
        "",
        "## 一、总体统计",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 执行次数 | {analysis['total_executions']} |",
        f"| 总订单数 | {analysis['total_orders']} |",
        f"| LGB加仓 | {analysis['total_boost']} ({analysis['boost_ratio']:.1%}) |",
        f"| LGB减仓 | {analysis['total_cut']} ({analysis['cut_ratio']:.1%}) |",
        f"| LGB中性 | {analysis['total_neutral']} ({analysis['neutral_ratio']:.1%}) |",
        "",
        "## 二、信号档位分布",
        "",
        "| 档位 | 次数 | 占比 |",
        "|------|------|------|",
    ]

    total_signal = sum(analysis["signal_buckets"].values())
    for bucket, count in analysis["signal_buckets"].items():
        ratio = count / total_signal if total_signal > 0 else 0
        lines.append(f"| {bucket} | {count} | {ratio:.1%} |")

    lines.extend(
        [
            "",
            "## 三、乘数分布",
            "",
            "| 乘数 | 次数 | 占比 |",
            "|------|------|------|",
        ]
    )
    total_mult = sum(analysis["multiplier_dist"].values())
    for mult, count in sorted(analysis["multiplier_dist"].items()):
        ratio = count / total_mult if total_mult > 0 else 0
        lines.append(f"| {mult} | {count} | {ratio:.1%} |")

    lines.extend(
        [
            "",
            "## 四、按标的统计",
            "",
            "| 标的代码 | 加仓 | 减仓 | 中性 | 总数 | 加仓占比 |",
            "|----------|------|------|------|------|----------|",
        ]
    )
    for code, stats in sorted(analysis["per_symbol_stats"].items()):
        boost_ratio = stats["boost"] / stats["total"] if stats["total"] > 0 else 0
        lines.append(
            f"| {code} | {stats['boost']} | {stats['cut']} | {stats['neutral']} | {stats['total']} | {boost_ratio:.1%} |"
        )

    lines.extend(
        [
            "",
            "## 五、按日期统计",
            "",
            "| 日期 | 加仓 | 减仓 | 中性 | 总数 |",
            "|------|------|------|------|------|",
        ]
    )
    for date, stats in sorted(analysis["per_date_stats"].items()):
        lines.append(
            f"| {date} | {stats['boost']} | {stats['cut']} | {stats['neutral']} | {stats['total']} |"
        )

    lines.extend(
        [
            "",
            "## 六、质量标志分布",
            "",
            "| 质量标志 | 次数 |",
            "|----------|------|",
        ]
    )
    for flag, count in analysis["quality_dist"].items():
        lines.append(f"| {flag} | {count} |")

    lines.extend(
        [
            "",
            "## 七、阈值优化建议",
            "",
        ]
    )
    for i, s in enumerate(analysis["suggestions"], 1):
        lines.append(f"{i}. {s}")

    lines.extend(
        [
            "",
            "## 八、当前阈值表 (参考)",
            "",
            "| 信号范围 | 质量标志 | 乘数 | 说明 |",
            "|----------|----------|------|------|",
            "| >= 0.3 | OK | 1.08 | 强看涨 +8% |",
            "| >= 0.15 | OK | 1.04 | 中看涨 +4% |",
            "| >= 0.05 | OK | 1.00 | 弱看涨 中性 |",
            "| >= -0.05 | - | 0.98 | 中性 -2% |",
            "| >= -0.15 | - | 0.92 | 弱看跌 -8% |",
            "| < -0.15 | - | 0.85 | 强看跌 -15% 风控 |",
            "| 任意 | LOW_QUALITY | 1.00 | 忽略 |",
            "",
            "---",
            f"**报告路径**: `{REPORTS_DIR / f'lgb_monitor_report_{datetime.now():%Y%m%d}.md'}`",
        ]
    )

    return "\n".join(lines)


# ============================================================
# 4. CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="LGB增强信号实盘监控 + 阈值优化分析")
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="分析历史日志并生成报告",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="分析最近N天 (默认30, 0=全部)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="生成Markdown报告文件",
    )
    args = parser.parse_args()

    if args.analyze:
        analysis = analyze_lgb_history(days=args.days)
        report = generate_analysis_report(analysis)

        if args.report:
            report_file = REPORTS_DIR / f"lgb_monitor_report_{datetime.now():%Y%m%d}.md"
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(report)
            logger.info(f"✓ 报告已生成: {report_file}")
        else:
            logger.info(report)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
