# -*- coding: utf-8 -*-
"""验证全部 105 标的 OHLCV 数据完整性（5年日K线）

校验项：
1. 文件存在性 (105 个 historical_{code}_5y_base.parquet)
2. 数据行数 >= 1000 (5 年日 K 约 1211 交易日, 容许 15% 缺失)
3. 时间范围 (start_date >= 2021-07-01, end_date 接近今日)
4. OHLCV 列完整, 无大量空值
5. 价格合理性 (close > 0, high >= low, high >= close >= low)

输出:
    完整性报告 + 不合格标的列表
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime
import pandas as pd

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from cache.symbol_universe import get_universe

# V9 回测实际使用的数据目录
OHLCV_DIR = PROJECT_ROOT / "data_cache"


def verify_one(symbol: str) -> dict:
    """验证单只标的的 OHLCV 数据完整性

    Args:
        symbol: 本地格式代码 (如 600519_SH)

    Returns:
        {
            "symbol": str,
            "code": str,
            "file_exists": bool,
            "rows": int,
            "start_date": str,
            "end_date": str,
            "days_coverage": int,
            "has_nan": bool,
            "price_valid": bool,
            "passed": bool,
            "issues": List[str],
        }
    """
    code = symbol.split("_")[0]
    result = {
        "symbol": symbol,
        "code": code,
        "file_exists": False,
        "rows": 0,
        "start_date": "",
        "end_date": "",
        "days_coverage": 0,
        "has_nan": False,
        "price_valid": False,
        "passed": False,
        "issues": [],
    }

    parquet_path = OHLCV_DIR / f"historical_{code}_5y_base.parquet"
    if not parquet_path.exists():
        result["issues"].append("文件不存在")
        return result

    result["file_exists"] = True

    try:
        df = pd.read_parquet(parquet_path)
    except Exception as e:
        result["issues"].append(f"读取失败: {e}")
        return result

    result["rows"] = len(df)
    # 5 年日 K 应有 ~1211 个交易日, 容许 15% 缺失 → 阈值 1000
    if result["rows"] < 1000:
        result["issues"].append(f"行数过少 ({result['rows']} < 1000)")

    # 时间范围 (date 既可能是列也可能是索引)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        start_dt = df["date"].min()
        end_dt = df["date"].max()
    elif df.index.name == "date" or hasattr(df.index, "min"):
        idx = pd.to_datetime(df.index)
        start_dt = idx.min()
        end_dt = idx.max()
    else:
        result["issues"].append("缺少 date 列/索引")
        return result

    result["start_date"] = str(start_dt.date())
    result["end_date"] = str(end_dt.date())
    result["days_coverage"] = (end_dt - start_dt).days

    # 起始日期不应晚于 2021-09-01 (5 年前 2021-07-26 + 1 个月容差)
    if start_dt > pd.Timestamp("2021-09-01"):
        result["issues"].append(
            f"起始日期过晚 ({result['start_date']} > 2021-09-01)"
        )

    # 结束日期应接近今日 (容许 60 天延迟, 因 baostock 数据更新有滞后)
    expected_end_min = pd.Timestamp.now() - pd.Timedelta(days=60)
    if end_dt < expected_end_min:
        result["issues"].append(
            f"结束日期过早 ({result['end_date']} < {expected_end_min.date()})"
        )

    # 空值检查
    expected_cols = ["open", "high", "low", "close", "volume"]
    missing_cols = [c for c in expected_cols if c not in df.columns]
    if missing_cols:
        result["issues"].append(f"缺少列: {missing_cols}")
        return result

    nan_count = df[expected_cols].isna().sum().sum()
    if nan_count > len(df) * 0.05:  # 容许 5% 空值
        result["has_nan"] = True
        result["issues"].append(f"空值过多 ({nan_count})")
    else:
        result["has_nan"] = False

    # 价格合理性
    df_clean = df.dropna(subset=expected_cols)
    if len(df_clean) == 0:
        result["issues"].append("清理空值后无数据")
        return result

    price_ok = (
        (df_clean["close"] > 0).all()
        and (df_clean["high"] >= df_clean["low"]).all()
        and (df_clean["high"] >= df_clean["close"]).all()
        and (df_clean["close"] >= df_clean["low"]).all()
        and (df_clean["volume"] >= 0).all()
    )
    result["price_valid"] = price_ok
    if not price_ok:
        result["issues"].append("价格不合理 (high<low 或 close<=0)")

    # 通过判定
    result["passed"] = len(result["issues"]) == 0
    return result


def main():
    print("=" * 80)
    print("OHLCV 数据完整性验证 (105 标的, 5 年日K线)")
    print(f"数据目录: {OHLCV_DIR}")
    print("=" * 80)

    universe = get_universe()
    print(f"\n标的池总数: {len(universe)}")

    results = []
    passed = 0
    failed = 0

    for i, sym in enumerate(universe, 1):
        r = verify_one(sym)
        results.append(r)
        if r["passed"]:
            passed += 1
        else:
            failed += 1
            print(
                f"  [{i:3d}/{len(universe)}] ✗ {sym} | {', '.join(r['issues'])}"
            )

        if i % 20 == 0:
            print(f"  进度: {i}/{len(universe)} | 通过 {passed} | 失败 {failed}")

    print("\n" + "=" * 80)
    print("验证结果汇总")
    print("=" * 80)
    print(f"  总标的数: {len(universe)}")
    print(f"  通过:     {passed}")
    print(f"  失败:     {failed}")
    print(f"  覆盖率:   {passed/len(universe)*100:.1f}%")

    # 数据质量统计
    rows_list = [r["rows"] for r in results if r["file_exists"]]
    if rows_list:
        print("\n数据行数统计:")
        print(f"  最小: {min(rows_list)}")
        print(f"  最大: {max(rows_list)}")
        print(f"  平均: {sum(rows_list)/len(rows_list):.0f}")
        print(f"  中位: {sorted(rows_list)[len(rows_list)//2]}")

    # 输出失败标的详情
    failed_results = [r for r in results if not r["passed"]]
    if failed_results:
        print(f"\n{'='*80}")
        print(f"失败标的详情 ({len(failed_results)} 个)")
        print(f"{'='*80}")
        for r in failed_results:
            print(f"  {r['symbol']}: {', '.join(r['issues'])}")
    else:
        print(f"\n✓ 全部 {len(universe)} 个标的 OHLCV 数据完整, 可用于 V9 回测")

    # 数据时间范围汇总
    print(f"\n{'='*80}")
    print("数据时间范围汇总")
    print(f"{'='*80}")
    start_dates = [r["start_date"] for r in results if r["start_date"]]
    end_dates = [r["end_date"] for r in results if r["end_date"]]
    if start_dates and end_dates:
        print(f"  最早起始: {min(start_dates)}")
        print(f"  最晚起始: {max(start_dates)}")
        print(f"  最早结束: {min(end_dates)}")
        print(f"  最晚结束: {max(end_dates)}")

    # 写入报告文件
    report_path = PROJECT_ROOT / "OHLCV_INTEGRITY_REPORT.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("OHLCV 数据完整性验证报告 (5 年日K线)\n")
        f.write(f"生成时间: {datetime.now().isoformat()}\n")
        f.write(f"数据目录: {OHLCV_DIR}\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"标的池总数: {len(universe)}\n")
        f.write(f"通过: {passed}\n")
        f.write(f"失败: {failed}\n")
        f.write(f"覆盖率: {passed/len(universe)*100:.1f}%\n\n")

        f.write("=" * 80 + "\n")
        f.write("所有标的详情\n")
        f.write("=" * 80 + "\n")
        for r in results:
            status = "✓" if r["passed"] else "✗"
            f.write(
                f"{status} {r['symbol']} | rows={r['rows']} | "
                f"{r['start_date']} ~ {r['end_date']} | "
                f"days={r['days_coverage']}"
            )
            if r["issues"]:
                f.write(f" | issues: {', '.join(r['issues'])}")
            f.write("\n")

    print(f"\n报告已写入: {report_path}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
