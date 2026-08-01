# -*- coding: utf-8 -*-
"""T02: 更新 planning.md, 标记 T02 完成."""
from __future__ import annotations

from pathlib import Path

PLANNING = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\docs\工程化达标_8.4\planning.md")

text = PLANNING.read_text(encoding="utf-8")

# 1. 更新 6A 工作流进度
text = text.replace(
    "- [~] Automate（执行） — T04/T01 完成, T02 待开始",
    "- [~] Automate（执行） — T04/T01/T02 完成, T03 待开始",
)

# 2. 更新 T02 状态
text = text.replace(
    "| T02 | pylint broad-except 升级为 error | pending | 3 天 | `.pylintrc` 配置 + 0 error |",
    "| T02 | pylint broad-except 升级为 error | **completed** | 1 天 | ✅ utils/ 0 broad-except error, 评分 10.00/10 |",
)

# 3. 更新进度追踪
text = text.replace(
    "- 已完成：2 (11.1%)\n- 进行中：0 (0%)\n- 待开始：16 (88.9%)",
    "- 已完成：3 (16.7%)\n- 进行中：0 (0%)\n- 待开始：15 (83.3%)",
)

# 4. 添加 T02 到已完成任务
text = text.replace(
    "- T01 (2026-07-27): mypy utils/ 错误清零 (560→0), 修复 kill_switch.py 4 处 None 运算 bug, 清理 unused-ignore",
    "- T01 (2026-07-27): mypy utils/ 错误清零 (560→0), 修复 kill_switch.py 4 处 None 运算 bug, 清理 unused-ignore\n"
    "- T02 (2026-07-27): pylint broad-except 升级为 error, kill_switch.py 9 处精确化, "
    "P1(risk/execution) 62 处 + P2 407 处添加 noqa 标记, utils/ 评分 10.00/10",
)

PLANNING.write_text(text, encoding="utf-8")
print("planning.md 已更新")
print("  - T02 状态: pending → completed")
print("  - 进度: 2/18 (11.1%) → 3/18 (16.7%)")
