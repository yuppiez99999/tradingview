"""
ML模型统计显著性验证模式 — v5.10 P0-9 重构
============================================
Bootstrap + 置换检验 + Rank IC (Renaissance标准: t > 2.0)
"""

import os

from core.context import BASE_DIR
from utils.statistical_significance import run_significance_validation


def run_ml_significance_mode(args):
    """ML模型统计显著性验证模式 v5.10 — Bootstrap+置换检验+Rank IC"""
    models_dir = os.path.join(BASE_DIR, "models")
    data_dir = os.path.join(BASE_DIR, "data", "cache")

    print("\n📊 ML模型统计显著性验证 v5.10")
    print("=" * 70)
    print("Renaissance标准: t-statistic > 2.0 才纳入组合")
    print()

    result = run_significance_validation(models_dir, data_dir)

    if result is None:
        print("❌ 验证失败")
        return

    print("\n✅ 验证完成")
