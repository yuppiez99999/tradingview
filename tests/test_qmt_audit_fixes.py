"""QMT 审计修复项集成测试 — 验证所有 P0 修复"""

import os
import sys

# 必须在 import 之前插入 sys.path
# 项目根目录 (用于 import utils.* / ms_strategy.*)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ms_strategy', 'src', 'execution'))
sys.path.insert(0, os.path.join(ROOT, 'ms_strategy', 'src', 'data'))
sys.path.insert(0, os.path.join(ROOT, 'ms_strategy', 'scripts'))

passed = 0
failed = 0

# ================================================================
# P0-10: T+0/T+1 交易制度
# ================================================================
from utils.trading_rules import get_trading_rule, is_t0_eligible  # noqa: E402

tests = [
    ("511880.SH 债券ETF", is_t0_eligible("511880.SH"), True),
    ("513100 跨境ETF", is_t0_eligible("513100"), True),
    ("518880 黄金ETF", is_t0_eligible("518880"), True),
    ("510300.SH 沪深300ETF(T+1)", is_t0_eligible("510300.SH"), False),
    ("510050.SH 上证50ETF(T+1)", is_t0_eligible("510050.SH"), False),
    ("600519.SH 茅台(T+1)", is_t0_eligible("600519.SH"), False),
    ("300308.SZ 创业板(T+1)", is_t0_eligible("300308.SZ"), False),
    ("IF.CFFEX 期货(T+0)", is_t0_eligible("IF.CFFEX", "FUTURE"), True),
    ("159920 恒生ETF(T+0)", is_t0_eligible("159920"), True),
    ("159941 纳指ETF(T+0)", is_t0_eligible("159941"), True),
]
for name, got, expected in tests:
    if got == expected:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} — got={got}, expected={expected}")

# 交易规则
rules = get_trading_rule("600519.SH")
assert rules["settlement"] == "T+1", f"Wrong settlement: {rules['settlement']}"
passed += 1
rules = get_trading_rule("IF.CFFEX", "FUTURE")
assert rules["settlement"] == "T+0", f"Wrong settlement: {rules['settlement']}"
passed += 1

print(f"  trading_rules: {passed}/{passed+failed}")

# ================================================================
# P0-2: 合约管理器回退
# ================================================================
tp = passed
from utils.wt_contracts_manager import ContractsManager  # noqa: E402

cm = ContractsManager()

# 精确匹配
ct = cm.get_contract("IF.CFFEX")
assert ct.product_class == "FUTURE", f"Wrong class: {ct.product_class}"
assert ct.contract_multiplier == 300.0, f"Wrong multiplier: {ct.contract_multiplier}"
passed += 2

# 期货月份匹配
ct = cm.get_contract("IF2507.CFFEX")
assert ct.product_class == "FUTURE", f"Futures fallback failed: {ct.product_class}"
assert ct.contract_multiplier == 300.0, "Wrong futures multiplier"
passed += 2

# 期权回退
ct = cm.get_contract("510050C2507M03000.SH")
assert ct.product_class == "OPTION", f"Option fallback failed: {ct.product_class}"
passed += 1

# 股票回退
ct = cm.get_contract("000001.SZ")
assert ct.product_class == "STOCK", "Stock fallback failed"
passed += 1

print(f"  contracts_manager: {passed-tp}/{6}")

# ================================================================
# P0-6: 期货换月
# ================================================================
tp = passed
from utils.futures_rollover_manager import FuturesRolloverManager  # noqa: E402

mgr = FuturesRolloverManager()

# 主力合约
active = mgr.get_active_contract("IF", "CFFEX")
assert active.endswith(".CFFEX"), f"Invalid contract: {active}"
assert "IF" in active, f"Missing IF prefix: {active}"
passed += 2

# 可交易性
assert mgr.is_tradable("IF2507.CFFEX"), "Tradable failed"
assert not mgr.is_tradable("IF.IDX"), "IDX should be non-tradable"
assert not mgr.is_tradable("IF00.CFFEX"), "Continuous should be non-tradable"
passed += 3

# 合约解析
parsed = mgr._parse_contract("IF2507.CFFEX")
assert parsed is not None, "Parse failed"
assert parsed[0] == "IF", f"Product mismatch: {parsed[0]}"
assert parsed[1] == "25", f"Year mismatch: {parsed[1]}"
assert parsed[2] == "07", f"Month mismatch: {parsed[2]}"
assert parsed[3] == "CFFEX", f"Exchange mismatch: {parsed[3]}"
passed += 5

print(f"  futures_rollover: {passed-tp}/{10}")

# ================================================================
# P0-8: 期权保证金
# ================================================================
tp = passed
from utils.option_margin_monitor import (  # noqa: E402
    OptionMarginMonitor,
    OptionPosition,
    calc_call_margin,
    calc_put_margin,
    parse_option_code,
)

# 期权代码解析
parsed = parse_option_code("510050C2507M03000.SH")
assert parsed is not None, "Option parse failed"
assert parsed["option_type"] == "CALL", f"Type mismatch: {parsed['option_type']}"
assert abs(parsed["strike"] - 3.000) < 0.001, f"Strike mismatch: {parsed['strike']}"
passed += 3

# 保证金计算
call_margin = calc_call_margin(3.2, 3.0, 0.05)
assert call_margin > 0, "Call margin should be > 0"
put_margin = calc_put_margin(3.0, 3.2, 0.05)
assert put_margin > 0, "Put margin should be > 0"
passed += 2

# 监控器
monitor = OptionMarginMonitor()
monitor.add_position(OptionPosition(
    symbol="510050P2507M03000.SH", underlying="510050.SH",
    option_type="PUT", side="SELL", strike=3.000,
    quantity=10, premium=0.0500, expiry_date="2025-07-25",
))
results = monitor.check_all({"510050.SH": 3.200}, available_funds=500000)
assert len(results) == 1, f"Expected 1 result, got {len(results)}"
assert results[0].required_margin > 0, "Margin should be > 0"
assert isinstance(results[0].warning_level, str), "Wrong warning_level type"
passed += 3

print(f"  option_margin: {passed-tp}/{8}")

# ================================================================
# P0-1: 后缀推断
# ================================================================
tp = passed
# 从 daily_trade_executor 导入 _infer_suffix
from daily_trade_executor import _infer_suffix  # noqa: E402

assert _infer_suffix("600519") == "600519.SH", f"600519: {_infer_suffix('600519')}"
assert _infer_suffix("000001") == "000001.SZ", f"000001: {_infer_suffix('000001')}"
assert _infer_suffix("300308") == "300308.SZ", f"300308: {_infer_suffix('300308')}"
assert _infer_suffix("510300") == "510300.SH", f"510300: {_infer_suffix('510300')}"
assert _infer_suffix("688981") == "688981.SH", f"688981: {_infer_suffix('688981')}"
assert _infer_suffix("159920") == "159920.SZ", f"159920: {_infer_suffix('159920')}"
assert _infer_suffix("830799") == "830799.BJ", f"830799: {_infer_suffix('830799')}"
passed += 7

print(f"  _infer_suffix: {passed-tp}/{7}")

# ================================================================
# P0-5: 对冲合约名解析
# ================================================================
tp = passed
from utils.futures_rollover_manager import FuturesRolloverManager  # noqa: E402

mgr2 = FuturesRolloverManager()

# 具体合约直接返回
resolved = mgr2.resolve_hedge_contract("IF2507.CFFEX")
assert resolved == "IF2507.CFFEX", f"具体合约应直接返回: {resolved}"
passed += 1

# 通用名解析
resolved = mgr2.resolve_hedge_contract("IF.CFFEX")
assert resolved is not None, "IF.CFFEX 解析失败"
assert resolved.endswith(".CFFEX"), f"交易所不对: {resolved}"
assert "IF" in resolved, f"产品不对: {resolved}"
passed += 2

# 兼容旧 API: IF_futures
resolved = mgr2.resolve_hedge_contract("IF_futures")
assert resolved is not None, "IF_futures 解析失败"
assert "IF" in resolved, f"产品不对: {resolved}"
passed += 1

# 无后缀名
resolved = mgr2.resolve_hedge_contract("IF")
assert resolved is not None, "IF 解析失败"
passed += 1

print(f"  resolve_hedge_contract: {passed-tp}/{5}")

# ================================================================
# P0-7: 期权行权/指派风险
# ================================================================
tp = passed
from utils.option_exercise_risk import (  # noqa: E402
    OptionExerciseRiskManager,
    _get_expiry_date,
    _parse_option_code,
)

# 代码解析
parsed = _parse_option_code("510050C2507M03000.SH")
assert parsed is not None, "解析失败"
assert parsed["option_type"] == "CALL"
assert abs(parsed["strike"] - 3.000) < 0.001
passed += 2

# 到期日计算
expiry = _get_expiry_date(2025, 7)
assert expiry.day >= 22 and expiry.day <= 28, f"到期日不对: {expiry}"
assert expiry.weekday() == 2, f"不是周三: {expiry}"
passed += 2

# 风险管理器
mgr3 = OptionExerciseRiskManager()
# 卖方实值期权风险 (2608 = 2026年8月, 未来到期)
result = mgr3.assess_risk(
    "510050C2608M03000.SH", "SELL", 10, 3.500, 0.05,
)
assert result is not None, "评估失败"
assert result.side == "SELL"
assert result.is_itm, "实值期权应 is_itm=True"
assert result.assignment_probability in ("CERTAIN", "HIGH", "MEDIUM", "LOW"), \
    f"意外状态: {result.assignment_probability}"
passed += 3

# 买方虚值期权风险
result = mgr3.assess_risk(
    "510050C2608M03000.SH", "BUY", 10, 2.800, 0.05,
)
assert result is not None
assert not result.is_itm, "虚值期权应 is_itm=False"
assert result.potential_loss > 0, "买方最大亏损 = 权利金"
passed += 2

# 批量检测
results = mgr3.check_all(
    [
        {"symbol": "510050C2608M03000.SH", "side": "SELL", "quantity": 10, "premium": 0.05},
        {"symbol": "510050P2608M03000.SH", "side": "BUY", "quantity": 5, "premium": 0.03},
    ],
    {"510050.SH": 3.200},
)
assert len(results) == 2, f"应有 2 个结果, got {len(results)}"
passed += 1

# 平仓建议
orders = mgr3.generate_close_orders(results, "MEDIUM")
assert isinstance(orders, list)
passed += 1

print(f"  option_exercise_risk: {passed-tp}/{11}")

# ================================================================
print(f"\n===== {passed} PASSED, {failed} FAILED =====")
if failed > 0:
    exit(1)
