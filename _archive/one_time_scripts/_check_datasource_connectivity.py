"""
数据源连通性检测脚本 (iFinD MCP / 通达信 / AKShare)
"""
import sys
from pathlib import Path

BASE = Path(r"E:\各种PY程序\28-终极量化交易系统8.4")
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "utils"))

print("=" * 72)
print("数据源连通性检测")
print("=" * 72)

# ============================================================
# 1. iFinD MCP 连接器
# ============================================================
print("\n=== 1. iFinD MCP 连接器 ===")
try:
    from ifind_client import IFindClient
    conn = IFindClient()
    print(f"✅ iFinD MCP 连接器初始化成功: {type(conn).__name__}")

    # 尝试获取一个股票数据 (平安银行 000001)
    if hasattr(conn, 'get_stock_data'):
        try:
            result = conn.get_stock_data('000001')
            if result:
                print(f"✅ iFinD 数据获取成功 (000001): {type(result).__name__}")
            else:
                print("⚠️ iFinD 数据获取返回空 (000001)")
        except Exception as e:
            print(f"⚠️ iFinD 数据获取失败: {e}")
    elif hasattr(conn, 'get_kline'):
        try:
            result = conn.get_kline('000001', '1d')
            if result:
                print(f"✅ iFinD K线获取成功 (000001): {type(result).__name__}")
            else:
                print("⚠️ iFinD K线获取返回空")
        except Exception as e:
            print(f"⚠️ iFinD K线获取失败: {e}")
    else:
        methods = [m for m in dir(conn) if not m.startswith('_')][:5]
        print(f"可用方法: {methods}")
except ImportError as e:
    print(f"❌ iFinD MCP 模块导入失败: {e}")
except Exception as e:
    print(f"❌ iFinD MCP 连接失败: {e}")

# ============================================================
# 2. 通达信数据源
# ============================================================
print("\n=== 2. 通达信数据源 (pytdx) ===")
try:
    from tdx_data_source import TDXDataSource
    ds = TDXDataSource()
    print(f"✅ 通达信数据源初始化成功: {type(ds).__name__}")

    # 尝试获取 K 线数据
    if hasattr(ds, 'get_kline'):
        try:
            kline = ds.get_kline('000001', period='1d', count=3)
            if kline:
                print(f"✅ 通达信 K线获取成功 (000001): {len(kline) if hasattr(kline, '__len__') else 'OK'}")
            else:
                print("⚠️ 通达信 K线获取返回空")
        except Exception as e:
            print(f"⚠️ 通达信 K线获取失败: {e}")
    elif hasattr(ds, 'get_security_bars'):
        try:
            bars = ds.get_security_bars(7, 0, '000001', 0, 3)
            if bars:
                print(f"✅ 通达信 get_security_bars 成功 (000001): {len(bars)} 条")
            else:
                print("⚠️ 通达信 get_security_bars 返回空")
        except Exception as e:
            print(f"⚠️ 通达信 get_security_bars 失败: {e}")
    else:
        methods = [m for m in dir(ds) if not m.startswith('_')][:5]
        print(f"可用方法: {methods}")
except ImportError as e:
    print(f"❌ 通达信模块导入失败: {e}")
except Exception as e:
    print(f"❌ 通达信数据源初始化失败: {e}")

# ============================================================
# 3. AKShare 数据源
# ============================================================
print("\n=== 3. AKShare 数据源 ===")
try:
    from akshare_data_source import AKShareDataSource
    ak = AKShareDataSource()
    print(f"✅ AKShare 数据源初始化成功: {type(ak).__name__}")
except ImportError as e:
    print(f"❌ AKShare 模块导入失败: {e}")
except Exception as e:
    print(f"❌ AKShare 数据源初始化失败: {e}")

# ============================================================
# 4. 数据源路由器 (MarketDataProvider)
# ============================================================
print("\n=== 4. MarketDataProvider 多数据源路由 ===")
try:
    from data_provider import MarketDataProvider
    dp = MarketDataProvider()
    print(f"✅ MarketDataProvider 初始化成功: {type(dp).__name__}")

    # 检查数据源优先级
    if hasattr(dp, 'source_health'):
        print("数据源健康状态:")
        for src, health in dp.source_health.items():
            status = "✅" if health.get('connected', False) else "❌"
            print(f"  {status} {src}: {health}")
    elif hasattr(dp, 'sources'):
        print(f"配置数据源: {dp.sources}")
    else:
        attrs = [a for a in dir(dp) if not a.startswith('_')][:10]
        print(f"MarketDataProvider 属性: {attrs}")
except ImportError as e:
    print(f"❌ MarketDataProvider 模块导入失败: {e}")
except Exception as e:
    print(f"❌ MarketDataProvider 初始化失败: {e}")

print("\n" + "=" * 72)
print("检测完成")
print("=" * 72)
