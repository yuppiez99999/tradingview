"""
组合总盈亏 + 对冲明细报告生成器
输入：config/positions.json + realtime_monitor/realtime_positions_YYYY-MM-DD.json
输出：每日报告归档/YYYY-MM-DD/组合总盈亏报告_YYYYMMDD.md
"""

import json
import os
import sys

from utils.datetime_utils import now_bj

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

POSITIONS_PATH = os.path.join(REPO_ROOT, "config", "positions.json")
REALTIME_DIR = os.path.join(REPO_ROOT, "realtime_monitor")
REPORT_DIR = os.path.join(REPO_ROOT, "每日报告归档")


def normalize_code(code: str) -> str:
    s = str(code).strip().lower()
    for suffix in (".sh", ".sz", ".bj"):
        if s.endswith(suffix):
            s = s[:-3]
            break
    if s.startswith("sh") or s.startswith("sz"):
        return s
    if s.startswith("6"):
        return f"sh{s}"
    if s.startswith(("0", "3")):
        return f"sz{s}"
    if s.startswith(("4", "8")):
        return f"bj{s}"
    # ETF 代码映射
    if s.startswith(("51", "58")):
        return f"sh{s}"
    if s.startswith(("15", "16")):
        return f"sz{s}"
    return f"sh{s}"


def load_positions():
    with open(POSITIONS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data


def load_realtime(date_str: str):
    path = os.path.join(REALTIME_DIR, f"realtime_positions_{date_str}.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {normalize_code(item["code"]): item for item in data.get("items", [])}


def build_position_report():
    positions_data = load_positions()
    positions = positions_data.get("positions", {})
    hedge_positions = positions_data.get("hedge_positions", {})
    meta = positions_data.get("meta", {})

    date_str = now_bj().strftime("%Y-%m-%d")
    realtime_map = load_realtime(date_str)

    rows = []
    total_cost = 0.0
    total_market = 0.0
    total_pnl = 0.0
    style_map = {}
    sector_map = {}

    for code, pos in positions.items():
        shares = int(pos.get("shares", 0))
        avg_cost = float(pos.get("avg_cost", 0.0))
        target_weight = float(pos.get("target_weight", 0.0))
        name = pos.get("name", code)
        style = pos.get("style", "其他")
        sector = pos.get("sector", "其他")
        ptype = pos.get("type", "STOCK")

        norm_code = normalize_code(code)
        rt = realtime_map.get(norm_code, {})
        latest = float(rt.get("latest", 0.0) or 0.0)
        change_ratio = rt.get("change_ratio", "")
        source = rt.get("source", "unknown")

        cost_value = shares * avg_cost
        market_value = shares * latest
        pnl = market_value - cost_value
        weight = target_weight * 100.0

        total_cost += cost_value
        total_market += market_value
        total_pnl += pnl

        style_map.setdefault(style, {"cost": 0.0, "market": 0.0, "pnl": 0.0})
        style_map[style]["cost"] += cost_value
        style_map[style]["market"] += market_value
        style_map[style]["pnl"] += pnl

        sector_map.setdefault(sector, {"cost": 0.0, "market": 0.0, "pnl": 0.0})
        sector_map[sector]["cost"] += cost_value
        sector_map[sector]["market"] += market_value
        sector_map[sector]["pnl"] += pnl

        rows.append(
            {
                "code": code,
                "name": name,
                "type": ptype,
                "style": style,
                "sector": sector,
                "shares": shares,
                "avg_cost": avg_cost,
                "latest": latest,
                "cost_value": cost_value,
                "market_value": market_value,
                "pnl": pnl,
                "weight": weight,
                "change_ratio": change_ratio,
                "source": source,
            }
        )

    # 对冲明细
    hedge_rows = []
    # IF_futures
    if_futures = hedge_positions.get("IF_futures", {})
    if if_futures:
        contracts = int(if_futures.get("target_contracts", 0))
        multiplier = int(if_futures.get("multiplier", 300))
        direction = if_futures.get("direction", "SELL")
        beta_reduction = float(if_futures.get("target_beta_reduction", 0.5))
        portfolio_return = (
            (total_market - total_cost) / total_cost if total_cost else 0.0
        )
        futures_pnl = -portfolio_return * total_market * beta_reduction
        hedge_rows.append(
            {
                "instrument": if_futures.get("instrument", "IF期货"),
                "direction": direction,
                "contracts": contracts,
                "multiplier": multiplier,
                "notional": contracts * multiplier * (total_market / 1000000.0),
                "pnl": futures_pnl,
                "note": f"Beta对冲约{beta_reduction*100:.0f}%组合风险",
            }
        )

    # ETF/个股 Put 期权
    code_map = {
        "ETF_put_options": "510050.SH",
        "588080_put_options": "588080.SH",
        "159915_put_options": "159915.SZ",
        "510300_put_options": "510300.SH",
    }
    for key, underlying_code in code_map.items():
        opt = hedge_positions.get(key, {})
        if not opt:
            continue
        contracts = int(opt.get("target_contracts", 0))
        premium_budget = float(opt.get("premium_budget", 0.0))
        norm_underlying = normalize_code(underlying_code)
        rt = realtime_map.get(norm_underlying, {})
        underlying_price = float(rt.get("latest", 0.0) or 0.0)
        strike = underlying_price * 0.95 if underlying_price else 0.0
        intrinsic = max(0.0, strike - underlying_price) * 10000 * contracts
        pnl = intrinsic - premium_budget
        hedge_rows.append(
            {
                "instrument": opt.get("instrument", underlying_code),
                "direction": opt.get("direction", "BUY"),
                "contracts": contracts,
                "multiplier": 10000,
                "notional": strike * 10000 * contracts if strike else 0.0,
                "pnl": pnl,
                "note": f"行权价约{strike:.3f}，已付权利金约{premium_budget/10000:.1f}万",
            }
        )

    # 组合总览
    total_return_pct = (total_pnl / total_cost * 100.0) if total_cost else 0.0
    hedge_capital = float(meta.get("hedge_capital", 1000000))
    stock_etf_capital = float(meta.get("stock_etf_capital", 4000000))
    total_capital = float(meta.get("total_capital", 5000000))

    # 保存中间数据
    out = {
        "date": date_str,
        "meta": meta,
        "positions": rows,
        "hedge_positions": hedge_rows,
        "summary": {
            "total_capital": total_capital,
            "stock_etf_capital": stock_etf_capital,
            "hedge_capital": hedge_capital,
            "total_cost": total_cost,
            "total_market": total_market,
            "total_pnl": total_pnl,
            "total_return_pct": total_return_pct,
            "position_count": len(rows),
            "hedge_count": len(hedge_rows),
        },
    }
    out_path = os.path.join(REALTIME_DIR, f"portfolio_summary_{date_str}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 生成 Markdown
    md_path = os.path.join(
        REPORT_DIR, date_str, f"组合总盈亏报告_{date_str.replace('-', '')}.md"
    )
    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    lines = []
    lines.append("# 📊 组合总盈亏报告")
    lines.append("")
    lines.append(f"**日期**: {date_str}")
    lines.append(f"**生成时间**: {now_bj().isoformat()}")
    lines.append(f"**总资本**: {total_capital:,.2f} 元")
    lines.append(f"**股票ETF账户**: {stock_etf_capital:,.2f} 元")
    lines.append(f"**期权对冲账户**: {hedge_capital:,.2f} 元")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 一、组合总览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 持仓数量 | {len(rows)} |")
    lines.append(f"| 对冲工具数 | {len(hedge_rows)} |")
    lines.append(f"| 总成本 | {total_cost:,.2f} 元 |")
    lines.append(f"| 总市值 | {total_market:,.2f} 元 |")
    lines.append(f"| 总盈亏 | {total_pnl:,.2f} 元 |")
    lines.append(f"| 总收益率 | {total_return_pct:.2f}% |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 二、持仓明细（按风格）")
    lines.append("")
    lines.append(
        "| 标的 | 类型 | 风格 | 持仓 | 成本价 | 最新价 | 盈亏 | 权重 | 涨跌幅 | 数据源 |"
    )
    lines.append(
        "|------|------|------|------|--------|--------|------|------|--------|--------|"
    )
    for item in rows:
        lines.append(
            f"| {item['name']} | {item['type']} | {item['style']} | {item['shares']} | {item['avg_cost']:.2f} | {item['latest']:.3f} | {item['pnl']:,.2f} | {item['weight']:.2f}% | {item['change_ratio']} | {item['source']} |"  # noqa: E501
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 三、风格归因")
    lines.append("")
    lines.append("| 风格 | 市值 | 盈亏 | 收益率 |")
    lines.append("|------|------|------|--------|")
    for style, v in sorted(
        style_map.items(), key=lambda x: x[1]["market"], reverse=True
    ):
        ret = (v["pnl"] / v["cost"] * 100.0) if v["cost"] else 0.0
        lines.append(f"| {style} | {v['market']:,.2f} | {v['pnl']:,.2f} | {ret:.2f}% |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 四、行业归因")
    lines.append("")
    lines.append("| 行业 | 市值 | 盈亏 | 收益率 |")
    lines.append("|------|------|------|--------|")
    for sector, v in sorted(
        sector_map.items(), key=lambda x: x[1]["market"], reverse=True
    ):
        ret = (v["pnl"] / v["cost"] * 100.0) if v["cost"] else 0.0
        lines.append(
            f"| {sector} | {v['market']:,.2f} | {v['pnl']:,.2f} | {ret:.2f}% |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 五、对冲明细")
    lines.append("")
    lines.append("| 工具 | 方向 | 合约数 | 名义金额 | 盈亏 | 备注 |")
    lines.append("|------|------|--------|----------|------|------|")
    for h in hedge_rows:
        lines.append(
            f"| {h['instrument']} | {h['direction']} | {h['contracts']} | {h['notional']:,.2f} | {h['pnl']:,.2f} | {h['note']} |"  # noqa: E501
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 六、数据源状态")
    lines.append("")
    lines.append("| 数据源 | 状态 | 备注 |")
    lines.append("|--------|------|------|")
    lines.append("| iFinD MCP | ❌ 配额超限 | 待冷却后恢复 |")
    lines.append("| Wind MCP | ❌ 单日请求次数超限 | 待冷却后恢复 |")
    lines.append("| 新浪 HTTP | ✅ 可用 | 当前主数据源 |")
    lines.append("| 本地 iFinD SDK | ❌ 实时行情权限不足 | 需联系同花顺开通 |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "*本报告由组合总盈亏报告系统自动生成  |  数据来源: config/positions.json + 新浪财经实时行情*"
    )
    lines.append(
        f"*生成时间: {now_bj().isoformat()}  |  仅供参考，不构成投资建议*"
    )

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return md_path


if __name__ == "__main__":
    build_position_report()
