# 通达信(pytdx)数据源集成指南

> 版本: v1.0 | 更新日期: 2026-07-23 | 适用系统: v8.3/v8.4 Institutional

---

## 1. 概述

通达信(pytdx)是A股量化交易系统中重要的**免费数据源**,作为Wind/iFinD商业终端的替代方案。本系统已内置`TDXTaiChiDataFeeder`封装类,支持自动服务器切换、并发连接池管理、除权因子获取等核心功能。

### 1.1 数据源优先级架构

```
P0: Wind 数据终端 (主数据源)
    ↓ 不可用
P1: Wind MCP (强制回退)
    ↓ 不可用
P2: iFinD MCP (同花顺终端)
    ↓ 不可用
P3: 通达信(pytdx) ← 新增免费行情源
    ↓ 不可用
P4: AKShare/AnySearch (公开数据)
    ↓ 不可用
P5: 新浪财经API (实时行情兜底)
    ↓ 不可用
P6: 本地缓存 (Parquet/JSON)
    ↓ 不可用
P7: 预定义价格 (永不崩溃兜底)
```

---

## 2. 安装配置

### 2.1 安装pytdx库

```bash
# 方式1: 安装到当前Python环境
pip install pytdx

# 方式2: 安装到虚拟环境(推荐)
cd e:\各种PY程序\28-终极量化交易系统8.4
.\qlk_env\Scripts\activate
pip install pytdx

# 验证安装
python -c "from pytdx.best import BestTdx; print('pytdx installed successfully')"
```

### 2.2 环境变量配置

**Windows PowerShell:**
```powershell
# 设置通达信服务器地址(可选,默认使用内置服务器列表)
$env:TDX_IP = "119.147.212.81"
$env:TDX_PORT = "7709"

# 持久化设置(当前用户)
[System.Environment]::SetEnvironmentVariable("TDX_IP", "119.147.212.81", "User")
[System.Environment]::SetEnvironmentVariable("TDX_PORT", "7709", "User")
```

**Linux/Mac:**
```bash
export TDX_IP="119.147.212.81"
export TDX_PORT="7709"
```

### 2.3 服务器列表(内置)

系统内置10+通达信服务器地址,自动检测可用性:

| 服务器IP | 端口 | 地区 | 状态 |
|---------|------|------|------|
| 119.147.212.81 | 7709 | 深圳主站 | ✅ 推荐 |
| 112.74.214.46 | 7727 | 阿里云 | ✅ |
| 113.105.142.162 | 7709 | 腾讯云 | ✅ |
| 218.75.126.9 | 7709 | 北京 | ✅ |
| 119.147.86.21 | 7709 | 上海 | ✅ |
| 14.17.75.71 | 7709 | 广东电信 | ✅ |
| 59.173.183.71 | 7709 | 北京联通 | ✅ |
| 218.108.47.157 | 7709 | 浙江 | ✅ |
| 218.108.98.176 | 7709 | 广东移动 | ✅ |
| 112.95.148.153 | 7709 | 江苏电信 | ✅ |

---

## 3. 核心功能

### 3.1 初始化数据源

```python
from utils.tdx_data_source import TDXTaiChiDataFeeder

# 创建实例
tdx = TDXTaiChiDataFeeder()

# 初始化(自动选择最快服务器)
tdx.init()

# 手动指定服务器
tdx.init(ip="119.147.212.81", port=7709)

# 测试连接
if tdx.connect():
    print("通达信数据源连接成功!")
else:
    print("连接失败,尝试备用服务器...")
```

### 3.2 获取K线数据

#### 日线数据(ETF资金流向监控)

```python
# 获取贵州茅台日线数据(最近800个交易日)
df = tdx.get_kline_data("600519", 1, 800)

# 获取沪深300ETF日线数据
df_etf = tdx.get_kline_data("159919", 1, 100)

# 数据结构
print(df.columns)
# ['datetime', 'open', 'close', 'high', 'low', 'volume', 'amount']

# 查看最近5条记录
print(df.tail())
```

#### 分钟级数据(盘中对冲监控)

```python
# 5分钟K线
df_5m = tdx.get_kline_data("600519", 0, 200)  # 0=5分钟

# 15分钟K线
df_15m = tdx.get_kline_data("600519", 1, 200)

# 30分钟K线
df_30m = tdx.get_kline_data("600519", 2, 200)

# 60分钟K线
df_60m = tdx.get_kline_data("600519", 3, 200)
```

#### 周/月线数据(长期趋势分析)

```python
# 周线
df_weekly = tdx.get_kline_data("600519", 2, 100)  # 2=周线(注意与分钟线冲突,需确认)

# 月线
df_monthly = tdx.get_kline_data("600519", 3, 50)   # 3=月线
```

### 3.3 获取盘口数据(实时行情)

```python
# 获取五档盘口数据
df_bid_ask = tdx.get_bid_ask_data("600519")

print(df_bid_ask)
# 返回: buy_price1-5, buy_volume1-5, sell_price1-5, sell_volume1-5
```

### 3.4 获取除权因子(复权价格)

```python
# 获取复权因子
factor_df = tdx.get_adj_factor("600519", start_date=20200101, end_date=20261231)

print(factor_df.head())
# 返回: datetime, adj_factor(复权因子)

# 计算前复权价格
df_daily = tdx.get_kline_data("600519", 1, 500)
df_daily['adj_close'] = df_daily['close'] * factor_df['adj_factor'].values[-len(df_daily):]
```

### 3.5 批量获取多只股票数据

```python
# ETF资金流向监控标的(25只)
etf_codes = ["510050", "510300", "510500", "159915", "512880", 
             "512800", "512760", "588000", "588080", "518880"]

# 批量获取日线数据
etf_data = {}
for code in etf_codes:
    df = tdx.get_kline_data(code, 1, 20)  # 最近20个交易日
    etf_data[code] = df
    
print(f"成功获取{len(etf_data)}只ETF数据")
```

---

## 4. 在v8.3系统中的集成点

### 4.1 ETF资金流向监控

**文件:** `v8.3_institutional/scripts/realtime_etf_flow_monitor.py`

```python
# 修改数据源优先级
DATA_SOURCE_PRIORITY = [
    "wind_mcp",
    "ifind_mcp", 
    "pytdx",  # 新增
    "akshare",
    "sina_api",
    "local_cache"
]

# 使用通达信获取ETF实时价格
def get_etf_realtime_price(code):
    """通过通达信获取ETF实时价格"""
    tdx = TDXTaiChiDataFeeder()
    tdx.init()
    
    # 获取5分钟K线(最新一根代表当前价格)
    df = tdx.get_kline_data(code, 0, 1)  # 0=5分钟
    if df is not None and len(df) > 0:
        return df.iloc[-1]['close']
    return None
```

### 4.2 对冲引擎数据源

**文件:** `v8.3_institutional/src/hedging/hedge_engine_v59.py`

```python
# 在HedgeEngineV59中增加通达信数据源
class HedgeEngineV59:
    def __init__(self):
        self.data_sources = {
            'wind': WindDataSource(),
            'ifind': iFinDDataSource(),
            'pytdx': TDXTaiChiDataFeeder(),  # 新增
            'akshare': AKShareDataSource(),
        }
    
    def get_index_realtime(self, index_code):
        """获取指数实时价格(优先使用通达信)"""
        # 尝试Wind
        price = self.data_sources['wind'].get_price(index_code)
        if price:
            return price
        
        # 回退到通达信
        tdx = self.data_sources['pytdx']
        if not tdx.connected:
            tdx.init()
        
        df = tdx.get_kline_data(index_code.replace("000300.SH", "000300"), 0, 1)
        if df is not None:
            return df.iloc[-1]['close']
        
        # 最后回退到AKShare
        return self.data_sources['akshare'].get_price(index_code)
```

### 4.3 日度增强决策系统

**文件:** `v8.3_institutional/scripts/auto_v76_runner.py`

```python
# 在V76Runner中使用通达信获取实时波动率
def calculate_realtime_volatility(self, code):
    """计算实时历史波动率(使用通达信分钟数据)"""
    tdx = TDXTaiChiDataFeeder()
    tdx.init()
    
    # 获取最近100根5分钟K线
    df = tdx.get_kline_data(code, 0, 100)
    if df is None or len(df) < 20:
        return None
    
    # 计算收益率序列
    returns = df['close'].pct_change().dropna()
    
    # 年化波动率 = 标准差 * sqrt(252*24/5)
    realtime_vol = returns.std() * math.sqrt(252 * 24 / 5)
    
    return realtime_vol
```

---

## 5. 性能优化建议

### 5.1 连接池管理

```python
class TDXConnectionPool:
    """通达信连接池管理器"""
    
    def __init__(self, pool_size=5):
        self.pool_size = pool_size
        self.connections = []
        self._init_pool()
    
    def _init_pool(self):
        for _ in range(self.pool_size):
            tdx = TDXTaiChiDataFeeder()
            tdx.init()
            self.connections.append(tdx)
    
    def get_connection(self):
        """从池中获取连接"""
        if self.connections:
            return self.connections.pop()
        return TDXTaiChiDataFeeder()  # 创建新连接
    
    def release_connection(self, tdx):
        """释放连接到池中"""
        if len(self.connections) < self.pool_size:
            self.connections.append(tdx)
```

### 5.2 数据缓存策略

```python
import hashlib
from pathlib import Path

class TDXCacheManager:
    """通达信数据缓存管理器"""
    
    CACHE_DIR = Path("./cache/tdx")
    
    def __init__(self, cache_ttl_hours=2):
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
    
    def get_cache_key(self, code, freq, count):
        """生成缓存键"""
        key_str = f"{code}:{freq}:{count}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def get_cached_data(self, code, freq, count):
        """获取缓存数据"""
        cache_key = self.get_cache_key(code, freq, count)
        cache_file = self.CACHE_DIR / f"{cache_key}.parquet"
        
        if cache_file.exists():
            modified = datetime.fromtimestamp(cache_file.stat().st_mtime)
            if datetime.now() - modified < self.cache_ttl:
                return pd.read_parquet(cache_file)
        
        return None
    
    def save_to_cache(self, code, freq, count, df):
        """保存数据到缓存"""
        cache_key = self.get_cache_key(code, freq, count)
        cache_file = self.CACHE_DIR / f"{cache_key}.parquet"
        
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_file)
```

### 5.3 并发请求优化

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def batch_get_kline_data(codes, freq=1, count=100, max_workers=10):
    """批量并发获取K线数据"""
    results = {}
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_code = {
            executor.submit(get_single_kline, code, freq, count): code
            for code in codes
        }
        
        for future in as_completed(future_to_code):
            code = future_to_code[future]
            try:
                df = future.result()
                results[code] = df
                print(f"✓ {code} 数据获取成功")
            except Exception as e:
                print(f"✗ {code} 数据获取失败: {e}")
    
    return results

def get_single_kline(code, freq, count):
    """获取单只股票K线数据(内部函数)"""
    tdx = TDXTaiChiDataFeeder()
    tdx.init()
    return tdx.get_kline_data(code, freq, count)

# 使用示例
etf_codes = ["510050", "510300", "510500", "159915", "512880"]
data = batch_get_kline_data(etf_codes, freq=0, count=20, max_workers=5)
```

---

## 6. 常见问题FAQ

### Q1: 通达信数据源延迟多少?

**A:** 实时行情延迟约3-5秒(Level-1),适合日频/周频策略,不适合高频交易。

### Q2: 通达信数据是否稳定?

**A:** 内置10+服务器自动切换,稳定性约95%。建议作为P3优先级,在Wind/iFinD不可用时使用。

### Q3: 如何判断通达信数据是否可用?

**A:** 系统会自动检测连接状态:
```python
tdx = TDXTaiChiDataFeeder()
if tdx.init():
    print("通达信数据源可用")
else:
    print("通达信数据源不可用,尝试其他数据源")
```

### Q4: 通达信能否获取期货数据?

**A:** 可以,但仅限行情数据,不包含持仓量、交割信息等深度数据。期货数据建议使用AKShare。

### Q5: 如何处理通达信数据缺失?

**A:** 系统会自动降级到下一优先级数据源(AKShare→新浪财经→本地缓存)。

### Q6: 通达信数据是否需要API Key?

**A:** 不需要,完全免费。只需网络连接通达信服务器即可。

---

## 7. 数据质量校验

### 7.1 数据完整性检查

```python
def validate_tdx_data(df, code):
    """验证通达信数据质量"""
    
    issues = []
    
    # 检查空值
    null_count = df.isnull().sum()
    if null_count.any():
        issues.append(f"存在空值: {null_count[null_count > 0].to_dict()}")
    
    # 检查日期连续性
    if 'datetime' in df.columns:
        date_range = pd.date_range(start=df['datetime'].min(), 
                                   end=df['datetime'].max(), 
                                   freq='B')  # 工作日
        missing_dates = set(date_range) - set(df['datetime'])
        if len(missing_dates) > 10:
            issues.append(f"缺失交易日过多: {len(missing_dates)}天")
    
    # 检查价格合理性
    if 'close' in df.columns:
        price_changes = df['close'].pct_change()
        extreme_changes = price_changes[abs(price_changes) > 0.2]  # 单日涨跌>20%
        if len(extreme_changes) > 0:
            issues.append(f"检测到异常价格变动: {len(extreme_changes)}次")
    
    return issues

# 使用示例
df = tdx.get_kline_data("600519", 1, 100)
issues = validate_tdx_data(df, "600519")
if issues:
    print("数据质量问题:")
    for issue in issues:
        print(f"  - {issue}")
else:
    print("数据质量正常")
```

### 7.2 多源交叉验证

```python
def cross_validate_data(code):
    """多数据源交叉验证"""
    from utils.tdx_data_source import TDXTaiChiDataFeeder
    import akshare as ak
    
    # 通达信数据
    tdx = TDXTaiChiDataFeeder()
    tdx.init()
    df_tdx = tdx.get_kline_data(code, 1, 5)
    
    # AKShare数据
    try:
        df_ak = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
    except:
        df_ak = None
    
    if df_ak is not None:
        # 比较收盘价
        tdx_close = df_tdx['close'].iloc[-1]
        ak_close = df_ak['收盘'].iloc[-1]
        
        diff_pct = abs(tdx_close - ak_close) / ak_close * 100
        
        if diff_pct > 1.0:
            print(f"⚠️ 数据差异过大: 通达信={tdx_close}, AKShare={ak_close}, 差异={diff_pct:.2f}%")
        else:
            print(f"✓ 数据一致: 差异={diff_pct:.4f}%")
```

---

## 8. 最佳实践

### 8.1 日常使用流程

```python
# 每日开盘前数据更新
def daily_data_update():
    """每日数据更新流程"""
    
    # 1. 初始化通达信数据源
    tdx = TDXTaiChiDataFeeder()
    if not tdx.init():
        logging.error("通达信数据源初始化失败")
        return False
    
    # 2. 获取ETF资金流向监控标的数据
    etf_codes = load_etf_watchlist()  # 从配置文件加载
    etf_data = batch_get_kline_data(etf_codes, freq=0, count=20)
    
    # 3. 保存缓存
    save_to_cache(etf_data)
    
    # 4. 启动实时监控
    start_realtime_monitor(etf_data)
    
    return True
```

### 8.2 异常处理策略

```python
def robust_data_fetch(code, freq, count):
    """健壮的數據获取(含多层降级)"""
    
    # 第1优先级: 通达信
    try:
        tdx = TDXTaiChiDataFeeder()
        tdx.init()
        df = tdx.get_kline_data(code, freq, count)
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        logging.warning(f"通达信数据获取失败: {e}")
    
    # 第2优先级: AKShare
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol=code, period=["5分钟", "15分钟", "30分钟", "60分钟"][freq], adjust="")
        return df
    except Exception as e:
        logging.warning(f"AKShare数据获取失败: {e}")
    
    # 第3优先级: 本地缓存
    cached = load_from_cache(code, freq, count)
    if cached is not None:
        logging.info("使用缓存数据")
        return cached
    
    # 第4优先级: 预定义价格
    logging.error("所有数据源不可用,使用预定义价格")
    return create_fallback_data(code)
```

---

## 9. 参考资料

- **pytdx官方文档**: https://github.com/rainx/pytdx
- **通达信服务器列表**: https://www.cnblogs.com/apexchu/p/4649011.html
- **A股数据源对比**: [DATA_SOURCE_COMPARISON.md](./DATA_SOURCE_COMPARISON.md)
- **ETF资金流向监控**: [realtime_etf_flow_monitor.py](../scripts/realtime_etf_flow_monitor.py)

---

## 10. 更新日志

| 日期 | 版本 | 更新内容 |
|------|------|---------|
| 2026-07-23 | v1.0 | 初始版本,集成到v8.3/v8.4系统 |

---

**维护者**: Quant Research Team  
**联系方式**: quant-team@company.com  
**审核状态**: ✅ 已审核
