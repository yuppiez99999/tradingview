"""测试 iFinD 配额状态"""
from utils.ifind_client import IFindClient

try:
    client = IFindClient()
    print("iFinD 客户端初始化: OK")
    print(f"  call_count: {client.call_count}")
    print(f"  error_count: {client.error_count}")
    print(f"  last_success: {client.last_success}")
    # 尝试拉取 1 个标的测试配额
    result = client.call('stock', 'get_stock_performance', {
        'query': '600519.SH从20250101到20250110的开盘价、收盘价'
    })
    if result.get('ok'):
        print("  配额测试: OK (返回数据)")
    else:
        if result.get('quota_exceeded'):
            print("  配额测试: 配额超限")
        else:
            err = str(result.get('error', ''))[:100]
            print(f"  配额测试: 失败 - {err}")
except Exception as e:
    print(f"iFinD 初始化失败: {e}")
