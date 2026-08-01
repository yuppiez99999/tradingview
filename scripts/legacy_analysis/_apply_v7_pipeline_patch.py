"""辅助脚本: 在 institutional_pipeline_runner.py 中接入 add_regime_aware_features

V7-Model: 修改两处
  1. import 语句中新增 add_regime_aware_features
  2. _build_lgb_feature_dict 中在 add_mean_reversion_features 之后调用新函数
"""
from pathlib import Path

TARGET = Path("institutional_pipeline_runner.py")
content = TARGET.read_text(encoding="utf-8")

if "add_regime_aware_features" in content:
    print("SKIP: add_regime_aware_features 已接入")
    raise SystemExit(0)

# === Patch 1: import 语句 ===
OLD_IMPORT = '''        add_mean_reversion_features,  # V6: 均值回归特征, 提升震荡市Alpha
    )'''
NEW_IMPORT = '''        add_mean_reversion_features,  # V6: 均值回归特征, 提升震荡市Alpha
        add_regime_aware_features,    # V7: regime-aware 特征, 解决 bull regime 信号失效
    )'''

if OLD_IMPORT not in content:
    print("ERROR: 未找到 import 锚点")
    raise SystemExit(1)
content = content.replace(OLD_IMPORT, NEW_IMPORT, 1)
print("OK: import 语句已修改")

# === Patch 2: 在 _build_lgb_feature_dict 中调用 ===
# 定位 add_mean_reversion_features 调用块
OLD_CALL = '''        # Step 2.5: V6 均值回归特征 (提升震荡市Alpha信号质量)
        # 动机: Window 1 (2023-07~2024-09) 年化仅2.47%, 根因是趋势跟踪特征在震荡市失效
        # 方案: 添加RSI极端值、价格Z-score、短期反转因子等9个均值回归特征
        try:
            featured_dict = add_mean_reversion_features(featured_dict)
            logger.debug("[LGB-WF] 均值回归特征已添加 (9个特征/标的)")
        except Exception as e:
            logger.warning("[LGB-WF] 均值回归特征失败: %s", e)'''

NEW_CALL = '''        # Step 2.5: V6 均值回归特征 (提升震荡市Alpha信号质量)
        # 动机: Window 1 (2023-07~2024-09) 年化仅2.47%, 根因是趋势跟踪特征在震荡市失效
        # 方案: 添加RSI极端值、价格Z-score、短期反转因子等9个均值回归特征
        try:
            featured_dict = add_mean_reversion_features(featured_dict)
            logger.debug("[LGB-WF] 均值回归特征已添加 (9个特征/标的)")
        except Exception as e:
            logger.warning("[LGB-WF] 均值回归特征失败: %s", e)

        # Step 2.6: V7 Regime-Aware 特征 (解决 bull regime 下 Alpha 信号失效)
        # 动机: V6.2 基线 WF Sharpe CV=0.55, bull regime 平均 -1.92%
        #   2024-06 案例: 688017/300308 在 bull regime 下高波动→大跌, LGB 信号失效
        # 方案: 添加 8 个 regime-aware 特征 (4 个 regime dummy + 2 个大盘指标 + 2 个交互项)
        #   核心交互: vol20_x_bull, mom20_x_bull 让模型学习 "bull × 高波动 → 低收益"
        try:
            featured_dict = add_regime_aware_features(featured_dict)
            logger.debug("[LGB-WF] regime-aware 特征已添加 (8个特征/标的)")
        except Exception as e:
            logger.warning("[LGB-WF] regime-aware 特征失败: %s", e)'''

if OLD_CALL not in content:
    print("ERROR: 未找到 _build_lgb_feature_dict 调用锚点")
    raise SystemExit(1)
content = content.replace(OLD_CALL, NEW_CALL, 1)
print("OK: _build_lgb_feature_dict 已接入 regime-aware 特征")

TARGET.write_text(content, encoding="utf-8")
print(f"行数: {len(content.splitlines())}")
