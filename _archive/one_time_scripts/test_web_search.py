import re

import requests


def test_web_search_coal(port):
    """测试 Web 搜索港口库存"""
    urls = [
        "https://finance.sina.com.cn/futures/quotes/ZC.shtml",
        "https://futures.eastmoney.com/",
        "https://www.baidu.com/s?wd=动力煤港口库存",
    ]

    for url in urls:
        try:
            print(f"\n测试 URL: {url}")
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            print(f"状态码: {resp.status_code}")
            if resp.status_code == 200:
                content = resp.text
                print(f"内容长度: {len(content)}")

                # 搜索港口库存
                patterns = [
                    rf'{port}港.*?(\d+(?:\.\d+)?)\s*万吨',
                    rf'{port}.*?(\d+(?:\.\d+)?)\s*万吨',
                    r'库存.*?(\d+(?:\.\d+)?)\s*万吨',
                ]
                for pattern in patterns:
                    m = re.search(pattern, content)
                    if m:
                        print(f"匹配到: {m.group(1)} 万吨")
                        return float(m.group(1))

                # 如果没有匹配到，打印一些内容
                print(f"未匹配到，内容片段: {content[:500]}")
        except Exception as e:
            print(f"错误: {e}")

    return None

for port in ["秦皇岛", "曹妃甸", "黄骅港"]:
    print(f"\n=== 测试 {port}港库存 ===")
    result = test_web_search_coal(port)
    if result:
        print(f"结果: {result} 万吨")
    else:
        print("结果: 未找到")
