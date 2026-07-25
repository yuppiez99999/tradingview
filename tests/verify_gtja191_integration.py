# -*- coding: utf-8 -*-
"""验证 GTJA191 Alpha144 在 28-终极量化交易系统7.1 中的集成"""

import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def check_imports():
    print("[1/4] 检查模块导入...")
    try:
        from utils.gtja191_factors import GTJA191Factors
        print("  [OK] GTJA191Factors 导入成功")
    except Exception as e:
        print(f"  [FAIL] GTJA191Factors 导入失败: {e}")
        return False

    try:
        from utils.factor_model import FactorModel, FactorResult
        print("  [OK] FactorModel 导入成功")
    except Exception as e:
        print(f"  [FAIL] FactorModel 导入失败: {e}")
        return False

    return True


def check_factor_calculation():
    print("[2/4] 检查 Alpha144 因子计算...")
    try:
        import pandas as pd
        import numpy as np
        from utils.gtja191_factors import GTJA191Factors

        dates = pd.date_range("2024-01-01", periods=60, freq="B")
        np.random.seed(42)
        close = 10 + np.cumsum(np.random.randn(60) * 0.1)
        amount = np.abs(np.random.randn(60) * 1e6) + 1e5

        df = pd.DataFrame({"close": close, "amount": amount}, index=dates)

        factors = GTJA191Factors(lookback=20)
        value = factors.alpha144(df)
        computed = factors.compute(df)

        assert value is not None, "Alpha144 不应为 None"
        assert "alpha144" in computed, "compute 应返回 alpha144"
        assert isinstance(value, float), "Alpha144 应为 float"
        print(f"  [OK] Alpha144={value:.6e}")
        return True
    except Exception as e:
        print(f"  [FAIL] Alpha144 计算异常: {e}")
        return False


def check_factor_model_integration():
    print("[3/4] 检查 FactorModel 集成 technical_alpha...")
    try:
        import pandas as pd
        import numpy as np
        from utils.factor_model import FactorModel

        dates = pd.date_range("2024-01-01", periods=80, freq="B")
        np.random.seed(7)
        close = 20 + np.cumsum(np.random.randn(80) * 0.2)
        amount = np.abs(np.random.randn(80) * 2e6) + 2e5

        df = pd.DataFrame({"close": close, "amount": amount}, index=dates)
        klines = {"000001": df}

        model = FactorModel()
        assert "technical_alpha" in model.DEFAULT_WEIGHTS, "technical_alpha 应在默认权重中"
        assert abs(sum(model.DEFAULT_WEIGHTS.values()) - 1.0) < 1e-6, "权重总和应为 1"

        results = model.evaluate(klines)
        result = results.get("000001")
        assert result is not None, "应返回 000001 的评估结果"
        assert "technical_alpha" in result.factors, "结果中应包含 technical_alpha"

        print(f"  [OK] technical_alpha={result.factors['technical_alpha']}")
        print(f"       composite={result.composite}, signal={result.signal}")
        return True
    except Exception as e:
        print(f"  [FAIL] FactorModel 集成异常: {e}")
        return False


def check_composite_signal():
    print("[4/4] 检查组合信号生成...")
    try:
        import pandas as pd
        import numpy as np
        from utils.factor_model import FactorModel

        dates = pd.date_range("2024-01-01", periods=80, freq="B")
        np.random.seed(9)
        close = 15 + np.cumsum(np.random.randn(80) * 0.25)
        amount = np.abs(np.random.randn(80) * 1.5e6) + 1.5e5

        df = pd.DataFrame({"close": close, "amount": amount}, index=dates)
        klines = {"000001": df, "000002": df * 1.05}

        model = FactorModel()
        results = model.evaluate(klines)
        summary = model.generate_signal(results)

        assert summary is not None
        print(f"  [OK] avg_composite={summary.get('avg_composite')}")
        print(f"       signal={summary.get('signal')}")
        return True
    except Exception as e:
        print(f"  [FAIL] 组合信号生成异常: {e}")
        return False


def main():
    results = []
    results.append(check_imports())
    results.append(check_factor_calculation())
    results.append(check_factor_model_integration())
    results.append(check_composite_signal())

    passed = sum(results)
    total = len(results)
    print("\n" + "=" * 50)
    print(f"验证结果: {passed}/{total} 项通过")

    if passed == total:
        print("结论: GTJA191 Alpha144 已成功集成到 28-终极量化交易系统7.1")
    else:
        print("结论: 集成存在异常，请检查上方报错")


if __name__ == "__main__":
    main()
