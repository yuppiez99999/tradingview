"""Streamlit UI 主入口 — 终极量化交易系统 8.4 (T5.6).

任务: T5.6
责任层: L7 归因 + 前端

设计目标:
    1. 基于 st.navigation + st.Page 实现 14 页面多页面应用
    2. HC-1: USE_STREAMLIT_UI Feature Flag 默认 False, 关闭时显示提示
    3. 鉴权整合 (生产环境强制)
    4. 盘中 1 分钟实时刷新 (HC-7)
    5. HC-7: 不修改 v8.3_institutional/monitor.py (保留作为旧版骨架)

启动方式:
    # 开发模式 (无需鉴权)
    streamlit run ui/app.py

    # 生产模式 (强制鉴权)
    TRADING_ENV=production streamlit run ui/app.py

页面分组:
    - 概览组: Dashboard / Trade Plan / Positions / Risk
    - 归因组: Attribution / Brinson / Barra / TCA
    - 运营组: Shadow / Backtest / Macro / Sector
    - 系统组: Logs / Config
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# 将项目根目录加入 sys.path, 确保导入可用
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 检查 Streamlit 是否可用
try:
    import streamlit as st  # type: ignore[import-not-found]
except ImportError:
    # logger 在第 57 行才定义, 此处 except 块若触发会抛 NameError
    # 改用 logging.getLogger 直接获取, 避免模块加载顺序依赖
    _ui_logger = logging.getLogger("ui.app")
    _ui_logger.info("=" * 60)
    _ui_logger.info("Streamlit 未安装, 请执行:")
    _ui_logger.info("    pip install streamlit streamlit-autorefresh")
    _ui_logger.info("=" * 60)
    sys.exit(1)

from ui.auth import (  # noqa: E402
    FLAG_NAME,
    SESSION_USERNAME_KEY,
    is_ui_enabled,
    require_auth,
)
from ui.data_loader import is_intraday_hours, streamlit_autorefresh  # noqa: E402
from ui.layout import render_sidebar  # noqa: E402

logger = logging.getLogger("ui.app")

# ============================================================
# 14 页面定义
# ============================================================

# 页面定义格式: (page_key, page_path, page_title, icon, group)
PAGES = [
    # 概览组
    ("dashboard",  "ui/pages/01_dashboard.py",  "概览",       "📊", "概览"),
    ("trade_plan", "ui/pages/02_trade_plan.py", "交易计划",   "📋", "概览"),
    ("positions",  "ui/pages/03_positions.py",  "持仓",       "💼", "概览"),
    ("risk",       "ui/pages/04_risk.py",       "风险",       "🛡️", "概览"),
    # 归因组
    ("attribution", "ui/pages/05_attribution.py", "归因面板", "🎯", "归因"),
    ("brinson",     "ui/pages/06_brinson.py",     "Brinson",  "⚖️", "归因"),
    ("barra",       "ui/pages/07_barra.py",       "Barra 因子", "📈", "归因"),
    ("tca",         "ui/pages/08_tca.py",         "TCA 执行",  "💰", "归因"),
    # 运营组
    ("shadow",    "ui/pages/09_shadow.py",    "Shadow 账户", "👁️", "运营"),
    ("backtest",  "ui/pages/10_backtest.py",  "回测",        "🔬", "运营"),
    ("macro",     "ui/pages/11_macro.py",     "宏观数据",    "🌍", "运营"),
    ("sector",    "ui/pages/12_sector.py",    "行业轮动",    "🔄", "运营"),
    # 系统组
    ("logs",      "ui/pages/13_logs.py",      "系统日志",    "📝", "系统"),
    ("config",    "ui/pages/14_config.py",    "配置",        "⚙️", "系统"),
]


def build_navigation():
    """构建多页面导航.

    使用 st.Page + st.navigation 实现 14 页面路由,
    按 4 个分组 (概览/归因/运营/系统) 组织.
    """
    project_root = Path(__file__).resolve().parent.parent

    # 按分组组织页面
    grouped_pages: dict = {}
    for page_key, page_path, page_title, icon, group in PAGES:
        abs_path = project_root / page_path
        if not abs_path.exists():
            logger.warning("页面文件不存在: %s", abs_path)
            continue
        page = st.Page(
            str(abs_path),
            title=page_title,
            icon=icon,
            default=(page_key == "dashboard"),
        )
        grouped_pages.setdefault(group, []).append(page)

    return st.navigation(grouped_pages)


def setup_page_config() -> None:
    """配置页面基础设置."""
    st.set_page_config(
        page_title="终极量化交易系统 8.4",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def setup_intraday_autorefresh(intraday_mode: bool) -> None:
    """设置盘中自动刷新.

    HC-7: 盘中 1 分钟级实时刷新.

    Args:
        intraday_mode: 是否启用盘中实时模式
    """
    if not intraday_mode:
        return
    if not is_intraday_hours():
        st.toast("⏰ 当前非盘中时段, 实时刷新已暂停", icon="ℹ️")
        return

    # 1 分钟 (60 秒) 自动刷新
    triggered = streamlit_autorefresh(interval_sec=60, key="ui_intraday_refresh")
    if triggered:
        logger.info("盘中自动刷新触发")


def render_disabled_state() -> None:
    """渲染 Feature Flag 关闭时的提示页."""
    st.set_page_config(
        page_title="终极量化交易系统 8.4 (UI 未启用)",
        page_icon="⚠️",
        layout="wide",
    )
    st.warning(
        f"⚠️ Streamlit UI 当前未启用 (Feature Flag `{FLAG_NAME}=False`).\n\n"
        f"如需启用, 请在 `v8.3_institutional/config/feature_flags.yaml` 中设置 "
        f"`{FLAG_NAME}: true` 并通过 `FeatureFlags.enable('{FLAG_NAME}', signer, co_signer)` 双签启用."
    )
    st.info(
        "**HC-1 硬约束**: 默认关闭以保护 V9 生产基线, 启用需双签审计.\n\n"
        "**HC-7 硬约束**: `v8.3_institutional/monitor.py` 保留作为旧版骨架, 不修改."
    )

    st.divider()
    st.subheader("📋 14 页面规划")
    st.caption("启用后将提供以下页面:")

    for group, items in _group_pages():
        with st.expander(f"{group} ({len(items)} 个页面)", expanded=True):
            for _page_key, page_path, page_title, icon, _ in items:
                st.markdown(f"- {icon} **{page_title}** — `{page_path}`")


def _group_pages():
    """按分组聚合 PAGES."""
    groups: dict = {}
    for item in PAGES:
        groups.setdefault(item[4], []).append(item)
    return groups.items()


def render_unauthenticated_state() -> None:
    """渲染未鉴权状态 (显示登录表单).

    注: 实际登录表单由 ui.auth.require_auth() 内部渲染,
    这里仅作为兜底, 不重复渲染.
    """
    pass


def main() -> None:
    """主入口函数."""
    # HC-1: Feature Flag 关闭时, 仅显示提示页
    if not is_ui_enabled():
        render_disabled_state()
        return

    setup_page_config()

    # 鉴权检查
    if not require_auth():
        # require_auth 已渲染登录表单, 此处直接退出
        return

    # 已鉴权, 进入主应用
    # 初始化 session_state 中的盘中模式标志
    if "ui_intraday_mode" not in st.session_state:
        st.session_state["ui_intraday_mode"] = is_intraday_hours()

    # 设置自动刷新
    setup_intraday_autorefresh(st.session_state["ui_intraday_mode"])

    # 渲染侧边栏
    username = st.session_state.get(SESSION_USERNAME_KEY, "")
    sidebar_state = render_sidebar(
        system_status="NORMAL",
        intraday_mode=st.session_state["ui_intraday_mode"],
        username=username,
        extra_metrics=[
            {"label": "运行模式", "value": "Live", "status": "NORMAL"},
            {"label": "Feature Flag", "value": "Enabled", "status": "NORMAL"},
        ],
    )
    # 同步侧边栏开关状态
    st.session_state["ui_intraday_mode"] = sidebar_state["intraday_mode"]

    # 构建并运行导航
    pg = build_navigation()
    pg.run()


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    main()
else:
    # 当作为模块导入时 (e.g. streamlit run), 也执行 main
    # Streamlit 会自动处理脚本重新执行
    pass
