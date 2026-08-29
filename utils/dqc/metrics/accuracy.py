"""准确性指标 (Accuracy) — A-01 ~ A-06.

检查点: P2 (缓存, 业务规则校验)
设计原则: 业务规则违反即阻断, 不允许明显错误数据进入因子计算
"""

from __future__ import annotations

import logging

import pandas as pd

from utils.dqc.event_types import DQCCheckpoint, DQCEvent, DQCLevel, make_event

logger = logging.getLogger("dqc.accuracy")

# 涨跌幅边界 (A股: 主板 ±10%, ST ±5%, 创业板/科创板 ±20%)
PRICE_CHANGE_LIMIT_MAIN = 0.10  # 主板 ±10%
PRICE_CHANGE_LIMIT_ST = 0.05  # ST ±5%
PRICE_CHANGE_LIMIT_GEM = 0.20  # 创业板/科创板 ±20%

# 价格异常跳变阈值 (超过此值需检查停牌)
PRICE_JUMP_THRESHOLD = 0.15


def check_accuracy(
    df: pd.DataFrame,
    checkpoint: DQCCheckpoint = DQCCheckpoint.P2_CACHE,
) -> list[DQCEvent]:
    """执行所有准确性检查 (A-01 ~ A-06).

    Args:
        df: 待检查的 DataFrame (要求含 OHLCV 字段)
        checkpoint: 检查点

    Returns:
        DQC 事件列表
    """
    events: list[DQCEvent] = []

    # A-01: 涨跌幅边界
    events.extend(_check_a01_price_change_limit(df, checkpoint))

    # A-02: OHLC 关系 (low ≤ open,close ≤ high)
    events.extend(_check_a02_ohlcl_relation(df, checkpoint))

    # A-03: 成交量非负
    events.extend(_check_a03_volume_non_negative(df, checkpoint))

    # A-04: 市值一致性 (若存在相关字段)
    events.extend(_check_a04_market_cap_consistency(df, checkpoint))

    # A-05: 价格异常跳变
    events.extend(_check_a05_price_jump(df, checkpoint))

    # A-06: 零价格检测
    events.extend(_check_a06_zero_price(df, checkpoint))

    return events


# ============================================================
# A-01: 涨跌幅边界
# ============================================================
def _check_a01_price_change_limit(
    df: pd.DataFrame,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """A-01: 涨跌幅边界 — |close/preclose - 1| 应在涨跌停范围内.

    阈值:
        主板 ≤ 11% (允许 1% 容差, 含分红除权)
        ST ≤ 6%
        创业板/科创板 ≤ 21%
    """
    events: list[DQCEvent] = []
    if df.empty or "close" not in df.columns:
        return events

    if "preclose" not in df.columns and "pre_close" not in df.columns:
        return events  # 无前收字段则跳过

    preclose_col = "preclose" if "preclose" in df.columns else "pre_close"

    # 计算涨跌幅
    df_calc = df.copy()
    df_calc["change_pct"] = df_calc["close"] / df_calc[preclose_col] - 1

    # 根据代码判断涨跌停限制 (简化版: 全部按主板 10% 检查, 超出 21% 一律 ERROR)
    threshold_main = PRICE_CHANGE_LIMIT_MAIN + 0.01  # 11% (1% 容差)
    threshold_gem = PRICE_CHANGE_LIMIT_GEM + 0.01  # 21%

    symbol_col = (
        "symbol"
        if "symbol" in df_calc.columns
        else ("code" if "code" in df_calc.columns else None)
    )
    if symbol_col is None:
        return events

    for symbol, group in df_calc.groupby(symbol_col):
        symbol_str = str(symbol)
        # 判断板块 (简化)
        code = symbol_str.split(".")[0].zfill(6)
        is_gem = code.startswith(("300", "688"))  # 创业板/科创板
        threshold = threshold_gem if is_gem else threshold_main

        # 排除 0 前收 (除权除息)
        valid = group[(group[preclose_col] != 0) & group[preclose_col].notna()]
        if valid.empty:
            continue

        max_change = valid["change_pct"].abs().max()
        if max_change > threshold:
            # 找出违规的行
            violations = valid[valid["change_pct"].abs() > threshold]
            events.append(
                make_event(
                    metric_id="A-01",
                    level=DQCLevel.ERROR,
                    checkpoint=checkpoint,
                    value=float(max_change),
                    threshold=float(threshold),
                    message=f"标的 {symbol_str} 涨跌幅 {max_change:.1%} 超阈值 {threshold:.1%}",
                    symbol=symbol_str,
                    violation_count=len(violations),
                    max_change_pct=float(max_change),
                )
            )

    return events


# ============================================================
# A-02: OHLC 关系
# ============================================================
def _check_a02_ohlcl_relation(
    df: pd.DataFrame,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """A-02: OHLC 关系 — low ≤ open,close ≤ high 必须成立."""
    events: list[DQCEvent] = []
    if df.empty:
        return events

    required = {"open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        return events  # C-05 已处理字段缺失

    # 检查 low ≤ open,close ≤ high
    invalid_low = df[(df["open"] < df["low"]) | (df["close"] < df["low"])]
    invalid_high = df[(df["open"] > df["high"]) | (df["close"] > df["high"])]

    if not invalid_low.empty:
        symbol_col = (
            "symbol"
            if "symbol" in df.columns
            else ("code" if "code" in df.columns else None)
        )
        symbols = invalid_low[symbol_col].unique().tolist() if symbol_col else []
        events.append(
            make_event(
                metric_id="A-02",
                level=DQCLevel.ERROR,
                checkpoint=checkpoint,
                value=float(len(invalid_low)),
                threshold=0.0,
                message=f"OHLC 关系违反: open/close < low ({len(invalid_low)} 行)",
                violation_count=len(invalid_low),
                violation_type="below_low",
                symbols=symbols[:5],
            )
        )

    if not invalid_high.empty:
        symbol_col = (
            "symbol"
            if "symbol" in df.columns
            else ("code" if "code" in df.columns else None)
        )
        symbols = invalid_high[symbol_col].unique().tolist() if symbol_col else []
        events.append(
            make_event(
                metric_id="A-02",
                level=DQCLevel.ERROR,
                checkpoint=checkpoint,
                value=float(len(invalid_high)),
                threshold=0.0,
                message=f"OHLC 关系违反: open/close > high ({len(invalid_high)} 行)",
                violation_count=len(invalid_high),
                violation_type="above_high",
                symbols=symbols[:5],
            )
        )

    return events


# ============================================================
# A-03: 成交量非负
# ============================================================
def _check_a03_volume_non_negative(
    df: pd.DataFrame,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """A-03: 成交量非负 — volume ≥ 0 必须成立."""
    events: list[DQCEvent] = []
    if df.empty or "volume" not in df.columns:
        return events

    negative = df[df["volume"] < 0]
    if not negative.empty:
        events.append(
            make_event(
                metric_id="A-03",
                level=DQCLevel.ERROR,
                checkpoint=checkpoint,
                value=float(len(negative)),
                threshold=0.0,
                message=f"成交量为负 ({len(negative)} 行)",
                violation_count=len(negative),
            )
        )

    return events


# ============================================================
# A-04: 市值一致性
# ============================================================
def _check_a04_market_cap_consistency(
    df: pd.DataFrame,
    checkpoint: DQCCheckpoint,
    threshold: float = 0.0001,
) -> list[DQCEvent]:
    """A-04: 市值一致性 — |总市值 - 股价×股本| / 总市值 < 0.01%.

    仅当存在 market_cap / shares 字段时检查.
    """
    events: list[DQCEvent] = []
    if df.empty:
        return events

    required = {"close", "market_cap", "shares"}
    if not required.issubset(df.columns):
        return events

    df_calc = df.copy()
    df_calc["calc_cap"] = df_calc["close"] * df_calc["shares"]
    df_calc["cap_diff"] = (df_calc["calc_cap"] - df_calc["market_cap"]).abs() / df_calc[
        "market_cap"
    ]

    violations = df_calc[df_calc["cap_diff"] > threshold]
    if not violations.empty:
        events.append(
            make_event(
                metric_id="A-04",
                level=DQCLevel.WARN,
                checkpoint=checkpoint,
                value=float(len(violations)),
                threshold=float(threshold),
                message=f"市值一致性偏差超阈值 ({len(violations)} 行)",
                violation_count=len(violations),
            )
        )

    return events


# ============================================================
# A-05: 价格异常跳变
# ============================================================
def _check_a05_price_jump(
    df: pd.DataFrame,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """A-05: 价格异常跳变 — |close_t / close_{t-1} - 1| > 15% 检查停牌.

    用于检测可能的复权错误或停牌数据缺失.
    """
    events: list[DQCEvent] = []
    if df.empty or "close" not in df.columns:
        return events

    symbol_col = (
        "symbol"
        if "symbol" in df.columns
        else ("code" if "code" in df.columns else None)
    )
    date_col = (
        "date"
        if "date" in df.columns
        else ("datetime" if "datetime" in df.columns else None)
    )
    if symbol_col is None or date_col is None:
        return events

    # 按标的+日期排序
    df_sorted = df.sort_values([symbol_col, date_col]).copy()
    df_sorted["prev_close"] = df_sorted.groupby(symbol_col)["close"].shift(1)
    df_sorted["jump_pct"] = (df_sorted["close"] / df_sorted["prev_close"] - 1).abs()

    # 排除 NaN (首日) 和 0 前收
    valid = df_sorted[
        df_sorted["prev_close"].notna()
        & (df_sorted["prev_close"] != 0)
        & (df_sorted["jump_pct"] > PRICE_JUMP_THRESHOLD)
    ]

    if not valid.empty:
        # 取前 5 个最大的跳变
        top_jumps = valid.nlargest(5, "jump_pct")
        for _, row in top_jumps.iterrows():
            events.append(
                make_event(
                    metric_id="A-05",
                    level=DQCLevel.WARN,
                    checkpoint=checkpoint,
                    value=float(row["jump_pct"]),
                    threshold=float(PRICE_JUMP_THRESHOLD),
                    message=f"标的 {row[symbol_col]} 价格跳变 {row['jump_pct']:.1%} (日期 {row[date_col]})",
                    symbol=str(row[symbol_col]),
                    date=str(row[date_col]),
                )
            )

    return events


# ============================================================
# A-06: 零价格检测
# ============================================================
def _check_a06_zero_price(
    df: pd.DataFrame,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """A-06: 零价格检测 — close == 0 必须不存在."""
    events: list[DQCEvent] = []
    if df.empty or "close" not in df.columns:
        return events

    zero_count = int((df["close"] == 0).sum())
    if zero_count > 0:
        events.append(
            make_event(
                metric_id="A-06",
                level=DQCLevel.ERROR,
                checkpoint=checkpoint,
                value=float(zero_count),
                threshold=0.0,
                message=f"收盘价为零 ({zero_count} 行)",
                zero_count=zero_count,
            )
        )

    return events
