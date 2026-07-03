# 量化策略系统 v5.10 用户指南

## 快速开始

### 1. 系统要求

- Python 3.8 或更高版本
- 必要的Python库：
  - pandas, numpy, matplotlib
  - yfinance (数据获取)
  - scikit-learn (机器学习)
  - scipy (科学计算)
  - requests (网络请求)

### 2. 安装依赖

```bash
pip install pandas numpy matplotlib yfinance scikit-learn scipy requests
```

### 3. 快速检查

运行快速系统检查：

```bash
python quick_check.py
```

检查系统是否就绪。

## 系统启动

### 1. 启动系统

```bash
# 自动执行模式（推荐）
python start_system.py

# 手动执行模式
python start_system.py manual

# 测试模式（不实际交易）
python start_system.py test

# 检查模式（只检查系统）
python start_system.py check
```

### 2. 监控系统

```bash
# 命令行监控
python monitor_dashboard.py

# Web界面监控（需要flask）
pip install flask
python monitor_dashboard.py --web
```

访问 http://localhost:5000 查看Web界面。

## 配置管理

### 1. 查看当前配置

```python
from config import Config

# 获取配置实例
config = Config.get_instance()

# 查看配置摘要
summary = config.get_config_summary()
print(f"总资金: {summary['total_capital']:,} 元")
print(f"股票ETF资金: {summary['stock_etf_capital']:,} 元")
print(f"对冲资金: {summary['hedge_capital']:,} 元")
```

### 2. 修改配置

```python
# 更新总资金
config.update_config({
    'total_capital': 6000000,
    'stock_etf_capital': 4800000,
    'hedge_capital': 1200000
})

# 修改执行时间
config.update_config({
    'execution_config': {
        'execution_time': '08:00'
    }
})

# 修改对冲比例
config.update_config({
    'hedge_config': {
        'delta_capital_ratio': 0.7,
        'vol_capital_ratio': 0.2,
        'tail_capital_ratio': 0.1
    }
})
```

### 3. 重置配置

```python
# 重置为默认配置
config.reset_config()
```

## 策略管理

### 1. Delta对冲策略

```python
from enhanced_delta_hedge import EnhancedDeltaHedge

# 创建策略
delta_hedge = EnhancedDeltaHedge(600000)

# 获取状态
status = delta_hedge.get_status()
print(f"状态: {status}")

# 获取对冲比率
hedge_ratio = delta_hedge.get_hedge_ratio()
print(f"对冲比率: {hedge_ratio}")

# 执行对冲
result = delta_hedge.execute_hedge()
print(f"对冲结果: {result}")
```

### 2. 波动率对冲策略

```python
from volatility_hedge import VolatilityHedge

# 创建策略
vol_hedge = VolatilityHedge(300000)

# 监控波动率
volatility = vol_hedge.get_volatility()
print(f"当前波动率: {volatility}")

# 执行对冲
if volatility > 0.2:  # 高波动率阈值
    result = vol_hedge.execute_hedge()
    print(f"对冲结果: {result}")
```

### 3. 尾部风险对冲策略

```python
from tail_risk_hedge import TailRiskHedge

# 创建策略
tail_hedge = TailRiskHedge(100000)

# 评估尾部风险
tail_risk = tail_hedge.evaluate_tail_risk()
print(f"尾部风险评分: {tail_risk}")

# 执行对冲
if tail_risk > 0.05:  # 尾部风险阈值
    result = tail_hedge.execute_hedge()
    print(f"对冲结果: {result}")
```

### 4. 智能触发器

```python
from smart_hedge_trigger import SmartHedgeTrigger

# 创建触发器
trigger = SmartHedgeTrigger()

# 运行模拟
simulation = trigger.run_simulation()
print(f"模拟结果: {simulation}")

# 获取建议
advice = trigger.get_advice()
print(f"触发建议: {advice}")
```

## 风险管理

### 1. 实时风险监控

```python
from enhanced_risk_manager import EnhancedRiskManager

# 创建风险管理器
risk_manager = EnhancedRiskManager(5000000)

# 监控风险
risk_status = risk_manager.monitor_risk()
print(f"风险状态: {risk_status}")

# 获取预警
alerts = risk_manager.get_alerts()
if alerts:
    print("⚠️ 风险预警:")
    for alert in alerts:
        print(f"  - {alert}")
```

### 2. 风险指标计算

```python
from utils.risk_metrics import calculate_var, calculate_es, calculate_max_drawdown, calculate_sharpe_ratio

# 示例数据
returns = [0.01, -0.02, 0.03, -0.01, 0.02, 0.01, -0.03, 0.02]
prices = [100, 101, 99, 102, 103, 104, 101, 102]

# 计算风险指标
var_95 = calculate_var(returns, 0.95)
es_95 = calculate_es(returns, 0.95)
max_dd, dd_date, dd_value = calculate_max_drawdown(prices)
sharpe = calculate_sharpe_ratio(returns)

print(f"VaR(95%): {var_95:.4f}")
print(f"ES(95%): {es_95:.4f}")
print(f"最大回撤: {max_dd:.4f}")
print(f"夏普比率: {sharpe:.4f}")
```

### 3. 压力测试

```python
# 运行压力测试
stress_results = risk_manager.run_stress_test()
print("压力测试结果:")
print(f"市场崩盘场景: {stress_results['market_crash']['impact']:.2%}")
print(f"流动性危机场景: {stress_results['liquidity_crisis']['impact']:.2%}")
print(f"利率冲击场景: {stress_results['rate_shock']['impact']:.2%}")
```

## 性能评估

### 1. 计算性能指标

```python
from utils.risk_metrics import calculate_performance_metrics

# 计算完整性能指标
performance = calculate_performance_metrics(returns, prices)
print("性能指标:")
print(f"年化收益率: {performance['annual_return']:.2%}")
print(f"最大回撤: {performance['max_drawdown']:.2%}")
print(f"夏普比率: {performance['sharpe_ratio']:.2f}")
print(f"波动率: {performance['volatility']:.2%}")
print(f"索提诺比率: {performance['sortino_ratio']:.2f}")
```

### 2. 策略优化

```python
from strategy_optimizer import StrategyOptimizer

# 创建优化器
optimizer = StrategyOptimizer(5000000)

# 运行优化
optimization = optimizer.optimize_parameters()
print("优化完成:")
print(f"最佳参数: {optimization['best_parameters']}")
print(f"优化后收益: {optimization['best_return']:.2%}")
```

## 数据管理

### 1. 获取市场数据

```python
from utils.data_provider import get_market_data, get_historical_data

# 获取当前市场数据
market_data = get_market_data()
print(f"当前市场数据: {market_data}")

# 获取历史数据
historical_data = get_historical_data('SPY', '1m')  # 1个月数据
print(f"历史数据长度: {len(historical_data)}")
```

### 2. 获取情绪数据

```python
from utils.data_provider import get_sentiment_data

# 获取市场情绪
sentiment = get_sentiment_data()
print(f"市场情绪: {sentiment}")
```

## 故障排除

### 1. 常见问题

**问题**：模块导入失败
```
ModuleNotFoundError: No module named 'yfinance'
```
**解决**：安装缺失的库
```bash
pip install yfinance
```

**问题**：数据获取失败
```
requests.exceptions.ConnectionError
```
**解决**：检查网络连接

**问题**：执行失败
```
PermissionError: [Errno 13] Permission denied
```
**解决**：检查文件权限

### 2. 日志查看

```python
from utils.logger import get_logger

# 获取日志记录器
logger = get_logger('system')

# 查看日志
with open('quant_strategy_system.log', 'r', encoding='utf-8') as f:
    print(f.read())
```

### 3. 系统重置

```python
# 重置配置
from config import Config
config = Config.get_instance()
config.reset_config()

# 重新启动系统
python start_system.py
```

## 最佳实践

### 1. 配置管理

- 定期备份配置文件
- 修改配置前先备份
- 测试配置修改

### 2. 风险管理

- 严格设置风险限额
- 定期检查风险指标
- 及时响应风险预警

### 3. 性能监控

- 定期查看性能指标
- 比较基准表现
- 优化策略参数

### 4. 系统维护

- 定期更新依赖库
- 检查系统日志
- 备份重要数据

## 联系支持

如需帮助，请检查：
1. 日志文件 `quant_strategy_system.log`
2. 本文档和项目文档
3. 运行系统检查 `quick_check.py`

## 免责声明

本系统仅供学习研究使用，不构成投资建议。投资有风险，请谨慎操作。