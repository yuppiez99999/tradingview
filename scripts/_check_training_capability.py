# -*- coding: utf-8 -*-
"""检查本地训练能力"""
from pathlib import Path
import time

print("=" * 70)
print("本地训练能力检查")
print("=" * 70)

# 1. Qlib 数据
print("\n=== 1. Qlib 训练数据 ===")
qlib_paths = [
    Path(r"E:\各种PY程序\28-终极量化交易系统7.1\qlib_data\cn_data"),
    Path(r"E:\各种PY程序\28-终极量化交易系统8.4\qlib_data\cn_data"),
]
for p in qlib_paths:
    print(f"  {p}: exists={p.exists()}")
    if p.exists():
        feat_dir = p / "features"
        cal_file = p / "calendars" / "day.txt"
        if feat_dir.exists():
            n = len([x for x in feat_dir.iterdir() if x.is_dir()])
            print(f"    features: {n} 个标的")
        if cal_file.exists():
            dates = cal_file.read_text(encoding='utf-8').splitlines()
            print(f"    calendar: {len(dates)} 天, 范围 {dates[0]} ~ {dates[-1]}")

# 2. 已训练模型
print("\n=== 2. 已训练模型 ===")
model_dirs = [
    Path("models/lgb_enhanced"),
    Path("models/lgb_tscv"),
    Path("models/autolearn"),
    Path("models/qlib_models"),
    Path("models/pipeline_factor_signals"),
]
for d in model_dirs:
    if d.exists():
        files = list(d.glob("*"))
        print(f"  {d}: {len(files)} 个文件")
        for f in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
            mtime = time.strftime('%Y-%m-%d %H:%M', time.localtime(f.stat().st_mtime))
            size_kb = f.stat().st_size / 1024
            print(f"    {f.name}: {size_kb:.1f}KB, {mtime}")
    else:
        print(f"  {d}: 不存在")

# 3. 训练日志
print("\n=== 3. 最近训练日志 ===")
logs = sorted(Path("logs").glob("lgb*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
for log in logs[:5]:
    mtime = time.strftime('%Y-%m-%d %H:%M', time.localtime(log.stat().st_mtime))
    size_kb = log.stat().st_size / 1024
    print(f"  {log.name}: {size_kb:.1f}KB, {mtime}")

# 4. 训练入口脚本
print("\n=== 4. 训练入口脚本 ===")
entries = [
    "lgb_enhanced_trainer.py",
    "lgb_tscv_trainer.py",
    "qlib_v9_train.py",
    "autolearn_trainer.py",
    "scripts/run_pipeline_factor_offline.py",
    "research/vibe_trading_factor_analysis/pipeline/pipeline_orchestrator.py",
    "ms_strategy/training/qlib_train_test.py",
    "ms_strategy/training/autolearn_trainer.py",
    "v8.3_institutional/src/ml/enhanced_trainer.py",
]
for e in entries:
    p = Path(e)
    if p.exists():
        size_kb = p.stat().st_size / 1024
        print(f"  ✅ {e} ({size_kb:.1f}KB)")
    else:
        print(f"  ❌ {e} (不存在)")

# 5. 硬件能力
print("\n=== 5. 硬件能力 ===")
try:
    import torch
    print(f"  PyTorch: {torch.__version__}")
    print(f"  CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"  CUDA version: {torch.version.cuda}")
        print(f"  GPU count: {torch.cuda.device_count()}")
except Exception as e:
    print(f"  PyTorch 检查失败: {e}")

try:
    import lightgbm as lgb
    print(f"  LightGBM: {lgb.__version__}")
except Exception as e:
    print(f"  LightGBM 检查失败: {e}")

try:
    import xgboost as xgb
    print(f"  XGBoost: {xgb.__version__}")
except Exception as e:
    print(f"  XGBoost 检查失败: {e}")
