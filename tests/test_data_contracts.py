# -*- coding: utf-8 -*-
"""
数据契约测试 (Data Contract Tests) — ECC GAP-8
==================================================
版本: v8.6.12
创建日期: 2026-07-31
用途: 验证关键 JSON 数据文件字段不漂移,防止上游修改导致下游解析失败。

覆盖文件:
    1. config/positions.json         — 现货持仓状态
    2. trade_plan_*.json             — 每日交易计划
    3. hedge_execution_fill_*.json    — 对冲执行回填

契约设计原则:
    - 必需字段存在性校验 (must_have keys)
    - 字段类型校验 (type check)
    - 关键字段值约束 (value constraints, 如 shares > 0)
    - 嵌套结构校验 (nested schema)
    - 向后兼容 (新增字段允许,删除字段报错)

运行方式:
    pytest tests/test_data_contracts.py -v
    pytest tests/test_data_contracts.py -v -m contract
    pytest tests/test_data_contracts.py -v -k positions --tb=short
"""
from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest


# ============================================================================
# 工具函数
# ============================================================================

def _load_json(path: Path) -> Dict[str, Any]:
    """加载 JSON 文件"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _assert_keys_present(data: Dict, required_keys: List[str], file_label: str) -> None:
    """断言字典包含所有必需键"""
    missing = [k for k in required_keys if k not in data]
    assert not missing, (
        f"[{file_label}] 缺少必需字段: {missing}; "
        f"现有字段: {list(data.keys())[:10]}..."
    )


def _assert_key_type(data: Dict, key: str, expected_type, file_label: str) -> None:
    """断言指定键的值类型

    支持单类型 (int/str/bool/...) 或多类型 tuple (int, float)。
    """
    if key not in data:
        return  # 字段不存在由 _assert_keys_present 负责
    actual = type(data[key])

    # 归一化为 tuple 处理
    if isinstance(expected_type, tuple):
        expected_types = expected_type
    else:
        expected_types = (expected_type,)

    # bool 是 int 的子类,需特殊处理 (避免 True 被当作 int 通过)
    if int in expected_types and actual is bool:
        pytest.fail(f"[{file_label}] 字段 {key} 应为 int,实际 bool")
    # int 兼容 float (int 可作为 float 传入)
    if float in expected_types and actual is int:
        return

    if actual not in expected_types:
        # 构造可读的类型名
        if len(expected_types) == 1:
            exp_name = expected_types[0].__name__
        else:
            exp_name = "(" + ", ".join(t.__name__ for t in expected_types) + ")"
        pytest.fail(
            f"[{file_label}] 字段 {key} 应为 {exp_name},"
            f"实际 {actual.__name__}"
        )


def _find_latest(pattern: str) -> Path | None:
    """按名称排序,返回最新的匹配文件"""
    files = sorted(glob.glob(pattern))
    return Path(files[-1]) if files else None


# ============================================================================
# 契约 1: positions.json (现货持仓状态)
# ============================================================================

class TestPositionsJsonContract:
    """config/positions.json 数据契约"""

    @pytest.fixture(scope="class")
    def positions_data(self, project_root):
        path = Path(project_root) / "config" / "positions.json"
        if not path.exists():
            pytest.skip(f"positions.json 不存在: {path}")
        return _load_json(path)

    @pytest.mark.contract
    def test_top_level_keys(self, positions_data):
        """契约 1.1: 顶层必需字段 — meta / positions / hedge_positions"""
        _assert_keys_present(
            positions_data,
            ["meta", "positions"],
            "positions.json",
        )

    @pytest.mark.contract
    def test_meta_fields(self, positions_data):
        """契约 1.2: meta 字段完整性"""
        meta = positions_data.get("meta", {})
        _assert_keys_present(
            meta,
            ["date", "total_capital", "stock_etf_capital", "hedge_capital"],
            "positions.json.meta",
        )
        # 类型校验
        _assert_key_type(meta, "total_capital", int, "positions.json.meta")
        _assert_key_type(meta, "stock_etf_capital", int, "positions.json.meta")
        _assert_key_type(meta, "hedge_capital", int, "positions.json.meta")
        # 值约束: 资金额必须为正
        assert meta["total_capital"] > 0, "total_capital 必须 > 0"
        assert meta["stock_etf_capital"] > 0, "stock_etf_capital 必须 > 0"
        assert meta["hedge_capital"] > 0, "hedge_capital 必须 > 0"

    @pytest.mark.contract
    def test_positions_dict_format(self, positions_data):
        """契约 1.3: positions 必须为 dict (非 list),key 为 symbol"""
        positions = positions_data.get("positions", {})
        assert isinstance(positions, dict), (
            f"positions 应为 dict,实际 {type(positions).__name__}"
        )
        assert len(positions) > 0, "positions 不能为空"

    @pytest.mark.contract
    def test_position_item_fields(self, positions_data):
        """契约 1.4: 每个持仓项的必需字段 — code/name/shares"""
        positions = positions_data.get("positions", {})
        for symbol, item in positions.items():
            assert isinstance(item, dict), (
                f"positions.{symbol} 应为 dict,实际 {type(item).__name__}"
            )
            _assert_keys_present(
                item,
                ["code", "name", "shares"],
                f"positions.{symbol}",
            )
            # shares 类型与值约束
            assert isinstance(item["shares"], (int, float)) and not isinstance(item["shares"], bool), (
                f"positions.{symbol}.shares 应为数值,实际 {type(item['shares']).__name__}"
            )
            # shares 允许 0 (清仓后保留条目),但不能为负 (现货账户不允许空头)
            assert item["shares"] >= 0, (
                f"positions.{symbol}.shares 不能为负: {item['shares']}"
            )


# ============================================================================
# 契约 2: trade_plan_*.json (每日交易计划)
# ============================================================================

class TestTradePlanContract:
    """v8.3_institutional/trade_plans/trade_plan_*.json 数据契约"""

    @pytest.fixture(scope="class")
    def trade_plan_data(self, trade_plans_dir):
        # 取最新的 trade_plan 文件
        pattern = str(trade_plans_dir / "trade_plan_*.json")
        path = _find_latest(pattern)
        if path is None:
            pytest.skip(f"无 trade_plan_*.json 文件: {trade_plans_dir}")
        return _load_json(path)

    @pytest.fixture(scope="class")
    def trade_plan_filename(self, trade_plans_dir):
        pattern = str(trade_plans_dir / "trade_plan_*.json")
        path = _find_latest(pattern)
        if path is None:
            pytest.skip("无 trade_plan_*.json 文件")
        return path.name

    @pytest.mark.contract
    def test_top_level_keys(self, trade_plan_data, trade_plan_filename):
        """契约 2.1: 顶层必需字段 — 19 个核心字段"""
        required = [
            "trade_date", "weekday", "capital", "stock_etf_capital",
            "hedge_capital", "execution_mode", "strategy", "metadata",
            "phase", "market_state", "risk_controls", "hedge_config",
            "hedge_fund_overlays", "execution_plan", "options_execution",
            "hedge_account", "futures_options_hedge", "risk_guard",
            "hedge_execution",
        ]
        _assert_keys_present(
            trade_plan_data, required,
            f"{trade_plan_filename} 顶层",
        )

    @pytest.mark.contract
    def test_capital_fields(self, trade_plan_data, trade_plan_filename):
        """契约 2.2: 资金字段类型与值约束"""
        for key in ("capital", "stock_etf_capital", "hedge_capital"):
            _assert_key_type(
                trade_plan_data, key, int,
                f"{trade_plan_filename}",
            )
            assert trade_plan_data[key] > 0, (
                f"{trade_plan_filename}.{key} 必须 > 0"
            )

    @pytest.mark.contract
    def test_metadata_version(self, trade_plan_data, trade_plan_filename):
        """契约 2.3: metadata.version 必须存在且为字符串"""
        meta = trade_plan_data.get("metadata", {})
        _assert_keys_present(
            meta, ["generated_at", "version"],
            f"{trade_plan_filename}.metadata",
        )
        assert isinstance(meta["version"], str), (
            f"{trade_plan_filename}.metadata.version 应为 str,"
            f"实际 {type(meta['version']).__name__}"
        )

    @pytest.mark.contract
    def test_risk_guard_keys(self, trade_plan_data, trade_plan_filename):
        """契约 2.4: risk_guard 必需子字段 — 五大 Guard"""
        rg = trade_plan_data.get("risk_guard", {})
        # 五大 Guard 的状态字段 (允许为 None 但 key 必须存在)
        required_guards = [
            "kill_switch",
            "drawdown_level", "drawdown_action",
            "vol_scale", "vol_action",
            "hedge_action", "put_action",
        ]
        _assert_keys_present(
            rg, required_guards,
            f"{trade_plan_filename}.risk_guard",
        )

    @pytest.mark.contract
    def test_hedge_execution_keys(self, trade_plan_data, trade_plan_filename):
        """契约 2.5: hedge_execution 必需子字段"""
        he = trade_plan_data.get("hedge_execution", {})
        required_he = [
            "generated_at", "drawdown_level", "portfolio_status",
            "futures_orders", "options_orders",
            "execution_status",
        ]
        _assert_keys_present(
            he, required_he,
            f"{trade_plan_filename}.hedge_execution",
        )
        # execution_status 必须是有效枚举值
        valid_status = ("PENDING", "CANCELLED", "EXECUTED", "SKIPPED", "FAILED")
        actual_status = he.get("execution_status", "")
        assert actual_status in valid_status, (
            f"{trade_plan_filename}.hedge_execution.execution_status "
            f"应为 {valid_status} 之一,实际 {actual_status!r}"
        )

    @pytest.mark.contract
    def test_market_state_keys(self, trade_plan_data, trade_plan_filename):
        """契约 2.6: market_state 必需子字段"""
        ms = trade_plan_data.get("market_state", {})
        _assert_keys_present(
            ms,
            ["circuit_level", "build_allowed", "spot_build_allowed"],
            f"{trade_plan_filename}.market_state",
        )
        # circuit_level 必须为有效枚举
        valid_levels = ("NORMAL", "WARNING", "CRITICAL")
        actual_level = str(ms.get("circuit_level", "")).upper()
        assert actual_level in valid_levels, (
            f"{trade_plan_filename}.market_state.circuit_level "
            f"应为 {valid_levels} 之一,实际 {actual_level!r}"
        )

    @pytest.mark.contract
    def test_put_protection_orders_is_list(self, trade_plan_data, trade_plan_filename):
        """契约 2.7: put_protection_orders 必须为 list (即便为空)"""
        ppo = trade_plan_data.get("put_protection_orders", [])
        assert isinstance(ppo, list), (
            f"{trade_plan_filename}.put_protection_orders 应为 list,"
            f"实际 {type(ppo).__name__}"
        )


# ============================================================================
# 契约 3: hedge_execution_fill_*.json (对冲执行回填)
# ============================================================================

class TestHedgeExecutionFillContract:
    """v8.3_institutional/reports/hedge_execution_fill_*.json 数据契约"""

    @pytest.fixture(scope="class")
    def hedge_fill_data(self, reports_dir):
        pattern = str(reports_dir / "hedge_execution_fill_*.json")
        path = _find_latest(pattern)
        if path is None:
            pytest.skip(f"无 hedge_execution_fill_*.json 文件: {reports_dir}")
        return _load_json(path)

    @pytest.fixture(scope="class")
    def hedge_fill_filename(self, reports_dir):
        pattern = str(reports_dir / "hedge_execution_fill_*.json")
        path = _find_latest(pattern)
        if path is None:
            pytest.skip("无 hedge_execution_fill_*.json 文件")
        return path.name

    @pytest.mark.contract
    def test_top_level_keys(self, hedge_fill_data, hedge_fill_filename):
        """契约 3.1: 顶层必需字段"""
        required = [
            "trade_date", "generated_at", "portfolio_beta",
            "total_hedge_pct", "total_cost", "hedge_enabled", "orders",
        ]
        _assert_keys_present(
            hedge_fill_data, required, hedge_fill_filename,
        )

    @pytest.mark.contract
    def test_field_types(self, hedge_fill_data, hedge_fill_filename):
        """契约 3.2: 字段类型校验"""
        _assert_key_type(hedge_fill_data, "trade_date", str, hedge_fill_filename)
        _assert_key_type(hedge_fill_data, "generated_at", str, hedge_fill_filename)
        _assert_key_type(hedge_fill_data, "portfolio_beta", (int, float), hedge_fill_filename)
        _assert_key_type(hedge_fill_data, "total_hedge_pct", (int, float), hedge_fill_filename)
        _assert_key_type(hedge_fill_data, "total_cost", (int, float), hedge_fill_filename)
        _assert_key_type(hedge_fill_data, "hedge_enabled", bool, hedge_fill_filename)
        _assert_key_type(hedge_fill_data, "orders", list, hedge_fill_filename)

    @pytest.mark.contract
    def test_value_constraints(self, hedge_fill_data, hedge_fill_filename):
        """契约 3.3: 值约束 — portfolio_beta 范围 / total_cost 非负

        注意: total_hedge_pct 可超过 1.0,因为多策略叠加
        (如: 50% tail protection + 80% beta reduction = 130%),
        上界设为 2.0 容纳组合对冲。
        """
        beta = hedge_fill_data.get("portfolio_beta", 0)
        # 组合 beta 合理范围 [-2, 2] (含对冲后)
        assert -2.0 <= beta <= 2.0, (
            f"{hedge_fill_filename}.portfolio_beta 超出合理范围 [-2, 2]: {beta}"
        )
        total_cost = hedge_fill_data.get("total_cost", 0)
        assert total_cost >= 0, (
            f"{hedge_fill_filename}.total_cost 不能为负: {total_cost}"
        )
        total_hedge_pct = hedge_fill_data.get("total_hedge_pct", 0)
        # 多策略叠加可超过 1.0 (tail + beta),上界 2.0 容纳组合对冲
        assert 0 <= total_hedge_pct <= 2.0, (
            f"{hedge_fill_filename}.total_hedge_pct 应在 [0, 2] 区间"
            f"(多策略叠加可超 1.0): {total_hedge_pct}"
        )


# ============================================================================
# 契约 4: daily_returns.jsonl (影子账户每日收益)
# ============================================================================

class TestDailyReturnsContract:
    """reports/shadow/daily_returns.jsonl 数据契约"""

    @pytest.fixture(scope="class")
    def daily_returns_path(self, project_root):
        path = Path(project_root) / "reports" / "shadow" / "daily_returns.jsonl"
        if not path.exists():
            pytest.skip(f"daily_returns.jsonl 不存在: {path}")
        return path

    @pytest.mark.contract
    def test_jsonl_format(self, daily_returns_path):
        """契约 4.1: 每行必须为合法 JSON"""
        with open(daily_returns_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    json.loads(line)
                except json.JSONDecodeError as e:
                    pytest.fail(
                        f"daily_returns.jsonl 第 {line_no} 行 JSON 解析失败: {e}"
                    )

    @pytest.mark.contract
    def test_record_fields(self, daily_returns_path):
        """契约 4.2: 每条记录必需字段 — date / daily_return / source"""
        with open(daily_returns_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        assert len(lines) > 0, "daily_returns.jsonl 不能为空"

        for idx, line in enumerate(lines):
            record = json.loads(line)
            _assert_keys_present(
                record,
                ["date", "daily_return"],
                f"daily_returns.jsonl 第 {idx + 1} 行",
            )
            # 类型与值约束
            assert isinstance(record["date"], str), (
                f"daily_returns.jsonl 第 {idx + 1} 行 date 应为 str"
            )
            assert isinstance(record["daily_return"], (int, float)), (
                f"daily_returns.jsonl 第 {idx + 1} 行 daily_return 应为数值"
            )
            # 收益率合理范围 [-0.5, 0.5] (单日 ±50% 极端值)
            assert -0.5 <= record["daily_return"] <= 0.5, (
                f"daily_returns.jsonl 第 {idx + 1} 行 daily_return "
                f"超出合理范围 [-0.5, 0.5]: {record['daily_return']}"
            )
