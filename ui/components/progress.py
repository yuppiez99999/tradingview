# -*- coding: utf-8 -*-
"""进度显示组件 — 适配 Streamlit"""
import time
import streamlit as st


class StreamlitProgress:
    """Streamlit 进度指示器"""

    def __init__(self, task_name: str, total_steps: int = 100):
        self.task_name = task_name
        self.total_steps = total_steps
        self.start_time = time.time()
        self._bar = st.progress(0, text=f"{task_name} — 初始化...")

    def update(self, step: int, message: str = ""):
        pct = min(step / self.total_steps, 1.0)
        elapsed = time.time() - self.start_time
        eta = ""
        if step > 0 and step < self.total_steps:
            eta_s = (elapsed / step) * (self.total_steps - step)
            eta = f" | 预计剩余 {int(eta_s)}s"
        self._bar.progress(pct, text=f"{self.task_name} — {message}{eta}")

    def complete(self, message: str = "完成"):
        elapsed = time.time() - self.start_time
        self._bar.progress(1.0, text=f"✅ {self.task_name} — {message} (耗时 {elapsed:.1f}s)")

    def error(self, message: str = "失败"):
        self._bar.progress(1.0, text=f"❌ {self.task_name} — {message}")


def run_with_progress(task_name: str, steps: list, total_steps: int = None):
    """
    按步骤列表执行任务并显示进度。
    steps = [(step_number, message, callable), ...]
    每个 callable 在对应步骤执行。
    """
    total = total_steps or len(steps)
    progress = StreamlitProgress(task_name, total)
    results = {}
    for step_num, message, func in steps:
        progress.update(step_num, message)
        try:
            results[message] = func()
        except Exception as e:
            st.error(f"步骤 '{message}' 失败: {e}")
            results[message] = None
    progress.complete()
    return results
