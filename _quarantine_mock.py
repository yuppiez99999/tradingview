# -*- coding: utf-8 -*-
"""归档 64 份 mock pipeline_backtest.json 到隔离目录"""
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC_DIR = BASE / "output" / "institutional_pipeline"
QUARANTINE = BASE / "output" / "_quarantine_mock_backtests_20260724"
QUARANTINE.mkdir(parents=True, exist_ok=True)

moved = 0
kept = 0
for f in SRC_DIR.rglob("pipeline_backtest.json"):
    content = f.read_text(encoding="utf-8", errors="ignore")
    if '"category": "mock"' in content or '"category":"mock"' in content:
        rel = f.relative_to(SRC_DIR)
        dest = QUARANTINE / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(dest))
        moved += 1
    else:
        kept += 1
        print(f"KEPT (real alpha): {f}")

# 清理空目录
for d in sorted(SRC_DIR.rglob("*"), reverse=True):
    if d.is_dir() and not any(d.iterdir()):
        d.rmdir()

# 写 README
readme = QUARANTINE / "README.md"
readme.write_text(f"""# Mock Backtests Quarantine

**归档时间**: 2026-07-24
**归档原因**: 这些 pipeline_backtest.json 的 alpha_evaluation.category = "mock",
即使用伪信号重跑实时管道, 而非真实历史模拟。

## 顶级对冲基金标准
backtest_integrity.py 守卫要求: 任何 mock alpha 结果不得作为有效回测/收益证据。
这些文件是 2026-07-18 修复前生成的残留, 已于 2026-07-24 归档隔离。

## 数量
共 {moved} 份 mock 回测文件被归档。

## 真实回测
请使用 `python institutional_pipeline_runner.py --mode backtest` 生成真实回测。
""", encoding="utf-8")

print(f"\n✓ 归档完成: {moved} 份 mock 回测 → {QUARANTINE}")
print(f"  保留 (real alpha): {kept} 份")
print(f"  README: {readme}")
