"""VolRegimeWeighter 验证脚本"""
import sys, os
sys.path.insert(0, os.getcwd())
from pathlib import Path
from unittest.mock import patch

print("=" * 60)
print("测试 1: 模块导入与基础加载")
print("=" * 60)
from utils.alpha.vol_regime_weighter import (
    VolRegimeWeighter, VolRegime, WeightSuggestion,
    classify_regime_by_vol, DEFAULT_WEIGHT_MATRIX, STYLE_CATEGORIES,
    REGIME_BULL, REGIME_NEUTRAL, REGIME_BEAR, REGIME_CRISIS,
)
print("  ✅ 导入成功")
print("  矩阵 regimes:", list(DEFAULT_WEIGHT_MATRIX.keys()))
print("  风格类别:", STYLE_CATEGORIES)

print()
print("=" * 60)
print("测试 2: Flag=False 降级行为 (HC-1)")
print("=" * 60)
w = VolRegimeWeighter()
print("  Flag enabled:", w.enabled)
s = w.compute_weights(current_weights={"科技": 0.235}, vix_value=32.5)
print("  降级标志:", s.degraded)
print("  降级原因:", s.degraded_reason)

print()
print("=" * 60)
print("测试 3: 临时启用 Flag, 运行完整 cycle")
print("=" * 60)
with patch("utils.alpha.vol_regime_weighter.VolRegimeWeighter._check_flag", return_value=True):
    w = VolRegimeWeighter(reports_dir=Path("reports/evolution"))
    print("  Flag enabled:", w.enabled)

    portfolio_snapshot = {
        "科技": 0.235, "新能源": 0.09, "医药": 0.15, "金融": 0.11,
        "宽基": 0.06, "资源": 0.09, "防御": 0.05, "现金": 0.05,
        "制造": 0.03, "顺周期": 0.03
    }

    test_cases = [(15.0, "bull"), (25.0, "neutral"), (35.0, "bear"), (50.0, "crisis")]
    for vix, expected in test_cases:
        result = w.run_cycle(
            portfolio_snapshot=portfolio_snapshot,
            vix_value=vix,
        )
        regime = result["regime"]
        report_path = result["report_path"]
        confidence = result["confidence"]
        suggested = result.get("suggested_weights", {})
        tech = suggested.get("科技", 0)
        cash = suggested.get("现金", 0)
        print("  VIX=%5.1f → regime=%-8s (expected %-8s) conf=%.2f  科技=%.4f  现金=%.4f" % (
            vix, regime, expected, confidence, tech, cash
        ))
        print("    报告:", report_path)

print()
print("=" * 60)
print("测试 4: 校验约束 (sum_to_one)")
print("=" * 60)
with patch("utils.alpha.vol_regime_weighter.VolRegimeWeighter._check_flag", return_value=True):
    w = VolRegimeWeighter()
    import json
    for vix in [15.0, 25.0, 35.0, 50.0]:
        result = w.run_cycle(
            portfolio_snapshot=portfolio_snapshot,
            vix_value=vix,
            reports_dir=Path("reports/evolution"),
        )
        report_path = Path(result["report_path"])
        with report_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        total = sum(data["suggested_weights"].values())
        status = "✅" if abs(total - 1.0) < 1e-6 else "❌"
        print("  VIX=%5.1f  total=%.6f  %s" % (vix, total, status))

print()
print("全部验证完成!")
