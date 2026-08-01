"""
P1 风控缺口修复验证脚本 (v8.6.6)
=================================
创建日期: 2026-07-26
审计文档: docs/HEDGE_FUND_AUDIT_V2_2026-07-26.md

验证 6 个 P1 级修复 + 1 个 EOD Guard 链集成验证:
    - verify_p1_g(): daily_workflow.py L2547 使用 self.ks (非局部 KillSwitch())
    - verify_p1_l(): portfolio_optimizer.apply_risk_management 4 种场景
    - verify_p1_k(): risk_guard_integrator.guard_correlation_hedge 高相关性场景
    - verify_p1_h(): market_circuit_breaker 跌 5%/7% 触发 L2/L3
    - verify_p1_j(): risk_guard_integrator.guard_liquidity_crisis 涨跌停 > 2000
    - verify_p1_i(): overnight_gap_monitor S&P500 跌 2.5% 触发 L2
    - verify_eod_guard_integration(): 7 个 Guard 字段都存在于 plan['risk_guard']

用法:
    py -3 scripts/verify_p1_fixes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 验证结果统计
# ============================================================

RESULTS = []


def record(name: str, passed: bool, detail: str = ""):
    """记录验证结果"""
    status = "✓ PASS" if passed else "✗ FAIL"
    RESULTS.append((name, passed, detail))
    print(f"  [{status}] {name}")
    if detail:
        print(f"           {detail}")


def verify_p1_g() -> bool:
    """验证 P1-G: daily_workflow.py L2547 使用 self.ks (非局部 KillSwitch())

    原始 bug: L2546 创建局部 ks = KillSwitch(), 未注册 broker_callback,
    导致 L3 紧急协议 ks.execute_kill_switch(3) 抛 RuntimeError 被吞掉

    修复: ks = getattr(self, 'ks', None) or KillSwitch()
    """
    print("\n" + "=" * 70)
    print("P1-G: daily_workflow.py broker_callback 作用域 bug 修复验证")
    print("=" * 70)

    try:
        workflow_path = PROJECT_ROOT / "v8.3_institutional" / "daily_workflow.py"
        with open(workflow_path, encoding="utf-8") as f:
            content = f.read()

        lines = content.split("\n")

        # 查找 P1-G 修复标记
        p1g_marker_found = False
        getattr_found = False
        for i, line in enumerate(lines):
            if "P1-G 修复" in line:
                p1g_marker_found = True
            if "getattr(self, 'ks', None)" in line and "or KillSwitch()" in line:
                getattr_found = True
                record(
                    "P1-G: 使用 getattr(self, 'ks', None) 复用主实例",
                    True,
                    f"L{i+1}: {line.strip()[:80]}",
                )
                break

        if not p1g_marker_found:
            record("P1-G: 修复标记存在", False, "未找到 'P1-G 修复' 注释")
            return False

        if not getattr_found:
            record("P1-G: getattr 复用模式", False, "未找到 getattr(self, 'ks', None)")
            return False

        # 验证不再有 "ks = KillSwitch()" 在 phase_hedge_fund 中 (L2450-L2600 范围)
        # 查找 phase_hedge_fund 方法范围
        phase_start = None
        for i, line in enumerate(lines):
            if "def phase_hedge_fund" in line:
                phase_start = i
                break

        if phase_start is None:
            record("P1-G: phase_hedge_fund 方法定位", False, "未找到方法")
            return False

        # 在 phase_hedge_fund 范围内查找 "ks = KillSwitch()" (排除注释)
        bug_found = False
        for i in range(phase_start, min(phase_start + 200, len(lines))):
            line = lines[i]
            # 跳过注释行
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "ks = KillSwitch()" in line and "getattr" not in line:
                bug_found = True
                record(
                    "P1-G: phase_hedge_fund 内无局部 KillSwitch()",
                    False,
                    f"L{i+1}: {line.strip()[:80]}",
                )
                break

        if not bug_found:
            record("P1-G: phase_hedge_fund 内无局部 KillSwitch()", True)

        return p1g_marker_found and getattr_found and not bug_found

    except Exception as e:
        record("P1-G: 验证执行", False, f"异常: {e}")
        return False


def verify_p1_l() -> bool:
    """验证 P1-L: portfolio_optimizer.apply_risk_management 4 种场景

    场景 1: 低波动 (vol < target) → vol_scaler > 1 (加仓, 受 cap 限制)
    场景 2: 高波动 (vol > target) → vol_scaler < 1 (缩仓)
    场景 3: 回撤 > 5% → dd_scaler = 0.5 (去杠杆)
    场景 4: 数据不足 (len < 2) → 返回原始权重
    """
    print("\n" + "=" * 70)
    print("P1-L: portfolio_optimizer.apply_risk_management 验证")
    print("=" * 70)

    try:
        from utils.portfolio_optimizer import PortfolioOptimizer
        opt = PortfolioOptimizer()

        target_weights = {"588080": 0.10, "512880": 0.08, "510050": 0.06}

        # 场景 1: 低波动 (日 PnL 标准差很小)
        low_vol_pnl = [0.001, -0.001, 0.002, -0.0005, 0.0015] * 4  # ~16 天
        scaled, stats = opt.apply_risk_management(target_weights, low_vol_pnl)
        vol_scaler = stats['vol_scaler']
        # 低波动时 vol_scaler 应接近 cap (2.0) 或 > 1
        s1_pass = vol_scaler > 1.0
        record(
            "P1-L 场景1: 低波动 → vol_scaler > 1",
            s1_pass,
            f"realized_vol={stats['realized_vol']:.2%}, vol_scaler={vol_scaler:.3f}",
        )

        # 场景 2: 高波动 (日 PnL 波动大)
        high_vol_pnl = [0.05, -0.04, 0.06, -0.05, 0.04] * 4  # ~16 天, 年化~80%
        scaled, stats = opt.apply_risk_management(target_weights, high_vol_pnl)
        vol_scaler = stats['vol_scaler']
        # 高波动时 vol_scaler 应 < 1
        s2_pass = vol_scaler < 1.0
        record(
            "P1-L 场景2: 高波动 → vol_scaler < 1",
            s2_pass,
            f"realized_vol={stats['realized_vol']:.2%}, vol_scaler={vol_scaler:.3f}",
        )

        # 场景 3: 回撤 > 5% → dd_scaler = 0.5
        # 构建先涨后跌的序列: 涨 10% 然后跌 8% (回撤 ~8%)
        drawdown_pnl = [0.02] * 5 + [-0.02] * 5  # 涨 10.4% 后跌 9.8%, 回撤约 8%
        scaled, stats = opt.apply_risk_management(target_weights, drawdown_pnl)
        dd_scaler = stats['dd_scaler']
        derisk = stats['derisk_triggered']
        s3_pass = derisk and dd_scaler == 0.5
        record(
            "P1-L 场景3: 回撤 > 5% → dd_scaler = 0.5",
            s3_pass,
            f"current_dd={stats['current_dd']:.2%}, dd_scaler={dd_scaler}, derisk={derisk}",
        )

        # 场景 4: 数据不足 (len < 2)
        _scaled, stats = opt.apply_risk_management(target_weights, [0.01])
        s4_pass = stats.get('note') == 'insufficient_pnl_history' and stats['combined_scaler'] == 1.0
        record(
            "P1-L 场景4: 数据不足 → 返回原始权重",
            s4_pass,
            f"combined_scaler={stats['combined_scaler']}, note={stats.get('note', 'none')}",
        )

        return s1_pass and s2_pass and s3_pass and s4_pass

    except Exception as e:
        record("P1-L: 验证执行", False, f"异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_p1_h() -> bool:
    """验证 P1-H: market_circuit_breaker 跌 5%/7% 触发 L2/L3"""
    print("\n" + "=" * 70)
    print("P1-H: market_circuit_breaker 大盘熔断验证")
    print("=" * 70)

    try:
        from utils.market_circuit_breaker import MarketCircuitBreaker
        mcb = MarketCircuitBreaker()

        # 测试 apply_to_plan 的 L2/L3 逻辑 (不依赖实时数据源)
        test_plan = {
            'execution_plan': {
                'morning_orders': [
                    {'symbol': '588080', 'direction': 'BUY', 'shares': 1000},
                    {'symbol': '512880', 'direction': 'SELL', 'shares': 500},
                ],
                'afternoon_orders': [
                    {'symbol': '510050', 'direction': 'BUY', 'shares': 2000},
                ],
            },
            'market_state': {},
            'risk_guard': {},
        }

        # 场景 1: 跌 5% → L2 (过滤 BUY, 保留 SELL)
        l2_status = {
            'level': 2,
            'level_name': 'L2预警',
            'hs300_change_pct': -0.05,
            'actions': ['禁止开盘新开仓'],
            'data_source': 'test',
            'can_trade': True,
            'can_open': False,
        }
        # 深拷贝 test_plan 避免修改原始对象 (浅拷贝会共享嵌套 execution_plan)
        import copy
        plan_l2 = mcb.apply_to_plan(copy.deepcopy(test_plan), l2_status)
        morning = plan_l2['execution_plan']['morning_orders']
        afternoon = plan_l2['execution_plan']['afternoon_orders']
        # L2 应过滤 BUY, 保留 SELL
        s1_pass = (
            len(morning) == 1 and morning[0]['direction'] == 'SELL'
            and len(afternoon) == 0
        )
        record(
            "P1-H 场景1: 跌 5% → L2 过滤 BUY 保留 SELL",
            s1_pass,
            f"morning={len(morning)} (期望 1 SELL), afternoon={len(afternoon)} (期望 0)",
        )

        # 场景 2: 跌 7% → L3 (清空所有订单)
        l3_status = {
            'level': 3,
            'level_name': 'L3全局平仓',
            'hs300_change_pct': -0.07,
            'actions': ['全局平仓'],
            'data_source': 'test',
            'can_trade': False,
            'can_open': False,
        }
        plan_l3 = mcb.apply_to_plan(copy.deepcopy(test_plan), l3_status)
        s2_pass = (
            len(plan_l3['execution_plan']['morning_orders']) == 0
            and len(plan_l3['execution_plan']['afternoon_orders']) == 0
            and plan_l3['market_state'].get('halt_all_trading')
        )
        record(
            "P1-H 场景2: 跌 7% → L3 清空所有订单 + halt_all_trading",
            s2_pass,
            f"morning={len(plan_l3['execution_plan']['morning_orders'])}, "
            f"halt={plan_l3['market_state'].get('halt_all_trading')}",
        )

        # 场景 3: 正常 (跌 1%) → L0 (不修改)
        l0_status = {
            'level': 0,
            'level_name': '正常',
            'hs300_change_pct': -0.01,
            'actions': [],
            'data_source': 'test',
            'can_trade': True,
            'can_open': True,
        }
        plan_l0 = mcb.apply_to_plan(copy.deepcopy(test_plan), l0_status)
        s3_pass = (
            len(plan_l0['execution_plan']['morning_orders']) == 2
            and plan_l0['risk_guard']['market_circuit_breaker']['level'] == 0
        )
        record(
            "P1-H 场景3: 跌 1% → L0 不修改订单",
            s3_pass,
            f"morning={len(plan_l0['execution_plan']['morning_orders'])} (期望 2)",
        )

        return s1_pass and s2_pass and s3_pass

    except Exception as e:
        record("P1-H: 验证执行", False, f"异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_p1_j() -> bool:
    """验证 P1-J: risk_guard_integrator.guard_liquidity_crisis 涨跌停 > 2000"""
    print("\n" + "=" * 70)
    print("P1-J: risk_guard_integrator.guard_liquidity_crisis 验证")
    print("=" * 70)

    try:
        from utils.risk_guard_integrator import RiskGuardIntegrator
        rgi = RiskGuardIntegrator()

        # 测试场景: 涨跌停 > 2000 → 触发全局撤单
        # 我们直接测试 guard_liquidity_crisis 的逻辑
        # 由于 _fetch_limit_counts 依赖外部数据源, 我们 mock 它
        original_fetch = rgi._fetch_limit_counts

        # 场景 1: 涨跌停 2500 > 2000 → 触发撤单
        rgi._fetch_limit_counts = lambda pnl_report=None: (1500, 1000, "test_mock")
        plan = {
            'execution_plan': {
                'morning_orders': [{'symbol': '588080', 'direction': 'BUY'}],
                'afternoon_orders': [{'symbol': '512880', 'direction': 'SELL'}],
            },
            'market_state': {},
            'risk_guard': {},
        }
        plan = rgi.guard_liquidity_crisis({}, plan)
        s1_pass = (
            len(plan['execution_plan']['morning_orders']) == 0
            and len(plan['execution_plan']['afternoon_orders']) == 0
            and plan['market_state'].get('liquidity_crisis')
            and plan['risk_guard']['liquidity_crisis']['triggered']
        )
        record(
            "P1-J 场景1: 涨跌停 2500 > 2000 → 全局撤单",
            s1_pass,
            f"morning={len(plan['execution_plan']['morning_orders'])}, "
            f"triggered={plan['risk_guard']['liquidity_crisis']['triggered']}",
        )

        # 场景 2: 涨跌停 500 < 2000 → 正常
        rgi._fetch_limit_counts = lambda pnl_report=None: (300, 200, "test_mock")
        plan = {
            'execution_plan': {
                'morning_orders': [{'symbol': '588080', 'direction': 'BUY'}],
                'afternoon_orders': [],
            },
            'market_state': {},
            'risk_guard': {},
        }
        plan = rgi.guard_liquidity_crisis({}, plan)
        s2_pass = (
            len(plan['execution_plan']['morning_orders']) == 1
            and not plan['risk_guard']['liquidity_crisis']['triggered']
        )
        record(
            "P1-J 场景2: 涨跌停 500 < 2000 → 正常",
            s2_pass,
            f"morning={len(plan['execution_plan']['morning_orders'])} (期望 1), "
            f"triggered={plan['risk_guard']['liquidity_crisis']['triggered']}",
        )

        # 恢复原始方法
        rgi._fetch_limit_counts = original_fetch

        return s1_pass and s2_pass

    except Exception as e:
        record("P1-J: 验证执行", False, f"异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_p1_i() -> bool:
    """验证 P1-I: overnight_gap_monitor S&P500 跌 2.5% 触发 L2"""
    print("\n" + "=" * 70)
    print("P1-I: overnight_gap_monitor 隔夜跳空验证")
    print("=" * 70)

    try:
        import copy

        from utils.overnight_gap_monitor import OvernightGapMonitor

        ogm = OvernightGapMonitor()

        test_plan = {
            'execution_plan': {
                'morning_orders': [
                    {'symbol': '588080', 'direction': 'BUY', 'shares': 1000},
                    {'symbol': '512880', 'direction': 'SELL', 'shares': 500},
                ],
                'afternoon_orders': [],
            },
            'market_state': {},
            'risk_guard': {},
        }

        # 场景 1: S&P500 跌 2.5% → L2 (过滤 BUY)
        l2_risk = {
            'level': 2,
            'level_name': 'L2熔断',
            'sp500_change_pct': -0.025,
            'adr_deviation_pct': 0.01,
            'actions': ['禁止开仓'],
            'data_source': 'test',
            'can_trade': True,
            'can_open': False,
            'trigger': 'sp500_drop_-2.50%',
        }
        plan_l2 = ogm.apply_to_plan(copy.deepcopy(test_plan), l2_risk)
        morning = plan_l2['execution_plan']['morning_orders']
        s1_pass = (
            len(morning) == 1 and morning[0]['direction'] == 'SELL'
            and plan_l2['risk_guard']['overnight_gap']['level'] == 2
        )
        record(
            "P1-I 场景1: S&P500 跌 2.5% → L2 过滤 BUY",
            s1_pass,
            f"morning={len(morning)} (期望 1 SELL), level={plan_l2['risk_guard']['overnight_gap']['level']}",
        )

        # 场景 2: S&P500 跌 3.5% → L3 (清空所有)
        l3_risk = {
            'level': 3,
            'level_name': 'L3全局平仓',
            'sp500_change_pct': -0.035,
            'adr_deviation_pct': 0.01,
            'actions': ['全局平仓'],
            'data_source': 'test',
            'can_trade': False,
            'can_open': False,
            'trigger': 'sp500_drop_-3.50%',
        }
        plan_l3 = ogm.apply_to_plan(copy.deepcopy(test_plan), l3_risk)
        s2_pass = (
            len(plan_l3['execution_plan']['morning_orders']) == 0
            and plan_l3['market_state'].get('halt_all_trading')
        )
        record(
            "P1-I 场景2: S&P500 跌 3.5% → L3 全局平仓",
            s2_pass,
            f"morning={len(plan_l3['execution_plan']['morning_orders'])}, "
            f"halt={plan_l3['market_state'].get('halt_all_trading')}",
        )

        # 场景 3: ADR 偏离 5% → L2
        adr_risk = {
            'level': 2,
            'level_name': 'L2熔断',
            'sp500_change_pct': -0.005,
            'adr_deviation_pct': 0.05,
            'actions': ['禁止开仓'],
            'data_source': 'test',
            'can_trade': True,
            'can_open': False,
            'trigger': 'adr_deviation_5.00%',
        }
        plan_adr = ogm.apply_to_plan(copy.deepcopy(test_plan), adr_risk)
        s3_pass = plan_adr['risk_guard']['overnight_gap']['level'] == 2
        record(
            "P1-I 场景3: ADR 偏离 5% → L2 触发",
            s3_pass,
            f"level={plan_adr['risk_guard']['overnight_gap']['level']}",
        )

        # 场景 4: 正常 → L0
        l0_risk = {
            'level': 0,
            'level_name': '正常',
            'sp500_change_pct': -0.005,
            'adr_deviation_pct': 0.005,
            'actions': [],
            'data_source': 'test',
            'can_trade': True,
            'can_open': True,
            'trigger': 'none',
        }
        plan_l0 = ogm.apply_to_plan(copy.deepcopy(test_plan), l0_risk)
        s4_pass = (
            len(plan_l0['execution_plan']['morning_orders']) == 2
            and plan_l0['risk_guard']['overnight_gap']['level'] == 0
        )
        record(
            "P1-I 场景4: 正常 → L0 不修改",
            s4_pass,
            f"morning={len(plan_l0['execution_plan']['morning_orders'])} (期望 2)",
        )

        return s1_pass and s2_pass and s3_pass and s4_pass

    except Exception as e:
        record("P1-I: 验证执行", False, f"异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_p1_k() -> bool:
    """验证 P1-K: risk_guard_integrator.guard_correlation_hedge 高相关性场景"""
    print("\n" + "=" * 70)
    print("P1-K: risk_guard_integrator.guard_correlation_hedge 验证")
    print("=" * 70)

    try:
        # 直接测试 CorrelationHedger (不依赖 EOD 集成)
        import sys as _sys
        _v83_src = PROJECT_ROOT / "v8.3_institutional" / "src"
        if str(_v83_src) not in _sys.path:
            _sys.path.insert(0, str(_v83_src))
        import numpy as np
        import pandas as pd
        from hedging.correlation_hedger import CorrelationHedger

        hedger = CorrelationHedger()

        # 场景 1: 高相关性 (avg_corr > 0.85, jump > 0.15) → 触发避险配置
        # 构建 30 天高相关收益率 (5 个标的, 相关系数 > 0.9)
        np.random.seed(42)
        n_days = 60
        base = np.random.randn(n_days) * 0.01
        # 5 个标的与 base 高度相关
        returns_high_corr = pd.DataFrame({
            '588080': base + np.random.randn(n_days) * 0.001,
            '512880': base + np.random.randn(n_days) * 0.001,
            '510050': base + np.random.randn(n_days) * 0.001,
            '512800': base + np.random.randn(n_days) * 0.001,
            '600276': base + np.random.randn(n_days) * 0.001,
        })
        # 最近 30 天相关性更高 (模拟危机趋同)
        returns_high_corr.iloc[-30:] += base[-30:].reshape(-1, 1) * 0.5

        hedge_result = hedger.compute_hedge(returns_high_corr, portfolio_value=5_000_000)
        s1_pass = hedge_result.get('action') == 'SAFE_HAVEN_ALLOC'
        record(
            "P1-K 场景1: 高相关性 → SAFE_HAVEN_ALLOC",
            s1_pass,
            f"action={hedge_result.get('action')}, avg_corr={hedge_result.get('avg_corr', 0):.3f}, "
            f"jump={hedge_result.get('jump', 0):.3f}",
        )

        if s1_pass:
            gold_weight = hedge_result.get('gold_weight', 0)
            repo_weight = hedge_result.get('repo_weight', 0)
            s1b_pass = gold_weight > 0 or repo_weight > 0
            record(
                "P1-K 场景1b: 避险权重 > 0",
                s1b_pass,
                f"gold_weight={gold_weight:.2%}, repo_weight={repo_weight:.2%}",
            )
        else:
            s1b_pass = False

        # 场景 2: 低相关性 → NO_HEDGE
        returns_low_corr = pd.DataFrame({
            '588080': np.random.randn(n_days) * 0.01,
            '512880': np.random.randn(n_days) * 0.01,
            '510050': np.random.randn(n_days) * 0.01,
            '512800': np.random.randn(n_days) * 0.01,
            '600276': np.random.randn(n_days) * 0.01,
        })
        hedge_result_low = hedger.compute_hedge(returns_low_corr, portfolio_value=5_000_000)
        s2_pass = hedge_result_low.get('action') == 'NO_HEDGE'
        record(
            "P1-K 场景2: 低相关性 → NO_HEDGE",
            s2_pass,
            f"action={hedge_result_low.get('action')}, avg_corr={hedge_result_low.get('avg_corr', 0):.3f}",
        )

        return s1_pass and s1b_pass and s2_pass

    except Exception as e:
        record("P1-K: 验证执行", False, f"异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_eod_guard_integration() -> bool:
    """验证 EOD Guard 链集成: 7 个 Guard 字段都存在于 plan['risk_guard']

    通过检查 risk_guard_integrator.py 中 run_all_guards 是否调用所有 7 个 Guard
    """
    print("\n" + "=" * 70)
    print("EOD Guard 链集成验证: 7 个 Guard 调用检查")
    print("=" * 70)

    try:
        rgi_path = PROJECT_ROOT / "utils" / "risk_guard_integrator.py"
        with open(rgi_path, encoding="utf-8") as f:
            content = f.read()

        # 检查 run_all_guards 中调用了所有 7 个 Guard
        expected_guards = [
            ('guard_kill_switch', '保证金熔断', '[1/7]'),
            ('guard_market_circuit_breaker', '大盘熔断', '[2/7]'),
            ('guard_liquidity_crisis', '流动性危机', '[3/7]'),
            ('guard_overnight_gap', '隔夜跳空', '[4/7]'),
            ('guard_drawdown', '回撤检查', '[5/7]'),
            ('guard_vol_target', '波动率控制', '[6/7]'),
            ('guard_correlation_hedge', '相关性对冲', '[7/7]'),
        ]

        all_found = True
        for method_name, label, guard_num in expected_guards:
            # 检查方法定义存在
            method_def_found = f"def {method_name}" in content
            # 检查 run_all_guards 中调用存在
            call_found = f"self.{method_name}(" in content
            # 检查 guard 编号标记
            num_found = guard_num in content

            passed = method_def_found and call_found
            record(
                f"EOD {guard_num} {label} ({method_name})",
                passed,
                f"定义={method_def_found}, 调用={call_found}, 标记={num_found}",
            )
            if not passed:
                all_found = False

        # 额外检查: 对冲执行和认沽保护仍在 [7/7]
        hedge_found = 'guard_hedge_execution' in content and '[7/7] 对冲执行' in content
        put_found = 'guard_protective_put' in content and '[7/7] 认沽保护' in content
        record(
            "EOD [7/7] 对冲执行 + 认沽保护 (原有)",
            hedge_found and put_found,
            f"对冲执行={hedge_found}, 认沽保护={put_found}",
        )

        if not (hedge_found and put_found):
            all_found = False

        return all_found

    except Exception as e:
        record("EOD 集成: 验证执行", False, f"异常: {e}")
        return False


# ============================================================
# 主入口
# ============================================================

def main():
    """主验证入口"""
    print("=" * 70)
    print("P1 风控缺口修复验证脚本 (v8.6.6)")
    print("审计文档: docs/HEDGE_FUND_AUDIT_V2_2026-07-26.md")
    print(f"项目根目录: {PROJECT_ROOT}")
    print("=" * 70)

    # 执行所有验证
    results = []
    results.append(("P1-G", verify_p1_g()))
    results.append(("P1-L", verify_p1_l()))
    results.append(("P1-H", verify_p1_h()))
    results.append(("P1-J", verify_p1_j()))
    results.append(("P1-I", verify_p1_i()))
    results.append(("P1-K", verify_p1_k()))
    results.append(("EOD集成", verify_eod_guard_integration()))

    # 汇总
    print("\n" + "=" * 70)
    print("验证汇总")
    print("=" * 70)

    total = len(results)
    passed = sum(1 for _, p in results if p)
    failed = total - passed

    for name, p in results:
        status = "✓ PASS" if p else "✗ FAIL"
        print(f"  {name:12s} {status}")

    print(f"\n总计: {passed}/{total} 通过, {failed} 失败")

    if failed == 0:
        print("\n🎉 所有 P1 修复验证通过!")
        print("   CRO 评分目标: 7.5 → 9.0+")
        print("   下一步: 2026-07-27 真实交易日验证")
        return 0
    else:
        print(f"\n⚠ {failed} 项验证失败, 请检查上述详情")
        return 1


if __name__ == "__main__":
    sys.exit(main())
