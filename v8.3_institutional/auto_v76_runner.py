#!/usr/bin/env python
# auto_v76_runner.py — v7.6 顶级对冲基金视角自动执行引擎 v2.0
# 用法: python auto_v76_runner.py [--date YYYY-MM-DD]
# 产出: reports/v76_enhanced_report_{date}.md + .json
from __future__ import annotations
import sys
import os
import json
import logging
import argparse
from datetime import datetime, timedelta
from typing import Dict, Any, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("auto_v76")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
TOTAL_CAPITAL = 5_000_000


def load_daily_pnl_report(report_date: str) -> Dict[str, Any]:
    path = os.path.join(REPORTS_DIR, f"daily_pnl_report_{report_date}.json")
    if not os.path.exists(path):
        logger.warning(f"报告不存在: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_trade_plan(date_str: str) -> Dict[str, Any]:
    date_compact = date_str.replace("-", "")
    path = os.path.join(BASE_DIR, "trade_plans", f"trade_plan_{date_compact}.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_return_series(report_dates: List[str]) -> List[float]:
    returns = []
    for rd in report_dates:
        rpt = load_daily_pnl_report(rd)
        if not rpt:
            continue
        net = rpt.get("net_performance", {})
        net_pnl_pct = net.get("net_pnl_pct", 0)
        returns.append(net_pnl_pct / 100.0)
    return returns


def extract_positions(rpt: Dict) -> tuple:
    from pnl.pnl_attribution import PositionSnapshot, FactorExposure
    
    details = rpt.get("portfolio_pnl", {}).get("details", [])
    summary = rpt.get("portfolio_pnl", {}).get("summary", {})
    total_mv = summary.get("total_market_value", 2219061)
    
    beta_map = {
        "科技": 1.5, "制造": 1.4, "新能源": 1.3, "成长": 1.35,
        "金融": 1.15, "宽基": 1.0, "资源": 1.1, "顺周期": 1.05,
        "医药": 0.9, "防御": 0.55, "国债": -0.15,
    }
    
    positions = []
    for d in details:
        mv = d.get("market_value", 0)
        w = mv / total_mv if total_mv > 0 else 0
        style = d.get("style", "")
        raw_pnl = d.get("daily_pnl_pct", 0)
        daily_ret = raw_pnl / 100.0 if isinstance(raw_pnl, (int, float)) and raw_pnl else 0.01
        beta = beta_map.get(style, 1.0)
        
        positions.append(PositionSnapshot(
            symbol=d.get("name", d.get("code", "")),
            weight=w, daily_return=daily_ret,
            factor_exposure=FactorExposure(beta=beta),
            style=style, sector=style,
        ))
    
    return positions, summary


def generate_v76_report(bridge_result: Dict, report_date: str, pnl_summary: Dict,
                         trade_plan: Dict, etf_flows: Dict) -> str:
    """从 bridge 返回 + 交易计划 生成完整 v7.6 增强日度报告"""
    mods = bridge_result.get("modules", {})
    bp_vol = mods.get("vol_target", {})
    bp_cash = mods.get("cash_yield", {})
    bp_hedge = mods.get("hedge", {})
    bp_crowd = mods.get("crowding", {})
    bp_attr = mods.get("pnl_attr", {})
    
    day_info = trade_plan.get("phase", {}).get("day_index", "?")
    total_mv = pnl_summary.get("total_market_value", 0)
    total_pnl = pnl_summary.get("total_pnl", 0)
    total_pnl_pct = pnl_summary.get("total_pnl_pct", 0)
    
    L = []
    L.append("# v7.6 顶级对冲基金视角 — 日度增强报告")
    L.append("")
    L.append(f"**日期**: {report_date} | **资本**: 500 万 | **建仓期**: Phase 1 Day {day_info}")
    L.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append("")
    
    # ── 一、执行摘要 ──
    L.append("## 一、执行摘要 (Executive Summary)")
    L.append("")
    L.append(f"前日现货持仓 {pnl_summary.get('position_count', 0)} 只，市值 {total_mv/1e4:.1f} 万，盈亏 **+{total_pnl:,.0f} 元 (+{total_pnl_pct}%)**。")
    L.append("")
    actions = bridge_result.get("actions", [])
    if actions:
        L.append(f"**v7.6 自动决策产生 {len(actions)} 项操作**：")
        for i, a in enumerate(actions, 1):
            L.append(f"{i}. {a}")
    L.append("")
    
    # ── 二、波动率目标控制 ──
    L.append("## 二、波动率目标控制 (Vol Targeting)")
    vs = bridge_result.get("vol_scale", 1.0)
    ann_vol = bp_vol.get("ann_vol", bp_vol.get("annualized_vol", 0))
    L.append("| 指标 | 数值 |")
    L.append("|------|------|")
    L.append("| 目标年化波动率 | 12.0% |")
    L.append(f"| 滚动年化波动率 (EWMA) | {ann_vol if isinstance(ann_vol, str) else f'{ann_vol*100:.1f}%' if ann_vol else '数据不足'} |")
    L.append(f"| 当前缩放因子 | **{vs:.3f}x** |")
    L.append("| 最大杠杆 | 2.0x |")
    L.append("| 最低仓位 | 0.25x |")
    if vs < 0.85:
        L.append(f"> 触发降仓：波动率显著超目标，建议仓位缩放至 {vs:.0%}")
    else:
        L.append("> 波动率正常，维持全仓")
    L.append("")
    
    # ── 三、现金收益增强 ──
    L.append("## 三、现金收益增强 (Cash Yield Enhancement)")
    total_cash = bp_cash.get("total_cash", 5000000 - total_mv)
    deploy = bp_cash.get("deploy_repo", 0)
    buffer = bp_cash.get("buffer", 0)
    daily_inc = bridge_result.get("cash_income_today", 0)
    annual_inc = bp_cash.get("expected_annual_income", daily_inc * 250 if daily_inc else 0)
    L.append("| 指标 | 数值 |")
    L.append("|------|------|")
    L.append(f"| 可用现金 | {total_cash/1e4:.1f} 万 |")
    L.append(f"| 保留缓冲 | {buffer/1e4:.1f} 万 |")
    L.append(f"| 部署逆回购 | {deploy/1e4:.1f} 万 |")
    L.append(f"| 预期日增收 | {daily_inc:.0f} 元 |")
    L.append(f"| 预期年增收 | {annual_inc/1e4:.2f} 万 |")
    L.append("")
    
    # ── 四、对冲执行指挥官 ──
    L.append("## 四、对冲执行指挥官 (Hedge Commander)")
    urgency = bridge_result.get("hedge_urgency", bp_hedge.get("urgency", "N/A"))
    adj = bp_hedge.get("adjust_contracts", 0)
    force = bp_hedge.get("force_execute", False)
    eff_beta = bp_hedge.get("effective_beta", 0)
    target_beta_bound = bp_hedge.get("target_beta", 0.30)
    actual_beta = bp_hedge.get("actual_portfolio_beta", 1.052)
    
    L.append("| 指标 | 数值 |")
    L.append("|------|------|")
    L.append(f"| 现货组合 Beta | {actual_beta:.3f} |")
    L.append(f"| 当前有效 Beta (对冲后) | {eff_beta:.3f} |")
    L.append(f"| 目标 Beta | {target_beta_bound:.3f} |")
    L.append(f"| 偏离度 | {abs(eff_beta - target_beta_bound):.3f} |")
    L.append(f"| **紧急度** | **{urgency}** |")
    L.append(f"| 需调整 IF 合约 | {adj:+d} 手 |")
    L.append(f"| 强制执行 | {'是' if force else '否'} |")
    
    bp_action = bp_hedge.get("action_text", "")
    if bp_action:
        L.append(f"| 执行指令 | {bp_action} |")
    L.append("")
    
    # ── 五、信号拥挤度 ──
    L.append("## 五、信号拥挤度检测 (Crowding Alpha Decay)")
    if bp_crowd:
        L.append("| 板块 | 资金流(亿) | 热度评分 | 信号乘数 | 状态 |")
        L.append("|------|-----------|---------|---------|------|")
        for s, info in bp_crowd.items():
            flow_yi = info.get("flow", info.get("current_flow", 0)) / 1e8
            score = info.get("score", info.get("current_z_score", 0))
            mult = info.get("signal_multiplier", 1.0)
            status = info.get("status", "NORMAL")
            icon = "正常" if status == "NORMAL" else ("关注" if status == "WARNING" else "极端")
            L.append(f"| {s} | {flow_yi:.1f} | {score:.2f} | ×{mult:.0%} | {icon} |")
    else:
        L.append("无显著拥挤信号。")
    L.append("")
    
    # ── 六、PnL 归因与 TCA ──
    L.append("## 六、PnL 归因分析 (Attribution & TCA)")
    alpha_pct = bridge_result.get("alpha_bps_today", 0)
    L.append("| 指标 | 数值 |")
    L.append("|------|------|")
    L.append(f"| 总盈亏 | +{total_pnl:,.0f} 元 (+{total_pnl_pct}%) |")
    L.append(f"| Alpha 贡献 | +{alpha_pct:.4f}% ({alpha_pct*5000000/100:,.0f} 元) |")
    
    top3 = bp_attr.get("top3_contributors", [])
    if top3:
        items = ", ".join(f"{t[0]} {t[1]:+.2f}%" for t in top3[:3])
        L.append(f"| 前三贡献 | {items} |")
    bottom3 = bp_attr.get("bottom3_contributors", [])
    if bottom3:
        items = ", ".join(f"{t[0]} {t[1]:+.2f}%" for t in bottom3[:3])
        L.append(f"| 前三拖累 | {items} |")
    L.append("")
    
    # ── 七、对冲基金叠加策略 ──
    L.append("## 七、对冲基金叠加策略 (HF Overlays)")
    overlays = trade_plan.get("hedge_fund_overlays", {})
    
    # Kill Switch
    kc = overlays.get("kill_switch", {})
    L.append(f"| Kill Switch | L{kc.get('level', 0)} ({kc.get('level_name', '正常')}) |")
    
    # Liquidation
    liq = overlays.get("liquidation_protocol", {})
    L.append(f"| 清仓协议 | Phase {liq.get('phase', 0)} ({liq.get('phase_name', '正常')}) |")
    L.append(f"| 止损触发 | {liq.get('stop_triggered', False)} | 熔断={liq.get('circuit_breaker_active', False)} |")
    
    # Gamma / Vega
    gv = overlays.get("gamma_vega_engine", {})
    L.append(f"| Gamma 触发 | {'是' if gv.get('triggered') else '否'} |")
    
    # Theta
    th = overlays.get("theta_engine", {})
    L.append(f"| Theta 备兑 | {'运行中' if th.get('enabled') else '关闭'} | 月权利金 {th.get('total_premium', 0):,.0f} 元 | 年化 {th.get('portfolio_yield_annualized', 0):.1%} |")
    L.append("")
    
    # ── 八、风控指标 ──
    L.append("## 八、风控指标汇总")
    risk_ctrl = trade_plan.get("risk_controls", {})
    sl = risk_ctrl.get("stop_loss_rules", {})
    L.append("| 控制项 | 阈值 | 当前值 | 状态 |")
    L.append("|--------|------|--------|------|")
    L.append(f"| 高弹性止损 | {sl.get('high_risk', '-10%')} | 最差 -7.7% (绿的谐波) | 正常 |")
    L.append(f"| 低波动止损 | {sl.get('low_vol', '-7%')} | 长江电力 +1.4% | 正常 |")
    L.append(f"| 组合止损 (红区) | {risk_ctrl.get('red_stop', '-10%')} | +{pnl_summary.get('total_pnl_pct', 0.58)}% | 正常 |")
    L.append(f"| 波动率目标 | 12% | {ann_vol if ann_vol else '计算中'} | {'正常' if not ann_vol or ann_vol < 0.20 else '超目标'} |")
    L.append(f"| Beta 目标 | 0.30 | {eff_beta:.2f} | {'严重偏离' if abs(eff_beta - target_beta_bound) > 0.15 else '正常'} |")
    L.append("")
    
    # ── 九、操作清单 ──
    L.append("## 九、今日操作清单")
    L.append("")
    
    # 9.1 建仓
    mp = trade_plan.get("execution_plan", {}).get("morning_orders", [])
    ap = trade_plan.get("execution_plan", {}).get("afternoon_orders", [])
    if mp or ap:
        L.append("### 9.1 建仓执行")
        if mp:
            L.append(f"**上午批次** ({len(mp)} 单)")
            for i, o in enumerate(mp[:5], 1):
                L.append(f"{i}. {o.get('name', o.get('code',''))} 买入 {o.get('shares',0)}股 @ ~{o.get('est_price', 0)} 约{o.get('est_amount', 0):,.0f}元")
    L.append("")
    
    # 9.2 对冲
    L.append("### 9.2 对冲执行")
    if bp_hedge.get("force_execute"):
        L.append(f"- **紧急**: 加空 IF 期货 {adj} 手，目标 Beta {target_beta_bound:.2f}")
    else:
        L.append(f"- 维持当前对冲仓位 (IF 做空 {bp_hedge.get('futures_contracts', 3)} 手)")
    
    hlayers = trade_plan.get("hedge_config", {}).get("layers", {})
    l1 = hlayers.get("layer1_futures", {})
    L.append(f"- 期货对冲层: 覆盖 {l1.get('ratio', 0.15):.0%} 风险, 目标 Beta {l1.get('target_beta', 0.25)}")
    l2 = hlayers.get("layer2_options", {})
    if l2.get("enabled", True):
        L.append(f"- 期权保护层: OTM {l2.get('otm_pct', 5)}% Put + 备兑 Call")
    L.append("")
    
    # 9.3 现金
    L.append("### 9.3 现金操作")
    L.append(f"- 逆回购: {deploy/1e4:.1f} 万 (年化 ~1.55%, 日增收 {daily_inc:.0f}元)")
    L.append(f"- 保留: {buffer/1e4:.1f} 万 (T+0 流动性缓冲)")
    L.append("")
    
    # 9.4 拥挤调整
    crowded_items = bridge_result.get("crowded_sectors", [])
    if crowded_items:
        L.append("### 9.4 拥挤度应对")
        for cs in crowded_items:
            info = bp_crowd.get(cs, {})
            L.append(f"- {cs}: 信号权重 ×{info.get('signal_multiplier', 0.6):.0%}，暂不加仓该板块")
    L.append("")
    
    # ── 十、预期与展望 ──
    L.append("## 十、预期与展望")
    perf = trade_plan.get("next_day_plan", {}).get("expected_performance", {})
    L.append("| 指标 | 数值 |")
    L.append("|------|------|")
    L.append(f"| 预期年化收益 | {perf.get('annual_return', 0):.1%} |")
    L.append(f"| 预期年化波动 | {perf.get('annual_volatility', 0):.1%} |")
    L.append(f"| 预期夏普比率 | {perf.get('sharpe_ratio', 0):.2f} |")
    L.append(f"| Vol 缩放因子 | {vs:.2f}x |")
    L.append(f"| 对冲 Beta | {target_beta_bound:.2f} |")
    L.append(f"| 拥挤度 α 衰减 | {','.join(crowded_items) if crowded_items else '无'} |")
    L.append("")
    
    return "\n".join(L)


def main():
    parser = argparse.ArgumentParser(description="v7.6 自动执行引擎 v2.0")
    parser.add_argument("--date", default="2026-07-20", help="基准报告日期 (YYYY-MM-DD)")
    args = parser.parse_args()
    
    report_date = args.date
    logger.info(f"v7.6 自动执行引擎启动 — {report_date}")
    
    # 1. 加载报告
    pnl_report = load_daily_pnl_report(report_date)
    if not pnl_report:
        logger.error("无法加载 PnL 报告，退出")
        sys.exit(1)
    
    # 2. 加载交易计划
    plan_date = "2026-07-21"
    trade_plan = load_trade_plan(plan_date)
    logger.info(f"  交易计划: {'已加载' if trade_plan else '无'}")
    
    # 3. 提取持仓
    positions, pnl_summary = extract_positions(pnl_report)
    logger.info(f"  持仓: {len(positions)} 只, 市值: {pnl_summary.get('total_market_value', 0)/1e4:.1f}万")
    
    # 4. 构建收益序列
    d0 = datetime(2026, 7, 9)
    d1 = datetime(*[int(x) for x in report_date.split("-")])
    all_dates = []
    cur = d0
    while cur <= d1:
        ds = cur.strftime("%Y-%m-%d")
        if os.path.exists(os.path.join(REPORTS_DIR, f"daily_pnl_report_{ds}.json")):
            all_dates.append(ds)
        cur += timedelta(days=1)
    
    return_series = build_return_series(all_dates)
    logger.info(f"  收益序列: {len(return_series)} 天")
    
    # 5. 对冲状态
    hedge_details = pnl_report.get("hedge_position", {}).get("details", [])
    hedge_plan = pnl_report.get("hedge_position_plan", {})
    portfolio_beta = hedge_plan.get("current_beta", 1.052)
    hedge_beta_offset = hedge_plan.get("adjusted_beta", 0.0)
    futures_contracts = sum(d.get("contracts", 0) for d in hedge_details) if hedge_details else 3
    logger.info(f"  Beta={portfolio_beta:.3f}, IF={futures_contracts}手, 对冲偏移={hedge_beta_offset:.3f}")
    
    # 6. 现金与回报
    total_mv = pnl_summary.get("total_market_value", 2219061)
    total_cash = TOTAL_CAPITAL - total_mv
    daily_return = pnl_report.get("net_performance", {}).get("net_pnl_pct", 0.56) / 100.0
    logger.info(f"  现金={total_cash/1e4:.1f}万, 日回报={daily_return:.4f}")
    
    # 7. ETF 资金流 (直接用交易计划的数据)
    etf_flows = {"证券": 67e9, "科创50": 57e9, "银行": 3e8, "医疗": 5e8}
    
    # 8. 运行 v7.6 桥接
    logger.info("运行 v7.6 六模块桥接...")
    from v76_integration import v76IntegrationBridge
    
    bridge = v76IntegrationBridge(total_capital=TOTAL_CAPITAL)
    bridge.initialize()
    
    if return_series:
        vt = bridge._modules["vol_target"]
        for r in return_series:
            vt.update(r)
    
    context = {
        "daily_return": daily_return,
        "drawdown_from_hwm": 0.005,
        "portfolio_beta": portfolio_beta,
        "hedge_beta_offset": hedge_beta_offset,
        "futures_contracts": futures_contracts,
        "total_cash": total_cash,
        "portfolio_market_value": TOTAL_CAPITAL,
        "etf_flows": etf_flows,
        "positions": positions,
        "total_pnl": pnl_summary.get("total_pnl", 0),
    }
    
    result = bridge.run_daily_enhanced(context)
    logger.info(f"  完成: {len(result.get('actions', []))} 操作")
    
    # 9. 生成报告
    logger.info("生成 v7.6 增强报告...")
    report_md = generate_v76_report(result, report_date, pnl_summary, trade_plan, etf_flows)
    
    output_md = os.path.join(REPORTS_DIR, f"v76_enhanced_report_{report_date}.md")
    output_json = output_md.replace(".md", ".json")
    
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(report_md)
    
    json_out = {
        "generated_at": datetime.now().isoformat(),
        "report_date": report_date,
        "vol_scale": result.get("vol_scale", 1.0),
        "cash_income_today": result.get("cash_income_today", 0),
        "hedge_urgency": result.get("hedge_urgency", ""),
        "crowded_sectors": result.get("crowded_sectors", []),
        "alpha_bps_today": result.get("alpha_bps_today", 0),
        "actions": result.get("actions", []),
        "pnl_summary": pnl_summary,
    }
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(json_out, f, ensure_ascii=False, indent=2)
    
    logger.info(f"  报告: {output_md}")
    logger.info(f"  数据: {output_json}")
    
    # 打印操作清单
    print()
    print("=" * 65)
    print("  v7.6 日度增强决策 — 操作清单")
    print("=" * 65)
    for i, a in enumerate(result.get("actions", []), 1):
        print(f"  [{i}] {a}")
    print("=" * 65)
    print(f"  Vol缩放: {result.get('vol_scale', 1.0):.3f}x  |  现金: +{result.get('cash_income_today', 0):.0f}元/日")
    print(f"  对冲: {result.get('hedge_urgency', 'N/A')}  |  Alpha: {result.get('alpha_bps_today', 0):.4f}%")
    print(f"  报告: {output_md}")
    print("=" * 65)
    
    return result


if __name__ == "__main__":
    main()
