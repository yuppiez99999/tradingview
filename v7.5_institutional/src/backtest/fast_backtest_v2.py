# -*- coding: utf-8 -*-
"""
快速回测引擎 v2.0 - 增强版
优化项:
  1. 年化收益按实际交易日复利计算
  2. 动态止损止盈机制
  3. 波动率自适应调仓
  4. 波动率倒数仓位管理
  5. 完善的回测指标体系
"""

import os
import yaml
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# 策略参数配置 v4.0 - 纯调仓版（无止损，靠再平衡控制风险）
STRATEGY_CONFIG = {
    'stop_loss': 0.50,           # 止损线 -50% (基本不触发)
    'take_profit': 0.30,         # 止盈线 +30%
    'base_rebalance_days': 20,   # 基础调仓间隔(天)
    'min_rebalance_days': 15,    # 最小调仓间隔
    'max_rebalance_days': 40,    # 最大调仓间隔
    'rebalance_threshold': 0.05, # 调仓触发阈值(偏离度)
    'commission_rate': 0.0005,   # 手续费率
    'risk_free_rate': 0.03,      # 无风险利率
    'max_single_weight': 0.28,   # 单只最大仓位
    'volatility_lookback': 20,   # 波动率回看期(天)
    'stop_loss_cooldown': 20,    # 止损冷却期(天)
}


def calculate_volatility(prices_series, window=20):
    """计算滚动波动率"""
    if len(prices_series) < window:
        return 0.25
    returns = prices_series.pct_change().dropna()
    if len(returns) < window:
        return returns.std() * np.sqrt(252)
    return returns.iloc[-window:].std() * np.sqrt(252)


def get_adaptive_rebalance_interval(current_volatility, base_volatility=0.25):
    """根据波动率动态调整调仓间隔"""
    vol_ratio = current_volatility / (base_volatility + 0.001)
    interval = int(STRATEGY_CONFIG['base_rebalance_days'] / max(0.5, min(2.0, vol_ratio)))
    return max(STRATEGY_CONFIG['min_rebalance_days'],
               min(STRATEGY_CONFIG['max_rebalance_days'], interval))


def get_volatility_adjusted_weights(target_weights, volatility_dict):
    """
    根据波动率调整目标权重
    低波动标的加仓，高波动标的减仓
    """
    adjusted = {}
    total_inv_vol = sum(w / max(0.05, volatility_dict.get(c, 0.25))
                       for c, w in target_weights.items())
    
    for code, weight in target_weights.items():
        vol = volatility_dict.get(code, 0.25)
        inv_vol = 1.0 / max(0.05, vol)
        adjusted[code] = min((inv_vol / total_inv_vol),
                            STRATEGY_CONFIG['max_single_weight'])
    
    # 归一化
    total_adj = sum(adjusted.values())
    for code in adjusted:
        adjusted[code] /= total_adj
    
    return adjusted


def main():
    print("=" * 70)
    print("  快速回测引擎 v4.0 - 纯调仓优化版")
    print("  策略: 波动率自适应再平衡 + 动态仓位 + 完整数据")
    print("=" * 70)

    with open('config/portfolio_v3.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    assets = config['assets']
    codes = [asset['code'] for asset in assets]
    names = {asset['code']: asset['name'] for asset in assets}
    target_weights = {asset['code']: asset['target_weight'] for asset in assets}

    print(f"\n📊 标的配置: {len(assets)} 只")
    print(f"{'名称':<12} {'代码':<10} {'目标权重':<10}")
    print("-" * 50)
    for asset in assets:
        print(f"{asset['name']:<12} {asset['code']:<10} {asset['target_weight']*100:>6.1f}%")
    print("-" * 50)

    # 加载数据
    print(f"\n📥 加载数据...")
    price_data = {}
    for asset in assets:
        code = asset['code']
        filepath = os.path.join('data/cache', f'kline_{code}_daily.parquet')
        if os.path.exists(filepath):
            df = pd.read_parquet(filepath)
            df.index = pd.to_datetime(df.index).normalize()
            df = df[~df.index.duplicated(keep='last')]
            price_data[code] = df.sort_index()
            print(f"  ✅ {names[code]} ({code}): {len(df)} 条, "
                  f"{df.index[0].date()} ~ {df.index[-1].date()}")

    if not price_data:
        print("❌ 没有可用数据")
        return

    # 构建日期序列
    all_dates = [set(df.index) for df in price_data.values()]
    dates = sorted(list(set.intersection(*all_dates)))
    if len(dates) < 100:
        dates = sorted(list(set.union(*all_dates)))
        print(f"  ⚠️ 共同日期不足，使用所有日期: {dates[0].date()} ~ {dates[-1].date()}")
    dates = dates[-1260:]
    n_days = len(dates)
    years = n_days / 252.0
    print(f"\n📅 回测区间: {dates[0].date()} ~ {dates[-1].date()}, "
          f"共 {n_days} 个交易日 ({years:.1f} 年)")

    # 初始化状态
    initial_capital = 1000000
    cash = initial_capital
    positions = {}          # {code: {'shares': int, 'avg_cost': float, 'entry_date': date}}
    position_stop_dates = {} # 止损冷却记录 {code: 最后止损日期}
    equity_curve = []
    peak_value = initial_capital
    max_drawdown = 0
    trade_count = 0
    win_count = 0
    loss_count = 0
    stop_loss_count = 0
    take_profit_count = 0
    last_rebalance = dates[0]
    trade_log = []

    print(f"\n🚀 开始回测...")
    print(f"   参数: 止损{STRATEGY_CONFIG['stop_loss']*100:.0f}% | "
          f"止盈{STRATEGY_CONFIG['take_profit']*100:.0f}% | "
          f"基础调仓{STRATEGY_CONFIG['base_rebalance_days']}天")

    for i, date in enumerate(dates):
        # 获取当日价格
        prices = {}
        for code in codes:
            if code in price_data:
                series = price_data[code]
                if date in series.index:
                    prices[code] = float(series.loc[date, 'close'])
                else:
                    prev_dates = series.index[series.index <= date]
                    if len(prev_dates) > 0:
                        prices[code] = float(series.loc[prev_dates[-1], 'close'])

        if not prices or len(prices) < len(codes) // 2:
            continue

        # 计算当前总资产
        total = cash
        for code, pos in positions.items():
            if code in prices:
                total += pos['shares'] * prices[code]

        # 更新峰值和最大回撤
        if total > peak_value:
            peak_value = total
        drawdown = (peak_value - total) / peak_value if peak_value > 0 else 0
        if drawdown > max_drawdown:
            max_drawdown = drawdown

        equity_curve.append({'date': date, 'value': total, 'drawdown': drawdown})

        # ====== 止损止盈检查 ======
        for code in list(positions.keys()):
            if code not in prices:
                continue
            pos = positions[code]
            current_price = prices[code]
            pnl_ratio = (current_price - pos['avg_cost']) / pos['avg_cost']

            should_sell = False
            sell_reason = ''

            if pnl_ratio <= -STRATEGY_CONFIG['stop_loss']:
                # 止损冷却期检查
                cooldown = STRATEGY_CONFIG.get('stop_loss_cooldown', 0)
                last_stop = position_stop_dates.get(code)
                if cooldown > 0 and last_stop is not None:
                    days_since_stop = (date - last_stop).days
                    if days_since_stop < cooldown:
                        continue  # 冷却期内跳过止损
                should_sell = True
                sell_reason = 'STOP_LOSS'
                stop_loss_count += 1
                position_stop_dates[code] = date
            elif pnl_ratio >= STRATEGY_CONFIG['take_profit']:
                should_sell = True
                sell_reason = 'TAKE_PROFIT'
                take_profit_count += 1

            if should_sell and pos['shares'] > 0:
                revenue = pos['shares'] * current_price
                commission = revenue * STRATEGY_CONFIG['commission_rate']
                cash += revenue - commission
                realized_pnl = (current_price - pos['avg_cost']) * pos['shares']
                if realized_pnl > 0:
                    win_count += 1
                else:
                    loss_count += 1
                trade_log.append({
                    'date': date, 'code': code, 'action': sell_reason,
                    'price': current_price, 'shares': pos['shares'],
                    'pnl': realized_pnl, 'pnl_pct': pnl_ratio
                })
                trade_count += 1
                del positions[code]

        # ====== 再平衡检查 ======
        # 计算组合波动率以决定调仓间隔
        portfolio_prices = {}
        for code in codes:
            if code in price_data:
                series = price_data[code]
                idx = series.index.get_indexer([date], method='ffill')
                if idx[0] >= 0 and idx[0] < len(series):
                    end_idx = min(idx[0] + 1, len(series))
                    start_idx = max(0, idx[0] - STRATEGY_CONFIG['volatility_lookback'])
                    recent = series.iloc[start_idx:end_idx]
                    if len(recent) > 1:
                        portfolio_prices[code] = recent['close'].values

        avg_volatility = 0.25
        if portfolio_prices:
            vols = []
            for code, price_arr in portfolio_prices.items():
                if len(price_arr) > 1:
                    rets = np.diff(price_arr) / price_arr[:-1]
                    vols.append(np.std(rets) * np.sqrt(252))
            if vols:
                avg_volatility = np.mean(vols)

        rebalance_interval = get_adaptive_rebalance_interval(avg_volatility)

        if i == 0 or (date - last_rebalance).days >= rebalance_interval:
            # 波动率调整后的目标权重
            vol_dict = {}
            for code in codes:
                if code in price_data:
                    series = price_data[code]
                    idx = series.index.get_indexer([date], method='ffill')
                    if idx[0] >= 0 and idx[0] < len(series):
                        end_idx = min(idx[0] + 1, len(series))
                        start_idx = max(0, idx[0] - STRATEGY_CONFIG['volatility_lookback'])
                        recent = series.iloc[start_idx:end_idx]
                        if len(recent) > 1:
                            rets = np.diff(recent['close'].values) / recent['close'].values[:-1]
                            vol_dict[code] = np.std(rets) * np.sqrt(252)

            adj_weights = get_volatility_adjusted_weights(target_weights, vol_dict)

            for code in codes:
                if code not in prices:
                    continue

                tw = adj_weights.get(code, target_weights.get(code))
                target_amount = total * tw
                current_shares = positions.get(code, {}).get('shares', 0)
                current_amount = current_shares * prices[code]
                diff_amount = target_amount - current_amount

                # 偏离度不足则跳过
                if abs(diff_amount) / total < STRATEGY_CONFIG['rebalance_threshold']:
                    continue

                price = prices[code]
                shares = int(abs(diff_amount) / price / 100) * 100

                if shares == 0:
                    continue

                if diff_amount > 0:
                    cost = shares * price * (1 + STRATEGY_CONFIG['commission_rate'])
                    if cost <= cash:
                        cash -= cost
                        if code not in positions:
                            positions[code] = {'shares': 0, 'avg_cost': 0, 'entry_date': date}
                        old_cost_val = positions[code]['shares'] * positions[code]['avg_cost']
                        positions[code]['shares'] += shares
                        positions[code]['avg_cost'] = (old_cost_val + shares * price) / positions[code]['shares']
                        positions[code]['entry_date'] = date
                        trade_count += 1
                else:
                    if code in positions and positions[code]['shares'] >= shares:
                        positions[code]['shares'] -= shares
                        revenue = shares * price * (1 - STRATEGY_CONFIG['commission_rate'])
                        cash += revenue
                        if positions[code]['shares'] == 0:
                            del positions[code]
                        trade_count += 1

            last_rebalance = date

        # 进度显示
        if (i + 1) % 200 == 0:
            pct = total / initial_capital
            print(f"  ⏳ {i+1}/{n_days} ({pct:.2%}) 回撤:{drawdown:.2%}")

    print(f"\n✅ 回测完成!")

    # ====== 指标计算 ======
    values = [e['value'] for e in equity_curve]
    dates_list = [e['date'] for e in equity_curve]

    initial = values[0]
    final = values[-1]
    total_return = (final - initial) / initial

    # 正确的年化收益计算: 复利
    annual_return = (1 + total_return) ** (1.0 / years) - 1 if years > 0 else total_return

    daily_rets = np.array(values[1:]) / np.array(values[:-1]) - 1
    annual_vol = np.std(daily_rets) * np.sqrt(252) if len(daily_rets) > 1 else 0
    sharpe = (annual_return - STRATEGY_CONFIG['risk_free_rate']) / annual_vol if annual_vol > 0 else 0

    # 胜率和盈利因子
    total_trades = win_count + loss_count
    win_rate = win_count / total_trades if total_trades > 0 else 0
    avg_win = np.mean([t['pnl'] for t in trade_log if t['pnl'] > 0]) if any(t['pnl'] > 0 for t in trade_log) else 0
    avg_loss = abs(np.mean([t['pnl'] for t in trade_log if t['pnl'] < 0])) if any(t['pnl'] < 0 for t in trade_log) else 1
    profit_factor = avg_win / avg_loss if avg_loss > 0 else float('inf')

    # Calmar比率
    calmar = annual_return / max_drawdown if max_drawdown > 0 else 0

    # 输出报告
    print(f"\n{'='*70}")
    print("  📊 回测结果 (v2.0 增强版)")
    print(f"{'='*70}")

    print(f"\n💰 收益指标:")
    print(f"  初始资金:     ¥{initial:>12,.0f}")
    print(f"  最终资金:     ¥{final:>12,.0f}")
    print(f"  总收益率:     {total_return*100:>+10.2f}%")
    print(f"  年化收益:     {annual_return*100:>+10.2f}%")
    print(f"  最高净值:     ¥{max(values):>12,.0f}")
    print(f"  最低净值:     ¥{min(values):>12,.0f}")

    print(f"\n📉 风险指标:")
    print(f"  年化波动:     {annual_vol*100:>10.2f}%")
    print(f"  最大回撤:     {max_drawdown*100:>10.2f}%")
    print(f"  夏普比率:     {sharpe:>10.2f}")
    print(f"  Calmar比率:   {calmar:>10.2f}")

    print(f"\n🎯 交易统计:")
    print(f"  总交易次数:   {trade_count:>10d}")
    print(f"  止损次数:     {stop_loss_count:>10d}")
    print(f"  止盈次数:     {take_profit_count:>10d}")
    print(f"  胜率:         {win_rate*100:>9.1f}%")
    print(f"  盈利因子:     {profit_factor:>10.2f}")

    print(f"\n{'='*70}")
    print(f"  🎯 目标达成:")
    print(f"  年化≥8%:  {'✅ 达标' if annual_return >= 0.08 else '❌ 未达标'} "
          f"({annual_return*100:.2f}%)")
    print(f"  回撤≤10%: {'✅ 达标' if max_drawdown <= 0.10 else '❌ 未达标'} "
          f"({max_drawdown*100:.2f}%)")

    # 最终持仓（前向填充价格）
    print(f"\n📋 最终持仓:")
    print(f"{'名称':<12} {'代码':<10} {'持仓':<8} {'成本价':<10} {'现价':<10} {'市值':<14} {'盈亏%':<8}")
    print("-" * 80)

    total_market_value = 0
    final_prices = {}
    for code in codes:
        if code in price_data:
            series = price_data[code]
            # 使用最后一个可用日期的价格
            valid_dates = series.index[series.index <= dates[-1]]
            if len(valid_dates) > 0:
                final_prices[code] = float(series.loc[valid_dates[-1], 'close'])

    for code in sorted(positions.keys()):
        pos = positions[code]
        shares = pos['shares']
        cost = pos['avg_cost']
        price = final_prices.get(code, cost)
        market_value = shares * price
        pnl_pct = (price - cost) / cost * 100 if cost > 0 else 0
        total_market_value += market_value
        print(f"{names.get(code,''):<12} {code:<10} {shares:<8} "
              f"{cost:<10.2f} {price:<10.2f} ¥{market_value:<12,.2f} {pnl_pct:>+7.1f}%")

    print("-" * 80)
    total_value = cash + total_market_value
    print(f"{'持仓市值':<22} ¥{total_market_value:>14,.2f}")
    print(f"{'可用现金':<22} ¥{cash:>14,.2f}")
    print(f"{'账户总值':<22} ¥{total_value:>14,.2f}")
    print("=" * 70)

    # 保存详细结果
    output_dir = 'data/cache'
    os.makedirs(output_dir, exist_ok=True)
    result_df = pd.DataFrame(equity_curve)
    result_df.to_csv(os.path.join(output_dir, 'backtest_v2_results.csv'), index=False, encoding='utf-8')
    if trade_log:
        pd.DataFrame(trade_log).to_csv(
            os.path.join(output_dir, 'backtest_v2_trades.csv'), index=False, encoding='utf-8')


def run_fast_backtest():
    """主系统入口点"""
    main()


if __name__ == "__main__":
    main()
