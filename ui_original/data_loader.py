"""Streamlit UI 统一数据加载器 — 终极量化交易系统 8.4 (T5.6).

任务: T5.6
责任层: L7 归因 + 前端

设计目标:
    1. 统一加载 reports/ 目录下的 JSON/YAML/JSONL 文件
    2. 内置 @st.cache_data 缓存 (TTL 60s, 盘中可降至 1s)
    3. 容错降级: 文件不存在/解析失败返回 None, 不阻塞 UI
    4. 支持日期模板替换 (e.g. "daily_panel_{date}.json")
    5. 内置 Streamlit 集成函数: 读取归因面板/Shadow/TCA/Theta 等

数据源路径 (基于 reports/ 目录约定):
    - reports/attribution/daily_panel_{date}.json   — 归因面板 JSON
    - reports/attribution/daily_panel_{date}.md     — 归因面板 Markdown
    - reports/shadow/admission_state.json           — Shadow 准入状态
    - reports/shadow/{date}_dsr.json                — Shadow DSR 报告
    - reports/theta_plans/theta_plan_{YYYYMMDD}.json — Theta 计划
    - reports/tca/fills_{date}.jsonl                — TCA 执行归因
    - reports/llm_router/calls_{date}.jsonl         — LLM 路由审计
    - reports/risk_bus_audit/events_{date}.jsonl    — 风险事件
    - reports/data_quality/data_quality_*.json      — 数据质量
    - reports/pnl_attribution/pnl_attribution_{date}.json — PnL 归因
    - reports/vibe_trading/*/pipeline_state.json    — 流水线状态
    - reports/strategy_registry/*.jsonl             — 策略注册审计
    - v8.3_institutional/config/*.yaml              — 配置文件
"""
from __future__ import annotations

import glob
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger("ui.data_loader")

# ============================================================
# 路径常量
# ============================================================

# 项目根目录 (ui/ 的上一级)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 报告根目录
REPORTS_DIR = _PROJECT_ROOT / "reports"

# 配置目录
CONFIG_DIR = _PROJECT_ROOT / "v8.3_institutional" / "config"

# 默认缓存 TTL (秒)
DEFAULT_CACHE_TTL = 60

# 盘中实时刷新 TTL (秒)
INTRADAY_CACHE_TTL = 1


# ============================================================
# 工具函数
# ============================================================

def get_project_root() -> Path:
    """获取项目根目录."""
    return _PROJECT_ROOT


def today_str(fmt: str = "%Y-%m-%d") -> str:
    """获取今天的日期字符串."""
    return datetime.now().strftime(fmt)


def format_date(d: str | date | datetime, fmt: str = "%Y-%m-%d") -> str:
    """格式化日期为字符串.

    Args:
        d: 日期 (字符串/date/datetime)
        fmt: 目标格式

    Returns:
        格式化后的日期字符串
    """
    if isinstance(d, str):
        return d
    if isinstance(d, datetime):
        return d.strftime(fmt)
    if isinstance(d, date):
        return d.strftime(fmt)
    raise TypeError(f"不支持的日期类型: {type(d).__name__}")


def resolve_path(path: str | Path) -> Path:
    """将路径解析为绝对路径.

    支持相对路径 (相对项目根目录) 和绝对路径.

    Args:
        path: 路径字符串或 Path 对象

    Returns:
        绝对 Path 对象
    """
    p = Path(path)
    if p.is_absolute():
        return p
    return _PROJECT_ROOT / p


def file_exists(path: str | Path) -> bool:
    """检查文件是否存在."""
    return resolve_path(path).exists()


# ============================================================
# 文件读取函数
# ============================================================

def read_json(
    path: str | Path,
    default: Any = None,
) -> Any:
    """读取 JSON 文件 (容错降级).

    Args:
        path: 文件路径
        default: 文件不存在或解析失败时的默认值

    Returns:
        解析后的 JSON 对象, 或 default
    """
    abs_path = resolve_path(path)
    if not abs_path.exists():
        logger.debug("JSON 文件不存在: %s", abs_path)
        return default
    try:
        with abs_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("JSON 读取失败 %s: %s", abs_path, e)
        return default


def read_yaml(
    path: str | Path,
    default: Any = None,
) -> Any:
    """读取 YAML 文件 (容错降级).

    Args:
        path: 文件路径
        default: 文件不存在或解析失败时的默认值

    Returns:
        解析后的 YAML 对象, 或 default
    """
    if yaml is None:
        logger.warning("PyYAML 未安装, 无法读取 YAML")
        return default

    abs_path = resolve_path(path)
    if not abs_path.exists():
        logger.debug("YAML 文件不存在: %s", abs_path)
        return default
    try:
        with abs_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        logger.warning("YAML 读取失败 %s: %s", abs_path, e)
        return default


def read_jsonl(
    path: str | Path,
    default: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """读取 JSONL 文件 (每行一个 JSON 对象).

    Args:
        path: 文件路径
        default: 文件不存在或解析失败时的默认值

    Returns:
        解析后的对象列表, 或 default
    """
    if default is None:
        default = []

    abs_path = resolve_path(path)
    if not abs_path.exists():
        logger.debug("JSONL 文件不存在: %s", abs_path)
        return default

    results: list[dict[str, Any]] = []
    try:
        with abs_path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        results.append(obj)
                except json.JSONDecodeError as e:
                    logger.debug("JSONL 第 %d 行解析失败 %s: %s", line_no, abs_path, e)
                    continue
    except OSError as e:
        logger.warning("JSONL 读取失败 %s: %s", abs_path, e)
        return default

    return results


def read_text(
    path: str | Path,
    default: str = "",
    encoding: str = "utf-8",
) -> str:
    """读取纯文本文件 (容错降级).

    Args:
        path: 文件路径
        default: 文件不存在时的默认值
        encoding: 文件编码

    Returns:
        文件内容字符串, 或 default
    """
    abs_path = resolve_path(path)
    if not abs_path.exists():
        logger.debug("文本文件不存在: %s", abs_path)
        return default
    try:
        return abs_path.read_text(encoding=encoding)
    except OSError as e:
        logger.warning("文本读取失败 %s: %s", abs_path, e)
        return default


def list_files(
    pattern: str,
    directory: str | Path = REPORTS_DIR,
) -> list[Path]:
    """按 glob 模式列出文件.

    Args:
        pattern: glob 模式 (e.g. "attribution/daily_panel_*.json")
        directory: 搜索根目录 (默认 reports/)

    Returns:
        匹配的文件 Path 列表, 按修改时间倒序
    """
    base = resolve_path(directory)
    full_pattern = str(base / pattern)
    matches = [Path(p) for p in glob.glob(full_pattern)]
    # 按修改时间倒序 (最新在前)
    matches.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return matches


def find_latest_file(
    pattern: str,
    directory: str | Path = REPORTS_DIR,
) -> Path | None:
    """查找最新的匹配文件.

    Args:
        pattern: glob 模式
        directory: 搜索根目录

    Returns:
        最新的文件 Path, 或 None
    """
    files = list_files(pattern, directory)
    return files[0] if files else None


# ============================================================
# 业务专用加载函数
# ============================================================

def load_attribution_panel(target_date: str | date | datetime | None = None) -> dict[str, Any] | None:
    """加载日级归因面板 JSON.

    Args:
        target_date: 目标日期 (None 时用今天)

    Returns:
        归因面板字典, 或 None
    """
    d = format_date(target_date) if target_date else today_str()
    path = REPORTS_DIR / "attribution" / f"daily_panel_{d}.json"
    return read_json(path, default=None)


def load_attribution_markdown(target_date: str | date | datetime | None = None) -> str:
    """加载日级归因面板 Markdown.

    Args:
        target_date: 目标日期 (None 时用今天)

    Returns:
        Markdown 字符串, 空字符串表示无文件
    """
    d = format_date(target_date) if target_date else today_str()
    path = REPORTS_DIR / "attribution" / f"daily_panel_{d}.md"
    return read_text(path, default="")


def load_shadow_state() -> dict[str, Any] | None:
    """加载 Shadow 账户准入状态."""
    return read_json(REPORTS_DIR / "shadow" / "admission_state.json", default=None)


def load_shadow_dsr(target_date: str | date | datetime | None = None) -> dict[str, Any] | None:
    """加载 Shadow DSR 报告.

    Args:
        target_date: 目标日期 (None 时用今天)

    Returns:
        DSR 报告字典, 或 None
    """
    d = format_date(target_date) if target_date else today_str()
    path = REPORTS_DIR / "shadow" / f"{d}_dsr.json"
    return read_json(path, default=None)


def load_theta_plan(target_date: str | date | datetime | None = None) -> dict[str, Any] | None:
    """加载 Theta 交易计划.

    Args:
        target_date: 目标日期 (None 时用今天)

    Returns:
        Theta 计划字典, 或 None
    """
    # theta_plans 文件名格式: theta_plan_YYYYMMDD.json
    d = format_date(target_date, fmt="%Y%m%d") if target_date else today_str(fmt="%Y%m%d")
    path = REPORTS_DIR / "theta_plans" / f"theta_plan_{d}.json"
    return read_json(path, default=None)


def load_tca_fills(target_date: str | date | datetime | None = None) -> list[dict[str, Any]]:
    """加载 TCA 执行归因 JSONL.

    Args:
        target_date: 目标日期 (None 时用今天)

    Returns:
        TCA 事件列表
    """
    d = format_date(target_date) if target_date else today_str()
    path = REPORTS_DIR / "tca" / f"fills_{d}.jsonl"
    return read_jsonl(path, default=[])


def load_risk_bus_events(target_date: str | date | datetime | None = None) -> list[dict[str, Any]]:
    """加载风险总线审计事件.

    Args:
        target_date: 目标日期 (None 时用今天)

    Returns:
        风险事件列表
    """
    d = format_date(target_date) if target_date else today_str()
    path = REPORTS_DIR / "risk_bus_audit" / f"events_{d}.jsonl"
    return read_jsonl(path, default=[])


def load_llm_router_calls(target_date: str | date | datetime | None = None) -> list[dict[str, Any]]:
    """加载 LLM 路由审计日志.

    Args:
        target_date: 目标日期 (None 时用今天)

    Returns:
        LLM 调用记录列表
    """
    d = format_date(target_date) if target_date else today_str()
    path = REPORTS_DIR / "llm_router" / f"calls_{d}.jsonl"
    return read_jsonl(path, default=[])


def load_pnl_attribution(target_date: str | date | datetime | None = None) -> dict[str, Any] | None:
    """加载 PnL 归因报告.

    Args:
        target_date: 目标日期 (None 时用今天)

    Returns:
        PnL 归因字典, 或 None
    """
    d = format_date(target_date) if target_date else today_str()
    path = REPORTS_DIR / "pnl_attribution" / f"pnl_attribution_{d}.json"
    return read_json(path, default=None)


def load_strategy_registry_events() -> list[dict[str, Any]]:
    """加载策略注册审计事件 (聚合所有日期)."""
    files = list_files("strategy_registry/*.jsonl")
    all_events: list[dict[str, Any]] = []
    for f in files:
        all_events.extend(read_jsonl(f, default=[]))
    return all_events


def load_pipeline_state(latest: bool = True) -> dict[str, Any] | None:
    """加载流水线状态.

    Args:
        latest: True 加载最新, False 加载最早

    Returns:
        流水线状态字典, 或 None
    """
    files = list_files("vibe_trading/*/pipeline_state.json")
    if not files:
        return None
    target = files[0] if latest else files[-1]
    return read_json(target, default=None)


def load_feature_flags_config() -> dict[str, Any]:
    """加载 Feature Flags 配置."""
    result = read_yaml(CONFIG_DIR / "feature_flags.yaml", default={})
    return result if isinstance(result, dict) else {}


def load_config_file(name: str) -> dict[str, Any]:
    """加载配置文件 (yaml).

    Args:
        name: 配置名 (不含扩展名, e.g. "feature_flags" / "kill_switch")

    Returns:
        配置字典
    """
    result = read_yaml(CONFIG_DIR / f"{name}.yaml", default={})
    return result if isinstance(result, dict) else {}


def load_recent_data_quality(n: int = 5) -> list[dict[str, Any]]:
    """加载最近 N 份数据质量报告.

    Args:
        n: 返回的报告数量

    Returns:
        数据质量报告列表 (按时间倒序)
    """
    files = list_files("data_quality/data_quality_*.json")[:n]
    return [read_json(f, default={}) for f in files]


# ============================================================
# Streamlit 缓存集成 (仅在使用 Streamlit 时调用)
# ============================================================

def get_cache_decorator(ttl: int = DEFAULT_CACHE_TTL):
    """获取 Streamlit 缓存装饰器 (若可用).

    Args:
        ttl: 缓存 TTL (秒)

    Returns:
        st.cache_data 装饰器, 或 no-op 装饰器
    """
    try:
        import streamlit as st  # type: ignore[import-not-found]
        # noqa: F401  # noqa: F401
        return st.cache_data(ttl=ttl, show_spinner=False)
    except (ImportError, RuntimeError):
        # 未安装 streamlit 或不在运行时上下文
        def _noop(func):
            return func
        return _noop


def streamlit_autorefresh(interval_sec: int = 60, key: str = "ui_autorefresh") -> bool:
    """触发 Streamlit 自动刷新.

    基于 st_autorefresh 组件或 st.rerun 实现.

    Args:
        interval_sec: 刷新间隔 (秒)
        key: 刷新键名

    Returns:
        是否触发了刷新
    """
    try:
        import streamlit as st  # type: ignore[import-not-found]
        # noqa: F401
        from streamlit_autorefresh import st_autorefresh  # type: ignore[import-not-found]
        st_autorefresh(interval=interval_sec * 1000, key=key)
        return True
    except ImportError:
        # streamlit_autorefresh 未安装, 降级为无自动刷新
        return False


def is_intraday_hours() -> bool:
    """判断当前是否为 A 股盘中时段.

    A 股交易时段:
        - 上午: 09:30 - 11:30
        - 下午: 13:00 - 15:00
        - 周一至周五 (节假日不判断)

    Returns:
        在盘中时段返回 True, 否则 False
    """
    now = datetime.now()
    # 周末
    if now.weekday() >= 5:
        return False

    t = now.hour * 100 + now.minute
    # 上午 09:30 - 11:30
    if 930 <= t <= 1130:
        return True
    # 下午 13:00 - 15:00
    if 1300 <= t <= 1500:
        return True
    return False


def get_cache_ttl(intraday_mode: bool = False) -> int:
    """获取当前应使用的缓存 TTL.

    Args:
        intraday_mode: 是否启用盘中实时模式

    Returns:
        TTL 秒数
    """
    if intraday_mode and is_intraday_hours():
        return INTRADAY_CACHE_TTL
    return DEFAULT_CACHE_TTL


__all__ = [
    # 路径常量
    "REPORTS_DIR",
    "CONFIG_DIR",
    "DEFAULT_CACHE_TTL",
    "INTRADAY_CACHE_TTL",
    # 工具函数
    "get_project_root",
    "today_str",
    "format_date",
    "resolve_path",
    "file_exists",
    # 文件读取
    "read_json",
    "read_yaml",
    "read_jsonl",
    "read_text",
    "list_files",
    "find_latest_file",
    # 业务专用
    "load_attribution_panel",
    "load_attribution_markdown",
    "load_shadow_state",
    "load_shadow_dsr",
    "load_theta_plan",
    "load_tca_fills",
    "load_risk_bus_events",
    "load_llm_router_calls",
    "load_pnl_attribution",
    "load_strategy_registry_events",
    "load_pipeline_state",
    "load_feature_flags_config",
    "load_config_file",
    "load_recent_data_quality",
    # Streamlit 集成
    "get_cache_decorator",
    "streamlit_autorefresh",
    "is_intraday_hours",
    "get_cache_ttl",
]
