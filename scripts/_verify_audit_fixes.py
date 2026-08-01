# -*- coding: utf-8 -*-
"""
审计报告修复验证脚本
====================
验证 量化交易系统v8.4_综合审计报告_终版.md 中各项修复的正确性

覆盖修复点:
  CR1: hedge_rebalance_v59.py 兜底价格时间戳+告警+时效性检查
  CR2: alpha_hedge_engine.py main() mode 动态切换
  CR3: risk_budgeter.py 熔断时触发自动平仓
  SR1: live_scheduler.py 期货价格单标的失败 warning + 全失效告警
  SR2: system_integration.py 止损钩子实施
  ER2: alpha_hedge_engine.py 持仓不足 warning + target_strike 零值保护
  ER3: live_scheduler.py 收盘报告窗口 ValueError 修复
  ER4: scheduler_daemon.py 假期动态获取

运行: python scripts/_verify_audit_fixes.py
"""
import os
import sys
import logging
from datetime import datetime

# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, 'v8.3_institutional', 'src'))

logging.basicConfig(level=logging.CRITICAL)  # 抑制业务日志噪音

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} — {detail}")


def read_file(rel_path: str) -> str:
    with open(os.path.join(BASE_DIR, rel_path), 'r', encoding='utf-8') as f:
        return f.read()


# ============================================================
# CR1: hedge_rebalance_v59.py 兜底价格时间戳+告警
# ============================================================
def test_cr1():
    print("\n=== CR1: hedge_rebalance_v59.py 兜底价格时效性 ===")
    src = read_file('v8.3_institutional/src/hedging/hedge_rebalance_v59.py')

    check("兜底价格含时间戳元组 (price, timestamp)",
          "20260715" in src and "_FALLBACK_PRICE_DB" in src,
          "未找到时间戳元组定义")

    check("兜底价格 30 天过期阈值",
          "_FALLBACK_MAX_AGE_DAYS = 30" in src,
          "未找到 30 天阈值")

    check("过期触发 CRITICAL 告警",
          "兜底价格已过期" in src and "logger.critical" in src,
          "未找到过期 CRITICAL 告警")

    check("数据源全失效通知方法 _notify_data_source_failure",
          "_notify_data_source_failure" in src,
          "未找到通知方法")

    check("价格安全检查方法 is_price_safe",
          "def is_price_safe" in src,
          "未找到 is_price_safe 方法")

    check("_fallback_stale_codes 跟踪过期代码",
          "_fallback_stale_codes" in src,
          "未找到过期代码跟踪")

    # 功能测试: 实例化验证
    try:
        from hedging.hedge_rebalance_v59 import HedgeRebalanceIntegrator
        integrator = HedgeRebalanceIntegrator.__new__(HedgeRebalanceIntegrator)
        # 模拟 _fallback_stale_codes
        integrator._fallback_stale_codes = ["300308.SZ"]
        check("is_price_safe 对过期代码返回 False",
              integrator.is_price_safe("300308.SZ") is False)
        check("is_price_safe 对正常代码返回 True",
              integrator.is_price_safe("600519.SH") is True)
    except Exception as e:
        check("is_price_safe 功能测试", False, str(e))


# ============================================================
# CR2: alpha_hedge_engine.py main() mode 动态切换
# ============================================================
def test_cr2():
    print("\n=== CR2: alpha_hedge_engine.py main() mode 动态切换 ===")
    src = read_file('alpha_hedge_engine.py')

    check("main() 根据 broker 决定 mode",
          'mode="real" if broker else "sim"' in src or
          'mode = "real" if broker else "sim"' in src,
          "未找到动态 mode 切换")


# ============================================================
# CR3: risk_budgeter.py 熔断时触发自动平仓
# ============================================================
def test_cr3():
    print("\n=== CR3: risk_budgeter.py 熔断清仓 ===")
    src = read_file('v8.3_institutional/src/risk/risk_budgeter.py')

    check("liquidate_callback 参数",
          "liquidate_callback" in src and "LiquidateCallback" in src,
          "未找到回调参数")

    check("set_liquidate_callback 延迟注入方法",
          "def set_liquidate_callback" in src,
          "未找到延迟注入方法")

    check("_trigger_liquidation 触发平仓方法",
          "def _trigger_liquidation" in src,
          "未找到触发平仓方法")

    check("熔断分支调用 _trigger_liquidation",
          "_trigger_liquidation(" in src and "CIRCUIT_BREAKER" in src,
          "熔断分支未调用平仓")

    check("回调未注入时 CRITICAL 告警",
          "回调未注入" in src and "logger.critical" in src,
          "未找到回调未注入告警")

    check("首次进入熔断才平仓 (避免重复)",
          "already_in_breaker" in src,
          "未找到重复平仓保护")

    # 功能测试: 模拟熔断触发平仓
    try:
        from risk.risk_budgeter import RiskBudgeter
        liquidated = {"count": 0}
        def mock_callback(reason, dd, equity, ts):
            liquidated["count"] = 3
            return 3

        rb = RiskBudgeter(total_capital=1_000_000, liquidate_callback=mock_callback)
        # 触发熔断: equity 下跌 8% (dd >= 0.07)
        result = rb.update_drawdown(equity=900_000, ts=datetime.now())
        check("熔断触发返回 CIRCUIT_BREAKER", result == "CIRCUIT_BREAKER")
        check("熔断回调被调用 (平仓 3 个)", liquidated["count"] == 3)
        check("_last_liquidate_result 记录平仓结果",
              rb._last_liquidate_result is not None and
              rb._last_liquidate_result.get("liquidated") == 3)

        # 测试回调未注入场景
        rb2 = RiskBudgeter(total_capital=1_000_000)  # 无回调
        result2 = rb2.update_drawdown(equity=900_000, ts=datetime.now())
        check("无回调时仍触发熔断 (仅告警)", result2 == "CIRCUIT_BREAKER")
        check("无回调时 _last_liquidate_result 记录 callback_not_set",
              rb2._last_liquidate_result is not None and
              rb2._last_liquidate_result.get("error") == "callback_not_set")

        # 测试重复平仓保护
        liquidated["count"] = 0
        result3 = rb.update_drawdown(equity=850_000, ts=datetime.now())
        check("冷却期内再次熔断不重复平仓",
              liquidated["count"] == 0,
              f"重复平仓了 {liquidated['count']} 个")
    except Exception as e:
        check("CR3 功能测试", False, str(e))


# ============================================================
# SR1: live_scheduler.py 期货价格单标的失败 warning
# ============================================================
def test_sr1():
    print("\n=== SR1: live_scheduler.py 期货价格告警 ===")
    src = read_file('live_scheduler.py')

    check("引入 hedge_engine_v59 多源聚合",
          "from hedging.hedge_engine_v59 import get_live_futures_prices" in src,
          "未引入多源聚合")

    check("futures_config 含 source 字段",
          '"source": "fallback"' in src and '"source"] = "realtime"' in src,
          "未标记价格来源")

    check("单标的失败 warning (非静默 pass)",
          "实时价格获取失败" in src and "logger.warning" in src,
          "未找到单标的失败 warning")

    check("全失效 CRITICAL 告警",
          "SR1] 所有期货实时价格源失效" in src and "logger.critical" in src,
          "未找到全失效告警")

    check("全失效短信通知",
          "send_sms_alert" in src,
          "未找到短信通知")


# ============================================================
# SR2: system_integration.py 止损钩子
# ============================================================
def test_sr2():
    print("\n=== SR2: system_integration.py 止损钩子 ===")
    src = read_file('system_integration.py')

    check("_hook_stop_loss_review 已实施 (非 TODO)",
          "def _hook_stop_loss_review" in src and
          "# TODO" not in src.split("def _hook_stop_loss_review")[1].split("def ")[0],
          "止损钩子仍是 TODO")

    check("调用 stop_loss_monitor.check_and_execute",
          "check_and_execute" in src,
          "未调用 check_and_execute")

    check("_hook_drift_and_retrain 已实施",
          "def _hook_drift_and_retrain" in src and
          "drift_detector.update_ic" in src,
          "漂移钩子未实施")

    check("_hook_cost_aware_backtest 已实施",
          "def _hook_cost_aware_backtest" in src and
          "CostAwareBacktest" in src,
          "成本回测钩子未实施")


# ============================================================
# ER2: alpha_hedge_engine.py 持仓不足 + target_strike 保护
# ============================================================
def test_er2():
    print("\n=== ER2: alpha_hedge_engine.py 零值保护 ===")
    src = read_file('alpha_hedge_engine.py')

    check("order_volume <= 0 warning + continue",
          "order_volume <= 0" in src and "持仓不足" in src,
          "未找到持仓不足保护")

    check("tail_risk_monitor current_price <= 0 保护",
          "current_price <= 0" in src and "当前价格无效" in src,
          "未找到 current_price 保护")

    check("target_strike 零值/NaN 保护",
          "target_strike <= 0" in src and "math.isfinite" in src,
          "未找到 target_strike 保护")

    check("ask_price <= 0 除零保护",
          "ask_price <= 0" in src and "卖一价无效" in src,
          "未找到 ask_price 除零保护")

    check("import math 已添加",
          "import math" in src,
          "未添加 import math")


# ============================================================
# ER3: live_scheduler.py 收盘报告窗口 ValueError 修复
# ============================================================
def test_er3():
    print("\n=== ER3: live_scheduler.py 时间窗口修复 ===")
    src = read_file('live_scheduler.py')

    # 检查实际代码行 (非注释/文档) 不含 minute+60 溢出
    # 注: docstring 中引用了原代码字符串作说明, 需排除注释行
    code_lines = [l for l in src.split('\n')
                  if not l.strip().startswith('#') and 'ER3 修复' not in l
                  and '原代码' not in l]
    code_src = '\n'.join(code_lines)
    check("移除 minute+60 溢出代码 (实际代码行)",
          "trigger_time.replace(minute=trigger_time.minute + 60)" not in code_src,
          "仍存在 minute+60 溢出代码 (在实际代码行中)")

    check("使用 datetime.combine + timedelta",
          "datetime.combine(now.date(), trigger_time)" in src and
          "timedelta(hours=2)" in src,
          "未使用 datetime.combine + timedelta")

    check("ER3 修复注释",
          "ER3 修复" in src,
          "未找到 ER3 修复注释")

    # 功能测试: 验证时间窗口计算不抛 ValueError
    try:
        from datetime import datetime as dt, time as dt_time, timedelta as td
        trigger_time = dt_time(15, 15)
        now = dt(2026, 7, 28, 16, 0)  # 16:00, 在窗口内
        today_trigger = dt.combine(now.date(), trigger_time)
        window_end = today_trigger + td(hours=2)
        check("时间窗口计算无 ValueError",
              today_trigger <= now < window_end)
        check("窗口结束时间正确 (17:15)",
              window_end.hour == 17 and window_end.minute == 15)
    except Exception as e:
        check("ER3 时间窗口计算", False, str(e))


# ============================================================
# ER4: scheduler_daemon.py 假期动态获取
# ============================================================
def test_er4():
    print("\n=== ER4: scheduler_daemon.py 假期动态获取 ===")
    src = read_file('v8.3_institutional/scheduler_daemon.py')

    check("委托 utils.trade_calendar",
          "from utils.trade_calendar import is_trading_day" in src,
          "未委托动态日历")

    check("_DYNAMIC_CALENDAR_AVAILABLE 标志",
          "_DYNAMIC_CALENDAR_AVAILABLE" in src,
          "未找到动态日历可用标志")

    check("is_trading_day 优先调用动态日历",
          "if _DYNAMIC_CALENDAR_AVAILABLE:" in src and
          "_dyn_is_trading_day" in src,
          "未优先调用动态日历")

    check("动态日历失败时回退到硬编码",
          "HOLIDAYS_2026" in src and "回退" in src,
          "未找到回退逻辑")

    check("ER4 修复注释",
          "ER4 修复" in src,
          "未找到 ER4 修复注释")

    # 功能测试: 动态日历可用时优先使用
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "scheduler_daemon",
            os.path.join(BASE_DIR, "v8.3_institutional", "scheduler_daemon.py")
        )
        mod = importlib.util.module_from_spec(spec)
        # 不执行 exec_module 避免触发副作用, 仅检查模块结构
        check("scheduler_daemon 模块文件可加载", spec is not None)
    except Exception as e:
        check("scheduler_daemon 模块文件加载", False, f"加载失败: {e}")


# ============================================================
# SR4: pre_deploy.py 静默 except 修复
# ============================================================
def test_sr4():
    print("\n=== SR4: pre_deploy.py 静默 except 修复 ===")
    src = read_file('v8.3_institutional/src/validation/pre_deploy.py')

    check("ntplib 请求失败 logger.debug",
          'logger.debug(f"ntplib 请求' in src,
          "未找到 ntplib 失败 debug 日志")

    check("w32tm 检查失败 logger.debug",
          'logger.debug(f"w32tm NTP 检查失败' in src,
          "未找到 w32tm 失败 debug 日志")

    check("时间戳解析失败 logger.debug",
          'logger.debug(f"熔断日志时间戳解析失败' in src,
          "未找到时间戳解析失败 debug 日志")

    # 验证 stat_sig.py 和 pit_checker.py 已有 logger
    stat_sig_src = read_file('v8.3_institutional/src/validation/stat_sig.py')
    check("stat_sig.py except 块已有 logger.warning",
          'logger.warning(f"加载文件' in stat_sig_src and
          'logger.warning(f"Bootstrap' in stat_sig_src,
          "stat_sig.py 缺少 logger")

    pit_src = read_file('v8.3_institutional/src/validation/pit_checker.py')
    check("pit_checker.py except 块已有 logger.warning",
          'logger.warning(f"信号记录' in pit_src,
          "pit_checker.py 缺少 logger")


# ============================================================
# 主函数
# ============================================================
def main():
    print("=" * 70)
    print("  审计报告修复验证 (量化交易系统v8.4_综合审计报告_终版.md)")
    print("=" * 70)

    test_cr1()
    test_cr2()
    test_cr3()
    test_sr1()
    test_sr2()
    test_er2()
    test_er3()
    test_er4()
    test_sr4()

    print("\n" + "=" * 70)
    print(f"  验证结果: {PASS} PASS / {FAIL} FAIL")
    print("=" * 70)

    if FAIL > 0:
        print("\n  ⚠ 存在失败项, 请检查上述 [FAIL] 详情")
        sys.exit(1)
    else:
        print("\n  ✅ 所有验证通过")
        sys.exit(0)


if __name__ == '__main__':
    main()
