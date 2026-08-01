import json

print('=' * 70)
print('  5年年化收益率与回撤预测 — 顶级对冲基金视角')
print('=' * 70)

# 1. 组合配置读取
positions_path = r'E:\各种PY程序\28-终极量化交易系统7.1\config\positions.json'
trade_plan_path = r'E:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\trade_plans\trade_plan_20260707.json'

with open(positions_path, encoding='utf-8') as f:
    positions = json.load(f)['positions']

with open(trade_plan_path, encoding='utf-8') as f:
    plan = json.load(f)

capital = plan['capital']
risk = plan['risk_controls']
hedge = plan['hedge_config']

print('\n【基础参数】')
print(f'初始资本: {capital:,.0f} RMB')
print(f'股票/ETF部分: {plan["stock_etf_capital"]:,.0f} RMB')
print(f'对冲资本: {plan["hedge_capital"]:,.0f} RMB')
print(f'策略: {plan["strategy"]}')

# 2. 组合构成分析
print('\n【组合构成】')
etf_amount = 0
stock_amount = 0
commodity_amount = 0
style_map = {}
for _code, pos in positions.items():
    amt = pos['phase1_amount']
    style = pos.get('style', '其他')
    ptype = pos.get('type', '个股')
    style_map[style] = style_map.get(style, 0) + amt
    if ptype == 'ETF':
        etf_amount += amt
    elif ptype == '商品':
        commodity_amount += amt
    else:
        stock_amount += amt

total_position = etf_amount + stock_amount + commodity_amount
print(f'ETF: {etf_amount:,.0f} RMB ({etf_amount/total_position*100:.1f}%)')
print(f'个股: {stock_amount:,.0f} RMB ({stock_amount/total_position*100:.1f}%)')
print(f'商品: {commodity_amount:,.0f} RMB ({commodity_amount/total_position*100:.1f}%)')
print(f'总持仓: {total_position:,.0f} RMB')
print('风格分布:')
for style, amt in sorted(style_map.items(), key=lambda x: -x[1]):
    print(f'  {style}: {amt:,.0f} RMB ({amt/total_position*100:.1f}%)')

# 3. 5年收益测算
print('\n【5年收益预测】')

# 假设参数
expected_return = 0.10  # 股票部分预期年化收益
rf_rate = 0.025         # 无风险利率
equity_ratio = 0.80     # 股票部分占比
hedge_cost = 0.015      # 对冲成本
rebalance_cost = 0.003  # 再平衡成本

# 夏普比率假设
sharpe_ratio = 0.65     # 基于多策略融合的系统化策略

# 基础收益
base_return = expected_return * equity_ratio

# 对冲调整：负成本或保护价值
# 在正常市场环境下，对冲成本约1-2%
net_return = base_return - hedge_cost - rebalance_cost

# 考虑外部报告因子提升
external_boost = 0.01  # 外部报告带来的alpha
net_return += external_boost

# 计算5年复合收益
years = 5
compound_return = (1 + net_return) ** years - 1
cagr = net_return

# 计算最大回撤
# 基于组合波动率
volatility = 0.18  # 组合年化波动率
max_dd = 2.5 * volatility * (1 - 0.3)  # 考虑对冲后
max_dd = min(max_dd, 0.20)  # 硬上限20%

# 卡玛比率
calmar = cagr / max_dd if max_dd > 0 else 0

# 索提诺比率
# 假设下行波动率约为总波动率的1.2倍
downside_vol = volatility * 1.2
sortino = (cagr - rf_rate) / downside_vol if downside_vol > 0 else 0

# 资金曲线模拟
print(f'股票部分预期年化: {expected_return*100:.1f}%')
print(f'对冲成本: -{hedge_cost*100:.1f}%')
print(f'再平衡成本: -{rebalance_cost*100:.1f}%')
print(f'外部报告Alpha: +{external_boost*100:.1f}%')
print(f'净年化收益率(CAGR): {cagr*100:.2f}%')
print(f'5年复合增长: {compound_return*100:.1f}%')
print(f'组合年化波动率: {volatility*100:.1f}%')
print(f'预测最大回撤: -{max_dd*100:.1f}%')
print(f'卡玛比率: {calmar:.2f}')
print(f'索提诺比率: {sortino:.2f}')

# 模拟资金曲线
print('\n【5年资金曲线模拟】')
balance = capital
print(f'初始资金: {balance:,.0f}')
for year in range(1, years + 1):
    # 随机年化收益
    import random
    random.seed(year * 100)
    annual_r = cagr + random.uniform(-0.05, 0.05)
    annual_r = max(annual_r, -max_dd)
    balance = balance * (1 + annual_r)
    print(f'第{year}年: 年化 {annual_r*100:+.1f}% | 期末余额 {balance:,.0f}')

print(f'5年后总资产: {balance:,.0f} RMB')
print(f'总收益率: {(balance/capital-1)*100:.1f}%')

# 4. 黑天鹅风险
print('\n【黑天鹅与不可抗力风险】')
print('1. 地缘政治冲突升级 → 流动性危机，A股波动率跃升30%+')
print('2. 全球央行政策转向 → 利率超预期上行，成长股估值重估')
print('3. 科技制裁加码 → 半导体/AI产业链供应链断裂')
print('4. 房地产市场尾部风险 → 信用链条传导至银行/保险')
print('5. 黑天鹅尾部事件 → 单日跌幅>8%，触发多层熔断')
print('6. 汇率危机 → 人民币急贬，资本外流压力')
print('7. 极端气候灾害 → 能源/粮食价格冲击，通胀失控')

print('\n【风险缓解措施】')
print('- 多层对冲: 期货Beta对冲 + 期权保护 + 黄金避险')
print('- 动态止损: 个股-8%~-12%，组合-15%熔断')
print('- 仓位管控: 单标的<10%，行业<30%')
print('- 外部报告监控: 每日舆情+ETF资金流向+康波周期')
print('- 降级机制: 三因子信号融合，单因子失效自动降权')

print('=' * 70)
print('  分析完成')
print('=' * 70)
