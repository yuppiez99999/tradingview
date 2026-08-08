"""深度探测 apizero API - 完整数据结构分析"""

import json
import os
import time
import warnings

os.environ["NO_PROXY"] = "apizero.cn,open-meteo.com"
os.environ["no_proxy"] = os.environ["NO_PROXY"]

import requests

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

API_KEY = os.environ.get("APIZERO_API_KEY", "")
BASE = "https://v1.apizero.cn/api"
if not API_KEY:
    print("[WARN] 未设置 APIZERO_API_KEY 环境变量, 探测将失败. 请先 export/set APIZERO_API_KEY=<key>")
LOCATION = "121.47,31.23"  # 上海


def get(url, **kwargs):
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}
    kwargs.setdefault("timeout", 15)
    kwargs.setdefault("verify", False)
    return session.get(url, **kwargs)


def explore_endpoint(name, params):
    """探索单个端点"""
    print(f"\n{'='*60}")
    print(f"📊 {name}")
    print(f"   参数: {json.dumps(params, ensure_ascii=False)}")

    try:
        resp = get(f"{BASE}/weather", params={**params, "key": API_KEY})
        if resp.status_code != 200:
            print(f"   ❌ HTTP {resp.status_code}")
            return None

        data = resp.json()
        print(f"   ✅ 成功! code={data.get('code')}, msg={data.get('msg')}")

        d = data.get("data", {})
        print(f"\n   📋 data 字段: {list(d.keys())}")

        # 逐字段展开
        for key in d:
            val = d[key]
            if isinstance(val, dict):
                print(f"\n   📌 {key} ({len(val)} fields):")
                for k2, v2 in val.items():
                    if isinstance(v2, (dict, list)):
                        print(f"      {k2}: [{type(v2).__name__}] {str(v2)[:150]}")
                    else:
                        print(f"      {k2}: {v2}")
            elif isinstance(val, list):
                print(f"\n   📌 {key}: [{len(val)} items]")
                if val:
                    first = val[0]
                    if isinstance(first, dict):
                        print(f"      首条字段: {list(first.keys())}")
                        for k2, v2 in first.items():
                            print(f"        {k2}: {str(v2)[:100]}")
                    else:
                        print(f"      首条: {str(first)[:200]}")
            elif isinstance(val, str) and len(val) > 100:
                print(f"\n   📌 {key}: {val[:200]}...")
            else:
                print(f"\n   📌 {key}: {val}")

        return d

    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        print(f"   ❌ 异常: {type(e).__name__}: {e}")
        return None


def main():
    print("=" * 60)
    print("apizero API 深度探测")
    print(f"Key: {API_KEY[:12]}...")
    print(f"Base: {BASE}")
    print("=" * 60)

    # 1. 综合天气 (默认 weather 类型)
    print("\n" + "#" * 60)
    print("# 1. 综合天气 (weather) - 上海")
    print("#" * 60)
    d1 = explore_endpoint("综合天气", {
        "type": "weather",
        "location": LOCATION,
        "alert": "true",
    })

    time.sleep(0.5)

    # 2. 实时天气
    print("\n" + "#" * 60)
    print("# 2. 实时天气 (realtime)")
    print("#" * 60)
    explore_endpoint("实时天气", {
        "type": "realtime",
        "location": LOCATION,
    })

    time.sleep(0.5)

    # 3. 小时预报 (15天)
    print("\n" + "#" * 60)
    print("# 3. 小时预报 (hourly, 360h = 15天)")
    print("#" * 60)
    d3 = explore_endpoint("小时预报", {
        "type": "hourly",
        "location": LOCATION,
        "hours": 360,
    })

    time.sleep(0.5)

    # 4. 天预报 (15天)
    print("\n" + "#" * 60)
    print("# 4. 天预报 (daily, 15天)")
    print("#" * 60)
    d4 = explore_endpoint("天预报", {
        "type": "daily",
        "location": LOCATION,
        "days": 15,
    })

    time.sleep(0.5)

    # 5. 分钟级降水
    print("\n" + "#" * 60)
    print("# 5. 分钟级降水 (minutely, 2小时)")
    print("#" * 60)
    explore_endpoint("分钟级降水", {
        "type": "minutely",
        "location": LOCATION,
    })

    # 6. 输出完整 JSON 结构摘要供开发
    print("\n\n" + "=" * 60)
    print("📋 完整数据结构摘要 (供适配器开发)")
    print("=" * 60)

    if d1:
        realtime = d1.get("realtime", {})
        d1.get("minutely", {})
        alerts = d1.get("alerts", [])
        summary = d1.get("summary", {})

        print(f"\n## realtime 实时天气字段 ({len(realtime)} 个):")
        for k in sorted(realtime.keys()):
            v = realtime[k]
            if isinstance(v, (dict, list)):
                print(f"  {k}: [{type(v).__name__}]")
            else:
                print(f"  {k}: {v}")

        print(f"\n## alerts 气象预警 ({len(alerts)} 条):")
        for alert in alerts:
            print(f"  - {alert}")

        print("\n## summary 摘要:")
        print(f"  {json.dumps(summary, ensure_ascii=False)[:300]}")

        # 检查 hourly 和 daily 是否在综合接口中
        print("\n## 综合接口包含的子模块:")
        for key in d1:
            val = d1[key]
            if isinstance(val, list):
                print(f"  {key}: list[{len(val)}]")
            elif isinstance(val, dict):
                print(f"  {key}: dict[{len(val)}]")
            else:
                print(f"  {key}: {type(val).__name__}")

    if d3:
        hourly_data = d3
        print("\n## hourly 小时预报:")
        if isinstance(hourly_data, dict):
            for k in hourly_data:
                v = hourly_data[k]
                if isinstance(v, list):
                    print(f"  {k}: list[{len(v)}]")
                    if v:
                        first = v[0]
                        if isinstance(first, dict):
                            print(f"    字段: {list(first.keys())}")
                            print(f"    首条: {json.dumps(first, ensure_ascii=False)[:200]}")
                else:
                    print(f"  {k}: {v}")

    if d4:
        daily_data = d4
        print("\n## daily 天预报:")
        if isinstance(daily_data, dict):
            for k in daily_data:
                v = daily_data[k]
                if isinstance(v, list):
                    print(f"  {k}: list[{len(v)}]")
                    if v:
                        first = v[0]
                        if isinstance(first, dict):
                            print(f"    字段: {list(first.keys())}")
                            print(f"    首条: {json.dumps(first, ensure_ascii=False)[:200]}")
                else:
                    print(f"  {k}: {v}")

    print("\n" + "=" * 60)
    print("✅ 探测完成!")
    print("   可开始编写 weather_data_adapter.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
