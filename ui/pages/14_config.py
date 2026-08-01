# -*- coding: utf-8 -*-
"""14 配置页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - Feature Flags 配置查看
    - ConfigManager 4 级优先级说明
    - 关键配置文件浏览 (kill_switch / multi_factor_signal / shadow_admission 等)
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # type: ignore[import-not-found]

from ui.auth import require_auth
from ui.data_loader import (
    CONFIG_DIR,
    load_config_file,
    load_feature_flags_config,
)
from ui.layout import (
    render_empty_state,
    render_page_header,
    render_status_badge,
    render_status_metric,
)


# 关键配置文件清单
KEY_CONFIG_FILES = [
    ("feature_flags", "Feature Flags 总开关"),
    ("kill_switch", "Kill Switch 熔断配置"),
    ("multi_factor_signal", "多因子信号融合配置"),
    ("brinson_attribution", "Brinson 归因配置"),
    ("factor_attribution", "因子归因配置"),
    ("daily_panel", "日级归因面板配置"),
    ("shadow_admission", "Shadow 准入配置"),
    ("llm_router", "LLM 路由配置"),
    ("ui_auth", "UI 鉴权配置"),
]


def main() -> None:
    """页面入口."""
    if not require_auth():
        st.stop()

    render_page_header(
        title="配置",
        subtitle="Feature Flags + ConfigManager 4 级优先级",
        icon="⚙️",
    )

    # ===== ConfigManager 4 级优先级说明 =====
    st.subheader("📚 ConfigManager 4 级优先级 (HC-5)")
    st.markdown("""
| 优先级 | 路径 | 说明 |
|--------|------|------|
| 1 | `QUANT_CONFIG_DIR` 环境变量 | 最高优先级 |
| 2 | `v8.3_institutional/config/` | 生产源 (默认) |
| 3 | `configs/` | v7.7 回退 |
| 4 | `ms_strategy/config/` | 策略特定回退 |
""")

    st.info(
        "**公共 API**: `get_config(name)` / `get_portfolio_config()` / `get_settings_config()` / "
        "`get_kill_switch_config()` / `get_execution_config()` / `get_backtest_config()` / "
        "`get_risk_budget_config()` 等\n\n"
        "**审计 API**: `list_available()` / `get_config_source(name)` (漂移检测)"
    )

    st.divider()

    # ===== Feature Flags 概览 =====
    st.subheader("🚩 Feature Flags")
    flags_config = load_feature_flags_config()
    if flags_config:
        flags = flags_config.get("flags", {})
        if isinstance(flags, dict):
            for flag_name, flag_value in flags.items():
                if isinstance(flag_value, dict):
                    enabled = flag_value.get("enabled", False)
                    description = flag_value.get("description", "")
                else:
                    enabled = bool(flag_value)
                    description = ""

                col1, col2, col3 = st.columns([3, 1, 4])
                with col1:
                    st.code(flag_name)
                with col2:
                    render_status_badge(
                        "NORMAL" if enabled else "DISABLED",
                        label="ON" if enabled else "OFF",
                        size="small",
                    )
                with col3:
                    st.caption(description)
        else:
            st.json(flags_config)
    else:
        render_empty_state("无 Feature Flags 配置", icon="🚩")

    st.divider()

    # ===== 关键配置文件浏览 =====
    st.subheader("📂 关键配置文件")
    selected_cfg = st.selectbox(
        "选择配置文件",
        options=[name for name, _ in KEY_CONFIG_FILES],
        format_func=lambda x: f"{x} ({dict(KEY_CONFIG_FILES).get(x, '')})",
    )

    if selected_cfg:
        cfg_data = load_config_file(selected_cfg)
        if cfg_data:
            # 显示配置来源
            try:
                from utils.config_manager import get_config_source
                source = get_config_source(selected_cfg)
                st.info(f"📁 配置来源: `{source}`")
            except Exception:
                raise  # Re-raise unknown exception

            with st.expander(f"查看 {selected_cfg}.yaml 内容", expanded=True):
                st.json(cfg_data)
        else:
            render_empty_state(f"配置文件 `{selected_cfg}.yaml` 不存在或为空", icon="📂")

    st.divider()

    # ===== 当前环境信息 =====
    st.subheader("🌐 当前环境信息")
    import os
    env_vars = {
        "TRADING_ENV": os.environ.get("TRADING_ENV", "(未设置)"),
        "QUANT_CONFIG_DIR": os.environ.get("QUANT_CONFIG_DIR", "(未设置)"),
    }

    for k, v in env_vars.items():
        render_status_metric(
            label=k,
            value=v,
            status="WARNING" if k == "TRADING_ENV" and v == "production" else "INFO",
        )

    if env_vars["TRADING_ENV"] == "production":
        st.warning("⚠️ 生产环境: 强制鉴权 + Kill Switch fail-closed 模式已激活")

    st.divider()

    # ===== 配置目录路径 =====
    st.subheader("📁 配置目录")
    st.code(str(CONFIG_DIR))


if __name__ == "__main__" or "streamlit" in __file__:
    main()
