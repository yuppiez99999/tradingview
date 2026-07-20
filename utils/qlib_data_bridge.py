# -*- coding: utf-8 -*-
"""
qlib 数据桥接层

职责：
- 将系统内 DataFrame/字典 转为 qlib 可消费格式
- 将 qlib 输出转回系统内部格式
- 不直接依赖 qlib 强导入，保持可降级
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import os

try:
    import pandas as pd
    _HAS_PANDAS = True
except Exception:
    pd = None
    _HAS_PANDAS = False


def to_qlib_symbol(symbol: str) -> str:
    """系统内代码 -> qlib code"""
    s = str(symbol).strip()
    for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    if not s:
        return s
    if s.startswith(("51", "58")):
        return f"{s}.SH"
    if s.startswith(("15", "16")):
        return f"{s}.SZ"
    if s.startswith(("00", "30")):
        return f"{s}.SZ"
    if s.startswith(("6",)):
        return f"{s}.SH"
    if s.startswith(("4", "8")):
        return f"{s}.BJ"
    return f"{s}.SH"


def from_qlib_symbol(qlib_code: str) -> str:
    """qlib code -> 系统内代码"""
    s = str(qlib_code).strip()
    if "." in s:
        code, exch = s.split(".", 1)
        exch = exch.upper()
        if exch == "SH":
            return code
        if exch == "SZ":
            return code
        if exch == "BJ":
            return code
    return s


def dataframe_to_qlib_record(df: Any) -> List[Dict[str, Any]]:
    """
    系统历史数据 DataFrame -> qlib 风格记录列表
    兼容无 pandas 时的兜底
    """
    if _HAS_PANDAS and isinstance(df, pd.DataFrame):
        if df.empty:
            return []
        if not hasattr(df.index, 'strftime'):
            df = df.copy()
            df.index = pd.to_datetime(df.index, errors='coerce')
        # 向量化重构：消除 iterrows() 逐行遍历，使用 numpy 数组操作
        idx = df.index
        dates = [ts.strftime('%Y-%m-%d') if hasattr(ts, 'strftime') else str(ts) for ts in idx]
        
        # 批量提取列数据为 numpy 数组，避免逐行 Series 创建
        opens = pd.to_numeric(df.get('open', pd.Series(0, index=idx)), errors='coerce').fillna(0).to_numpy(dtype=float)
        highs = pd.to_numeric(df.get('high', pd.Series(0, index=idx)), errors='coerce').fillna(0).to_numpy(dtype=float)
        lows = pd.to_numeric(df.get('low', pd.Series(0, index=idx)), errors='coerce').fillna(0).to_numpy(dtype=float)
        closes = pd.to_numeric(df.get('close', pd.Series(0, index=idx)), errors='coerce').fillna(0).to_numpy(dtype=float)
        volumes = pd.to_numeric(df.get('volume', pd.Series(0, index=idx)), errors='coerce').fillna(0).to_numpy(dtype=float)
        
        # amount 列不存在时回退到 volume
        if 'amount' in df.columns:
            amounts = pd.to_numeric(df['amount'], errors='coerce').fillna(0).to_numpy(dtype=float)
        else:
            amounts = volumes.copy()
        
        # 使用列表推导式批量构建记录（比 iterrows + append 快 10-50 倍）
        records = [
            {
                'date': dates[i],
                'open': float(opens[i]),
                'high': float(highs[i]),
                'low': float(lows[i]),
                'close': float(closes[i]),
                'volume': float(volumes[i]),
                'amount': float(amounts[i]),
            }
            for i in range(len(df))
        ]
        return records
    return []


def qlib_signal_to_system(qlib_signal: Any) -> Dict[str, Any]:
    """
    qlib 信号/预测 -> 系统统一信号格式
    尽量兼容多种常见返回结构
    """
    if isinstance(qlib_signal, dict):
        score = qlib_signal.get('score', qlib_signal.get('y', qlib_signal.get('pred', 0)))
        direction = qlib_signal.get('direction', qlib_signal.get('signal', 'neutral'))
        confidence = float(qlib_signal.get('confidence', qlib_signal.get('confidence_level', 0)) or 0)
        return {
            'score': float(score) if score is not None else 0.0,
            'direction': str(direction),
            'confidence': confidence,
            'source': 'qlib',
        }
    if hasattr(qlib_signal, 'item'):
        try:
            score = float(qlib_signal.item())
        except Exception:
            score = 0.0
        return {'score': score, 'direction': 'buy' if score > 0 else 'sell', 'confidence': 0.0, 'source': 'qlib'}
    return {'score': 0.0, 'direction': 'neutral', 'confidence': 0.0, 'source': 'qlib'}


def get_qlib_cache_root() -> str:
    """qlib 数据缓存根目录，默认放在项目内"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, '.qlib_cache')

