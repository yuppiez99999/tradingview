# -*- coding: utf-8 -*-
"""快速验证主系统导入与 ETF 流程"""
import sys
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
    result = system.run_etf_flow_analysis()
    print("etf_flow_analysis result:", type(result), result is None and "None" or "ok")
except Exception as e:
    print("run_etf_flow_analysis FAIL:", e)

try:
    etf_result = getattr(system, 'etf_flow_result', None)
    print("etf_flow_result:", etf_result is None and "None" or "ok")
except Exception as e:
    print("etf_flow_result check FAIL:", e)
