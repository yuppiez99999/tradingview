# -*- coding: utf-8 -*-
"""conftest.py — E2E 测试层专用 fixture (tests/e2e/)

pytest 自动加载规则: 仅识别名为 conftest.py 的文件
本文件为 tests/e2e/ 目录的 conftest.py

提供端到端测试所需的黄金数据加载:
    - e2e_reports_dir: v8.3 真实历史报告目录
    - e2e_trade_plans_dir: v8.3 真实交易计划目录
    - e2e_pnl_reports: 加载所有真实 pnl 报告
    - e2e_trade_plans: 加载所有真实交易计划
"""
import json
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def e2e_reports_dir(project_root):
    """v8.3 真实历史报告目录"""
    return Path(project_root) / "v8.3_institutional" / "reports"


@pytest.fixture(scope="session")
def e2e_trade_plans_dir(project_root):
    """v8.3 真实交易计划目录"""
    return Path(project_root) / "v8.3_institutional" / "trade_plans"


@pytest.fixture(scope="session")
def e2e_pnl_reports(e2e_reports_dir):
    """加载所有真实 pnl 报告 (黄金数据源)

    Yields:
        dict: {filename_stem: report_dict}

    数据缺失时 skip 而非 fail
    """
    if not e2e_reports_dir.exists():
        pytest.skip(f"E2E 报告目录不存在: {e2e_reports_dir}")

    pnl_files = sorted(e2e_reports_dir.glob("daily_pnl_report_*.json"))
    if not pnl_files:
        pytest.skip(f"E2E 无 pnl 报告文件: {e2e_reports_dir}")

    reports = {}
    for f in pnl_files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                reports[f.stem] = json.load(fp)
        except Exception:
            continue

    if not reports:
        pytest.skip("E2E 报告全部加载失败")

    return reports


@pytest.fixture(scope="session")
def e2e_trade_plans(e2e_trade_plans_dir):
    """加载所有真实交易计划"""
    if not e2e_trade_plans_dir.exists():
        pytest.skip(f"E2E 交易计划目录不存在: {e2e_trade_plans_dir}")

    plan_files = sorted(e2e_trade_plans_dir.glob("trade_plan_*.json"))
    if not plan_files:
        pytest.skip(f"E2E 无交易计划文件: {e2e_trade_plans_dir}")

    plans = {}
    for f in plan_files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                plans[f.stem] = json.load(fp)
        except Exception:
            continue

    if not plans:
        pytest.skip("E2E 交易计划全部加载失败")

    return plans
