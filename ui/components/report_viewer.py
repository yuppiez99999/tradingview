# -*- coding: utf-8 -*-
"""报告预览组件"""
import os
import streamlit as st


def render_report_viewer(report: str, title: str = "报告预览", download_name: str = None):
    """渲染报告内容并提供下载按钮"""
    st.markdown(f"### 📄 {title}")

    with st.expander("查看完整报告", expanded=True):
        st.markdown(report)

    if download_name:
        st.download_button(
            label=f"📥 下载 {download_name}",
            data=report,
            file_name=download_name,
            mime="text/markdown" if download_name.endswith('.md') else "text/plain",
        )


def browse_report_directory(dir_path: str, pattern: str = None):
    """浏览报告目录，返回文件列表（5分钟缓存）"""
    return _browse_cached(dir_path, pattern)


@st.cache_data(ttl=300)
def _browse_cached(dir_path: str, pattern: str = None):
    if not os.path.exists(dir_path):
        return []
    files = []
    for root, dirs, filenames in os.walk(dir_path):
        for f in filenames:
            if pattern and pattern not in f:
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, dir_path)
            stat = os.stat(full)
            files.append({
                'name': f,
                'path': full,
                'rel_path': rel,
                'size_kb': stat.st_size / 1024,
                'mtime': stat.st_mtime,
            })
    files.sort(key=lambda x: x['mtime'], reverse=True)
    return files


def read_report_file(filepath: str) -> str:
    """安全读取报告文件内容"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(filepath, 'r', encoding='gbk') as f:
                return f.read()
        except Exception:
            return "⚠️ 无法读取此文件（编码不支持）"
    except Exception as e:
        return f"⚠️ 读取失败: {e}"
