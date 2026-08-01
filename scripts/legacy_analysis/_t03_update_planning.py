# -*- coding: utf-8 -*-
"""T03: 更新 planning.md."""
from __future__ import annotations

from pathlib import Path

PLANNING = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\docs\工程化达标_8.4\planning.md")

text = PLANNING.read_text(encoding="utf-8")

# 1. 更新 6A 工作流进度
text = text.replace(
    "- [~] Automate（执行） — T04/T01/T02 完成, T03 待开始",
    "- [~] Automate（执行） — T04/T01/T02/T03 完成, T05 待开始",
)

# 2. 更新 T03 状态
text = text.replace(
    "| T03 | pytest 覆盖率基线测量 | pending | 1 天 | 生成 baseline 报告 |",
    "| T03 | pytest 覆盖率基线测量 | **completed** | 1 天 | ✅ 覆盖率 21.95%, 缺口 58.05% (T13 目标 80%) |",
)

# 3. 更新进度追踪
text = text.replace(
    "- 已完成：3 (16.7%)\n- 进行中：0 (0%)\n- 待开始：15 (83.3%)",
    "- 已完成：4 (22.2%)\n- 进行中：0 (0%)\n- 待开始：14 (77.8%)",
)

# 4. 添加 T03 到已完成任务
old_line = "- T02 (2026-07-27): pylint broad-except 升级为 error, kill_switch.py 9 处精确化, P1(risk/execution) 62 处 + P2 407 处添加 noqa 标记, utils/ 评分 10.00/10"
new_line = old_line + "\n- T03 (2026-07-28): pytest 覆盖率基线测量, 总覆盖率 21.95% (11594/48838 行), 分支覆盖率 15.85%, 缺口 58.05% (T13 目标 80%), 需补 ~564 个测试用例"
text = text.replace(old_line, new_line)

PLANNING.write_text(text, encoding="utf-8")
print("planning.md 已更新")
print("  - T03 状态: pending -> completed")
print("  - 进度: 3/18 (16.7%) -> 4/18 (22.2%)")
