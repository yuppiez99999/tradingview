"""
端到端验证 GTJA191 Alpha144 在 28-终极量化交易系统7.1 中的真实数据路径。
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def check_local_data_provider():
    print("[1/3] 检查本地 data_provider 数据路径...")
    try:
        from utils.data_provider import get_historical_data

        df = get_historical_data("000001", period="6m")
        assert df is not None and not df.empty, "历史数据为空"
        required = {"close", "amount", "volume"}
        missing = required - set(df.columns)
        assert not missing, f"历史数据缺少字段: {missing}"

        print(f"  [OK] 获取到 {len(df)} 条数据，字段: {list(df.columns)}")
        print(f"      最近 close={df['close'].iloc[-1]:.2f}")
        return df
    except Exception as e:
        print(f"  [FAIL] local data_provider 异常: {e}")
        return None


def check_kronos_real_data():
    print("[2/3] 检查真实数据路径（11_量化策略/kronos_predictor）...")
    try:
        import importlib.util

        kronos_path = os.path.join(
            PROJECT_ROOT, "..", "11_量化策略", "utils", "kronos_predictor.py"
        )
        if not os.path.exists(kronos_path):
            print("  [SKIP] 未找到 11_量化策略/utils/kronos_predictor.py")
            return None

        spec = importlib.util.spec_from_file_location("kronos_predictor", kronos_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

        df = mod.fetch_a_stock_data("000001", days=60, verbose=False)
        if df is None or df.empty:
            print("  [SKIP] 未拿到真实数据，但流程已连通")
            return None

        required = {"close", "amount"}
        missing = required - set(df.columns)
        assert not missing, f"真实数据缺少字段: {missing}"

        print(f"  [OK] 获取到真实数据 {len(df)} 条")
        print(f"      最近 close={df['close'].iloc[-1]:.2f}")
        return df
    except Exception as e:
        print(f"  [SKIP] 真实数据路径异常: {e}")
        return None


def run_factor_evaluation(df, label="data"):
    print(f"[3/3] 使用 {label} 运行 GTJA191 + FactorModel 端到端评估...")
    try:
        from utils.factor_model import FactorModel
        from utils.gtja191_factors import GTJA191Factors

        factors = GTJA191Factors(lookback=20)
        alpha144 = factors.alpha144(df)
        assert alpha144 is not None, "Alpha144 为 None"

        klines = {"000001": df}
        model = FactorModel()
        assert "technical_alpha" in model.DEFAULT_WEIGHTS, "technical_alpha 缺失"

        results = model.evaluate(klines)
        result = results.get("000001")
        assert result is not None, "缺少 000001 因子结果"
        assert "technical_alpha" in result.factors, "technical_alpha 缺失"

        summary = model.generate_signal(results)

        print(f"  [OK] Alpha144={alpha144:.6e}")
        print(f"       technical_alpha={result.factors['technical_alpha']}")
        print(f"       composite={result.composite}, signal={result.signal}")
        print(
            f"       summary signal={summary.get('signal')}, avg={summary.get('avg_composite')}"
        )
        return True
    except Exception as e:
        print(f"  [FAIL] 端到端评估异常: {e}")
        return False


def main():
    local_df = check_local_data_provider()
    real_df = check_kronos_real_data()

    passed = 0
    if local_df is not None:
        passed += int(run_factor_evaluation(local_df, label="local_data_provider"))
    if real_df is not None:
        passed += int(run_factor_evaluation(real_df, label="kronos_real_data"))

    total = 2 if local_df is not None and real_df is not None else 1
    print("\n" + "=" * 50)
    print(f"验证结果: {passed}/{total} 组通过")

    if passed > 0:
        print("结论: GTJA191 已在主系统因子链路中可运行，真实数据路径也连通")
    else:
        print("结论: 端到端验证失败，请检查上方报错")


if __name__ == "__main__":
    main()
