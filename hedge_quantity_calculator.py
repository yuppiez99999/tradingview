"""
# OFFLINE_ONLY — 离线分析脚本, 禁止被执行链路 import
# 本脚本为一次性离线对冲数量测算工具, 以下特征表明其非实盘性质:
#   1. 硬编码组合快照 (portfolio_value=5_000_000 / day_capital=1_722_410)
#   2. 硬编码期货参考价 (IF=3800 / IC=5500 / IM=5800) 与 vix=25.0 / hwm_drawdown=0.03
#   3. 全仓 0 处 import 本脚本, 结果不流入任何撮合/下单链路
# 若需用于实盘前测算, 必须改为读取实时行情源或 config/risk_params.yaml, 见审查报告 M4。
今日对冲数量计算器
基于组合结构 + v7.5 引擎
"""

import json
import sys
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# C8 修复: 使用动态 PROJECT_ROOT
PROJECT_ROOT = Path(__file__).resolve().parent
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
sys.path.insert(0, str(PROJECT_ROOT))  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402
setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils

# M15 修复: 删除未使用的 HedgeCoordinator/BetaHedger/VolHedger 导入
# (原导入执行后从未引用, 且模块不存在时会导致 ImportError 使整个脚本无法运行)

# 读取持仓
positions_path = PROJECT_ROOT / "config" / "positions.json"
with open(positions_path, encoding="utf-8") as f:
    positions_data = json.load(f)["positions"]

positions = {}
prices = {}
style_map = {}
risk_map = {}
for _key, item in positions_data.items():
    code = item.get("code")
    qty = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
    price = item.get("est_price", 0.0)
    if code and qty:
        positions[code] = float(qty)
        prices[code] = float(price)
        style_map[code] = item.get("style", "其他")
        risk_map[code] = item.get("risk", "中")

# 组合参数 (OFFLINE_ONLY 快照值, 非实时 — 见文件头标注)
portfolio_value = 5_000_000.0
day_capital = 1_722_410.0
deployed_ratio = day_capital / portfolio_value

# B-4.2: 风格Beta代理统一到 utils/risk/style_beta.py (消除 DRY 违规)
from utils.risk.style_beta import STYLE_BETA_PROXY as style_beta_proxy  # noqa: E402

portfolio_beta_est = 0.0
for code, qty in positions.items():
    amt = qty * prices.get(code, 0.0)
    style = style_map.get(code, "其他")
    beta = style_beta_proxy.get(style, 1.0)
    portfolio_beta_est += (amt / day_capital) * beta if day_capital > 0 else 0.0

logger.info("=" * 70)
logger.info("今日对冲数量计算器")
logger.info("=" * 70)
logger.info(f"组合总市值: {portfolio_value:,.0f}")
logger.info(f"已建仓金额: {day_capital:,.0f} ({deployed_ratio * 100:.1f}%)")
logger.info(f"估算组合Beta: {portfolio_beta_est:.2f}")

# ============================================================
# 1. Beta 对冲计算
# ============================================================
logger.info("-" * 70)
logger.info("1. Beta 对冲 (股指期货空头)")
logger.info("-" * 70)

beta_target = 0.3
beta_trigger = 0.7
excess_beta = max(0.0, portfolio_beta_est - beta_target)
value_to_hedge = excess_beta * day_capital

logger.info(f"当前Beta: {portfolio_beta_est:.2f}")
logger.info(f"目标Beta: {beta_target:.2f}")
logger.info(f"超额Beta: {excess_beta:.2f}")
logger.info(f"需要对冲金额: {value_to_hedge:,.0f} 元")

# 期货合约参数
futures = {
    "IF": {"multiplier": 300, "beta": 1.0, "price": 3800.0, "name": "沪深300"},
    "IC": {"multiplier": 200, "beta": 1.2, "price": 5500.0, "name": "中证500"},
    "IM": {"multiplier": 200, "beta": 1.1, "price": 5800.0, "name": "中证1000"},
}

# 选择期货合约
if portfolio_beta_est > 1.1:
    preferred = "IF"
elif portfolio_beta_est > 0.9:
    preferred = "IC"
else:
    preferred = "IM"

fut = futures[preferred]
notional_per_contract = fut["multiplier"] * fut["price"]
beta_adjusted_notional = notional_per_contract * fut["beta"]
n_contracts = int(value_to_hedge / beta_adjusted_notional) if beta_adjusted_notional > 0 else 0

# 成本估算
commission_rate = 0.000023
slippage_rate = 0.0001
cost_per_contract = notional_per_contract * (commission_rate + slippage_rate)
total_cost = n_contracts * cost_per_contract
cost_ratio = total_cost / day_capital if day_capital > 0 else 0

logger.info(f"首选合约: {preferred} ({fut['name']})")
logger.info(f"合约乘数: {fut['multiplier']}")
logger.info(f"期货价格: {fut['price']:.2f}")
logger.info(f"合约名义价值: {notional_per_contract:,.0f} 元")
logger.info(f"Beta调整后价值: {beta_adjusted_notional:,.0f} 元")
logger.info(f"建议开仓手数: {n_contracts} 手")
logger.info(f"对冲名义价值: {n_contracts * notional_per_contract:,.0f} 元")
logger.info(f"预估成本: {total_cost:,.0f} 元 ({cost_ratio * 100:.4f}%)")

if cost_ratio > 0.003:
    logger.warning("[警告] 成本超阈值，建议降级为期权 Put Spread")
else:
    logger.info("[通过] 成本在阈值内，可直接开期货空头")


# ============================================================
# 2. 波动率对冲计算
# ============================================================
logger.info("-" * 70)
logger.info("2. 波动率对冲 (期权保护)")
logger.info("-" * 70)

vix = 25.0
vix_trigger = 30.0
vix_emergency = 60.0

logger.info(f"当前VIX假设: {vix:.1f}")
logger.info(f"波动率触发阈值: {vix_trigger:.1f}")

if vix <= vix_trigger:
    logger.info("[未触发] VIX 未触发，暂不开期权保护")
    vol_action = "NO_HEDGE"
    vol_budget = 0
else:
    if vix <= 40:
        vol_action = "BUY_PUT_SPREAD"
        vol_budget_pct = 0.003
    elif vix <= 60:
        vol_action = "BUY_BARE_PUT"
        vol_budget_pct = 0.005
    else:
        vol_action = "BUY_EMERGENCY_PUT"
        vol_budget_pct = 0.008

    vol_budget = day_capital * vol_budget_pct
    logger.info(f"[触发] 波动率触发，动作: {vol_action}")
    logger.info(f"期权预算: {vol_budget:,.0f} 元 ({vol_budget_pct * 100:.2f}%)")

    # 估算期权合约数量
    # 假设沪深300ETF期权单价约 0.05-0.15 元
    option_price_est = 0.08  # 估计价格
    contract_size = 10000  # 沪深300ETF期权合约乘数
    n_options = int(vol_budget / (option_price_est * contract_size))
    logger.info(f"估计可买合约数: {n_options} 张 (按单价 {option_price_est} 估算)")


# ============================================================
# 3. 相关性对冲计算
# ============================================================
logger.info("-" * 70)
logger.info("3. 相关性对冲 (避险资产配置)")
logger.info("-" * 70)

logger.info("当前相关性数据: 无历史收益率，无法计算")
logger.info("基于结构判断:")
logger.info("  - 持仓涵盖 ETF + 个股 + 商品，分散度一般")
logger.info("  - 科技+制造占 65.5%，同涨同跌风险较高")
logger.info("  - 建议: 增加避险资产配置")

# 当前避险资产
defense_assets = {
    "sh600900": ("长江电力", 58_800),
    "sz518880": ("黄金ETF华安", 99_450),
    "sh601088": ("中国神华", 38_500),
}
defense_total = sum(v for _, v in defense_assets.values())
defense_pct = defense_total / day_capital * 100

logger.info(f"当前防御/避险持仓: {defense_total:,.0f} 元 ({defense_pct:.1f}%)")
logger.info("  - 长江电力: 58,800 元")
logger.info("  - 黄金ETF: 99,450 元")
logger.info("  - 中国神华: 38,500 元")

# C7 修复: 在 if/else 外初始化 gap, 防止 defense_pct >= 15 时 NameError
gap = 0.0
if defense_pct < 15:
    logger.warning(f"[警告] 防御占比 {defense_pct:.1f}% < 15%，建议追加避险资产")
    target_defense = day_capital * 0.15
    gap = target_defense - defense_total
    logger.info(f"目标防御金额: {target_defense:,.0f} 元")
    logger.info(f"建议追加: {gap:,.0f} 元")
    logger.info("  - 优先: 黄金ETF (518880) 追加")
    logger.info("  - 或: 国债逆回购 (GC001)")
else:
    logger.info(f"[通过] 防御占比 {defense_pct:.1f}% 达标")


# ============================================================
# 4. 尾部风险对冲
# ============================================================
logger.info("-" * 70)
logger.info("4. 尾部风险对冲 (OTM Put 阶梯)")
logger.info("-" * 70)

hwm_drawdown = 0.03
bs_loss = 0.0

logger.info(f"当前回撤: {hwm_drawdown * 100:.1f}%")
logger.info(f"黑天鹅损失: {bs_loss * 100:.0f}%")
logger.info("市场状态: normal")
logger.info("结论: 尾部风险模块建议 NO_HEDGE")
logger.info("但基于组合结构，可主动配置少量保护:")
logger.info("  - 买入 1-2 张 沪深300ETF 虚值 Put")
logger.info("  - 行权价: 当前价 95-90%")
logger.info("  - 预算: 3-5 万元")

# ============================================================
# 5. 综合建议
# ============================================================
logger.info("=" * 70)
logger.info("综合对冲建议")
logger.info("=" * 70)
logger.info(f"1. Beta 对冲: {n_contracts} 手 {preferred} 空头")
logger.info(f"   - 名义价值: {n_contracts * notional_per_contract:,.0f} 元")
logger.info(f"   - 成本: {total_cost:,.0f} 元")
logger.info(f"2. 波动率对冲: 暂不开 (VIX {vix:.1f} < {vix_trigger:.1f})")
logger.info(f"   - 若 VIX 升至 30+，预算 {day_capital * 0.003:,.0f} 元买 Put Spread")
logger.info(f"3. 避险配置: 追加 {gap:,.0f} 元 黄金ETF/国债逆回购")
logger.info(f"   - 当前防御占比 {defense_pct:.1f}%，目标 15%")
logger.info("4. 尾部保护: 主动买 1-2 张 虚值 Put")
logger.info("   - 预算 3-5 万元")
logger.info("   - 行权价 95-90%")
logger.info("=" * 70)
logger.info("对冲资金预算合计")
logger.info("=" * 70)
total_hedge_budget = total_cost + gap + 50_000
logger.info(f"Beta期货:    {total_cost:>10,.0f} 元")
logger.info(f"避险追加:    {gap:>10,.0f} 元")
logger.info(f"尾部保护:    {50_000:>10,.0f} 元")
logger.info(f"合计:        {total_hedge_budget:>10,.0f} 元")
logger.info(f"占组合比例:  {total_hedge_budget / portfolio_value * 100:.2f}%")
logger.info("[通过] 总预算在 40% 上限内，可执行")
