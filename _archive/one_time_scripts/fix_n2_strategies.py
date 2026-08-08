import sys

BASE = r"e:\各种PY程序\28-终极量化交易系统8.4"
sys.path.insert(0, BASE)

file_path = r"e:\各种PY程序\28-终极量化交易系统8.4\scripts\strategy_evaluator.py"
with open(file_path, encoding="utf-8") as f:
    content = f.read()

# 精确替换错误的字符串 (含中文引号)
old = 'reason=f"feature_flag_disabled ({feature_flag_name})"'
new = 'reason=f"feature_flag_disabled ({self._feature_flag_name})"'
if old in content:
    content = content.replace(old, new)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ 修复完成")
else:
    # 检查实际内容中的写法
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if "feature_flag_disabled" in line:
            print(f"行 {i+1}: {line.strip()}")
