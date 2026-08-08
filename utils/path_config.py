"""
集中路径配置模块 (Centralized Path Configuration)
===================================================

设计动机 (2026-08-01):
- C 盘空间不足 (仅剩 2.56GB), Python/pip 缓存与回测数据持续占用 C 盘
- 历史上 data_cache/output/reports/models 路径分散硬编码在 5+ 个文件中
- 需要统一管控, 一处配置全局生效, 支持将数据存储迁移到 D 盘

使用方式:
1. 在 .env 中设置 QUANT_DATA_ROOT=D:\\QuantData
2. 所有数据目录 (data_cache/output/reports/models/logs) 自动切到 D 盘
3. 不设置时向后兼容, 使用项目根目录 (E:\\各种PY程序\\28-终极量化交易系统8.4)

路径优先级:
    QUANT_DATA_ROOT 环境变量 (最高)
        ↓
    项目根目录 BASE_DIR (向后兼容默认值)

目录结构 (当 QUANT_DATA_ROOT=D:\\QuantData 时):
    D:\\QuantData\\
    ├── data_cache\\        # 历史行情缓存 (parquet)
    ├── output\\            # pipeline 输出
    ├── reports\\           # 分析报告
    ├── models\\             # ML 模型文件
    └── logs\\              # 运行日志
"""

from __future__ import annotations

import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def _load_env_file(env_path: Path) -> None:
    """手动解析 .env 文件并注入环境变量 (不依赖 python-dotenv)。

    仅设置尚未存在的环境变量, 不覆盖已有的。
    """
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # .env 解析失败不影响程序运行 (fail-safe)
        pass


# 项目根目录 (代码所在位置, 不可变)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 加载 .env 文件 (确保 QUANT_DATA_ROOT 等配置可用)
_load_env_file(_PROJECT_ROOT / ".env")

# 数据根目录: 优先环境变量, 默认项目根目录 (向后兼容)
_DATA_ROOT_ENV = os.environ.get("QUANT_DATA_ROOT", "").strip()
DATA_ROOT: Path = Path(_DATA_ROOT_ENV) if _DATA_ROOT_ENV else _PROJECT_ROOT

# 确保数据根目录存在 (仅在可写时创建)
try:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    # D 盘不存在或不可写时, 回退到项目根目录 (fail-safe)
    DATA_ROOT = _PROJECT_ROOT


def get_project_root() -> Path:
    """返回项目代码根目录 (E:\\各种PY程序\\28-终极量化交易系统8.4)"""
    return _PROJECT_ROOT


def get_data_root() -> Path:
    """返回数据存储根目录 (QUANT_DATA_ROOT 或项目根目录)"""
    return DATA_ROOT


def get_data_cache_dir() -> Path:
    """历史行情缓存目录 (parquet 文件)"""
    return DATA_ROOT / "data_cache"


def get_output_dir() -> Path:
    """pipeline 输出目录"""
    return DATA_ROOT / "output"


def get_reports_dir() -> Path:
    """分析报告目录"""
    return DATA_ROOT / "reports"


def get_models_dir() -> Path:
    """ML 模型文件目录"""
    return DATA_ROOT / "models"


def get_logs_dir() -> Path:
    """运行日志目录"""
    return DATA_ROOT / "logs"


def get_institutional_pipeline_report_dir() -> Path:
    """机构级 pipeline 报告目录 (output/institutional_pipeline)"""
    return get_output_dir() / "institutional_pipeline"


# ═══════════════════════════════════════════════════════════════════════
# v8.4+ 统一路径: 所有模块用这些函数获取路径, 禁止硬编码绝对路径
# ═══════════════════════════════════════════════════════════════════════

def get_config_dir() -> Path:
    """持仓/策略配置文件目录 (config/)"""
    return _PROJECT_ROOT / "config"


def get_v8_src_dir() -> Path:
    """v8.3 机构级模块源码目录 (v8.3_institutional/src/)"""
    return _PROJECT_ROOT / "v8.3_institutional" / "src"


def get_v8_root_dir() -> Path:
    """v8.3 机构级模块根目录"""
    return _PROJECT_ROOT / "v8.3_institutional"


def get_utils_dir() -> Path:
    """工具模块目录 (utils/)"""
    return _PROJECT_ROOT / "utils"


def get_tools_dir() -> Path:
    """辅助工具脚本目录 (tools/)"""
    return _PROJECT_ROOT / "tools"


def get_scripts_dir() -> Path:
    """批量/定时脚本目录 (scripts/)"""
    return _PROJECT_ROOT / "scripts"


def get_tests_dir() -> Path:
    """测试目录 (tests/)"""
    return _PROJECT_ROOT / "tests"


def get_research_dir() -> Path:
    """研究/因子挖掘目录 (research/)"""
    return _PROJECT_ROOT / "research"


def get_lgb_trainer_dir() -> Path:
    """LGB 训练器目录 (lgb_trainer/)"""
    return _PROJECT_ROOT / "lgb_trainer"


def get_ai_decision_dir() -> Path:
    """AI 决策模块目录 (ai_decision/)"""
    return _PROJECT_ROOT / "ai_decision"


def get_daily_workflow_dir() -> Path:
    """每日工作流脚本目录 (15_每日工作流/)"""
    return _PROJECT_ROOT / "15_每日工作流"


def setup_sys_path() -> None:
    """将项目根 + v8.3 根 + v8.3 src + utils 注入 sys.path (替代各处 sys.path.insert 硬编码).

    调用方式: 在模块顶部 import 之后立刻调用:
        from utils.path_config import setup_sys_path
        setup_sys_path()

    注入路径 (按优先级, 项目根在最前):
        1. _PROJECT_ROOT              — 项目根 (utils 包根目录)
        2. v8.3_institutional/        — 机构级根模块 (autolearn_trainer 等)
        3. v8.3_institutional/src/    — 机构级 src 子模块 (hedging/signals/risk 等)
        4. utils/                     — utils 子模块

    等价于旧写法:
        sys.path.insert(0, r"e:\\各种PY程序\\28-终极量化交易系统8.4")
        sys.path.insert(0, r"e:\\各种PY程序\\28-终极量化交易系统8.4\\v8.3_institutional")
        sys.path.insert(0, r"e:\\各种PY程序\\28-终极量化交易系统8.4\\v8.3_institutional\\src")
        sys.path.insert(0, r"e:\\各种PY程序\\28-终极量化交易系统8.4\\utils")
    """
    import sys as _sys
    _roots = [
        str(_PROJECT_ROOT),
        str(get_v8_root_dir()),  # v8.3_institutional/ (autolearn_trainer 等根模块)
        str(get_v8_src_dir()),   # v8.3_institutional/src/ (hedging/signals/risk 等)
        str(get_utils_dir()),   # utils/
    ]
    # 先 remove 已存在路径再 insert(0), 确保项目根始终在最前.
    # 修复路径遮蔽 bug: 旧版 "if p not in sys.path" 会让已存在的项目根被推后,
    # 导致 utils/reporting 等子目录遮蔽项目根的 reporting/ai 等顶层包.
    for p in reversed(_roots):
        if p in _sys.path:
            _sys.path.remove(p)
        _sys.path.insert(0, p)


def get_historical_base_file(symbol: str) -> Path:
    """获取标的 5y 基础缓存文件路径 (historical_{symbol}_5y_base.parquet)

    Args:
        symbol: 标的代码 (如 600519)
    Returns:
        parquet 文件完整路径
    """
    return get_data_cache_dir() / f"historical_{symbol}_5y_base.parquet"


def describe_paths() -> dict:
    """返回所有路径配置的描述 (用于日志/诊断)"""
    using_custom = _DATA_ROOT_ENV != "" and DATA_ROOT != _PROJECT_ROOT
    return {
        "project_root": str(_PROJECT_ROOT),
        "data_root": str(DATA_ROOT),
        "data_root_source": "QUANT_DATA_ROOT env" if using_custom else "project_root (default)",
        "data_cache": str(get_data_cache_dir()),
        "output": str(get_output_dir()),
        "reports": str(get_reports_dir()),
        "models": str(get_models_dir()),
        "logs": str(get_logs_dir()),
        "using_d_drive": str(DATA_ROOT).upper().startswith("D:"),
    }


# 模块加载时打印路径配置 (方便排查)
if __name__ == "__main__":
    import json
    logger.info(json.dumps(describe_paths(), indent=2, ensure_ascii=False))
