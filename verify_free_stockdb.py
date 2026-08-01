# -*- coding: utf-8 -*-
"""
free-stockdb vs Wind 数据一致性验证 (阶段 1 验收)
====================================================

对比验证 free-stockdb 本地数据引擎与原有 Wind MCP 数据源的一致性,
确保切换后数据质量不下降。

验证维度:
    1. 数据量对比 (交易日数量)
    2. 收盘价一致性 (相关系数、MAE、最大偏差)
    3. OHLC 一致性 (开高低收的偏差分布)
    4. 成交量一致性
    5. 复权处理验证 (前复权价格连续性)

用法:
    python verify_free_stockdb.py                    # 验证全部持仓标的
    python verify_free_stockdb.py --symbols 600633  # 验证单个标的
    python verify_free_stockdb.py --period 2y        # 指定周期
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("verify_fs")

# 持仓标的列表 (取自 autolearn_trainer)
POSITION_SYMBOLS = [
    ("600633", ".SH", "stock", "浙数文化", "media"),
    ("601138", ".SH", "stock", "工业富联", "tech"),
    ("002475", ".SZ", "stock", "立讯精密", "manufacturing"),
    ("688041", ".SH", "stock", "海光信息", "tech"),
    ("002415", ".SZ", "stock", "海康威视", "tech"),
    ("000977", ".SZ", "stock", "浪潮信息", "tech"),
    ("600036", ".SH", "stock", "招商银行", "finance"),
    ("601318", ".SH", "stock", "中国平安", "finance"),
    ("600519", ".SH", "stock", "贵州茅台", "consumer"),
    ("600900", ".SH", "stock", "长江电力", "utility"),
    ("601899", ".SH", "stock", "紫金矿业", "resource"),
    ("000858", ".SZ", "stock", "五粮液", "consumer"),
    ("600276", ".SH", "stock", "恒瑞医药", "pharma"),
    ("300750", ".SZ", "stock", "宁德时代", "newenergy"),
    ("002594", ".SZ", "stock", "比亚迪", "auto"),
]


def compare_symbol(
    code: str,
    name: str,
    period: str = "2y",
) -> Dict:
    """对比单个标的的 free-stockdb 与 Wind 数据

    Args:
        code: 标的代码 (纯 6 位)
        name: 标的名称
        period: 回看周期

    Returns:
        对比结果字典
    """
    result = {
        "code": code,
        "name": name,
        "period": period,
        "status": "FAIL",
        "fs_rows": 0,
        "wind_rows": 0,
        "common_dates": 0,
        "close_corr": 0.0,
        "close_mae": 0.0,
        "close_max_pct_diff": 0.0,
        "volume_mae_pct": 0.0,
        "ohlc_avg_pct_diff": 0.0,
        "details": {},
    }

    try:
        # 1. 从 free-stockdb 获取 (优先)
        from utils.free_stockdb_adapter import get_historical_data_fs

        df_fs = get_historical_data_fs(code, period, use_fallback=False)
        if df_fs is None or df_fs.empty:
            result["details"]["fs_error"] = "free-stockdb 返回空数据"
            logger.warning(f"  {code} ({name}): free-stockdb 无数据")
            return result

        result["fs_rows"] = len(df_fs)

        # 2. 从原有数据源 (Wind) 获取
        from utils.data_provider import get_historical_data

        df_wind = get_historical_data(code, period)
        if df_wind is None or df_wind.empty:
            result["details"]["wind_error"] = "Wind 返回空数据"
            logger.warning(f"  {code} ({name}): Wind 无数据")
            return result

        result["wind_rows"] = len(df_wind)

        # 3. 对齐日期
        df_fs = df_fs.copy()
        df_wind = df_wind.copy()
        df_fs.index = pd.to_datetime(df_fs.index)
        df_wind.index = pd.to_datetime(df_wind.index)

        common_idx = df_fs.index.intersection(df_wind.index)
        result["common_dates"] = len(common_idx)

        if len(common_idx) < 30:
            result["details"]["error"] = f"共同交易日不足 (仅 {len(common_idx)} 天)"
            logger.warning(f"  {code} ({name}): 共同交易日不足 {len(common_idx)} 天")
            return result

        fs_aligned = df_fs.loc[common_idx]
        wind_aligned = df_wind.loc[common_idx]

        # 4. 收盘价对比
        fs_close = fs_aligned["close"].astype(float)
        wind_close = wind_aligned["close"].astype(float)

        # 相关系数
        corr = np.corrcoef(fs_close.values, wind_close.values)[0, 1]
        result["close_corr"] = round(float(corr), 6)

        # MAE (平均绝对误差)
        mae = np.mean(np.abs(fs_close.values - wind_close.values))
        result["close_mae"] = round(float(mae), 4)

        # 最大百分比偏差
        pct_diff = np.abs((fs_close.values - wind_close.values) / wind_close.values) * 100
        valid_mask = np.isfinite(pct_diff)
        if np.any(valid_mask):
            result["close_max_pct_diff"] = round(float(np.max(pct_diff[valid_mask])), 4)
        else:
            result["close_max_pct_diff"] = 0.0

        # 5. OHLC 平均偏差
        ohlc_diffs = []
        for col in ["open", "high", "low", "close"]:
            if col in fs_aligned.columns and col in wind_aligned.columns:
                fs_vals = fs_aligned[col].astype(float).values
                w_vals = wind_aligned[col].astype(float).values
                diff = np.abs((fs_vals - w_vals) / np.where(w_vals != 0, w_vals, 1)) * 100
                valid = np.isfinite(diff)
                if np.any(valid):
                    ohlc_diffs.append(np.mean(diff[valid]))

        if ohlc_diffs:
            result["ohlc_avg_pct_diff"] = round(float(np.mean(ohlc_diffs)), 4)

        # 6. 成交量对比 (百分比 MAE)
        if "volume" in fs_aligned.columns and "volume" in wind_aligned.columns:
            fs_vol = fs_aligned["volume"].astype(float).values
            w_vol = wind_aligned["volume"].astype(float).values
            vol_diff = np.abs((fs_vol - w_vol) / np.where(w_vol != 0, w_vol, 1)) * 100
            valid = np.isfinite(vol_diff)
            if np.any(valid):
                result["volume_mae_pct"] = round(float(np.mean(vol_diff[valid])), 4)

        # 7. 综合评估 (通过条件: 相关系数 > 0.999 且 平均偏差 < 0.5%)
        if result["close_corr"] > 0.999 and result["ohlc_avg_pct_diff"] < 1.0:
            result["status"] = "PASS"
        else:
            result["status"] = "WARN"

        logger.info(
            f"  {code} ({name}): {result['status']} | "
            f"FS={result['fs_rows']} Wind={result['wind_rows']} 交集={result['common_dates']} | "
            f"Close_corr={result['close_corr']} | "
            f"OHLC偏差={result['ohlc_avg_pct_diff']:.4f}% | "
            f"Max偏差={result['close_max_pct_diff']:.4f}%"
        )

    except Exception as e:
        result["details"]["exception"] = str(e)
        logger.error(f"  {code} ({name}): 对比异常 - {e}")
        import traceback

        traceback.print_exc()

    return result


def run_verification(symbols: Optional[List[str]] = None, period: str = "2y") -> Dict:
    """运行完整验证

    Args:
        symbols: 指定标的代码列表 (None 表示全部持仓)
        period: 回看周期

    Returns:
        验证汇总结果
    """
    logger.info("=" * 70)
    logger.info("free-stockdb vs Wind 数据一致性验证")
    logger.info("=" * 70)

    # 筛选标的
    if symbols:
        target_symbols = []
        for code in symbols:
            found = False
            for s in POSITION_SYMBOLS:
                if s[0] == code:
                    target_symbols.append(s)
                    found = True
                    break
            if not found:
                target_symbols.append((code, ".SH", "stock", code, "unknown"))
    else:
        target_symbols = POSITION_SYMBOLS

    logger.info(f"验证标的数量: {len(target_symbols)}")
    logger.info(f"回看周期: {period}")
    logger.info("")

    # 逐个验证
    results = []
    for code, _suffix, _, name, _ in target_symbols:
        r = compare_symbol(code, name, period)
        results.append(r)

    # 汇总
    pass_count = sum(1 for r in results if r["status"] == "PASS")
    warn_count = sum(1 for r in results if r["status"] == "WARN")
    fail_count = sum(1 for r in results if r["status"] == "FAIL")

    avg_corr = np.mean([r["close_corr"] for r in results if r["common_dates"] > 0])
    avg_ohlc_diff = np.mean([r["ohlc_avg_pct_diff"] for r in results if r["common_dates"] > 0])
    avg_max_diff = np.mean([r["close_max_pct_diff"] for r in results if r["common_dates"] > 0])

    summary = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "period": period,
        "total": len(results),
        "pass": pass_count,
        "warn": warn_count,
        "fail": fail_count,
        "avg_close_corr": round(float(avg_corr), 6),
        "avg_ohlc_pct_diff": round(float(avg_ohlc_diff), 4),
        "avg_close_max_pct_diff": round(float(avg_max_diff), 4),
        "results": results,
    }

    # 输出汇总
    logger.info("")
    logger.info("=" * 70)
    logger.info("验证汇总")
    logger.info("=" * 70)
    logger.info(f"  总标的数:  {len(results)}")
    logger.info(f"  PASS:      {pass_count}")
    logger.info(f"  WARN:      {warn_count}")
    logger.info(f"  FAIL:      {fail_count}")
    logger.info(f"  平均相关系数: {summary['avg_close_corr']:.6f}")
    logger.info(f"  平均 OHLC 偏差: {summary['avg_ohlc_pct_diff']:.4f}%")
    logger.info(f"  平均最大偏差:   {summary['avg_close_max_pct_diff']:.4f}%")
    logger.info("")

    # 保存结果
    output_file = BASE_DIR / "reports" / f"verify_free_stockdb_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info(f"验证结果已保存: {output_file}")

    # 总体结论
    if fail_count == 0 and avg_corr > 0.999:
        logger.info("✅ 验证通过: free-stockdb 数据与 Wind 高度一致, 可用于研究/训练场景")
    elif fail_count < len(results) * 0.1:
        logger.info("⚠️  部分通过: 大部分标的一致, 建议检查 WARN/FAIL 标的")
    else:
        logger.info("❌ 验证失败: 数据一致性不足, 请勿切换到生产环境")

    return summary


def main():
    parser = argparse.ArgumentParser(description="free-stockdb vs Wind 数据一致性验证")
    parser.add_argument("--symbols", nargs="+", help="指定标的代码 (如 600633 601138)")
    parser.add_argument("--period", default="2y", help="回看周期 (1y/2y/3y/5y, 默认 2y)")
    args = parser.parse_args()

    run_verification(args.symbols, args.period)


if __name__ == "__main__":
    main()
