# -*- coding: utf-8 -*-
"""tests/e2e — 端到端测试层 (测试金字塔顶层, 占比 ~5%)

设计原则:
    - 使用真实历史报告 / 真实交易计划作为黄金数据
    - 验证完整业务链路 (EOD → Guard 链 → trade_plan.json)
    - 允许执行时间 >5s, 但应有上限
    - 使用 @pytest.mark.e2e 标记
    - 数据缺失时 skip, 而非 fail

子模块:
    test_eod_full_chain_e2e.py — EOD 全链路: pnl_report → 7 Guard → trade_plan
"""
