"""Shadow 账户 daily_returns.jsonl 数据清洗脚本

识别并标记数据质量问题:
  - missing  : 缺失的交易日 (断档, 生成占位记录)
  - backtest : 回测回填数据 (source 含 backtest, 非真实交易)
  - fixed    : 修复值 (source 含 fixed, 人工或脚本修复)
  - real     : 真实市场馈送 (source 含 real_market / w13a, 真实交易数据)
  - unknown  : 无法分类

额外标记 (quality_flags):
  - zero_return : daily_return == 0.0 (可能占位或数据问题)
  - fixed_value : 带 updated_at 字段 (非原始写入, 经过修正)

输出:
  - daily_returns_cleaned.jsonl : 带质量标签的完整记录 (含缺失占位, 按日期排序)
  - cleaning_report.md          : 人类可读清洗报告

不修改原文件, 生成新文件供对比.

用法:
    python scripts/clean_shadow_returns.py
    python scripts/clean_shadow_returns.py --input reports/shadow/daily_returns.jsonl
    python scripts/clean_shadow_returns.py --start-date 2026-07-27 --end-date 2026-08-03
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj

# 路径处理 (兼容直接运行 / -m 运行)
_DIR = Path(__file__).resolve().parent
_PROJ = _DIR.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

# Windows 控制台 UTF-8 输出 (幂等)
for _name in ("stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if _stream is not None and getattr(_stream, "encoding", "").lower() != "utf-8":
        _buffer = getattr(_stream, "buffer", None)
        if _buffer is not None:
            try:
                setattr(
                    sys,
                    _name,
                    io.TextIOWrapper(_buffer, encoding="utf-8", errors="replace"),
                )
            except Exception:
                pass

logger = logging.getLogger(__name__)

# 默认路径
_DEFAULT_INPUT = _PROJ / "reports" / "shadow" / "daily_returns.jsonl"
_DEFAULT_OUTPUT_DIR = _PROJ / "reports" / "shadow"

# 2026 年 A 股已知节假日 (非周末). 观察期 07-27~08-03 无法定假日, 此列表主要
# 供后续扩展使用; 暂留空, 节假日可能被误标为 missing, 报告中会提示人工核实.
HOLIDAYS_2026: set[str] = set()


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------


def load_records(file_path: Path) -> list[dict[str, Any]]:
    """加载 jsonl 文件, 每行一个 JSON 记录.

    Args:
        file_path: jsonl 文件路径

    Returns:
        记录列表 (按文件顺序), 空文件返回 []

    Raises:
        FileNotFoundError: 文件不存在
        json.JSONDecodeError: JSON 格式错误 (跳过错误行并记录 warning)
    """
    if not file_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {file_path}")

    records: list[dict[str, Any]] = []
    with open(file_path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("第 %d 行 JSON 解析失败, 跳过: %s", line_no, exc)
    logger.info("加载 %d 条记录 from %s", len(records), file_path.name)
    return records


# ---------------------------------------------------------------------------
# 数据质量分类
# ---------------------------------------------------------------------------


def classify_quality(record: dict[str, Any]) -> tuple[str, list[str]]:
    """分类单条记录的数据质量.

    分类优先级: real > fixed > backtest > unknown
    (real 优先级最高, 因为真实市场数据即使被修复过也是真实数据)

    Args:
        record: 单条 daily_returns 记录

    Returns:
        (quality_label, quality_flags)
        - quality_label: real / backtest / fixed / unknown
        - quality_flags: 额外标记列表 (zero_return / fixed_value)
    """
    source = str(record.get("source", "")).lower()
    daily_return = record.get("daily_return")
    flags: list[str] = []

    # 额外标记: 零收益
    if daily_return is not None and float(daily_return) == 0.0:
        flags.append("zero_return")

    # 额外标记: 经过修正 (有 updated_at 说明非原始写入)
    if "updated_at" in record:
        flags.append("fixed_value")

    # 主分类 (按优先级)
    if "real_market" in source or "w13a" in source:
        return "real", flags
    if "fixed" in source:
        return "fixed", flags
    if "backtest" in source:
        return "backtest", flags
    return "unknown", flags


# ---------------------------------------------------------------------------
# 缺失日期检测
# ---------------------------------------------------------------------------


def is_trading_day(d: date) -> bool:
    """判断是否为 A 股交易日 (排除周末和已知节假日).

    Args:
        d: 日期

    Returns:
        True 为交易日, False 为非交易日

    Note:
        节假日列表 HOLIDAYS_2026 可能不全, 未覆盖的法定假日会被误判为交易日,
        从而可能把该日标为 missing. 报告中会提示人工核实.
    """
    if d.weekday() >= 5:  # 周六=5, 周日=6
        return False
    if d.isoformat() in HOLIDAYS_2026:
        return False
    return True


def detect_missing_dates(
    records: list[dict[str, Any]],
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[date]:
    """检测观察期内缺失的交易日.

    Args:
        records: 已有记录列表
        start_date: 观察期起始 (None 则用最早记录日期)
        end_date: 观察期结束 (None 则用最新记录日期或今天)

    Returns:
        缺失的交易日列表 (按日期升序)
    """
    if not records:
        return []

    # 从记录中提取日期
    existing_dates: set[date] = set()
    for r in records:
        d = _parse_date(r.get("date"))
        if d is not None:
            existing_dates.add(d)

    if not existing_dates:
        return []

    # 确定观察期范围
    if start_date is None:
        start_date = min(existing_dates)
    if end_date is None:
        end_date = max(existing_dates)
        today = date.today()
        if today > end_date:
            end_date = today  # 观察期延伸到今天

    # 遍历范围内的每个交易日, 找出缺失
    missing: list[date] = []
    current = start_date
    while current <= end_date:
        if is_trading_day(current) and current not in existing_dates:
            missing.append(current)
        current += timedelta(days=1)
    return missing


def _parse_date(value: Any) -> date | None:
    """解析日期字符串 (支持 YYYY-MM-DD 格式).

    Args:
        value: 日期值 (字符串或 date 对象)

    Returns:
        date 对象, 解析失败返回 None
    """
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# 构建清洗后记录
# ---------------------------------------------------------------------------


def build_cleaned_records(
    records: list[dict[str, Any]],
    missing_dates: list[date],
) -> list[dict[str, Any]]:
    """构建清洗后的完整记录列表 (含缺失占位, 按日期排序).

    Args:
        records: 原始记录列表
        missing_dates: 缺失日期列表

    Returns:
        清洗后的记录列表, 每条带 quality 和 quality_flags 字段
    """
    cleaned: list[dict[str, Any]] = []

    # 处理已有记录
    for r in records:
        record = dict(r)  # 浅拷贝, 不修改原数据
        quality, flags = classify_quality(record)
        record["quality"] = quality
        record["quality_flags"] = flags
        cleaned.append(record)

    # 生成缺失日期的占位记录
    for d in missing_dates:
        placeholder = {
            "date": d.isoformat(),
            "daily_return": None,
            "source": "missing",
            "quality": "missing",
            "quality_flags": ["gap_detected"],
        }
        cleaned.append(placeholder)

    # 按日期排序
    cleaned.sort(key=lambda x: _parse_date(x.get("date")) or date.min)
    return cleaned


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def write_cleaned_jsonl(records: list[dict[str, Any]], output_path: Path) -> None:
    """写清洗后的 jsonl 文件.

    Args:
        records: 清洗后的记录列表
        output_path: 输出文件路径

    Raises:
        OSError: 文件写入失败
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info("清洗后记录写入 %s (%d 条)", output_path.name, len(records))


def write_report(
    records: list[dict[str, Any]],
    missing_dates: list[date],
    output_path: Path,
    input_file: Path,
) -> None:
    """写人类可读的清洗报告 (Markdown).

    Args:
        records: 清洗后的记录列表
        missing_dates: 缺失日期列表
        output_path: 报告输出路径
        input_file: 原始输入文件路径 (用于报告中引用)
    """
    # 统计各质量类型
    stats: dict[str, int] = {}
    zero_count = 0
    fixed_count = 0
    for r in records:
        q = r.get("quality", "unknown")
        stats[q] = stats.get(q, 0) + 1
        flags = r.get("quality_flags", [])
        if "zero_return" in flags:
            zero_count += 1
        if "fixed_value" in flags:
            fixed_count += 1

    total = len(records)
    real_pct = (stats.get("real", 0) / total * 100) if total else 0

    # 日期范围
    dates = [_parse_date(r.get("date")) for r in records if _parse_date(r.get("date"))]
    date_range = f"{min(dates)} ~ {max(dates)}" if dates else "N/A"

    lines: list[str] = []
    lines.append("# Shadow 账户 daily_returns.jsonl 数据清洗报告\n")
    lines.append(f"> 生成时间: {now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 输入文件: `{input_file}`\n")

    lines.append("## 一、数据概览\n")
    lines.append("| 维度 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 日期范围 | {date_range} |")
    lines.append(f"| 总记录数 (含缺失占位) | {total} |")
    lines.append(f"| 原始记录数 | {total - len(missing_dates)} |")
    lines.append(f"| 缺失交易日 | {len(missing_dates)} |")
    lines.append(f"| 真实市场数据占比 | {real_pct:.1f}% |")
    lines.append("")

    lines.append("## 二、数据质量分类统计\n")
    lines.append("| 质量标签 | 含义 | 记录数 | 占比 |")
    lines.append("|---------|------|--------|------|")
    label_meaning = {
        "real": "真实市场馈送 (可信, 可用于决策)",
        "backtest": "回测回填 (非真实交易, 仅供参考)",
        "fixed": "修复值 (人工/脚本修正, 需核实)",
        "missing": "缺失交易日 (断档, 无数据)",
        "unknown": "无法分类 (需人工检查)",
    }
    for label in ["real", "backtest", "fixed", "missing", "unknown"]:
        count = stats.get(label, 0)
        pct = (count / total * 100) if total else 0
        meaning = label_meaning.get(label, "")
        lines.append(f"| `{label}` | {meaning} | {count} | {pct:.1f}% |")
    lines.append("")

    lines.append("## 三、额外标记统计\n")
    lines.append("| 标记 | 含义 | 记录数 |")
    lines.append("|------|------|--------|")
    lines.append(f"| `zero_return` | 日收益为 0 (可能占位或数据问题) | {zero_count} |")
    lines.append(
        f"| `fixed_value` | 带 updated_at (非原始写入, 经过修正) | {fixed_count} |"
    )
    lines.append("")

    # 逐日明细
    lines.append("## 四、逐日明细\n")
    lines.append("| 日期 | 日收益 | 质量标签 | 数据来源 | 额外标记 |")
    lines.append("|------|--------|---------|---------|---------|")
    for r in records:
        d = r.get("date", "?")
        ret = r.get("daily_return")
        ret_str = f"{ret*100:+.4f}%" if ret is not None else "—"
        q = r.get("quality", "?")
        src = r.get("source", "?")
        flags = ", ".join(r.get("quality_flags", [])) or "—"
        lines.append(f"| {d} | {ret_str} | `{q}` | {src} | {flags} |")
    lines.append("")

    # 缺失日期
    if missing_dates:
        lines.append("## 五、缺失交易日 (需补录)\n")
        lines.append("以下交易日应有数据但缺失, 建议排查原因并补录:\n")
        for d in missing_dates:
            lines.append(
                f"- **{d.isoformat()}** ({['周一','周二','周三','周四','周五','周六','周日'][d.weekday()]})"
            )
        lines.append("")
        lines.append("**补录方法**:")
        lines.append(
            "1. 排查当日权重文件 (trade_plan / strategy_plan / positions.json) 是否存在"
        )
        lines.append("2. 检查数据源 (TDX) 当日是否可用")
        lines.append("3. 重跑 `ShadowRealDataFeeder.feed_single_date(date)`")
        lines.append("4. 验证 `daily_returns.jsonl` 实际包含该日期")
        lines.append("")
    else:
        lines.append("## 五、缺失交易日\n")
        lines.append("无缺失交易日 ✅\n")

    # 风险提示
    lines.append("## 六、风险提示与清洗建议\n")
    if stats.get("real", 0) < 5:
        lines.append(
            f"- ⚠️ 真实市场数据仅 {stats.get('real', 0)} 天, **不足以计算有意义的年化收益**"
        )
        lines.append("  - 至少需要 20-30 个交易日才能算可信的年化夏普")
    if stats.get("backtest", 0) > 0:
        lines.append(
            f"- ⚠️ 有 {stats.get('backtest', 0)} 天回测回填数据, **非真实交易**, 计算实盘收益时应剔除"
        )
    if zero_count > 0:
        lines.append(
            f"- ⚠️ 有 {zero_count} 天零收益, 可能是占位值或数据问题, **需核实是否真实零收益**"
        )
    if missing_dates:
        lines.append(
            f"- ⚠️ 有 {len(missing_dates)} 天断档, **观察期数据不连续**, 影响 PSI/漂移检测可信度"
        )
    lines.append("- ℹ️ 节假日列表可能不全, 法定假日可能被误标为 missing, 需人工核实")
    lines.append("")

    lines.append("## 七、可信度评估\n")
    credible_days = stats.get("real", 0)
    if credible_days == 0:
        verdict = "❌ 不可信 — 无真实市场数据"
    elif credible_days < 5:
        verdict = "❌ 不可信 — 真实数据不足 5 天"
    elif credible_days < 20:
        verdict = "⚠️ 勉强可参考 — 真实数据不足 20 天, 统计意义有限"
    else:
        verdict = "✅ 可信 — 真实数据 ≥ 20 天, 可用于初步分析"
    lines.append(f"**当前可信度**: {verdict}\n")
    lines.append(
        f"基于真实市场数据的天数: **{credible_days} / {total - len(missing_dates)}** 天"
    )
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("清洗报告写入 %s", output_path.name)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def run_cleaning(
    input_file: Path,
    output_dir: Path,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[list[dict[str, Any]], list[date]]:
    """执行完整清洗流程.

    Args:
        input_file: 输入 jsonl 文件
        output_dir: 输出目录
        start_date: 观察期起始 (可选)
        end_date: 观察期结束 (可选)

    Returns:
        (cleaned_records, missing_dates)

    Raises:
        FileNotFoundError: 输入文件不存在
    """
    # 1. 加载
    records = load_records(input_file)

    # 2. 检测缺失
    missing_dates = detect_missing_dates(records, start_date, end_date)
    if missing_dates:
        logger.info("检测到 %d 个缺失交易日", len(missing_dates))

    # 3. 构建清洗后记录
    cleaned = build_cleaned_records(records, missing_dates)

    # 4. 输出
    output_jsonl = output_dir / "daily_returns_cleaned.jsonl"
    output_report = output_dir / "cleaning_report.md"
    write_cleaned_jsonl(cleaned, output_jsonl)
    write_report(cleaned, missing_dates, output_report, input_file)

    return cleaned, missing_dates


def main() -> None:
    """命令行入口."""
    parser = argparse.ArgumentParser(
        description="清洗 Shadow 账户 daily_returns.jsonl, 标记缺失和回测回填数据"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=_DEFAULT_INPUT,
        help=f"输入 jsonl 文件 (默认: {_DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help=f"输出目录 (默认: {_DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="观察期起始日期 YYYY-MM-DD (默认: 从数据推断)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="观察期结束日期 YYYY-MM-DD (默认: 到今天)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="启用 DEBUG 级别日志",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    start_d = date.fromisoformat(args.start_date) if args.start_date else None
    end_d = date.fromisoformat(args.end_date) if args.end_date else None

    try:
        cleaned, missing = run_cleaning(args.input, args.output_dir, start_d, end_d)
    except FileNotFoundError as exc:
        logger.error("文件不存在: %s", exc)
        sys.exit(1)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("清洗失败: %s", exc)
        sys.exit(1)

    # 控制台摘要
    stats: dict[str, int] = {}
    for r in cleaned:
        q = r.get("quality", "unknown")
        stats[q] = stats.get(q, 0) + 1
    print("\n========== 清洗完成 ==========")
    print(f"输入文件 : {args.input}")
    print(f"输出目录 : {args.output_dir}")
    print(f"总记录数 : {len(cleaned)} (含 {len(missing)} 个缺失占位)")
    print(f"质量分布 : {stats}")
    print(f"报告文件 : {args.output_dir / 'cleaning_report.md'}")
    print(f"清洗文件 : {args.output_dir / 'daily_returns_cleaned.jsonl'}")
    print("================================\n")


if __name__ == "__main__":
    main()
