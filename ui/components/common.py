"""公共样式注入 — 全页面统一样式"""
import os

import streamlit as st


def inject_global_style():
    css_path = os.path.join(os.path.dirname(__file__), "global_style.css")
    if os.path.exists(css_path):
        with open(css_path, encoding="utf-8") as f:
            css = f.read()
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
