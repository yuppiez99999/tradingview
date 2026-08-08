"""
今日开仓对冲决策 - 优化版
基于组合结构的智能评估 + v7.5 对冲引擎
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
import logging

logger = logging.getLogger(__name__)

# S3修复: 用 PROJECT_ROOT 替代硬编码绝对路径
PROJECT_ROOT = Path(__file__).resolve().parent
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
sys.path.insert(0, str(PROJECT_ROOT))  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402
setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils

from hedging.hedge_coordinator import HedgeCoordinator

# ============================================================
# 1. 读取持仓
# ============================================================
DATA_DIR = PROJECT_ROOT / "config"
positions_path = DATA_DIR / "positions.json"
plan_path = str(DATA_DIR / "500万建仓计划_20260706.json")

with open(positions_path, encoding="utf-8") as f:
    positions_data = json.load(f)["positions"]

with open(plan_path, encoding="utf-8") as f:
    plan_data = json.load(f)

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

# ============================================================
# 2. 组合结构分析
# ============================================================
portfolio_value = 5_000_000.0
day_capital = 1_722_410.0
deployed_ratio = day_capital / portfolio_value

# 按风格统计
style_amounts = defaultdict(float)
style_counts = defaultdict(int)
for code, qty in positions.items():
    amt = qty * prices.get(code, 0.0)
    style = style_map.get(code, "其他")
    style_amounts[style] += amt
    style_counts[style] += 1

# 按风险统计
risk_amounts = defaultdict(float)
for code, qty in positions.items():
    amt = qty * prices.get(code, 0.0)
    risk = risk_map.get(code, "中")
    risk_amounts[risk] += amt

# 集中度分析
sorted_positions = sorted(positions.items(), key=lambda x: x[1] * prices.get(x[0], 0.0), reverse=True)
top5 = sorted_positions[:5]
top5_amount = sum(qty * prices.get(code, 0.0) for code, qty in top5)

# B-4.2: 风格Beta代理统一到 utils/risk/style_beta.py (消除 DRY 违规)
# 注: 本脚本引用 7.1 旧路径, 用 try/except 兼容 8.4 统一模块导入
try:
    from utils.risk.style_beta import STYLE_BETA_PROXY as style_beta_proxy
except ImportError:
    # 回退: 7.1 旧路径环境下使用硬编码 (与 utils/risk/style_beta.py 保持同步)
    style_beta_proxy = {
        "宽基": 0.95, "高端制造": 1.15, "科技": 1.20, "制造": 1.05,
        "新能源": 1.10, "医药": 0.85, "化工": 1.00, "银行": 0.75,
        "防御": 0.60, "顺周期": 1.10, "避险": -0.10, "红利": 0.70, "成长": 1.25,
    }

portfolio_beta_est = 0.0
for code, qty in positions.items():
    amt = qty * prices.get(code, 0.0)
    style = style_map.get(code, "其他")
    beta = style_beta_proxy.get(style, 1.0)
    portfolio_beta_est += (amt / day_capital) * beta if day_capital > 0 else 0.0

# ============================================================
# 3. 运行 v7.5 对冲引擎 (使用空数据作为 baseline)
# ============================================================
returns = pd.DataFrame()
market_returns = pd.Series(dtype=float)
vix = 25.0
hwm_drawdown = 0.03
bs_loss = 0.0

coordinator = HedgeCoordinator(enable_tail_risk=True)
plan = coordinator.coordinate(
    positions=positions,
    prices=prices,
    returns=returns,
    market_returns=market_returns,
    vix=vix,
    portfolio_value=portfolio_value,
    hwm_drawdown=hwm_drawdown,
    bs_loss=bs_loss,
)

# ============================================================
# 4. 优化判断逻辑
# ============================================================
logger.info("=" * 60)
logger.info("今日开仓对冲决策 - 优化版")
logger.info("=" * 60)
logger.info("日期: 2026-07-06")
logger.info("阶段: 第一阶段-底仓建立")
logger.info(f"组合总市值: {portfolio_value:,.0f}")
logger.info(f"今日建仓金额: {day_capital:,.0f}")
logger.info(f"建仓进度: {deployed_ratio * 100:.1f}%")

logger.info("-" * 60)
logger.info("一、组合结构分析")
logger.info("-" * 60)
logger.info("按风格分布:")
for style in [
    "宽基",
    "高端制造",
    "科技",
    "制造",
    "新能源",
    "医药",
    "化工",
    "银行",
    "防御",
    "顺周期",
    "避险",
    "红利",
    "成长",
]:
    if style in style_amounts:
        amt = style_amounts[style]
        pct = amt / day_capital * 100 if day_capital > 0 else 0
        logger.info(f"  {style:8s}: {amt:>10,.0f} 元 ({pct:5.1f}%)")
logger.info(f"估算组合Beta: {portfolio_beta_est:.2f}")
logger.info(f"Top5集中度: {top5_amount / day_capital * 100:.1f}%")
for i, (code, qty) in enumerate(top5, 1):
    name = style_map.get(code, code)
    amt = qty * prices.get(code, 0.0)
    logger.info(f"  {i}. {name:12s} {amt:>10,.0f} 元")

logger.info("按风险分布:")
for risk in ["低", "中", "中高", "高"]:
    if risk in risk_amounts:
        amt = risk_amounts[risk]
        pct = amt / day_capital * 100 if day_capital > 0 else 0
        logger.info(f"  {risk:4s}: {amt:>10,.0f} 元 ({pct:5.1f}%)")

logger.info("-" * 60)
logger.info("二、v7.5 对冲引擎原始输出")
logger.info("-" * 60)
logger.info(f"动作: {plan.get('action')}")
logger.info(f"市场状态: {plan.get('regime')}")
logger.info(f"组合Beta: {plan.get('portfolio_beta')}")
logger.info(f"总对冲比例: {float(plan.get('total_hedge_pct', 0.0) or 0.0) * 100:.2f}%")
logger.info(f"总成本比例: {float(plan.get('total_cost_pct', 0.0) or 0.0) * 100:.4f}%")
logger.info(f"最大对冲上限: {float(plan.get('max_hedge_limit', 0.0) or 0.0) * 100:.2f}%")
logger.info("分项:")
for k in ["beta_hedge", "vol_hedge", "corr_hedge", "tail_hedge"]:
    logger.info(f"  {k}: {plan.get('summary', {}).get(k, 'N/A')}")

logger.info("-" * 60)
logger.info("三、优化判断")
logger.info("-" * 60)

# 优化判断逻辑
beta_over = portfolio_beta_est > 0.7
tech_heavy = style_amounts.get("科技", 0) / day_capital > 0.20 if day_capital > 0 else False
mfg_heavy = style_amounts.get("制造", 0) / day_capital > 0.30 if day_capital > 0 else False
high_risk_ratio = risk_amounts.get("高", 0) / day_capital if day_capital > 0 else 0
defense_weak = (
    (style_amounts.get("防御", 0) + style_amounts.get("避险", 0)) / day_capital < 0.15 if day_capital > 0 else True
)
single_large = top5_amount / day_capital > 0.40 if day_capital > 0 else False

# 剩余待建仓比例
future_ratio = 1.0 - deployed_ratio

if beta_over or tech_heavy or mfg_heavy or high_risk_ratio > 0.30 or single_large:
    logger.info("结论: 建议开启/准备对冲")
    logger.info("理由:")
    if beta_over:
        logger.info(f"  - 估算组合Beta {portfolio_beta_est:.2f} > 0.7 触发阈值")
    if tech_heavy:
        tech_pct = style_amounts.get("科技", 0) / day_capital * 100
        logger.info(f"  - 科技风格占比 {tech_pct:.1f}% > 20%，进攻性过强")
    if mfg_heavy:
        mfg_pct = style_amounts.get("制造", 0) / day_capital * 100
        logger.info(f"  - 制造风格占比 {mfg_pct:.1f}% > 30%，周期敞口过大")
    if high_risk_ratio > 0.30:
        logger.info(f"  - 高风险标的占比 {high_risk_ratio * 100:.1f}% > 30%，波动风险偏高")
    if single_large:
        logger.info(f"  - Top5集中度 {top5_amount / day_capital * 100:.1f}% > 40%，个股集中度风险")
    if defense_weak:
        logger.info("  - 防御/避险资产占比偏低，组合缺乏下跌保护")
else:
    logger.info("结论: 暂不开启额外对冲")
    logger.info("理由:")
    logger.info(f"  - 估算组合Beta {portfolio_beta_est:.2f} <= 0.7")
    logger.info(f"  - 科技+制造合计 {(style_amounts.get('科技', 0) + style_amounts.get('制造', 0)) / day_capital * 100:.1f}%")
    logger.info(f"  - 高风险占比 {high_risk_ratio * 100:.1f}%")
    logger.info("  - 当前仅第一阶段22.5%底仓，暴露可控")

logger.info("-" * 60)
logger.info("四、对冲建议 (分阶段)")
logger.info("-" * 60)
logger.info(f"今日开仓: {day_capital:,.0f} 元")
logger.info(f"后续待建仓: {portfolio_value * future_ratio:,.0f} 元")
logger.info("如果决定对冲，建议分三步走：")
logger.info("1. 今日建仓后观察窗口")
logger.info("   - 不急于立即开对冲仓位")
logger.info("   - 观察尾盘涨跌幅、波动率变化")
logger.info("   - 若尾盘跳水 > 1.5%，转为立即保护")
logger.info("2. 明日开盘前确认")
logger.info("   - 根据隔夜 A50/外围市场判断")
logger.info("   - 若外围走弱，启动 Beta 对冲")
logger.info("   - 若 VIX > 30，启动波动率对冲")
logger.info("3. 第二阶段前锁定")
logger.info("   - 第二阶段(7/20)前完成对冲框架搭建")
logger.info("   - 预留 100 万对冲资金中的 20-30%")
logger.info("   - 优先使用：")
logger.info("     * Beta对冲: IF/IC 股指期货空头")
logger.info("     * 尾部保护: 黄金ETF(518880)已持有")
logger.info("     * 期权保护: 沪深300ETF期权 Put Spread")

logger.info("-" * 60)
logger.info("五、关键触发条件")
logger.info("-" * 60)
logger.info("Beta对冲触发: 组合Beta > 0.7")
logger.info("波动率对冲触发: VIX > 30")
logger.info("相关性对冲触发: 持仓平均相关 > 0.85 且跳升 > 0.15")
logger.info("尾部风险触发: 回撤 > 5% 或 VIX > 25")
logger.info("当前状态:")
logger.info(f"  估算Beta: {portfolio_beta_est:.2f} {'(已触发)' if portfolio_beta_est > 0.7 else '(未触发)'}")
logger.info(f"  VIX假设: {vix:.1f} {'(已触发)' if vix > 30 else '(未触发)'}")
logger.info(f"  当前回撤: {hwm_drawdown * 100:.1f}%")
logger.info(f"  市场状态: {plan.get('regime')}")

logger.info("-" * 60)
logger.info("六、下一步动作")
logger.info("-" * 60)
if portfolio_beta_est > 0.7 or high_risk_ratio > 0.30:
    logger.info("[建议] 启动 Beta 对冲评估")
    logger.info("  - 计算实际持仓 Beta (需要历史收益率)")
    logger.info("  - 确定 IF/IC/IM 期货空头手数")
    logger.info("  - 估算对冲成本，若 > 0.3% 则降级为期权保护")
else:
    logger.info("[建议] 暂缓对冲，继续观察")
    logger.info("  - 明日开盘前重新评估")
    logger.info("  - 关注外围市场 + A50 走势")
    logger.info("  - 若 VIX 抬升或回撤扩大，立即启动")

logger.info("=" * 60)
logger.info("备注: 本结果为基于持仓结构的结构化评估")
logger.info("      真实 Beta/相关性需历史收益率数据支持")
logger.info("=" * 60)
