"""T07: 移除 from __future__ import annotations 修复 dataclass 兼容."""
from pathlib import Path

FILE = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional\src\validation\dsr_bootstrap.py")

text = FILE.read_text(encoding="utf-8")
old = '''    5. 同时给出 P5/P25/Median/P75/P95 分位数
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np'''
new = '''    5. 同时给出 P5/P25/Median/P75/P95 分位数
"""
import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np'''
if old in text:
    FILE.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("已移除 from __future__ import annotations")
else:
    print("ERROR: 未找到原始代码")
