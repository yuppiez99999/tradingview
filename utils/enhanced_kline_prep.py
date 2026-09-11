# ruff: noqa: T201  # CLI 数据准备工具: 进度以 print 输出 (与 ml_enhanced_trainer 一致), 不做 P0 阻断豁免
"""enhanced_kline_prep.py — --train-enhanced (ML增强训练 v2.0) 数据准备层

背景: v8.6 合并时 ml_enhanced_trainer 数据布局沿用 v5.10 平铺约定
(data_dir/kline_{code6}_daily.parquet), 而主仓 data/cache/klines 是
791 标的的全市场陈旧缓存 (最新 07-28)。直接喂给 --train-enhanced 会:
  1) 用陈旧数据训练; 2) 全市场合并训练远超 v5.10 "持仓单模型" 设计。

本模块把训练标的池收敛到当前持仓 (config/positions.json), 并沿
data_provider 降级链 (Wind MCP > TDX > AKShare > 新浪) 拉取最新前复权
日K线, 归一化后落盘为 v5.10 平铺布局, 供 EnhancedMLTrainer 消费。

布局约定:
    data/cache/enhanced_klines/kline_600276_daily.parquet
    index: DatetimeIndex(name='date', naive), 列: open/high/low/close/volume

用法 (通常无需直接调用, 由统一入口 --train-enhanced 自动触发):
    from utils.enhanced_kline_prep import prepare_enhanced_kline_cache
    prep = prepare_enhanced_kline_cache(
        out_dir=os.path.join(BASE_DIR, "data", "cache", "enhanced_klines"),
        base_dir=BASE_DIR,
    )
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from utils.datetime_utils import now_bj

# 缓存最新日期距今超过该日历天数则刷新 (覆盖周末, 避免每周多次全量拉取)
_DEFAULT_REFRESH_DAYS = 3
# 少于该行数的缓存视为数据不足, 触发刷新 (120 日均线 + 标签窗口后仍需可训练样本)
_MIN_BARS = 200
_CODE_RE = re.compile(r"(\d{6})")
_KEEP_COLS = ("open", "high", "low", "close", "volume")


def _load_position_codes(positions_path: str) -> list[str]:
    """从 positions.json 递归收集 code/symbol/ticker (与 run_auto_retrain 同口径).

    解析失败返回空列表 (由调用方决定行为), 绝不抛异常阻断训练.
    """
    out: list[str] = []
    if not positions_path or not os.path.exists(positions_path):
        return out
    try:
        with open(positions_path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"[prep] positions.json 读取失败: {exc}")
        return out

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("code", "symbol", "ticker") and isinstance(value, str):
                    if value.strip():
                        out.append(value.strip())
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    seen: set = set()
    uniq: list[str] = []
    for code in out:
        if code not in seen:
            seen.add(code)
            uniq.append(code)
    return uniq


def _code6(symbol: str) -> str:
    """600276.SH / 600276 / 159915.SZ -> 6 位无后缀数字 (v5.10 文件名口径)."""
    match = _CODE_RE.search(symbol)
    if match:
        return match.group(1)
    return symbol.replace(".SH", "").replace(".SZ", "")


def _needs_refresh(parquet_path: str) -> bool:
    """缓存陈旧或行数不足即需刷新. 任何读取异常按需刷新处理 (fail-open)."""
    try:
        df = pd.read_parquet(parquet_path, columns=["close"])
        if df is None or len(df) < _MIN_BARS:
            return True
        # pandas 无类型标注: 先收敛为 datetime 恢复 mypy 推断, 再计算缓存天数
        _ts_any = pd.Timestamp(df.index[-1])
        if getattr(_ts_any, "tzinfo", None) is not None:
            _ts_any = _ts_any.tz_convert(None)
        last_dt: datetime = _ts_any.to_pydatetime()
        age_days = (now_bj() - last_dt).days
        return age_days > _DEFAULT_REFRESH_DAYS
    except (OSError, ValueError, TypeError, KeyError, ImportError):  # fail-open: 读不了就刷新
        return True


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """归一化为增强训练输入: naive 交易日 index + 数值化 OHLCV."""
    keep = [c for c in _KEEP_COLS if c in df.columns]
    if not keep:
        raise ValueError("无 OHLCV 列")
    out = df[keep].copy()
    for col in keep:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["close"])
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out.index.name = "date"
    return out


def prepare_enhanced_kline_cache(
    out_dir: str,
    base_dir: Optional[str] = None,
    symbols: Optional[list[str]] = None,
    period: str = "2y",
    force: bool = False,
) -> Optional[dict[str, Any]]:
    """为 --train-enhanced 准备持仓K线缓存 (降级链刷新 + v5.10 平铺布局).

    Args:
        out_dir: 平铺缓存输出目录 (由调用方决定, 建议 data/cache/enhanced_klines)
        base_dir: 28仓根 (用于定位 config/positions.json)
        symbols: 训练标的清单; None 时从 positions.json 自动收集当前持仓
        period: data_provider 拉取周期 ('1y'/'2y'/'3y')
        force: 强制全量刷新 (默认只刷新缺失/陈旧/行数不足的缓存)

    Returns:
        统计 dict {n_available, refreshed, skipped, failed, out_dir, symbols};
        无任何可用数据时返回 None (调用方应终止训练, 勿静默回退陈旧全市场).
    """
    base_dir = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if symbols is None:
        symbols = _load_position_codes(os.path.join(base_dir, "config", "positions.json"))
    if not symbols:
        print("[prep] positions.json 无可用标的, 终止")
        return None

    os.makedirs(out_dir, exist_ok=True)

    try:
        from utils.data_provider import get_historical_data  # noqa: PLC0415
    except (ImportError, OSError, AttributeError) as exc:  # fail-open
        print(f"[prep] data_provider 加载失败: {exc}")
        return None

    ok = refreshed = skipped = failed = 0
    problems: list[str] = []

    for idx, sym in enumerate(symbols, 1):
        code = _code6(sym)
        target = os.path.join(out_dir, f"kline_{code}_daily.parquet")
        if not force and os.path.exists(target) and not _needs_refresh(target):
            skipped += 1
            print(f"[prep] [{idx}/{len(symbols)}] {sym} 缓存新鲜, 跳过")
            continue
        try:
            df = get_historical_data(sym, period)
            if df is None or df.empty or "close" not in df.columns:
                raise ValueError("返回空数据")
            df = _normalize(df)
            if len(df) < _MIN_BARS:
                raise ValueError(f"有效行数不足: {len(df)} < {_MIN_BARS}")
            df.to_parquet(target, index=True)
            ok += 1
            refreshed += 1
            print(
                f"[prep] [{idx}/{len(symbols)}] {sym} 刷新 {len(df)} bars "
                f"-> kline_{code}_daily.parquet"
            )
        except (OSError, ValueError, TypeError, KeyError, IndexError, MemoryError) as exc:  # 单标的失败不影响其它标的
            failed += 1
            problems.append(f"{sym}: {exc}")
            print(f"[prep] [{idx}/{len(symbols)}] {sym} 失败: {exc}")

    print(
        f"[prep] 完成: 可用 {ok + skipped} (刷新 {refreshed} | 跳过 {skipped}) "
        f"| 失败 {failed} | 输出 {out_dir}"
    )
    if problems:
        print(f"[prep] 失败明细: {'; '.join(problems[:8])}")

    if ok == 0 and skipped == 0:
        return None
    return {
        "n_available": ok + skipped,
        "refreshed": refreshed,
        "skipped": skipped,
        "failed": failed,
        "out_dir": out_dir,
        "symbols": symbols,
    }


if __name__ == "__main__":
    # 独立验证入口: python utils/enhanced_kline_prep.py
    _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _result = prepare_enhanced_kline_cache(
        os.path.join(_base, "data", "cache", "enhanced_klines"),
        base_dir=_base,
    )
    if _result:
        print(f"RESULT n_available={_result['n_available']}")
    else:
        print("RESULT None")
