"""报告管理 — 浏览/搜索/下载历史报告"""

import os
import sys
from typing import Any

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from datetime import datetime

import streamlit as st

from ui.components.report_viewer import browse_report_directory, read_report_file

st.title("📝 报告管理")
st.caption("浏览、搜索和下载历史报告")

from ui.components.common import inject_global_style
from ui.components.system_status import render_alert_card

inject_global_style()

# 报告目录
REPORTS_DIR = os.path.join(_BASE_DIR, "reports")
ARCHIVE_DIR = os.path.join(_BASE_DIR, "..", "每日报告归档")

# === 目录选择 ===
st.subheader("📁 选择报告目录")
dir_choice = st.radio(
    "来源", ["reports/ (即时报告)", "每日报告归档/ (归档报告)"], horizontal=True
)
scan_dir = REPORTS_DIR if "即时" in dir_choice else ARCHIVE_DIR

# === 搜索过滤 ===
col1, col2 = st.columns([3, 1])
with col1:
    search_term = st.text_input(
        "🔍 搜索文件名", placeholder="例如: 康波、ETF、再平衡..."
    )
with col2:
    file_ext = st.selectbox("文件类型", ["全部", ".md", ".txt", ".json", ".html"])

# === 浏览文件 ===
files = browse_report_directory(scan_dir)

# 应用过滤
if search_term:
    files = [f for f in files if search_term.lower() in f["name"].lower()]
if file_ext != "全部":
    files = [f for f in files if f["name"].endswith(file_ext)]

st.caption(f"找到 {len(files)} 个文件")

if not files:
    render_alert_card("无报告", "📭 没有匹配的报告文件", level="info")
    st.stop()

# === 文件列表 ===
st.subheader("📋 报告列表")

# 分页
page_size = 20
page = st.number_input("页码", 1, max(1, len(files) // page_size + 1), 1) - 1
start = page * page_size
end = min(start + page_size, len(files))

_preview_cache = st.session_state.get("_preview_cache", {})

for f in files[start:end]:
    mtime_str = datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M")
    cols = st.columns([4, 1, 1])
    with cols[0]:
        st.markdown(f"📄 **{f['name']}** — `{f['rel_path']}`")
        st.caption(f"🕐 {mtime_str} | 📏 {f['size_kb']:.1f} KB")
    with cols[1]:
        if st.button("📖 预览", key=f"preview_{f['path']}", use_container_width=True):
            st.session_state["preview_file"] = f["path"]
            st.session_state["preview_name"] = f["name"]
            _preview_cache[f["path"]] = read_report_file(f["path"])
            st.session_state["_preview_cache"] = _preview_cache
    with cols[2]:
        content = read_report_file(f["path"])
        ext = os.path.splitext(f["name"])[1]
        mime = "text/markdown" if ext == ".md" else "text/plain"
        st.download_button(
            "📥",
            content,
            file_name=f["name"],
            mime=mime,
            key=f"dl_{f['path']}",
            use_container_width=True,
        )

st.session_state["_preview_cache"] = _preview_cache

# === 预览 ===
if "preview_file" in st.session_state and "preview_name" in st.session_state:
    st.markdown("---")
    st.subheader(f"📄 预览: {st.session_state['preview_name']}")

    cache_key = st.session_state["preview_file"]
    if cache_key in _preview_cache:
        content = _preview_cache[cache_key]
    else:
        content = read_report_file(cache_key)
        _preview_cache[cache_key] = content

    ext = os.path.splitext(st.session_state["preview_name"])[1]

    if ext == ".md":
        st.markdown(content)
    else:
        st.code(content, language="text" if ext == ".txt" else None)

    if st.button("❌ 关闭预览"):
        del st.session_state["preview_file"]
        del st.session_state["preview_name"]
        st.rerun()

# === 目录统计 ===
st.sidebar.subheader("📊 报告统计")
st.sidebar.metric("总报告数", len(files))
if files:
    total_size_mb = sum(f["size_kb"] for f in files) / 1024
    st.sidebar.metric("总大小", f"{total_size_mb:.1f} MB")

    # 按日期分组
    dates: dict[str, Any] = {}
    for f in files:
        d = datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d")
        dates[d] = dates.get(d, 0) + 1
    st.sidebar.caption(f"覆盖 {len(dates)} 天")
