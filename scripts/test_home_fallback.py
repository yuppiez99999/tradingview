# -*- coding: utf-8 -*-
"""模拟计划任务环境下 _home 解析为 systemprofile 时的修复验证"""
import os

# 模拟计划任务环境：_home = C:\windows\system32\config\systemprofile
simulated_home = r"C:\windows\system32\config\systemprofile"

# 构造 call.py 路径（错误的）
wrong_call_path = os.path.join(simulated_home, ".trae", "skills", "ifind-finance-data", "call.py")
print(f"错误的 call.py 路径: {wrong_call_path}")
print(f"文件存在: {os.path.isfile(wrong_call_path)}")

# 构造正确的 call.py 路径
correct_call_path = r"C:\Users\Administrator\.trae\skills\ifind-finance-data\call.py"
print(f"\n正确的 call.py 路径: {correct_call_path}")
print(f"文件存在: {os.path.isfile(correct_call_path)}")

# 模拟修复逻辑
fallback_dirs = []
for env_key in ("USERPROFILE", "HOMEDRIVE", "LOCALAPPDATA", "APPDATA"):
    env_val = os.environ.get(env_key, "")
    print(f"\n检查 {env_key}: {env_val}")
    if env_val and os.path.isdir(env_val) and 'system32' not in env_val.lower():
        candidate = os.path.join(env_val, ".trae", "skills", "ifind-finance-data", "call.py")
        print(f"  候选路径: {candidate}")
        print(f"  文件存在: {os.path.isfile(candidate)}")
        if os.path.isfile(candidate):
            fallback_dirs.append(os.path.dirname(candidate))

if fallback_dirs:
    print(f"\n✅ 找到回退目录: {fallback_dirs[0]}")
else:
    print("\n❌ 未找到回退目录")
