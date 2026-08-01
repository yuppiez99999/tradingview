# -*- coding: utf-8 -*-
"""验证 path_config 路径配置是否正确指向 D 盘"""
import json
import os
import sys

# 添加项目根目录到 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.path_config import describe_paths

result = describe_paths()
print(json.dumps(result, indent=2, ensure_ascii=False))

if result.get("using_d_drive"):
    print("\n[OK] Data root is on D drive!")
else:
    print("\n[WARNING] Data root is NOT on D drive. Check .env QUANT_DATA_ROOT setting.")
    sys.exit(1)
