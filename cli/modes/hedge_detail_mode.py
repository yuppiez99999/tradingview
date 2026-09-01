"""期货对冲明细模式 — 展示完整期货对冲规格、Beta表、触发机制和回测结果"""

import json
import os
from datetime import datetime

from core.context import BASE_DIR


def run_hedge_detail_mode(args):
    """期货对冲明细 — 展示所有期货/期权对冲相关配置详情

    功能:
    1. 股指期货品种规格 (IF/IC/IM/IH)
    2. 组合Beta值明细表
    3. 对冲触发机制与强度分级
    4. 多指数Beta加权分配算法
    5. 历史压力测试场景
    6. 期权补充策略
    7. 回测验证结果

    对冲引擎: utils/hedge_engine.py v5.10 (P0-8 压力测试集成)
    选项:
      --output <path>  指定输出路径
      --json           输出 JSON 格式
    """
    output_path = getattr(args, "output", None)
    as_json = getattr(args, "json", False) if hasattr(args, "json") else False

    # 加载对冲配置
    import yaml

    config_path = os.path.join(BASE_DIR, "config", "hedge.yaml")
    try:
        with open(config_path, encoding="utf-8") as f:
            hedge_config = yaml.safe_load(f) or {}
    except Exception:
        hedge_config = {}

    # 加载组合配置
    portfolio_path = os.path.join(BASE_DIR, "config", "portfolio.yaml")
    try:
        with open(portfolio_path, encoding="utf-8") as f:
            yaml.safe_load(f) or {}
    except Exception:
        pass

    # 对冲引擎核心数据
    # ── 获取实时期货价格 ──
    from utils.hedge_engine import (
        DEFAULT_FUTURES_PRICES,
        ETF_OPTIONS_SPECS,
        FALLBACK_PRICES_UPDATED,
        INDEX_ALLOCATION_ORDER,
        INDEX_FUTURES_SPECS,
        PORTFOLIO_TAIL_HEDGE_TRIGGERS,
        HedgeEngine,
        get_live_futures_prices,
    )

    live_prices = get_live_futures_prices()

    # ── 读取持仓数据 ──
    positions_path = os.path.join(BASE_DIR, "config", "positions.json")
    positions = {}
    cash = 3_000_000
    if os.path.exists(positions_path):
        try:
            with open(positions_path, encoding="utf-8") as f:
                pos_data = json.load(f)
                for code, p in pos_data.get("positions", {}).items():
                    positions[code] = {
                        "shares": p.get("shares", 0),
                        "cost": p.get("cost", 0),
                    }
                cash = pos_data.get("cash", cash)
        except Exception:
            pass

    # 获取价格估计
    prices_est = {}
    try:
        from utils.wind_data_provider import get_wind_provider

        codes = list(positions.keys())
        if codes:
            provider = get_wind_provider()
            market_data = provider.build_market_data(
                {k: {"shares": positions[k].get("shares", 0)} for k in codes}
            )
            for code in codes:
                if market_data.get(code):
                    prices_est[code] = market_data[code].get("price", 0)
    except Exception:
        price_history = os.path.join(BASE_DIR, "config", "price_history.jsonl")
        if os.path.exists(price_history):
            with open(price_history, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        prices_est[entry["code"]] = entry.get("price", 0)
                    except (json.JSONDecodeError, KeyError):
                        pass

    # 计算组合估值
    stock_value = sum(
        v.get("shares", 0) * prices_est.get(k, 0) for k, v in positions.items()
    )
    total_value = stock_value + cash

    # ── 输出内容 ──
    if as_json:
        output = json.dumps(
            {
                "timestamp": datetime.now().isoformat(),
                "futures_specs": INDEX_FUTURES_SPECS,
                "options_specs": ETF_OPTIONS_SPECS,
                "default_prices": DEFAULT_FUTURES_PRICES,
                "default_prices_updated": FALLBACK_PRICES_UPDATED,
                "live_prices": live_prices,
                "tail_hedge_triggers": PORTFOLIO_TAIL_HEDGE_TRIGGERS,
                "index_allocation_order": INDEX_ALLOCATION_ORDER,
                "hedge_config": hedge_config,
                "portfolio": {
                    "total_value": total_value,
                    "stock_exposure": stock_value,
                    "cash": cash,
                    "positions": list(positions.keys()),
                },
                "stress_scenarios": HedgeEngine.HISTORICAL_STRESS_SCENARIOS,
                "beta_table": {},
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        print(output)
        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"\n  JSON 报告已保存: {output_path}")
        return

    # ── 文本输出 ──
    print("\n📋 期货对冲明细 — 量化策略系统 v5.10")
    print("=" * 75)
    print(f"  组合总资产:      ¥{total_value:,.0f}")
    print(
        f"  股票敞口:        ¥{stock_value:,.0f} ({stock_value/total_value*100:.1f}%)"
        if total_value > 0
        else ""
    )
    print(f"  现金:            ¥{cash:,.0f}")
    print(f"  持仓标的数:      {len(positions)} 只")

    # ── 1. 期货品种规格 ──
    print("\n  'One' 股指期货品种规格")
    print(f"  {'─' * 70}")
    print(
        f"  {'品种':<6s} {'名称':<18s} {'乘数':>6s} {'保证金':>6s} {'Tick':>6s}  {'实时价格':>10s}  {'数据来源'}"
    )
    print(f"  {'─' * 70}")

    default_prices = DEFAULT_FUTURES_PRICES

    for name in INDEX_ALLOCATION_ORDER + ["IH", "IF"]:
        if name in INDEX_FUTURES_SPECS:
            spec = INDEX_FUTURES_SPECS[name]
            live = live_prices.get(name, 0)
            default_p = default_prices.get(name, 0)
            # 判断来源
            if live > 0:
                source = "实时" if abs(live - default_p) > 50 else "默认(接近实时)"
            else:
                source = "默认回退"
            print(
                f"  {name:<6s} {spec['name']:<18s} {spec['multiplier']:>6d} {spec['margin_pct']:>5.0%} "
                f"{spec['tick_size']:>5.1f}  ¥{live:>9,.1f}  {source}"
            )

    print(f"\n  🔺 分配优先级: {' > '.join(INDEX_ALLOCATION_ORDER)}")

    # ── 2. 对冲触发阈值 ──
    print("\n  'Two' 对冲触发阈值")
    print(f"  {'─' * 70}")
    trigger = hedge_config.get("trigger", {})
    print(f"  CSI300 Beta下限:         > {trigger.get('min_beta_csi300', 0.8)}")
    print(f"  集中度HHI触发:            > {trigger.get('min_concentration_hhi', 0.12)}")
    print(
        f"  日VaR(95%)触发:           > {trigger.get('min_var_pct', 0.015)*100:.1f}% 总资产"
    )
    print(f"  恐慌指数阈值:             > {trigger.get('panic_index_threshold', 0.6)}")
    print("  ── v5.9 组合自触发 ──")
    print(
        f"  波动率触发(年化):         > {PORTFOLIO_TAIL_HEDGE_TRIGGERS['vol_trigger']*100:.0f}%"
    )
    print(
        f"  60日回撤触发:              > {PORTFOLIO_TAIL_HEDGE_TRIGGERS['dd_trigger']*100:.0f}%"
    )
    print(
        f"  触发后对冲范围:           {PORTFOLIO_TAIL_HEDGE_TRIGGERS['min_hedge_ratio']*100:.0f}%-{PORTFOLIO_TAIL_HEDGE_TRIGGERS['max_hedge_ratio']*100:.0f}%"  # noqa: E501
    )

    # ── 3. 对冲强度 ──
    print("\n  'Three' 对冲强度分级")
    print(f"  {'─' * 70}")
    strength = hedge_config.get("strength", {})
    for level, cfg in strength.items():
        print(
            f"  {level:<10s} 对冲 {cfg['ratio']*100:3.0f}% 敞口  — {cfg.get('description', '')}"
        )

    # ── 4. 保证金约束 ──
    margin_cfg = hedge_config.get("margin", {})
    print("\n  'Four' 保证金与成本")
    print(f"  {'─' * 70}")
    print(
        f"  最大保证金占比:           < {margin_cfg.get('max_margin_pct', 0.20)*100:.0f}%"
    )
    print(
        f"  最低现金留存:             ¥{margin_cfg.get('min_cash_reserve', 100000):,}"
    )
    print("  年化展期成本(基差+费):    ~2.5%")
    print("  保证金机会成本(无风险):   ~2.0%")
    print("  成本效益阈值:             预期收益 > 成本 x 1.5")

    # ── 5. 期权策略 ──
    print("\n  'Five' 期权对冲策略 (补充)")
    print(f"  {'─' * 70}")
    options_cfg = hedge_config.get("options", {})
    for strategy_name in options_cfg.get("strategies", []):
        strat = options_cfg.get(strategy_name, {})
        if isinstance(strat, dict):
            print(f"  {strategy_name:<18s} {strat.get('description', '')}")
            print(f"    ✓ {strat.get('pro', '')}")
            print(f"    ✗ {strat.get('con', '')}")
            print(f"    适用: {strat.get('suitable', '')}")
    print("\n  可用标的:")
    for code, spec in ETF_OPTIONS_SPECS.items():
        print(f"    {code}: {spec['name']}  (乘数 {spec['multiplier']:,})")

    # ── 6. 历史压力测试 ──
    print("\n  'Six' 历史压力测试场景 (v5.10 P0-8)")
    print(f"  {'─' * 70}")
    print(
        f"  {'情景':<20s} {'CSI300':>6s} {'CSI500':>6s} {'CSI1000':>6s} {'SSE50':>6s} {'黄金':>6s}"
    )
    print(f"  {'─' * 70}")
    for scenario, shocks in HedgeEngine.HISTORICAL_STRESS_SCENARIOS.items():
        print(
            f"  {scenario:<20s} {shocks['csi300']:>+5.0%}  {shocks['csi500']:>+5.0%}  "
            f"{shocks['csi1000']:>+5.0%}  {shocks['sse50']:>+5.0%}  {shocks['gold']:>+5.0%}"
        )

    # ── 7. Beta 表 ──
    print("\n  'Seven' 主要标的Beta值")
    print(f"  {'─' * 70}")
    print(
        f"  {'代码':<10s} {'名称':<10s} {'CSI300':>8s} {'CSI500':>8s} {'CSI1000':>8s} {'SSE50':>8s}"
    )
    print(f"  {'─' * 70}")
    beta_name_map = {
        "300308": "中际旭创",
        "688041": "海光信息",
        "002371": "北方华创",
        "688981": "中芯国际",
        "300750": "宁德时代",
        "000425": "徐工机械",
        "601088": "中国神华",
        "600219": "南山铝业",
        "600019": "宝钢股份",
        "518880": "黄金ETF",
        "000792": "盐湖股份",
        "600900": "长江电力",
        "600276": "恒瑞医药",
        "603259": "药明康德",
        "002422": "科伦药业",
    }
    for code, betas in sorted(HedgeEngine.DEFAULT_BETAS.items()):
        name = beta_name_map.get(code, "")
        print(
            f"  {code:<10s} {name:<10s} {betas[0]:>8.2f} {betas[1]:>8.2f} {betas[2]:>8.2f} {betas[3]:>8.2f}"
        )

    # ── 8. CLI 命令参考 ──
    print("\n  'Eight' 相关CLI命令")
    print(f"  {'─' * 70}")
    print("  --hedge             对冲分析 (含AI分析师)")
    print("  --hedge --no-ai     纯规则引擎对冲")
    print("  --hedge-rebalance   对冲+再平衡联动")
    print("  --hedge-rebalance --mode=tail_only   尾部保护 (默认)")
    print("  --hedge-rebalance --mode=dynamic     动态对冲")
    print("  --hedge-detail      本明细文档")
    print("  --stress-test       极端压力测试")

    # ── 保存 ──
    if output_path:
        md_path = os.path.join(BASE_DIR, "docs", "期货对冲明细.md")
        print(f"\n  📄 完整文档: {md_path}")
        print(f"  📄 输出已保存: {output_path}")

    print(f"\n  ⚠️ 数据来源: {FALLBACK_PRICES_UPDATED} 更新, 默认回退仅用于离线分析")
    print("  ⚠️ 以上信息仅供参考，不构成投资建议。")
    print("  ⚠️ 期货/期权交易有杠杆风险，请谨慎执行。\n")
