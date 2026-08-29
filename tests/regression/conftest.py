"""V9 基线回归测试 — conftest.

模块整合 8.4 — T1.8

提供以下 fixture:
    - v9_baseline_lock: V9_BASELINE_LOCK.txt 路径与解析内容
    - v9_backtest_json: V9 回测原始结果 JSON (含 30 个月 records)
    - v9_dsr_maxpass_json: V9 DSR max_pass 评估结果 JSON
    - v9_baseline_metrics: 整合的基线指标字典 (供测试断言)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest


def pytest_configure(config):
    """注册 nightly marker (用于 Layer 5 完整回测, CI 默认跳过)."""
    config.addinivalue_line(
        "markers",
        "nightly: 仅 nightly CI 运行的长耗时回归测试 (>30 分钟, 如 V9 完整回测)",
    )


# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 基线锁定文件
BASELINE_LOCK_FILE = PROJECT_ROOT / "docs" / "模块整合_8.4" / "V9_BASELINE_LOCK.txt"

# 基线 JSON 目录
VALIDATION_REPORTS_DIR = PROJECT_ROOT / "output" / "validation_reports"


def _find_latest_json(prefix: str) -> Path:
    """在 validation_reports 目录中查找指定前缀的最新 JSON 文件.

    Args:
        prefix: 文件前缀 (如 "v9_regime_specific_backtest_" 或 "v9_dsr_maxpass_")

    Returns:
        最新文件的 Path

    Raises:
        FileNotFoundError: 未找到任何匹配文件
    """
    if not VALIDATION_REPORTS_DIR.exists():
        raise FileNotFoundError(
            f"validation_reports 目录不存在: {VALIDATION_REPORTS_DIR}"
        )

    candidates = sorted(VALIDATION_REPORTS_DIR.glob(f"{prefix}*.json"))
    if not candidates:
        raise FileNotFoundError(
            f"未找到 {prefix}*.json 文件 (目录: {VALIDATION_REPORTS_DIR})"
        )
    # 取最新一个 (文件名含时间戳, 字典序 = 时间序)
    return candidates[-1]


def _parse_baseline_lock(text: str) -> dict[str, str]:
    """解析 V9_BASELINE_LOCK.txt 文本为字典.

    Args:
        text: LOCK 文件原始文本

    Returns:
        包含 commit_hash / commit_subject / commit_time / lock_date 等字段的字典
    """
    result: dict[str, str] = {}

    # commit hash (40 位十六进制)
    m = re.search(r"基线 commit hash:\s*([0-9a-f]{40})", text)
    if m:
        result["commit_hash"] = m.group(1)

    # commit 主题
    m = re.search(r"基线 commit 主题:\s*(.+)$", text, re.MULTILINE)
    if m:
        result["commit_subject"] = m.group(1).strip()

    # commit 时间
    m = re.search(r"基线 commit 时间:\s*(.+)$", text, re.MULTILINE)
    if m:
        result["commit_time"] = m.group(1).strip()

    # 锁定日期
    m = re.search(r"锁定日期:\s*(.+)$", text, re.MULTILINE)
    if m:
        result["lock_date"] = m.group(1).strip()

    # 各项指标 (作为字符串保留, 测试中再转换)
    for key, pattern in [
        ("dsr_max_pass", r"DSR.*?max_pass:\s*(\d+)"),
        ("annual_return", r"年化收益率:\s*([\d.]+)%?"),
        ("max_drawdown", r"最大回撤:\s*([\d.]+)%?"),
        ("sharpe_cv", r"Sharpe CV.*?:\s*([\d.]+)"),
    ]:
        m = re.search(pattern, text)
        if m:
            result[key] = m.group(1)

    return result


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(scope="session")
def v9_baseline_lock() -> dict[str, Any]:
    """加载 V9_BASELINE_LOCK.txt.

    Returns:
        含 raw_text 与 parsed 字段的字典

    Raises:
        FileNotFoundError: LOCK 文件不存在
    """
    if not BASELINE_LOCK_FILE.exists():
        raise FileNotFoundError(
            f"V9_BASELINE_LOCK.txt 不存在: {BASELINE_LOCK_FILE}\n"
            f"HC-1 硬约束要求该文件必须存在以锁定基线 commit"
        )
    text = BASELINE_LOCK_FILE.read_text(encoding="utf-8")
    parsed = _parse_baseline_lock(text)
    return {
        "path": BASELINE_LOCK_FILE,
        "raw_text": text,
        "parsed": parsed,
    }


@pytest.fixture(scope="session")
def v9_backtest_json() -> dict[str, Any]:
    """加载 V9 回测原始结果 JSON (含 30 个月 records).

    Returns:
        回测结果字典 (含 symbols / period / months / annual_return /
        max_drawdown / win_rate / records 等字段)
    """
    path = _find_latest_json("v9_regime_specific_backtest_")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {"path": path, "data": data}


@pytest.fixture(scope="session")
def v9_dsr_maxpass_json() -> dict[str, Any]:
    """加载 V9 DSR max_pass 评估结果 JSON.

    Returns:
        DSR 评估字典 (含 sharpe_annual / max_pass / sharpe_cv /
        annual_return / max_drawdown / win_rate / all_pass 等字段)
    """
    path = _find_latest_json("v9_dsr_maxpass_")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {"path": path, "data": data}


@pytest.fixture(scope="session")
def v9_baseline_metrics(
    v9_baseline_lock: dict[str, Any],
    v9_dsr_maxpass_json: dict[str, Any],
) -> dict[str, Any]:
    """整合的基线指标字典.

    优先取 DSR maxpass JSON 中的精确值 (Bailey 标准公式结果),
    LOCK 文件中的值仅作参考 (旧公式 DSR=8 已被 v2 公式 DSR=18 取代).

    Returns:
        含 dsr_max_pass / annual_return / max_drawdown / sharpe_cv /
        win_rate / n_months / commit_hash 等字段的字典
    """
    dsr_data = v9_dsr_maxpass_json["data"]
    lock_parsed = v9_baseline_lock["parsed"]

    return {
        # 评估指标 (来自 DSR maxpass JSON, 标准公式)
        "dsr_max_pass": int(dsr_data.get("max_pass", 0)),
        "dsr_max_pass_old": int(dsr_data.get("max_pass_old_formula", 0)),
        "annual_return": float(dsr_data.get("annual_return", 0.0)),
        "max_drawdown": float(dsr_data.get("max_drawdown", 0.0)),
        "sharpe_cv": float(dsr_data.get("sharpe_cv", 999.0)),
        "sharpe_cv_rolling": float(dsr_data.get("sharpe_cv_new_rolling", 999.0)),
        "win_rate": float(dsr_data.get("win_rate", 0.0)),
        "n_months": int(dsr_data.get("n_months", 0)),
        "sharpe_annual": float(dsr_data.get("sharpe_annual", 0.0)),
        # 基线 commit (来自 LOCK 文件)
        "commit_hash": lock_parsed.get("commit_hash", ""),
        "commit_subject": lock_parsed.get("commit_subject", ""),
        "lock_date": lock_parsed.get("lock_date", ""),
        # 验收阈值 (HC-1, 来自 ALIGNMENT 文档)
        "thresholds": {
            "dsr_max_pass_min": 5,
            "annual_return_min": 0.15,
            "max_drawdown_max": 0.10,
            "sharpe_cv_max": 1.0,
            "win_rate_min": 0.60,  # 额外指标
        },
    }
