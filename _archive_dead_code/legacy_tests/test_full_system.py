# -*- coding: utf-8 -*-
"""验证主系统完整流程"""
import sys
import os

print("Python:", sys.version)

try:
    import comprehensive_quant_system_v7 as cqs
    print("main system import OK")
except Exception as e:
    print("main system import FAIL:", e)
    sys.exit(1)

try:
    system = cqs.ComprehensiveQuantSystemV7()
    print("system init OK")
except Exception as e:
    print("system init FAIL:", e)
    sys.exit(1)

try:
    system.run_full_simulation()
    print("run_full_simulation OK")
except Exception as e:
    print("run_full_simulation FAIL:", e)
    import traceback
    traceback.print_exc()
