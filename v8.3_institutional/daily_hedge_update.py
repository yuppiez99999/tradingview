# -*- coding: utf-8 -*-
"""
daily_hedge_update.py
功能：
1. 使用 Wind MCP 更新历史收益率数据
2. 运行对冲决策
3. 生成对冲报告
"""
import sys
import os
import json
import pandas as pd
from datetime import datetime

sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统8.4')
sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional\src')

from wind_mcp_fetcher import wind_get_quote, wind_get_kline
from hedging.hedge_coordinator import HedgeCoordinator


def _to_wind_code(symbol: str):
    s = str(symbol).strip()
    for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    if s.startswith(("51", "58")):
        return f"{s}.SH", True
    if s.startswith(("15", "16")):
        return f"{s}.SZ", True
    if s.startswith(("00", "30")):
        return f"{s}.SZ", False
    if s.startswith("6"):
        return f"{s}.SH", False
    if s.startswith(("4", "8")):
        return f"{s}.BJ", False
    return f"{s}.SH", False


def _get_historical_kline(symbol: str, days: int = 252):
    wind_code, is_fund = _to_wind_code(symbol)
    items = wind_get_kline(wind_code, days=days, is_fund=is_fund)
    if not items:
        return None
    records = []
    for k in items:
        close = k.get("close") or k.get("CLOSE") or k.get("MATCH") or k.get("match")
        if close is None:
            continue
        try:
            close = float(close)
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        date = k.get("date") or k.get("DATE") or k.get("trade_date") or k.get("time") or k.get("TIME")
        records.append({"date": date, "close": close})
    if not records:
        return None
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    df.sort_index(inplace=True)
    return df


def update_returns():
    """更新历史收益率数据 - Wind MCP 直连"""
    print('=' * 60)
    print('1. 更新历史收益率数据')
    print('=' * 60)
    
    positions_path = r'e:\各种PY程序\28-终极量化交易系统8.4\config\positions.json'
    with open(positions_path, 'r', encoding='utf-8') as f:
        positions_data = json.load(f)['positions']
    
    symbols = [item.get('code') for item in positions_data.values() if item.get('code')]
    returns_data = {}
    success_count = 0
    fail_count = 0
    
    for symbol in symbols:
        try:
            df = _get_historical_kline(symbol, days=252)
            if df is not None and not df.empty:
                df['return'] = df['close'].pct_change()
                returns_data[symbol] = df['return'].dropna()
                success_count += 1
            else:
                fail_count += 1
        except Exception:
            fail_count += 1
    
    print(f'更新完成: 成功 {success_count}, 失败 {fail_count}')
    
    if returns_data:
        returns_df = pd.DataFrame(returns_data)
        returns_path = r'e:\各种PY程序\28-终极量化交易系统8.4\config\returns_history.json'
        returns_df.to_json(returns_path, orient='split', date_format='iso')
        
        market_symbol = '510300'
        market_df = _get_historical_kline(market_symbol, days=252)
        if market_df is not None and not market_df.empty:
            market_returns = market_df['close'].pct_change().dropna()
            market_path = r'e:\各种PY程序\28-终极量化交易系统8.4\config\market_returns.json'
            market_returns.to_json(market_path, orient='split', date_format='iso')
        
        return returns_df, market_returns if 'market_returns' in dir() else None
    else:
        return None, None


def run_hedge_decision():
    """运行对冲决策 - Wind MCP 直连"""
    print()
    print('=' * 60)
    print('2. 运行对冲决策')
    print('=' * 60)
    
    positions_path = r'e:\各种PY程序\28-终极量化交易系统8.4\config\positions.json'
    with open(positions_path, 'r', encoding='utf-8') as f:
        positions_data = json.load(f)['positions']
    
    positions = {}
    prices = {}
    for _key, item in positions_data.items():
        code = item.get('code')
        qty = item.get('phase1_shares') or item.get('total_shares') or item.get('shares', 0)
        if not code or not qty:
            continue
        wind_code, is_fund = _to_wind_code(code)
        quote = wind_get_quote(wind_code, is_fund=is_fund)
        price = None
        if quote and quote.get('price') is not None:
            try:
                price = float(quote['price'])
            except (TypeError, ValueError):
                price = None
        if price is None or price <= 0:
            price = float(item.get('est_price', 0.0) or 0.0)
        positions[code] = float(qty)
        prices[code] = price
    
    # 加载历史数据
    returns_path = r'e:\各种PY程序\28-终极量化交易系统8.4\config\returns_history.json'
    market_path = r'e:\各种PY程序\28-终极量化交易系统8.4\config\market_returns.json'
    
    returns = pd.DataFrame()
    market_returns = pd.Series(dtype=float)
    
    if os.path.exists(returns_path) and os.path.exists(market_path):
        try:
            returns = pd.read_json(returns_path, orient='split')
            market_returns = pd.read_json(market_path, orient='split', typ='series')
            returns.columns = returns.columns.astype(str)
        except Exception:
            pass
    
    # 运行对冲引擎
    coordinator = HedgeCoordinator(enable_tail_risk=True)
    plan = coordinator.coordinate(
        positions=positions,
        prices=prices,
        returns=returns,
        market_returns=market_returns,
        vix=25.0,
        portfolio_value=5_000_000.0,
        hwm_drawdown=0.03,
        bs_loss=0.0,
    )
    
    print(f'动作: {plan.get("action")}')
    print(f'组合Beta: {plan.get("portfolio_beta"):.4f}')
    print(f'总对冲比例: {float(plan.get("total_hedge_pct", 0.0) or 0.0)*100:.2f}%')
    print(f'总成本比例: {float(plan.get("total_cost_pct", 0.0) or 0.0)*100:.4f}%')
    print(f'市场状态: {plan.get("regime")}')
    
    return plan


def generate_report(plan):
    """生成对冲报告"""
    print()
    print('=' * 60)
    print('3. 生成对冲报告')
    print('=' * 60)
    
    report_dir = r'e:\各种PY程序\28-终极量化交易系统8.4\reports'
    os.makedirs(report_dir, exist_ok=True)
    
    report = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'time': datetime.now().strftime('%H:%M:%S'),
        'action': plan.get('action'),
        'portfolio_beta': float(plan.get('portfolio_beta', 0.0) or 0.0),
        'total_hedge_pct': float(plan.get('total_hedge_pct', 0.0) or 0.0),
        'total_cost_pct': float(plan.get('total_cost_pct', 0.0) or 0.0),
        'regime': str(plan.get('regime')),
        'orders': plan.get('orders', []),
        'summary': plan.get('summary', {}),
    }
    
    report_path = os.path.join(report_dir, f'hedge_decision_{datetime.now().strftime("%Y%m%d")}.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f'报告已保存: {report_path}')
    
    # 生成可读报告
    readme_path = os.path.join(report_dir, f'hedge_decision_{datetime.now().strftime("%Y%m%d")}.md')
    with open(readme_path, 'w', encoding='utf-8') as f:
        f.write(f'# 对冲决策报告 - {report["date"]}\n\n')
        f.write('## 决策结果\n\n')
        f.write(f'- **动作**: {report["action"]}\n')
        f.write(f'- **组合Beta**: {report["portfolio_beta"]:.4f}\n')
        f.write(f'- **总对冲比例**: {report["total_hedge_pct"]*100:.2f}%\n')
        f.write(f'- **总成本比例**: {report["total_cost_pct"]*100:.4f}%\n')
        f.write(f'- **市场状态**: {report["regime"]}\n\n')
        
        if report['orders']:
            f.write('## 对冲指令\n\n')
            for i, order in enumerate(report['orders'], 1):
                f.write(f'### {i}. {order.get("hedge_type", "UNKNOWN")}\n\n')
                f.write(f'- 动作: {order.get("action")}\n')
                f.write(f'- 标的: {order.get("instrument", "N/A")}\n')
                f.write(f'- 手数: {order.get("contracts", "N/A")}\n')
                f.write(f'- 名义价值: {order.get("notional", 0):,.0f}\n')
                f.write(f'- 预估成本: {order.get("estimated_cost", order.get("budget", 0)):,.0f}\n\n')
        else:
            f.write('## 结论\n\n')
            f.write('当前无需开启额外对冲。\n')
    
    print(f'可读报告: {readme_path}')

if __name__ == '__main__':
    print('每日对冲自动更新 - Wind MCP')
    print('=' * 60)
    print(f'运行时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print()
    
    # 1. 更新收益率数据
    returns_df, market_returns = update_returns()
    
    # 2. 运行对冲决策
    plan = run_hedge_decision()
    
    # 3. 生成报告
    generate_report(plan)
    
    print()
    print('=' * 60)
    print('更新完成')
    print('=' * 60)
