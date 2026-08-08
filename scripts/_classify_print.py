"""分类 utils/ 中的 print 调用: __main__ 块内 vs 生产代码内"""
import re
from pathlib import Path
from collections import Counter

ROOT = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\utils")

def classify_file(fpath: Path) -> tuple[int, int, int]:
    """返回 (total_print, in_main_block, in_production)"""
    try:
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return (0, 0, 0)

    total = 0
    in_main = 0
    in_prod = 0
    in_main_block = False
    in_docstring = False
    docstring_char = None

    for line in lines:
        stripped = line.strip()

        # docstring 状态切换
        if not in_docstring:
            if stripped.startswith('"""') and stripped.count('"""') == 1:
                in_docstring = True
                docstring_char = '"""'
                continue
            elif stripped.startswith("'''") and stripped.count("'''") == 1:
                in_docstring = True
                docstring_char = "'''"
                continue
        else:
            if docstring_char in stripped:
                in_docstring = False
                docstring_char = None
            continue

        # __main__ 块检测
        if re.match(r"^if\s+__name__\s*==\s*['\"]__main__['\"]", stripped):
            in_main_block = True

        if re.search(r"(?<![\w\.])print\s*\(", line):
            if stripped.startswith("#") or stripped.startswith(">>>"):
                continue
            total += 1
            if in_main_block:
                in_main += 1
            else:
                in_prod += 1

    return (total, in_main, in_prod)

files_stats = []
total_prod = 0
total_main = 0
for fpath in ROOT.rglob("*.py"):
    if "__pycache__" in str(fpath):
        continue
    t, m, p = classify_file(fpath)
    if t > 0:
        files_stats.append((fpath, t, m, p))
        total_prod += p
        total_main += m

files_stats.sort(key=lambda x: -x[3])
print(f"=== utils/ print 分类 (共 {len(files_stats)} 个文件含 print) ===")
print(f"  __main__ 块内 (CLI 自检, 合理): {total_main}")
print(f"  生产代码内 (需迁移到 logger):  {total_prod}")
print(f"  总计: {total_main + total_prod}")
print()
print("=== 生产代码内 print 最多的文件 (Top 15) ===")
for fpath, t, m, p in files_stats[:15]:
    if p == 0:
        break
    rel = fpath.relative_to(ROOT.parent)
    print(f"  {rel}  total={t} main={m} prod={p}")
