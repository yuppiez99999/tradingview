"""T05: 更新 planning.md."""
from __future__ import annotations

from pathlib import Path

PLANNING = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\docs\工程化达标_8.4\planning.md")

text = PLANNING.read_text(encoding="utf-8")

# 1. 更新 6A 工作流进度
text = text.replace(
    "- [~] Automate（执行） — T04/T01/T02/T03 完成, T05 待开始",
    "- [~] Automate（执行） — T04/T01/T02/T03/T05 完成, T06 待开始",
)

# 2. 更新 T05 状态
text = text.replace(
    "| T05 | SimulatedBroker 接入 Almgren-Chriss | pending | 1 周 | 滑点随成交量缩放 |",
    "| T05 | SimulatedBroker 接入 Almgren-Chriss | **completed** | 1 天 | ✅ 平方根滑点模型, 19/19 unit test 通过 |",
)

# 3. 更新进度追踪
text = text.replace(
    "- 已完成：4 (22.2%)\n- 进行中：0 (0%)\n- 待开始：14 (77.8%)",
    "- 已完成：5 (27.8%)\n- 进行中：0 (0%)\n- 待开始：13 (72.2%)",
)

# 4. 添加 T05 到已完成任务
old_line = "- T03 (2026-07-28): pytest 覆盖率基线测量, 总覆盖率 21.95% (11594/48838 行), 分支覆盖率 15.85%, 缺口 58.05% (T13 目标 80%), 需补 ~564 个测试用例"
new_line = old_line + "\n- T05 (2026-07-28): SimulatedBroker 接入 Almgren-Chriss 平方根滑点模型, slip_bps = η × σ × √(qty/ADV) × vol_scaling, 上下限保护 [0.1×fixed, 100bps], 19 个 unit test 全通过, 向后兼容旧 API"
text = text.replace(old_line, new_line)

PLANNING.write_text(text, encoding="utf-8")
print("planning.md 已更新")
print("  - T05 状态: pending -> completed")
print("  - 进度: 4/18 (22.2%) -> 5/18 (27.8%)")
