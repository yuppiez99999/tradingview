# -*- coding: utf-8 -*-
"""tests/unit — 单元测试层 (测试金字塔底层, 占比 ~70%)

设计原则:
    - 全 mock, 不依赖外部数据源 / 网络 / 文件 IO
    - 单测执行 <1s
    - 一个 bug 对应至少一个回归测试 (函数名含 bug 编号)
    - 使用 @pytest.mark.unit 标记

子模块:
    test_kill_switch_unit.py            — P0-D / P1-G 回归
    test_hedge_execution_engine_unit.py — P0-E 回归
    test_overnight_gap_monitor_unit.py  — BUG#1 回归
    test_market_circuit_breaker_unit.py — BUG#1b 回归
    test_risk_guard_integrator_unit.py  — BUG#4 回归
    test_pnl_report_compat_unit.py      — 报告数据结构兼容性
"""
