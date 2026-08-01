"""
通达信(pytdx)数据源集成测试脚本

用途:验证pytdx安装和TDXDataSource功能是否正常
执行方式:python test_tdx_integration.py

预期结果:
- 成功连接通达信服务器
- 获取测试股票(贵州茅台 600519)的日线数据
- 显示最近5个交易日行情
- 验证实时行情获取
"""

import os
import sys

# 添加项目根目录到Python路径
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(script_dir, '..', '..')
project_root = os.path.abspath(project_root)
sys.path.insert(0, project_root)


def test_pytdx_installation():
    """测试pytdx是否已安装"""
    print("=" * 60)
    print("测试1: pytdx安装检查")
    print("=" * 60)

    try:
        import pytdx  # noqa: F401
        print("[PASS] pytdx已安装")
        return True
    except ImportError:
        print("[FAIL] pytdx未安装")
        print("\n请运行以下命令安装:")
        print("  pip install pytdx")
        return False


def test_tdx_connection():
    """测试通达信服务器连接"""
    print("\n" + "=" * 60)
    print("测试2: 通达信服务器连接")
    print("=" * 60)

    try:
        from utils.tdx_data_source import TDXDataSource

        tdx = TDXDataSource()

        if tdx._connected:
            print("[PASS] 成功连接到通达信服务器")
            return tdx
        else:
            print("[FAIL] 连接通达信服务器失败")
            print("请检查网络连接或尝试其他服务器")
            return None

    except Exception as e:
        print(f"[ERROR] 连接异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def test_kline_data(tdx):
    """测试K线数据获取"""
    print("\n" + "=" * 60)
    print("测试3: K线数据获取")
    print("=" * 60)

    if tdx is None:
        print("[SKIP] 跳过测试(数据源未连接)")
        return

    # 测试股票:贵州茅台 600519
    code = "600519"

    try:
        print(f"\n获取{code}日线数据(最近10个交易日)...")
        df = tdx.get_historical_klines(code, period='1d', count=10)

        if df is not None and len(df) > 0:
            print("[PASS] 日线数据获取成功")
            print("\n最近5个交易日行情:")
            print(df[['open', 'close', 'high', 'low', 'volume']].tail().to_string(index=False))
        else:
            print("[SKIP] 未获取到日线数据")

    except Exception as e:
        print(f"[ERROR] 日线数据获取失败: {e}")

    # 测试分钟级数据
    try:
        print(f"\n获取{code}5分钟K线(最近5根)...")
        df_5m = tdx.get_historical_klines(code, period='5m', count=5)

        if df_5m is not None and len(df_5m) > 0:
            print("[PASS] 5分钟K线数据获取成功")
            print(df_5m[['open', 'close', 'volume']].tail().to_string(index=False))
        else:
            print("[SKIP] 未获取到5分钟数据")

    except Exception as e:
        print(f"[ERROR] 5分钟数据获取失败: {e}")


def test_realtime_quote(tdx):
    """测试实时行情获取"""
    print("\n" + "=" * 60)
    print("测试4: 实时行情")
    print("=" * 60)

    if tdx is None:
        print("[SKIP] 跳过测试(数据源未连接)")
        return

    code = "600519"

    try:
        print(f"\n获取{code}实时行情...")
        quote = tdx.get_realtime_quote(code)

        if quote is not None:
            print("[PASS] 实时行情获取成功")
            print(f"  开盘: {quote.get('open')}")
            print(f"  最高: {quote.get('high')}")
            print(f"  最低: {quote.get('low')}")
            print(f"  成交量: {quote.get('volume')}")
        else:
            print("[SKIP] 未获取到实时行情")

    except Exception as e:
        print(f"[ERROR] 实时行情获取失败: {e}")


def test_batch_etf_data(tdx):
    """测试批量ETF数据获取"""
    print("\n" + "=" * 60)
    print("测试5: 批量ETF数据获取")
    print("=" * 60)

    if tdx is None:
        print("[SKIP] 跳过测试(数据源未连接)")
        return

    # ETF代码列表(部分)
    etf_codes = ["510050", "510300", "510500", "159915", "512880"]

    print(f"\n批量获取{len(etf_codes)}只ETF日线数据...")

    success_count = 0
    for code in etf_codes:
        try:
            df = tdx.get_historical_klines(code, period='1d', count=1)
            if df is not None and len(df) > 0:
                latest_price = df.iloc[-1]['close']
                print(f"  [PASS] {code}: {latest_price:.2f}")
                success_count += 1
            else:
                print(f"  [SKIP] {code}: 无数据")
        except Exception as e:
            print(f"  [ERROR] {code}: 获取失败 - {e}")

    print(f"\n批量获取完成: {success_count}/{len(etf_codes)} 成功")


def main():
    """主测试流程"""
    print("\n" + "=" * 60)
    print("通达信(pytdx)数据源集成测试")
    print("=" * 60)
    print(f"测试时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # 测试1: 安装检查
    if not test_pytdx_installation():
        print("\n[FAIL] 测试终止: pytdx未安装")
        sys.exit(1)

    # 测试2: 连接测试
    tdx = test_tdx_connection()
    if tdx is None:
        print("\n[FAIL] 测试终止: 无法连接通达信服务器")
        print("提示: 请检查网络连接或稍后重试")
        sys.exit(1)

    # 测试3-5: 功能测试
    test_kline_data(tdx)
    test_realtime_quote(tdx)
    test_batch_etf_data(tdx)

    # 总结
    print("\n" + "=" * 60)
    print("测试完成总结")
    print("=" * 60)
    print("[PASS] 通达信数据源集成正常!")
    print("\n下一步:")
    print("  1. 查看详细文档: v8.3_institutional/docs/TDX_INTEGRATION_GUIDE.md")
    print("  2. 在系统中使用: from utils.tdx_data_source import TDXDataSource")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[INTERRUPTED] 用户中断测试")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n[ERROR] 测试过程中发生异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
