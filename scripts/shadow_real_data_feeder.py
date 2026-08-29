#!/usr/bin/env python3
"""Shadow 真实数据注入器 CLI 入口 — W1.3a Day 3 (2026-08-06).

=================================================================
功能:
    转发到 utils.alpha.shadow_real_data_feeder.main(), 提供独立 CLI 入口.
    从 MarketDataProvider 拉取真实行情, 按当日持仓权重计算组合日收益,
    增量写入 reports/shadow/daily_returns.jsonl.

集成点:
    1. v84_PostMarket (15:30 盘后) → run_daily_eod_workflow.py 阶段四点五
       (修复 G1 缺口: 原 daily_workflow.py 不存在, shadow 阶段一直失败)
    2. v84_EvolutionEval (16:05) 兜底: 评估前确保当日数据已写入
    3. 手动 CLI: 历史回填 / 离线 dry-run / 多源交叉校验

用法:
    # 单日注入 (生产, 从最新 plan 文件读权重)
    py scripts/shadow_real_data_feeder.py --date 2026-08-04

    # 历史回填 (回填观察期)
    py scripts/shadow_real_data_feeder.py --start 2026-07-23 --end 2026-08-04

    # 离线 dry-run (不写盘, 仅验证)
    py scripts/shadow_real_data_feeder.py --date 2026-08-04 --dry-run

    # 指定权重文件
    py scripts/shadow_real_data_feeder.py --date 2026-08-04 --weights-file path/to/weights.json

退出码:
    0 = 成功 (至少一日注入成功)
    1 = 失败 (数据源初始化失败 / 参数错误 / 无成功注入)

HC 合规:
    - HC-1: 不切任何 Feature Flag
    - HC-4: 只写 daily_returns.jsonl, 不修改 V9 基线 / positions.json
=================================================================
"""

from __future__ import annotations

import sys
from pathlib import Path

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 强制 UTF-8 输出, 解决 SYSTEM 账户 / Windows GBK 乱码
# (与 run_daily_eod_workflow.py 保持一致的编码处理)
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

# 转发到 feeder 主模块的 main()
from utils.alpha.shadow_real_data_feeder import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
