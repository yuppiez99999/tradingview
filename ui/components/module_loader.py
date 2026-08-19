"""共享模块加载器 — 避免10个页面重复 importlib 加载主系统模块

用法:
    from ui.components.module_loader import get_system_module
    mod = get_system_module()
"""
from __future__ import annotations

import importlib.util
import os
import sys

import streamlit as st

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODULE_PATH = os.path.join(_BASE_DIR, '量化策略系统 v5.10.py')


@st.cache_resource
def _load_system_module():
    """缓存整个会话的主系统模块 — 只 exec 一次"""
    if _BASE_DIR not in sys.path:
        sys.path.insert(0, _BASE_DIR)
    spec = importlib.util.spec_from_file_location('quant_system', _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get_system_module():
    """返回缓存的主系统模块。首次调用 exec 模块，后续命中缓存。"""
    return _load_system_module()
