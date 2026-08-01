# -*- coding: utf-8 -*-
"""T02: 修复 Fix 3 (kill_switch.py line 101)"""
from pathlib import Path

p = Path(__file__).resolve().parent / "utils" / "kill_switch.py"
c = p.read_text(encoding="utf-8")

old = """            except Exception as e2:
                logger.error(f"全部加载路径失败: {e2}")
                return {}"""
new = """            except (FileNotFoundError, yaml.YAMLError, OSError) as e2:
                logger.error(f"全部加载路径失败: {e2}")
                return {}"""

if old in c:
    c = c.replace(old, new, 1)
    p.write_text(c, encoding="utf-8")
    print("✅ Fix 3 已修复")
else:
    print("⚠ 未找到")
