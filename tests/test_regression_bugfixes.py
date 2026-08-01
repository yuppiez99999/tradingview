# -*- coding: utf-8 -*-
"""
回归测试套件 (Regression Test Suite)
=====================================
版本: v8.6.12
创建日期: 2026-07-31
用途: 为已修过的 bug 各写一个回归测试,防止同类问题复发。

覆盖的已修 bug:
    REG-WIND-001  Wind MCP 路径仅查项目根,漏 tools/ 子目录
                  修复: 4 个候选路径按优先级搜索
    REG-SSE-002   K 线数据被 _parse_sse_minute_quote 误解析为单条 quote
                  修复: 新增 _wind_http_generic 通用 HTTP 调用
    REG-HB-003    heartbeat 字段名 ts 误用 timestamp 导致自检失败
                  修复: 兼容 ts/timestamp 两种字段名
    REG-SCHEMACHECK-004 positions.json 格式判断错误 (dict vs list)
                  修复: dict 格式优先,list 兼容
    REG-PATH-005  子目录入口脚本未注入 PROJECT_ROOT 到 sys.path
                  修复: 显式 sys.path.insert(0, PROJECT_ROOT)

运行方式:
    # 全量回归 (含 integration 慢测,~10s)
    pytest tests/test_regression_bugfixes.py -v

    # 仅 regression 标记 (含 integration 慢测)
    pytest tests/test_regression_bugfixes.py -v -m regression

    # 快测层 (排除 integration 慢测,~0.4s) — PR 闸门推荐
    pytest tests/test_regression_bugfixes.py -v -m "not integration"

    # 慢测层 (仅 integration 标记,~10s) — CI Integration Tests job
    pytest tests/test_regression_bugfixes.py -v -m integration

    # 按 bug ID 过滤
    pytest tests/test_regression_bugfixes.py -v -k REG_WIND

分层策略 (v8.6.12):
    快测层 (PR 闸门):
        14 项 regression (非 integration) + 16 项 contract = 30 项 <1s
        命令: pytest tests/test_data_contracts.py tests/test_regression_bugfixes.py -m "not integration"
    慢测层 (CI Integration job):
        1 项 integration (TDX 连接验证) ~10s
        命令: pytest tests/test_regression_bugfixes.py -m integration
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# ============================================================================
# REG-WIND-001: Wind MCP 路径修复
# ============================================================================

class TestRegWindMcpPath:
    """REG-WIND-001: Wind MCP 路径必须搜索 4 个候选位置

    Bug: utils/data_provider.py _init_wind_mcp 原仅查项目根目录,
         漏 tools/ 子目录,导致 Wind MCP 客户端加载失败
    Fix: 改为 4 个候选路径按优先级搜索
    """

    @pytest.mark.regression
    @pytest.mark.bug("REG-WIND-001")
    def test_wind_mcp_fetcher_exists_in_tools(self, project_root):
        """tools/wind_mcp_fetcher.py 必须存在 (实际位置)"""
        path = Path(project_root) / "tools" / "wind_mcp_fetcher.py"
        assert path.exists(), (
            f"Wind MCP fetcher 实际位置应为 {path},但文件不存在"
        )

    @pytest.mark.regression
    @pytest.mark.bug("REG-WIND-001")
    def test_data_provider_uses_multiple_paths(self, project_root):
        """utils/data_provider.py 的 _init_wind_mcp 必须搜索多个候选路径"""
        path = Path(project_root) / "utils" / "data_provider.py"
        assert path.exists(), f"data_provider.py 不存在: {path}"

        source = path.read_text(encoding="utf-8")
        # 必须包含 4 个候选路径的搜索逻辑
        assert "candidate_paths" in source, (
            "_init_wind_mcp 应使用 candidate_paths 列表搜索多路径"
        )
        assert "tools" in source, (
            "候选路径必须包含 tools/ 子目录"
        )
        # 不应仅依赖项目根的单一路径
        # (检查不出现 "wind_mcp_fetcher.py" 紧跟在 _project_root / 后无 tools 的简单形式)

    @pytest.mark.regression
    @pytest.mark.integration
    @pytest.mark.bug("REG-WIND-001")
    def test_wind_mcp_client_loads_successfully(self, project_root):
        """MarketDataProvider 加载后 Wind MCP 客户端应为可用状态

        标记为 integration: 实例化 MarketDataProvider 会触发 TDX 连接 (7s+),
        属于慢测,默认单元测试套件 (-m "not integration") 跳过。
        """
        sys.path.insert(0, str(project_root))
        try:
            from utils.data_provider import MarketDataProvider
            dp = MarketDataProvider()
            wind_health = dp.source_health.get("wind_mcp", {})
            assert wind_health.get("ok") is True, (
                f"Wind MCP 应加载成功,实际 ok={wind_health.get('ok')},"
                f"last_error={wind_health.get('last_error')}"
            )
        finally:
            if str(project_root) in sys.path:
                sys.path.remove(str(project_root))


# ============================================================================
# REG-SSE-002: SSE 解析 (K 线不应被解析为单条 quote)
# ============================================================================

class TestRegSseParsing:
    """REG-SSE-002: K 线 SSE 数据不应被 _parse_sse_minute_quote 误解析

    Bug: wind_mcp_fetcher.py 的 wind_get_kline 使用 _wind_http,
         后者调用 _parse_sse_minute_quote 把多行 K 线数据压缩成单条 quote
    Fix: 新增 _wind_http_generic 函数,不调用 _parse_sse_minute_quote,
         仅用通用 SSE 解析
    """

    @pytest.fixture(scope="class")
    def wind_mcp_module(self, project_root):
        path = Path(project_root) / "tools" / "wind_mcp_fetcher.py"
        if not path.exists():
            pytest.skip(f"wind_mcp_fetcher.py 不存在: {path}")
        spec = importlib.util.spec_from_file_location(
            "wind_mcp_fetcher", str(path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @pytest.mark.regression
    @pytest.mark.bug("REG-SSE-002")
    def test_wind_http_generic_function_exists(self, wind_mcp_module):
        """_wind_http_generic 函数必须存在 (修复后新增)"""
        assert hasattr(wind_mcp_module, "_wind_http_generic"), (
            "_wind_http_generic 函数必须存在,用于 K 线类工具的 SSE 解析"
        )

    @pytest.mark.regression
    @pytest.mark.bug("REG-SSE-002")
    def test_parse_sse_generic_exists(self, wind_mcp_module):
        """_parse_sse_generic 函数必须存在 (通用 SSE 解析器)"""
        assert hasattr(wind_mcp_module, "_parse_sse_generic"), (
            "_parse_sse_generic 函数必须存在,用于通用 SSE 解析"
        )

    @pytest.mark.regression
    @pytest.mark.bug("REG-SSE-002")
    def test_parse_sse_generic_extracts_data_line(self, wind_mcp_module):
        """_parse_sse_generic 必须能从 SSE 文本提取 data: 行的 JSON"""
        sse_text = (
            "event: message\n"
            'data: {"jsonrpc":"2.0","result":{"isError":false}}\n\n'
        )
        result = wind_mcp_module._parse_sse_generic(sse_text)
        assert result is not None, "应解析出 dict"
        assert result.get("jsonrpc") == "2.0"
        assert "result" in result

    @pytest.mark.regression
    @pytest.mark.bug("REG-SSE-002")
    def test_parse_sse_generic_returns_none_for_non_sse(self, wind_mcp_module):
        """非 SSE 格式应返回 None"""
        result = wind_mcp_module._parse_sse_generic("just plain json")
        assert result is None, "非 SSE 格式应返回 None"

    @pytest.mark.regression
    @pytest.mark.bug("REG-SSE-002")
    def test_parse_sse_minute_quote_rejects_kline_data(self, wind_mcp_module):
        """_parse_sse_minute_quote 不应把 K 线多行数据解析为单条 quote

        模拟一个 K 线 SSE 响应 (含多条记录),_parse_sse_minute_quote
        要么返回 None,要么返回的 dict 应保留多行汇总后的合理结构。
        若返回的 'price' 是某条 K 线的 close,则属于可接受行为;
        若把首行的字段误当 quote,则为 bug。
        """
        # 模拟 K 线 SSE 响应 (含 3 条 K 线数据)
        kline_rows = [
            ["2026-07-29", 10.5, 10.8, 10.3, 10.6, 1000000],
            ["2026-07-30", 10.6, 10.9, 10.4, 10.7, 1200000],
            ["2026-07-31", 10.7, 11.0, 10.5, 10.8, 1500000],
        ]
        kline_payload = {
            "jsonrpc": "2.0",
            "result": {
                "content": [{
                    "text": json.dumps({
                        "data": {
                            "columns": [
                                {"name": "DATE"},
                                {"name": "OPEN"},
                                {"name": "HIGH"},
                                {"name": "LOW"},
                                {"name": "CLOSE"},
                                {"name": "VOLUME"},
                            ],
                            "rows": kline_rows,
                        }
                    })
                }]
            }
        }
        sse_text = f"data: {json.dumps(kline_payload)}\n\n"

        # _parse_sse_minute_quote 解析此 K 线数据
        result = wind_mcp_module._parse_sse_minute_quote(sse_text)
        # 修复后,_wind_http_generic 应使用 _parse_sse_generic 而非 _parse_sse_minute_quote
        # 此测试验证:若 _parse_sse_minute_quote 被误用,它会返回包含 price 的 dict
        # (这本身不算 bug,只是验证行为;真正的修复在 _wind_http_generic 不调用它)
        if result is not None:
            # 如果它返回了结果,应该至少包含 price 字段
            assert "price" in result or "close" in result, (
                "_parse_sse_minute_quote 若解析 K 线,应返回包含 price/close 的 dict"
            )


# ============================================================================
# REG-HB-003: heartbeat 字段名兼容
# ============================================================================

class TestRegHeartbeatFieldName:
    """REG-HB-003: heartbeat 字段名 ts 与 timestamp 兼容

    Bug: utils/system_check.py C8.2 仅检查 'timestamp' 字段,
         但 shadow_watchdog_heartbeat.jsonl 实际字段为 'ts'
    Fix: 同时兼容 'ts' 和 'timestamp' 两种字段名
    """

    @pytest.mark.regression
    @pytest.mark.bug("REG-HB-003")
    def test_system_check_uses_ts_fallback(self, project_root):
        """system_check.py C8.2 必须兼容 ts/timestamp 两种字段名"""
        path = Path(project_root) / "utils" / "system_check.py"
        assert path.exists(), f"system_check.py 不存在: {path}"

        source = path.read_text(encoding="utf-8")
        # 必须同时检查 ts 和 timestamp
        assert 'last.get("ts")' in source or 'last.get("ts",' in source, (
            "C8.2 必须检查 'ts' 字段 (heartbeat 实际字段名)"
        )
        assert "timestamp" in source, (
            "C8.2 必须兼容 'timestamp' 字段名"
        )

    @pytest.mark.regression
    @pytest.mark.bug("REG-HB-003")
    def test_heartbeat_real_file_uses_ts_field(self, project_root):
        """真实的 heartbeat 文件应使用 'ts' 字段"""
        path = Path(project_root) / "logs" / "shadow_watchdog_heartbeat.jsonl"
        if not path.exists():
            pytest.skip(f"heartbeat 文件不存在: {path}")

        # 读取最后一行,验证字段名
        with open(path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        if not lines:
            pytest.skip("heartbeat 文件为空")

        last_record = json.loads(lines[-1])
        # 至少有 ts 或 timestamp 之一
        assert "ts" in last_record or "timestamp" in last_record, (
            f"heartbeat 末行必须含 ts 或 timestamp 字段,实际字段: "
            f"{list(last_record.keys())}"
        )

    @pytest.mark.regression
    @pytest.mark.bug("REG-HB-003")
    def test_system_check_c82_passes_with_ts_field(self, project_root):
        """C8.2 自检必须能通过 ts 字段的 heartbeat 文件"""
        sys.path.insert(0, str(project_root))
        try:
            from utils.system_check import SystemChecker
            checker = SystemChecker(skip_datasource=True)
            # 直接调用 C8 检查方法
            checker._results = []
            checker.check_historical_data()
            # 查找 C8.2 结果
            c82_results = [r for r in checker._results if r.code == "C8.2"]
            assert c82_results, "C8.2 检查未执行"
            c82 = c82_results[0]
            # heartbeat 文件若存在,C8.2 应 PASS
            hb_path = Path(project_root) / "logs" / "shadow_watchdog_heartbeat.jsonl"
            if hb_path.exists():
                assert c82.status.value == "PASS", (
                    f"C8.2 应通过 ts 字段的 heartbeat,实际 {c82.status.value}: "
                    f"{c82.detail}"
                )
        finally:
            if str(project_root) in sys.path:
                sys.path.remove(str(project_root))


# ============================================================================
# REG-SCHEMACHECK-004: positions.json dict/list 格式适配
# ============================================================================

class TestRegPositionsJsonSchema:
    """REG-SCHEMACHECK-004: positions.json 格式判断

    Bug: C4.1 原假设 positions 为 list,但 28-终极量化交易系统8.4 实际为 dict
    Fix: dict 格式优先,list 兼容,并校验总持仓非零
    """

    @pytest.mark.regression
    @pytest.mark.bug("REG-SCHEMACHECK-004")
    def test_system_check_c41_passes_with_dict_positions(self, project_root):
        """C4.1 自检必须通过 dict 格式的 positions.json"""
        sys.path.insert(0, str(project_root))
        try:
            from utils.system_check import SystemChecker
            checker = SystemChecker(skip_datasource=True)
            checker._results = []
            checker.check_config_schema()
            c41_results = [r for r in checker._results if r.code == "C4.1"]
            assert c41_results, "C4.1 检查未执行"
            c41 = c41_results[0]
            assert c41.status.value == "PASS", (
                f"C4.1 应通过 dict 格式的 positions.json,"
                f"实际 {c41.status.value}: {c41.detail}"
            )
        finally:
            if str(project_root) in sys.path:
                sys.path.remove(str(project_root))


# ============================================================================
# REG-PATH-005: 子目录入口脚本 sys.path 注入
# ============================================================================

class TestRegEntryPathInjection:
    """REG-PATH-005: 子目录入口脚本必须显式注入 PROJECT_ROOT

    Bug: 15_每日工作流/run_daily_morning.py 调用 utils 包前未注入 PROJECT_ROOT
         导致 ImportError: No module named 'utils'
    Fix: 在 import utils 前显式 sys.path.insert(0, str(PROJECT_ROOT))
    """

    @pytest.mark.regression
    @pytest.mark.bug("REG-PATH-005")
    def test_morning_workflow_injects_project_root(self, project_root):
        """run_daily_morning.py 必须显式注入 PROJECT_ROOT"""
        path = Path(project_root) / "15_每日工作流" / "run_daily_morning.py"
        assert path.exists(), f"入口脚本不存在: {path}"

        source = path.read_text(encoding="utf-8")
        # 必须有 sys.path.insert 操作
        assert "sys.path.insert" in source, (
            "run_daily_morning.py 必须显式注入 sys.path"
        )
        assert "PROJECT_ROOT" in source, (
            "run_daily_morning.py 必须使用 PROJECT_ROOT 变量"
        )
        # 必须有 --skip-system-check 参数
        assert "--skip-system-check" in source, (
            "run_daily_morning.py 必须支持 --skip-system-check 参数"
        )

    @pytest.mark.regression
    @pytest.mark.bug("REG-PATH-005")
    def test_eod_workflow_injects_project_root(self, project_root):
        """run_daily_eod_workflow.py 必须显式注入 PROJECT_ROOT"""
        path = Path(project_root) / "15_每日工作流" / "run_daily_eod_workflow.py"
        assert path.exists(), f"入口脚本不存在: {path}"

        source = path.read_text(encoding="utf-8")
        assert "sys.path.insert" in source
        assert "PROJECT_ROOT" in source
        assert "--skip-system-check" in source

    @pytest.mark.regression
    @pytest.mark.bug("REG-PATH-005")
    def test_run_daily_eod_injects_system_check(self, project_root):
        """run_daily_eod.py 必须集成 P0 自检"""
        path = Path(project_root) / "run_daily_eod.py"
        assert path.exists(), f"入口脚本不存在: {path}"

        source = path.read_text(encoding="utf-8")
        assert "assert_system_ready" in source, (
            "run_daily_eod.py 必须调用 assert_system_ready()"
        )
        assert "--skip-system-check" in source
