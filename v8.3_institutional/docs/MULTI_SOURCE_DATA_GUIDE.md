"""
混合数据源系统使用指南 (Hybrid Data Source System User Guide)
==========================================================

## 系统概述

混合数据源系统整合了多个A股数据源的优势，提供多层次、互补的数据获取架构：

### 数据源组成

1. **通达信 (TDX)** - 实时行情和K线数据
   - 优势：毫秒级行情、完整的历史K线、丰富的技术指标
   - 用途：日内交易信号、实时风控、技术因子计算
   
2. **东方财富 (EastMoney)** - 资金流向和龙虎榜
   - 优势：主力资金流向、游资动向、概念板块热度
   - 用途：资金流因子、情绪因子、热点追踪
   
3. **AKShare** - 免费开源数据源
   - 优势：覆盖全面、无需API Key、持续更新
   - 用途：财务数据、宏观数据、补充行情
   
4. **Wind/iFinD** - 机构级数据（可选）
   - 优势：数据质量高、覆盖完整、经过验证
   - 用途：基准数据、合规报告、估值参考

---

## 快速开始

### 1. 安装依赖

```bash
# 安装AKShare（免费数据源）
pip install akshare

# 安装东方财富数据抓取工具
pip install requests beautifulsoup4

# Wind客户端需要单独安装Wind Python API
# iFinD客户端需要单独安装同花顺Python API
```

### 2. 初始化混合数据源管理器

```python
from src.data.hybrid_data_manager import get_hybrid_data_manager

# 创建管理器（自动检测可用的数据源）
manager = get_hybrid_data_manager(config={
    'tdx_enabled': True,           # 启用通达信
    'eastmoney_enabled': True,     # 启用东方财富
    'akshare_enabled': True,       # 启用AKShare
    'wind_enabled': False,         # 禁用Wind（需要许可证）
    'cross_validation': True,      # 启用交叉验证
    'quality_threshold': 70.0      # 质量阈值
})

# 查看数据源状态
status = manager.get_data_source_status()
for name, info in status.items():
    print(f"{name}: 健康度={info['health_score']}, 优先级={info['priority']}")
```

### 3. 获取实时行情

```python
# 获取贵州茅台实时价格
symbol = '600519.SH'
price_data = manager.get_realtime_price(symbol)

print(f"股票代码: {price_data['symbol']}")
print(f"最新价: {price_data['price']}")
print(f"涨跌幅: {price_data['change']}%")
print(f"成交量: {price_data['volume']}")
```

### 4. 获取K线数据

```python
# 获取日线数据
kline_df = manager.get_kline_data(
    symbol='600519.SH',
    period='daily',          # daily/weekly/monthly/1min/5min
    start_date='2026-07-01',
    end_date='2026-07-23',
    adjust='hfq'             # qfq(前复权)/hfq(后复权)/''(不复权)
)

print(f"数据形状: {kline_df.shape}")
print(kline_df.head())
```

### 5. 获取资金流向

```python
# 获取ETF资金流向
flow_data = manager.get_flow_data('510300.SH')

print(f"主力净流入: {flow_data.get('main_net_flow', 0):,.0f}元")
print(f"主力净流入占比: {flow_data.get('main_net_flow_ratio', 0):.2%}")
print(f"散户净流入: {flow_data.get('retail_net_flow', 0):,.0f}元")
```

---

## 数据质量验证

### 1. 实时数据验证

```python
from src.data.data_quality_validator import DataQualityValidator

validator = DataQualityValidator()

# 验证实时数据
realtime_data = {
    'symbol': '600519.SH',
    'price': 1800.50,
    'volume': 5000,
    'change': 1.25
}

report = validator.validate_realtime_data('600519.SH', realtime_data, 'test_source')

print(f"质量评分: {report.quality_score}")
print(f"覆盖率: {report.coverage_rate:.2%}")
print(f"是否合格: {'✓ 是' if report.is_acceptable else '✗ 否'}")
print(f"问题列表: {report.issues}")
```

### 2. K线数据验证

```python
# 验证K线数据质量
kline_report = validator.validate_kline_data('600519.SH', kline_df, 'test_source')

print(f"异常值数量: {kline_report.outlier_count}")
print(f"缺失值数量: {kline_report.missing_count}")
print(f"质量评分: {kline_report.quality_score}")
```

### 3. 多源交叉验证

```python
# 从多个数据源获取数据并交叉验证
is_consistent = manager.cross_validate_data('600519.SH', 'realtime_price')

if is_consistent:
    print("✓ 多源数据一致")
else:
    print("✗ 多源数据存在差异，请检查")
```

### 4. 异常值检测与清洗

```python
# 检测异常值（MAD法）
series = pd.Series([100, 102, 98, 101, 105, 200, 99, 103])
is_normal, outlier_count = validator.detect_data_anomalies('test', series, method='mad')

print(f"异常值数量: {outlier_count}")

# 清洗异常值（用中位数替换）
df_cleaned = validator.clean_outliers(df_with_outliers, ['price', 'volume'], method='mad')
```

---

## ETF资金流向监控

### 1. 创建监控器

```python
from scripts.multi_source_etf_flow_monitor import MultiSourceETFFlowMonitor

monitor = MultiSourceETFFlowMonitor(config={
    'alert_threshold': 10000000,  # 1000万告警阈值
    'quality_threshold': 70.0,
    'auto_validate': True,
    'cross_validation': True
})
```

### 2. 获取ETF资金流向

```python
# 监测多只ETF
etf_list = [
    '510300.SH',  # 沪深300ETF
    '510500.SH',  # 中证500ETF
    '510050.SH',  # 上证50ETF
    '159919.SZ',  # 创业板ETF
]

flow_data = monitor.get_etf_flow_data(etf_list)

for data in flow_data:
    print(f"{data.symbol} {data.name}: "
          f"主力净流入={data.main_inflow:,.0f}元 "
          f"({data.main_ratio:.2%})")
```

### 3. 获取资金流入/流出排行

```python
# 资金流入Top 5
top_inflow = monitor.get_top_inflow_etfs(etf_list, top_n=5)
print("\n=== 资金流入Top 5 ===")
print(top_inflow[['symbol', 'name', 'main_inflow', 'main_ratio']])

# 资金流出Top 5
top_outflow = monitor.get_top_outflow_etfs(etf_list, top_n=5)
print("\n=== 资金流出Top 5 ===")
print(top_outflow[['symbol', 'name', 'main_inflow', 'main_ratio']])
```

### 4. 告警监控

```python
# 获取告警记录
alerts = monitor.get_alerts(clear=False)

for alert in alerts:
    print(f"[{alert['timestamp']}] {alert.get('message', alert.get('type'))}")
```

---

## 数据源降级机制

系统实现了智能降级机制，确保在某个数据源不可用时自动切换到备用数据源：

### 降级优先级

**实时行情：**
1. 通达信（毫秒级，最优）
2. Wind（机构级，高精度）
3. 东方财富（补充）
4. AKShare（兜底）

**K线数据：**
1. 通达信（历史数据最完整）
2. Wind
3. 东方财富
4. AKShare

**资金流向：**
1. 东方财富（最详细）
2. 通达信
3. AKShare

### 自动切换示例

```python
# 当通达信不可用时，自动切换到Wind
try:
    price = manager.get_realtime_price('600519.SH')
    print(f"成功获取价格: {price['price']}")
except RuntimeError as e:
    print(f"所有数据源均失败: {e}")
```

---

## 性能优化建议

### 1. 数据缓存

```python
# 系统自动缓存数据，可通过以下方法手动控制
from src.data.hybrid_data_manager import get_hybrid_data_manager

manager = get_hybrid_data_manager()

# 清除特定缓存
manager.invalidate_cache('600519.SH_realtime_price')

# 清除所有缓存
manager.clear_all_cache()
```

### 2. 批量获取

```python
# 批量获取多只股票行情
symbols = ['600519.SH', '000001.SZ', '300750.SZ']

for symbol in symbols:
    try:
        price = manager.get_realtime_price(symbol)
        print(f"{symbol}: {price['price']}")
    except:
        print(f"{symbol}: 获取失败")
```

### 3. 质量阈值调整

```python
# 根据需求调整质量阈值
config = {
    'quality_threshold': 60.0,  # 降低到60分（更宽松）
    # 'quality_threshold': 85.0,  # 提高到85分（更严格）
}

manager = get_hybrid_data_manager(config)
```

---

## 常见问题 (FAQ)

### Q1: 如何判断哪个数据源正在使用？

```python
# 查看最佳数据源
best_source = manager.get_best_data_source('realtime_price')
print(f"当前使用的数据源: {best_source}")
```

### Q2: 如何处理数据源连接失败？

系统会自动降级到下一个可用数据源。如需手动检查：

```python
status = manager.get_data_source_status()
for name, info in status.items():
    if not info['is_available']:
        print(f"数据源 {name} 不可用")
```

### Q3: 如何添加自定义数据源？

在 `HybridDataSourceManager` 中注册新的数据源：

```python
manager.register_data_source(
    name='custom_source',
    priority=DataSourcePriority.MEDIUM,
    check_func=lambda: True  # 返回True表示可用
)
```

### Q4: 数据质量评分如何计算？

综合评分公式：
- 覆盖率 (40%)：数据完整性
- 延迟 (30%)：数据新鲜度
- 新鲜度 (20%)：更新时间间隔
- 错误率 (10%)：数据准确性

### Q5: 如何获取历史资金流向？

```python
from datetime import datetime, timedelta

yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
flow_data = manager.get_flow_data('600519.SH', date=yesterday)
```

---

## 技术支持

如遇问题，请查看：
1. 日志文件：`hybrid_data_source_test.log`
2. 测试报告：`hybrid_data_source_test_report_*.log`
3. 数据源状态：`manager.get_data_source_status()`

---

**作者**: Agnes-2.0 Flash Team  
**版本**: v1.0  
**日期**: 2026-07-23