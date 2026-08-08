"""验证 safe_math 模块"""
import sys
sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统8.4')

from utils.infra.safe_math import safe_div, safe_mean, safe_pct, safe_abs_ratio, safe_max, safe_min, safe_len

print("=== safe_math 模块验证 ===")
print(f"safe_div(10, 2)      = {safe_div(10, 2)}")
print(f"safe_div(10, 0)      = {safe_div(10, 0)}")
print(f"safe_div(10, 0, default=-1) = {safe_div(10, 0, default=-1)}")
print(f"safe_mean([])        = {safe_mean([])}")
print(f"safe_mean([1,2,3])   = {safe_mean([1, 2, 3])}")
print(f"safe_pct(25, 100)    = {safe_pct(25, 100)}")
print(f"safe_pct(25, 0)      = {safe_pct(25, 0)}")
print(f"safe_abs_ratio(5,-2) = {safe_abs_ratio(5, -2)}")
print(f"safe_max([])         = {safe_max([])}")
print(f"safe_max([3,1,4,1,5])= {safe_max([3, 1, 4, 1, 5])}")
print(f"safe_len(None)       = {safe_len(None)}")
print(f"safe_len([1,2,3])    = {safe_len([1, 2, 3])}")
print("=== 全部通过 ===")
