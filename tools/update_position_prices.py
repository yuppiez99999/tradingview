"""
持仓实时价格更新器
功能：
1. 从数据源获取实时价格
2. 更新 positions.json 中的 est_price
3. 计算持仓盈亏
4. 生成价格更新报告
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')

from utils.data_provider import MarketDataProvider


def extract_price(market_data: dict, symbol: str = '', provider = None, old_price: float = 0.0):
    if not market_data:
        return 0.0, ''
    source = market_data.get('source', '')
    price = float(
        market_data.get('index_price') if market_data.get('index_price') is not None else
        market_data.get('price') if market_data.get('price') is not None else
        market_data.get('last') if market_data.get('last') is not None else
        market_data.get('current') if market_data.get('current') is not None else
        0.0
    )
    simulated = price == 3000
    # Wind MCP 实时行情优先，其次 iFinD，最后 AKShare
    if source == 'wind_mcp' and price and price > 0:
        return price, source
    if source == 'ifind_mcp' and price and price > 0:
        return price, source
    if source == 'akshare_stock_realtime' and price and price > 0:
        return price, source
    if (price > 0 or simulated) and provider and symbol:
        try:
            hist = provider.get_historical_data(symbol, '5d')
            if hist is not None and not hist.empty and 'close' in hist.columns:
                last_close = float(hist['close'].dropna().iloc[-1])
                if last_close and last_close > 0:
                    if old_price > 0 and last_close > 10 * old_price:
                        return 0.0, f"{source}+historical_rejected"
                    if old_price > 0 and last_close < old_price / 10:
                        return 0.0, f"{source}+historical_rejected"
                    looks_synthetic = (
                        'returns' in hist.columns and
                        abs(float(hist['close'].iloc[-1]) - 3000) < 500
                    )
                    if looks_synthetic:
                        return 0.0, f"{source}+historical_rejected"
                    if simulated or price > 10 * last_close or price < last_close / 10:
                        price = last_close
                        source = f"{source}+historical_fallback".strip('+')
        except Exception:
            raise  # Re-raise unknown exception
    if price == 3000:
        return 0.0, source
    return price, source

def load_positions():
    """加载持仓数据"""
    path = r'e:\各种PY程序\28-终极量化交易系统7.1\config\positions.json'
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return data

def save_positions(data):
    """保存持仓数据"""
    path = r'e:\各种PY程序\28-终极量化交易系统7.1\config\positions.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def update_prices():
    """更新持仓价格"""
    print('=' * 70)
    print('持仓实时价格更新器')
    print('=' * 70)
    print(f'运行时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print()

    # 加载持仓
    data = load_positions()
    positions = data.get('positions', {})
    meta = data.get('meta', {})

    print(f'持仓标的数: {len(positions)}')
    print(f'总资金: {meta.get("total_capital", 0):,.0f}')
    print(f'已建仓: {meta.get("day_capital", 0):,.0f}')
    print()

    # 获取实时价格
    provider = MarketDataProvider()
    update_count = 0
    fail_count = 0
    price_changes = []

    print('-' * 70)
    print(f'{"代码":12s} {"名称":12s} {"旧价格":>8s} {"新价格":>8s} {"变化":>8s} {"持仓金额":>12s}')
    print('-' * 70)

    for _key, item in positions.items():
        code = item.get('code')
        old_price = item.get('est_price', 0.0)
        shares = item.get('phase1_shares') or item.get('total_shares') or item.get('shares', 0)

        if not code or not shares:
            continue

        # 获取实时价格
        try:
            market_data = provider.get_market_data(code)
            extracted = extract_price(market_data, code, provider, old_price)
            if isinstance(extracted, tuple):
                real_time_price, price_source = extracted
            else:
                real_time_price, price_source = extracted, market_data.get('source', 'unknown')
            if real_time_price and real_time_price > 0:
                if old_price > 0:
                    if price_source == 'akshare_stock_realtime':
                        pass
                    elif real_time_price > 50 * old_price or real_time_price < old_price / 50:
                        fail_count += 1
                        print(f'{code:12s} {item.get("name", ""):12s} 价格异常: 旧={old_price:.2f}, 新={real_time_price:.2f} [{price_source}]')
                        continue
                    if 'historical_fallback' in price_source:
                        if abs(real_time_price - old_price) / old_price > 0.20:
                            fail_count += 1
                            print(f'{code:12s} {item.get("name", ""):12s} 历史回退偏差过大: 旧={old_price:.2f}, 新={real_time_price:.2f} [{price_source}]')
                            continue
                new_price = real_time_price
                item['est_price'] = new_price
                item['last_update'] = datetime.now().isoformat()
                item['price_source'] = price_source

                change_pct = ((new_price - old_price) / old_price * 100) if old_price > 0 else 0.0
                position_value = shares * new_price

                print(f'{code:12s} {item.get("name", ""):12s} '
                      f'{old_price:>8.2f} {new_price:>8.2f} '
                      f'{change_pct:>+7.2f}% {position_value:>12,.0f} [{price_source}]')

                price_changes.append({
                    'code': code,
                    'name': item.get('name', ''),
                    'old_price': old_price,
                    'new_price': new_price,
                    'change_pct': change_pct,
                    'position_value': position_value,
                    'source': price_source
                })
                update_count += 1
            else:
                fail_count += 1
                print(f'{code:12s} {item.get("name", ""):12s} 获取价格失败: 返回数据异常')
        except Exception as e:
            fail_count += 1
            print(f'{code:12s} {item.get("name", ""):12s} 获取价格失败: {e}')

    print('-' * 70)
    print(f'更新成功: {update_count}, 失败: {fail_count}')
    print()

    # 计算总持仓市值
    total_value = sum(
        item.get('phase1_shares', 0) * item.get('est_price', 0.0)
        for item in positions.values()
    )

    # 计算总盈亏
    total_cost = meta.get('day_capital', 0.0)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

    print('=' * 70)
    print('持仓概览')
    print('=' * 70)
    print(f'总持仓市值: {total_value:>12,.0f}')
    print(f'总成本:     {total_cost:>12,.0f}')
    print(f'总盈亏:     {total_pnl:>+12,.0f}')
    print(f'总盈亏率:   {total_pnl_pct:>+11.2f}%')
    print()

    # 按风格统计
    style_stats = {}
    for item in positions.values():
        style = item.get('style', '其他')
        value = item.get('phase1_shares', 0) * item.get('est_price', 0.0)
        style_stats[style] = style_stats.get(style, 0) + value

    print('-' * 70)
    print(f'{"风格":12s} {"市值":>12s} {"占比":>8s}')
    print('-' * 70)
    for style, value in sorted(style_stats.items(), key=lambda x: x[1], reverse=True):
        pct = (value / total_value * 100) if total_value > 0 else 0.0
        print(f'{style:12s} {value:>12,.0f} {pct:>7.1f}%')
    print('-' * 70)
    print(f'{"合计":12s} {total_value:>12,.0f} {"100.0%":>8s}')
    print()

    # 保存更新后的数据
    save_positions(data)
    print('持仓数据已保存到: config/positions.json')
    print()

    # 生成报告
    report = {
        'update_time': datetime.now().isoformat(),
        'update_count': update_count,
        'fail_count': fail_count,
        'total_value': total_value,
        'total_cost': total_cost,
        'total_pnl': total_pnl,
        'total_pnl_pct': total_pnl_pct,
        'price_changes': price_changes,
        'style_stats': {k: v for k, v in style_stats.items()}
    }

    report_dir = r'e:\各种PY程序\28-终极量化交易系统7.1\reports'
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f'price_update_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f'价格更新报告: {report_path}')
    print()
    print('=' * 70)
    print('更新完成')
    print('=' * 70)

    return report

if __name__ == '__main__':
    update_prices()
