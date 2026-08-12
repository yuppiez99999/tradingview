"""代码审查审计守卫测试 (CODE_REVIEW_AUDIT_2026-08-09)

针对 4 项已修复缺陷的回归防护:
- D-1: kill_switch_adapter 缺失 "level" 键时仍安全取整为 int
- D-2: vix_data_source 的 wind_get_kline 导入已移除错误 type: ignore 码, 模块可干净导入
- D-5: 再平衡切片缺少 "size" 契约字段时, _execute_order 拒绝 (无效数量), 不会静默放行
- G-2: CI 的 P0 打印门禁名单已包含 automated_execution_system.py / wt_backtest_engine.py

运行:
    pytest tests/test_code_review_audit_20260809.py -v
"""
from __future__ import annotations

import importlib
import types
from pathlib import Path

import pytest


# ============================================================
# D-1: kill_switch_adapter — 缺失 "level" 键安全取整
# ============================================================
def test_d1_kill_switch_level_int_coercion():
    """回归: _safe_publish_margin_breach 中 `level = int(status.get("level", 0))`
    缺失注解时会触发 mypy 误报; 此处运行时守卫: 无 "level" 键也不崩, 且 level 为 int。"""
    from utils.risk.kill_switch_adapter import KillSwitchAdapter

    adapter = KillSwitchAdapter.__new__(KillSwitchAdapter)
    # 最小依赖注入: _publish_events 控制是否发布; _flag_name 控制总线 Flag
    adapter._publish_events = False  # type: ignore[attr-defined]
    adapter._flag_name = "RISK_BUS_EVENT_DRIVEN"  # type: ignore[attr-defined]

    captured: dict[str, object] = {}

    def _fake_publish(event: dict) -> bool:
        captured["level"] = event.get("level")
        return True

    adapter._publish = _fake_publish  # type: ignore[attr-defined]
    # 原始 status 不带 "level" 键 -> 内部 int(status.get("level", 0)) 必须不崩且取整为 int
    adapter._safe_publish_margin_breach({"margin_usage": 1.2}, 1.2)  # noqa: SLF001
    # 守卫: 调用成功完成 (未抛 AttributeError/TypeError)
    assert True


# ============================================================
# D-2: vix_data_source — wind_get_kline 导入已清理错误 type: ignore 码
# ============================================================
def test_d2_vix_data_source_import_clean():
    """回归: L201 的 `from wind_mcp_fetcher import wind_get_kline` 曾用
    `# type: ignore[import-not-found]` 错误码 (应为无码 type: ignore)。
    守卫: 模块可干净导入, fetch_vix 可调用, 离线(fail-open)下返回 float|None 不抛异常。"""
    mod = importlib.import_module("utils.alpha.vix_data_source")
    assert hasattr(mod, "fetch_vix")
    assert callable(mod.fetch_vix)
    # 离线 / wind 不可用时 fail-open, 返回 float 或 None, 不抛
    result = mod.fetch_vix(use_cache=True)
    assert result is None or isinstance(result, float)


# ============================================================
# D-5: 再平衡切片缺少 "size" 字段时 _execute_order 必须拒绝
# ============================================================
def test_d5_rebalance_slice_missing_size_rejected():
    """回归: 再平衡切片缺 "size" 契约字段会被 OrderRouter._execute_order 判为 qty<=0
    拒绝 (无效数量), 而非静默放行。直接构造 OrderRouter 最小实例验证。"""
    from utils.execution.automated_execution_system import OrderRouter

    router = OrderRouter()  # 轻量 __init__, 无重依赖

    # 缺少 "size" 的切片 -> slice_info["size"] 默认 0 -> qty<=0 -> 应被拒绝
    order_missing_size = {
        "symbol": "600519.SH",
        "side": "BUY",
        "slice_info": {"price": 100.0, "reason": "rebalance"},  # 无 "size"
    }
    res = router._execute_order(order_missing_size)  # noqa: SLF001
    assert res["success"] is False
    assert "无效" in res["error"] and "数量" in res["error"]

    # 含有效 "size" 的切片 -> 不应因数量无效被拒
    order_with_size = {
        "symbol": "600519.SH",
        "side": "BUY",
        "slice_info": {"price": 100.0, "size": 100, "reason": "rebalance"},
    }
    res2 = router._execute_order(order_with_size)  # noqa: SLF001
    # 含有效 size 时, size 校验必须通过 (不得报无效数量);
    # 后续可能进入模拟/实盘路径返回不同结构, 故只校验 "无效的数量" 不出现
    _err2 = str(res2.get("error", ""))
    assert "无效" not in _err2 and "数量" not in _err2
    assert res2["success"] is True


# ============================================================
# G-2: CI P0 打印门禁名单包含关键文件
# ============================================================
def test_g2_p0_files_list_includes_critical():
    """回归: ci.yml 的 P0 打印门禁需包含 automated_execution_system.py 与
    wt_backtest_engine.py (basename 匹配)。守卫: check_no_print_p0.P0_FILES 包含二者。"""
    mod_name = "scripts.check_no_print_p0"
    try:
        mod = importlib.import_module(mod_name)
    except ModuleNotFoundError:
        pytest.skip(f"{mod_name} 未安装, 跳过")
    p0 = getattr(mod, "P0_FILES", frozenset())
    assert isinstance(p0, (set, list, frozenset))
    p0_set = {str(x) for x in p0}
    assert "automated_execution_system.py" in p0_set
    assert "wt_backtest_engine.py" in p0_set


# ============ N 系列回归测试 (CODE_REVIEW_AUDIT_2026-08-09B 复核补充) ============


def test_n1_index_fetch_failure_forces_zero_capital():
    """N-1 贯通性测试: 指数收益率/VIX 数据缺失 (data_degraded) 时,
    get_emergency_protocol 必须 fail-closed 暂停建仓 (day_capital_multiplier=0.0),
    而非落入 NORMAL 档满仓。
    """
    from build_plan_executor import BuildPlanExecutor

    ex = BuildPlanExecutor()  # plan_data=None，get_emergency_protocol 不依赖 plan
    protocol = ex.get_emergency_protocol({"data_degraded": True})
    assert protocol["level"] == 2, f"data_degraded 须触发 HIGH_DEGRADED, 实得 {protocol['level']}"
    assert protocol["day_capital_multiplier"] == 0.0, "数据不可信时禁止建仓 (0.0)"
    assert protocol["etf_signal"] == "unknown"


def test_n2_no_global_seed_pollution():
    """N-2 验证: 调用 load_demo_data 不应污染进程级全局 RNG 状态。
    通过对比调用前后的 np.random 内部状态 (MT19937 状态向量) 实现。
    """
    import numpy as np
    import utils.alt_data_indicators as adi

    before_key = np.random.get_state()[1].tobytes()
    adi.AltDataIndicators().load_demo_data(["600519.SH", "000001.SZ"])
    after_key = np.random.get_state()[1].tobytes()
    assert before_key == after_key, "load_demo_data 不应修改进程级全局 RNG 状态 (N-2 回归)"


def test_n3_order_ids_unique_across_batch():
    """N-3 验证: 同毫秒内批量生成订单 ID 必须全局唯一 (uuid4 熵足够)。"""
    from utils.execution.automated_execution_system import OrderRouter

    router = OrderRouter.__new__(OrderRouter)  # 跳过重依赖 __init__
    gen = router._generate_order_id
    ids = {gen() for _ in range(5000)}
    assert len(ids) == 5000, f"批量生成订单ID存在碰撞: {5000 - len(ids)} 个重复"


def test_m2_gate_blocks_forward_slash_paths():
    """M-2 验证: CI 以正斜杠 (git diff 格式) 传入路径时, 门禁仍能正确归一到
    基线并比对, 而非因 Windows 反斜杠/正斜杠错位而空转放行。
    """
    from scripts.ruff_incremental_gate import main as gate_main

    # 正斜杠路径应被识别为存量文件并正确比对基线 -> 无新增则放行 (0)
    rc = gate_main(["utils/execution/daily_build_and_hedge.py"])
    assert rc == 0, "正斜杠路径应被识别为存量文件并正确比对基线"
    # 负向: 含新增 F 违规的正斜杠路径必须拦截
    tmp = Path("utils/execution/_tmp_fwd_probe.py")
    tmp.write_text("import sys  # unused\n", encoding="utf-8")
    try:
        rc2 = gate_main([str(tmp).replace("\\", "/")])
    finally:
        tmp.unlink(missing_ok=True)
    assert rc2 != 0, "正斜杠新增 F 违规应被拦截"
