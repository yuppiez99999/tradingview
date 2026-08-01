# -*- coding: utf-8 -*-
"""
单标的完整报告验证：GTJA191 Alpha144 -> FactorModel -> 交易建议输出
"""

import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def fmt(val):
    return f"{val:.4f}" if isinstance(val, (int, float)) else str(val)


def section(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def check_single_stock_full_report(code: str = "000001"):
    section(f"单标的完整报告验证：{code}")

    # 1. 数据获取
    print("[1/5] 获取行情数据...")
    try:
        from utils.data_provider import get_historical_data
        df = get_historical_data(code, period="6m")
        if df is None or df.empty:
            print(f"  [SKIP] 本地数据源未返回 {code} 数据")
            df = None
        else:
            print(f"  [OK] 本地数据 {len(df)} 条，最近 close={df['close'].iloc[-1]:.2f}")
    except Exception as e:
        print(f"  [SKIP] 本地数据异常: {e}")
        df = None

    # 2. GTJA191 因子
    print("[2/5] 计算 GTJA191 Alpha144...")
    alpha144 = None
    technical_alpha = None
    try:
        from utils.gtja191_factors import GTJA191Factors
        if df is not None and not df.empty and "close" in df.columns and "amount" in df.columns:
            factors = GTJA191Factors(lookback=20)
            alpha144 = factors.alpha144(df)
            technical_alpha = round(max(-1.0, min(1.0, 1.0 - float(alpha144) * 1e8)), 4) if alpha144 is not None else None
            print(f"  [OK] Alpha144={fmt(alpha144)}")
            print(f"       technical_alpha={fmt(technical_alpha)}")
        else:
            print("  [SKIP] 数据不足，无法计算 Alpha144")
    except Exception as e:
        print(f"  [FAIL] GTJA191 计算异常: {e}")

    # 3. FactorModel
    print("[3/5] 运行 FactorModel 综合评分...")
    try:
        from utils.factor_model import FactorModel
        if df is not None and not df.empty:
            klines = {code: df}
            model = FactorModel()
            results = model.evaluate(klines)
            result = results.get(code)
            if result:
                print(f"  [OK] composite={fmt(result.composite)}, signal={result.signal}")
                for k, v in result.factors.items():
                    print(f"       {k}={fmt(v)}")
            else:
                print("  [WARN] 未返回该标的评估结果")
        else:
            print("  [SKIP] 无数据，跳过 FactorModel")
    except Exception as e:
        print(f"  [FAIL] FactorModel 异常: {e}")

    # 4. 自动执行系统交易建议输出
    print("[4/5] 检查自动执行系统交易建议输出...")
    try:
        from build_plan_executor import BuildPlanExecutor
        executor = BuildPlanExecutor()
        sheet = executor.generate_daily_orders(datetime.now().date())
        target_orders = [o for o in sheet.morning_orders + sheet.afternoon_orders if o.code == code]
        if target_orders:
            o = target_orders[0]
            print(f"  [OK] 找到 {code} 的交易指令")
            print(f"       shares={o.shares}, est_price={o.est_price}, limit_price={o.limit_price}")
            print(f"       technical_alpha={fmt(o.technical_alpha)}")
        else:
            print(f"  [INFO] 今日无 {code} 交易指令，仅展示计算链路")
    except Exception as e:
        print(f"  [FAIL] 自动执行系统异常: {e}")

    # 5. 真实数据路径补充检查
    print("[5/5] 真实数据路径补充检查...")
    try:
        import importlib.util
        kronos_path = os.path.join(PROJECT_ROOT, "..", "11_量化策略", "utils", "kronos_predictor.py")
        if os.path.exists(kronos_path):
            spec = importlib.util.spec_from_file_location("kronos_predictor", kronos_path)
            mod = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(mod)
            real_df = mod.fetch_a_stock_data(code, days=60, verbose=False)
            if real_df is not None and not real_df.empty:
                print(f"  [OK] 真实数据 {len(real_df)} 条")
                factors = GTJA191Factors(lookback=20)
                real_alpha144 = factors.alpha144(real_df)
                real_technical_alpha = round(max(-1.0, min(1.0, 1.0 - float(real_alpha144) * 1e8)), 4) if real_alpha144 is not None else None
                print(f"       real Alpha144={fmt(real_alpha144)}")
                print(f"       real technical_alpha={fmt(real_technical_alpha)}")
            else:
                print("  [SKIP] 真实数据未返回，保留本地验证结果")
        else:
            print("  [SKIP] 未找到 11_量化策略/utils/kronos_predictor.py")
    except Exception as e:
        print(f"  [SKIP] 真实数据路径异常: {e}")


def main():
    check_single_stock_full_report("000001")
    print("\n完成。")


if __name__ == "__main__":
    main()
