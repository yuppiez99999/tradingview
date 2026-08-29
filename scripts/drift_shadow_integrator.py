#!/usr/bin/env python3
"""DriftShadowIntegrator CLI 入口 — W1.3b Day 4 (2026-08-10).

=================================================================
功能:
    转发到 utils.alpha.drift_shadow_integrator.main(), 提供独立 CLI 入口.
    桥接 DriftMonitor + DelayedLabelTracker + daily_returns.jsonl,
    执行漂移检测、IC/IC_IR 计算、IC_IR 退化告警.

集成点:
    1. v84_PostMarket (15:30 盘后) → run_daily_eod_workflow.py 阶段四点七
       (在 Shadow 数据注入后执行漂移检测)
    2. v84_EvolutionEval (16:05) 兜底: 评估前确保漂移数据已更新
    3. 手动 CLI: 单日集成 / 历史回填 / PSI 阈值校准

用法:
    # 单日集成
    py scripts/drift_shadow_integrator.py --date 2026-08-07

    # 历史回填
    py scripts/drift_shadow_integrator.py --start 2026-07-23 --end 2026-08-07

    # PSI 阈值校准
    py scripts/drift_shadow_integrator.py --calibrate-psi

退出码:
    0 = 成功
    1 = 失败

HC 合规:
    - HC-1: 不切 Feature Flag (用 sim_mode=True 绕过)
    - HC-4: 只读评估, 不修改 V9 基线
=================================================================
"""

from __future__ import annotations

import sys
from pathlib import Path

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 强制 UTF-8 输出
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

# 转发到主模块的 main()
from utils.alpha.drift_shadow_integrator import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
