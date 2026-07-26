# -*- coding: utf-8 -*-
"""tests/integration — 集成测试层 (测试金字塔中层, 占比 ~25%)

设计原则:
    - 测试模块间协作 (Guard 链 / 数据流 / 状态传递)
    - 允许使用 mock 的外部依赖, 但模块间调用必须真实
    - 单测执行 <5s
    - 使用 @pytest.mark.integration 标记

子模块:
    test_eod_guard_chain_integration.py     — 7 Guard 链完整协作
    test_kill_switch_protocol_integration.py — KillSwitch 三级熔断协议端到端
    test_fail_closed_integration.py         — fail-closed 保守保护机制
    test_report_compat_integration.py       — 真实报告 → PortfolioOptimizer 数据流
    test_hedge_dedup_integration.py         — 对冲订单去重 + apply_to_plan
"""
