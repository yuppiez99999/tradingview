# -*- coding: utf-8 -*-
"""T02: 更新 .pylintrc 启用 broad-except 检查"""
from pathlib import Path

p = Path(__file__).resolve().parent / ".pylintrc"
c = p.read_text(encoding="utf-8")

# 1. 移除 broad-except 禁用
old = "    broad-except,              # 风控系统需要兜底异常处理\n"
new = "    # broad-except 已启用 (T02, 2026-07-27): 风控路径禁止 broad exception\n"
if old in c:
    c = c.replace(old, new, 1)
    print("✅ 已移除 broad-except 禁用")
else:
    print("⚠ 未找到 broad-except 禁用行")

p.write_text(c, encoding="utf-8")
print("✅ .pylintrc 已更新")
