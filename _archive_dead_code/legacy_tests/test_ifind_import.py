# -*- coding: utf-8 -*-
import sys
import traceback
import importlib.util

ifind_call_path = r'C:\Users\Administrator\.trae\skills\ifind-finance-data\call.py'
try:
    spec = importlib.util.spec_from_file_location('ifind_call', ifind_call_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("iFinD import OK")
    print("call:", mod.call)
    print("list_tools:", mod.list_tools)
except Exception as e:
    print("iFinD import FAIL:", e)
    traceback.print_exc()
