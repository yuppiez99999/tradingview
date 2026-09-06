"""验证 path_config 路径配置 (2026-09-05 起自包含模式: 数据根默认 = 项目根).

判定:
- data_root == project_root           → 自包含模式 (推荐, 换机直接拷工程目录)
- using_d_drive                       → 异盘部署模式 (QUANT_DATA_ROOT 覆盖, 保留兼容)
- 其他异常                             → FAIL
"""

import json
import os
import sys

# 添加项目根目录到 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.path_config import describe_paths

result = describe_paths()
print(json.dumps(result, indent=2, ensure_ascii=False))

if result.get("data_root") == result.get("project_root"):
    print("\n[OK] Data root is project-internal (自包含模式, 换机直接拷工程目录).")
elif result.get("using_d_drive"):
    print("\n[OK] Data root on external drive (异盘部署模式, QUANT_DATA_ROOT 覆盖).")
else:
    print("\n[WARNING] Unexpected data_root configuration.")
    sys.exit(1)
