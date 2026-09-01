"""pytest 配置：将 mobius_addon 加入 sys.path，使 _common / agents / search 可导入。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
