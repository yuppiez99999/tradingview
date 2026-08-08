# -*- coding: utf-8 -*-
"""对冲分析模式 — 评估组合风险并生成指数期货/期权对冲方案"""
import os
import json
from datetime import datetime

from core.context import BASE_DIR, logger, ProgressIndicator, get_archive_dir


def run_hedge_mode(args):
    """对冲分析模式 — 评估组合风险并生成指数期货/期权对冲方案
    
    功能:
    1. 评估组合Beta(沪深300/中证500/中证1000/上证50)
    2. 计算VaR/CVaR尾部风险
    3. Taleb+Burry+Druckenmiller 三位一体对冲分析
    4. 生成IF/IC/IH/IM期货做空方案 或 protective put/collar/put spread 期权策略
    5. 估计对冲成本与效果
    """
    print("\n🛡️ AI Hedge Fund — 对冲策略分析")
    print("=" * 70)
    
    progress = ProgressIndicator("对冲分析", 6)
    
    try:
        from utils.hedge_engine import (
            HedgeEngine, HedgeSignalStrength,
            INDEX_FUTURES_SPECS, ETF_OPTIONS_SPECS,
            calculate_portfolio_beta,
        )
        _HEDGE_OK = True
    except ImportError as e:
        print(f"  ❌ 对冲引擎加载失败: {e}")
        _HEDGE_OK = False
        return
    
    progress.update(1, "加载持仓数据...")
    
    # 读取持仓配置
    positions_path = os.path.join(BASE_DIR, 'config', 'positions.json')
    portfolio_path = os.path.join(BASE_DIR, 'config', 'portfolio.yaml')
    
    positions = {}
    cash = 3_000_000
    
    if os.path.exists(positions_path):
        with open(positions_path, 'r', encoding='utf-8') as f:
            pos_data = json.load(f)
            for code, p in pos_data.get('positions', {}).items():
                positions[code] = {'shares': p.get('shares', 0), 'cost': p.get('avg_cost', p.get('cost', 0))}
            cash = pos_data.get('cash', cash)
    
    # 获取实时价格
    progress.update(2, "获取实时价格...")
    prices = {}
    try:
        from utils.wind_data_provider import get_wind_provider
        codes = list(positions.keys())
        if codes:
            provider = get_wind_provider()
            market_data = provider.build_market_data({k: {"shares": positions[k].get("shares", 0)} for k in codes})
            for code in codes:
                if market_data.get(code):
                    prices[code] = market_data[code].get("price", 0)
    except Exception:
        # 降级到价格历史
        pricing_path = os.path.join(BASE_DIR, 'config', 'price_history.jsonl')
        if os.path.exists(pricing_path):
            with open(pricing_path, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        prices[entry['code']] = entry.get('price', 0)
                    except (json.JSONDecodeError, KeyError):
                        continue
    
    # 计算组合总值
    stock_value = sum(v.get('shares', 0) * prices.get(k, 0) for k, v in positions.items())
    total_value = stock_value + cash
    
    progress.update(3, "评估组合风险...")
    
    engine = HedgeEngine(portfolio_value=total_value)
    
    # 组合风险
    risk = engine.assess_portfolio_risk(positions, prices)
    
    print(f"\n  📊 组合风险评估")
    print(f"  {'─' * 50}")
    print(f"  组合总资产:    ¥{risk.total_value:,.0f}")
    print(f"  股票敞口:      ¥{risk.stock_exposure:,.0f} ({risk.stock_exposure/risk.total_value*100:.1f}%)" if risk.total_value > 0 else f"  股票敞口:      ¥{risk.stock_exposure:,.0f}")
    print(f"  现金:          ¥{risk.cash:,.0f}")
    print(f"  组合Beta:      CSI300={risk.beta_csi300:.3f} | CSI500={risk.beta_csi500:.3f} | CSI1000={risk.beta_csi1000:.3f} | SSE50={risk.beta_sse50:.3f}")
    print(f"  30日波动率:    {risk.volatility_30d*100:.1f}%")
    print(f"  日VaR(95%):    ¥{risk.var_95_daily:,.0f} ({risk.var_95_daily/risk.total_value*100:.2f}%)" if risk.total_value > 0 else f"  日VaR(95%):    ¥0")
    print(f"  日CVaR(95%):   ¥{risk.cvar_95_daily:,.0f}")
    print(f"  集中度(HHI):   {risk.concentration_risk:.4f}")
    
    # 市场信号
    progress.update(4, "收集市场信号...")
    market_signals = {}
    
    # 获取四大股指期货实时价格（多源回退: AKShare → 新浪 → efinance → 默认值）
    from utils.hedge_engine import get_live_futures_prices
    futures_prices = get_live_futures_prices()
    if futures_prices:
        print(f"  📡 期货价格来源: 已获取 {len([k for k in futures_prices if futures_prices[k] > 0])}/4 品种")
    
    # 对冲信号强度判断
    strength, score = engine.determine_hedge_signal_strength(risk, market_signals)
    strength_names = {
        HedgeSignalStrength.NO_HEDGE: "无需对冲 ✓",
        HedgeSignalStrength.LIGHT: "轻度对冲 ⚡",
        HedgeSignalStrength.MODERATE: "中度对冲 ⚠️",
        HedgeSignalStrength.STRONG: "强力对冲 🔴",
        HedgeSignalStrength.FULL: "完全对冲 🚨",
    }
    
    print(f"\n  📡 对冲信号")
    print(f"  {'─' * 50}")
    print(f"  信号强度:      {strength_names.get(strength, '未知')}")
    print(f"  对冲评分:      {score:.2f}/1.0")
    
    # 生成对冲方案
    progress.update(5, "生成对冲方案...")
    
    if strength != HedgeSignalStrength.NO_HEDGE:
        # 期货方案
        hedge_ratio = engine.compute_optimal_hedge_ratio(risk, strength)
        print(f"  对冲比率:      {hedge_ratio*100:.0f}% ({risk.stock_exposure*hedge_ratio:,.0f})" if risk.stock_exposure > 0 else f"  对冲比率:      {hedge_ratio*100:.0f}%")
        
        futures_result = engine.generate_futures_hedge(risk, hedge_ratio, futures_prices)
        
        print(f"\n  📉 期货对冲方案")
        print(f"  {'─' * 50}")
        if futures_result.get("contracts"):
            for code, detail in futures_result["contracts"].items():
                spec = detail.get("spec", {})
                n = detail["contracts"]
                notional = detail["notional"]
                margin = detail["margin"]
                print(f"  {code} {spec.get('name', code)}: 做空 {n} 手")
                print(f"    名义价值: ¥{notional:,.0f} | 保证金: ¥{margin:,.0f}")
            print(f"\n  总名义价值:    ¥{futures_result['total_notional']:,.0f}")
            print(f"  总保证金需求:  ¥{futures_result['total_margin']:,.0f}")
            print(f"  保证金占比:    {futures_result['total_margin']/total_value*100:.1f}%" if total_value > 0 else "  保证金占比:    N/A")
        else:
            print(f"  {futures_result.get('reason', '无期货方案')}")
        
        # 期权方案 (作为替代/补充)
        options_result = engine.generate_options_hedge(risk, hedge_ratio, strategy="protective_put")
        print(f"\n  📊 期权对冲方案 (保护性看跌)")
        print(f"  {'─' * 50}")
        print(f"  策略:          {options_result.get('strategy', 'N/A')}")
        print(f"  标的:          {options_result.get('underlying', 'N/A')}")
        print(f"  合约张数:      {options_result.get('contracts', 0)}")
        print(f"  预估权利金:    ¥{options_result.get('total_premium', 0):,.0f}" if options_result.get('total_premium') else f"  预估权利金:    ¥{options_result.get('total_premium', 0):,.0f}")
        print(f"  权利金占比:    {options_result.get('total_premium_pct', 0):.2f}%")
        print(f"  最大保护额度:  ¥{options_result.get('max_protection', 0):,.0f}")
        
        # 对冲效果预估
        expected_beta = risk.beta_csi300 * (1 - hedge_ratio)
        print(f"\n  📈 对冲效果预估")
        print(f"  {'─' * 50}")
        print(f"  对冲后Beta:    {expected_beta:.3f} (原{risk.beta_csi300:.3f})")
        print(f"  预期回撤减少:  ~{hedge_ratio*60:.0f}%")
        
        # 调用AI对冲分析师 (可选，需要LLM API)
        if not getattr(args, 'no_ai', False):
            progress.update(6, "AI对冲分析...")
            print(f"\n  🧠 AI 对冲分析师 (Taleb+Burry+Druckenmiller)")
            print(f"  {'─' * 50}")
            
            try:
                from quant_modules.ai_hedge_fund.agents.hedge_analyst import hedge_analyst_agent
                
                analyst_state = {
                    "messages": [],
                    "data": {
                        "tickers": list(positions.keys()),
                        "portfolio": {
                            "cash": cash,
                            "positions": {
                                code: {"long": pos['shares'], "short": 0, "long_cost_basis": pos['cost']}
                                for code, pos in positions.items()
                            },
                        },
                        "market_data": {
                            "total_value": total_value,
                            "beta_csi300": risk.beta_csi300,
                            "beta_csi500": risk.beta_csi500,
                            "var_95": risk.var_95_daily,
                            "cvar_95": risk.cvar_95_daily,
                            "concentration_hhi": risk.concentration_risk,
                        },
                        "analyst_signals": {},
                    },
                    "metadata": {
                        "show_reasoning": getattr(args, 'show_reasoning', False),
                        "model_name": getattr(args, 'model', 'deepseek-chat'),
                        "model_provider": getattr(args, 'provider', 'DeepSeek'),
                    },
                }
                
                result = hedge_analyst_agent(analyst_state)
                hedge_signal = result["data"]["analyst_signals"].get("hedge_analyst_agent", {})
                
                if hedge_signal:
                    signal_name = hedge_signal.get("signal", "neutral")
                    signal_emoji = {"bearish": "🔴", "neutral": "🟡", "bullish": "🟢"}.get(signal_name, "⚪")
                    print(f"    {signal_emoji} 对冲信号: {signal_name}")
                    print(f"    对冲比率: {hedge_signal.get('hedge_ratio', 0)*100:.0f}%")
                    print(f"    紧急程度: {hedge_signal.get('urgency_score', 0)*100:.0f}%")
                    
                    reasoning = hedge_signal.get('reasoning', '')
                    if reasoning:
                        try:
                            r = json.loads(reasoning)
                            if 'risk_warnings' in r:
                                for w in r['risk_warnings']:
                                    print(f"    ⚠️ {w}")
                            if 'hedge_recommendation' in r:
                                rec = r['hedge_recommendation']
                                print(f"    推荐工具: {rec.get('preferred_instrument', 'N/A')}")
                                print(f"    执行时机: {rec.get('execution_timing', 'N/A')}")
                        except (json.JSONDecodeError, KeyError):
                            if len(reasoning) > 100:
                                reasoning = reasoning[:100] + "..."
                            print(f"    详情: {reasoning}")
            except Exception as e:
                print(f"    ⚠️ AI分析跳过: {e}")
        
        # 保存报告
        try:
            report_data = {
                'timestamp': datetime.now().isoformat(),
                'mode': 'hedge_analysis',
                'risk': {
                    'total_value': risk.total_value,
                    'stock_exposure': risk.stock_exposure,
                    'beta_csi300': risk.beta_csi300,
                    'beta_csi500': risk.beta_csi500,
                    'var_95_daily': risk.var_95_daily,
                    'cvar_95_daily': risk.cvar_95_daily,
                    'concentration_hhi': risk.concentration_risk,
                },
                'hedge_recommendation': {
                    'strength': strength.name,
                    'score': score,
                    'hedge_ratio': hedge_ratio,
                    'futures': futures_result if strength != HedgeSignalStrength.NO_HEDGE else {},
                    'options': options_result if strength != HedgeSignalStrength.NO_HEDGE else {},
                },
            }
            report_dir = get_archive_dir(base_dir=BASE_DIR)
            report_path = os.path.join(report_dir, f'Hedge_Analysis_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)
            print(f"\n  📄 报告已保存: {report_path}")
        except Exception as e:
            logger.warning(f"对冲报告保存失败: {e}")
    else:
        progress.update(6, "跳过对冲...")
        print(f"\n  ✅ 组合风险可控，无需对冲")
    
    progress.complete("✅ 对冲分析完成")
    print(f"\n  ⚠️ 以上分析仅供参考，不构成投资建议。")
    print(f"  期货/期权交易有杠杆风险，请谨慎执行。")
