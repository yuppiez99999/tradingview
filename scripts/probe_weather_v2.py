"""彩云天气 API 探测 v2 - 尝试多端点 + SSL 容错"""

import os
import sys
import time
import warnings

# 绕过系统代理, 设置NO_PROXY
os.environ["NO_PROXY"] = (
    "caiyunapp.com,apizero.cn,qweather.com,seniverse.com,"
    "moji.com,open-meteo.com,aliyun.com,tianqi.2345.com"
)
os.environ["no_proxy"] = os.environ["NO_PROXY"]

import requests

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

API_KEY = os.environ.get("APIZERO_API_KEY", "")
if not API_KEY:
    print("[WARN] 未设置 APIZERO_API_KEY 环境变量, 探测将失败. 请先 export/set APIZERO_API_KEY=<key>")

TEST_LOCATION = "121.47,31.23"  # 上海

# 不同服务商的URL模式
URL_TEMPLATES = {
    # 彩云天气 - 标准路径
    "caiyun_v2.6_rt": "https://api.caiyunapp.com/v2.6/{key}/{loc}/realtime.json",
    "caiyun_v2.6_hr": "https://api.caiyunapp.com/v2.6/{key}/{loc}/hourly.json",
    "caiyun_v2.6_dy": "https://api.caiyunapp.com/v2.6/{key}/{loc}/daily.json",
    "caiyun_v2.6_wt": "https://api.caiyunapp.com/v2.6/{key}/{loc}/weather.json",
    # 彩云天气 - query 参数方式
    "caiyun_query": "https://api.caiyunapp.com/v2.6/{key}/weather.json?lng={lng}&lat={lat}",
    # apizero 代理 (彩云天气封装)
    "apizero": "https://v1.apizero.cn/api/weather",
    # 和风天气 (可能是 tj_live 背后的服务商)
    "qweather_rt": "https://devapi.qweather.com/v7/weather/now",
    # Open-Meteo (免费, 验证网络)
    "openmeteo": "https://api.open-meteo.com/v1/forecast",
    # 通用免费天气 (心知天气格式)
    "seniverse": "https://api.seniverse.com/v3/weather/now.json",
}


def test_url(name, url, params=None, headers=None, verify=True):
    """测试单个 URL (绕过系统代理)"""
    print(f"\n  [{name}] {url[:80]}...")
    t0 = time.time()
    try:
        # 创建绕过代理的 session
        session = requests.Session()
        session.trust_env = False  # 不使用环境代理
        session.proxies = {"http": None, "https": None}

        resp = session.get(
            url, params=params, headers=headers,
            timeout=15, verify=verify,
        )
        elapsed = (time.time() - t0) * 1000
        print(f"    状态: {resp.status_code}, 耗时: {elapsed:.0f}ms")

        if resp.status_code == 200:
            data = resp.json()
            # 打印顶层结构
            if isinstance(data, dict):
                print(f"    ✅ 成功! 键: {list(data.keys())}")
                # 深入一层
                for k, v in list(data.items())[:3]:
                    if isinstance(v, dict):
                        print(f"      {k}: {list(v.keys())[:8]}")
                    elif isinstance(v, list):
                        print(f"      {k}: [{len(v)} items]")
                    else:
                        print(f"      {k}: {str(v)[:100]}")
            return {"ok": True, "data": data}
        else:
            print(f"    ❌ HTTP {resp.status_code}: {resp.text[:150]}")
            return {"ok": False, "status": resp.status_code}
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        elapsed = (time.time() - t0) * 1000
        err_type = type(e).__name__
        print(f"    ❌ {err_type} ({elapsed:.0f}ms): {str(e)[:120]}")
        return {"ok": False, "error": str(e)}


def main():
    print("=" * 60)
    print("天气 API 多端点探测 (绕过系统代理)")
    print(f"Key: {API_KEY[:12]}...")
    print(f"NO_PROXY: {os.environ.get('NO_PROXY', '未设置')[:80]}")
    print("=" * 60)

    results = {}

    # ---------- 组1: 彩云天气标准端点 ----------
    print("\n--- 组1: 彩云天气 v2.6 标准端点 ---")
    for ep_name in ["caiyun_v2.6_rt", "caiyun_v2.6_hr", "caiyun_v2.6_dy", "caiyun_v2.6_wt"]:
        url = URL_TEMPLATES[ep_name].format(key=API_KEY, loc=TEST_LOCATION)
        results[ep_name] = test_url(ep_name, url, verify=False)
        time.sleep(0.8)

    # ---------- 组2: 彩云天气 query 参数方式 ----------
    print("\n--- 组2: 彩云天气 query 参数 ---")
    url = URL_TEMPLATES["caiyun_query"].format(key=API_KEY, lng="121.47", lat="31.23")
    results["caiyun_query"] = test_url("caiyun_query", url, verify=False)
    time.sleep(0.8)

    # ---------- 组3: apizero 代理 ----------
    print("\n--- 组3: apizero 代理 ---")
    url = URL_TEMPLATES["apizero"]
    results["apizero"] = test_url(
        "apizero", url,
        params={"type": "weather", "location": TEST_LOCATION, "key": API_KEY},
        verify=False,
    )
    time.sleep(0.8)

    # ---------- 组4: 和风天气 (免费key测试) ----------
    print("\n--- 组4: 和风天气 ---")
    url = URL_TEMPLATES["qweather_rt"]
    results["qweather_rt"] = test_url(
        "qweather_rt", url,
        params={"location": "10102001", "key": API_KEY},
        verify=False,
    )
    time.sleep(0.8)

    # ---------- 组5: Open-Meteo 免费 ----------
    print("\n--- 组5: Open-Meteo (免费, 验证网络连通性) ---")
    url = URL_TEMPLATES["openmeteo"]
    results["openmeteo"] = test_url(
        "openmeteo", url,
        params={
            "latitude": 31.23, "longitude": 121.47,
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
            "hourly": "temperature_2m,wind_speed_10m,precipitation",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "forecast_days": 15,
        },
        verify=True,
    )

    # 总结
    print(f"\n\n{'='*60}")
    print("探测总结")
    print(f"{'='*60}")

    for name, res in results.items():
        status = "✅" if res.get("ok") else "❌"
        results[name] = res.get("ok", False)
        print(f"  {status} {name}")

    # 统计
    ok_count = sum(1 for v in results.values() if v)
    total = len(results)

    print(f"\n成功率: {ok_count}/{total}")

    if ok_count == 0:
        print("\n⚠️ 所有端点均不可达")
        print("   可能原因:")
        print("   1. 系统代理/防火墙阻断了所有HTTPS出站")
        print("   2. tj_live_ key 是内部网络专用, 需在特定环境使用")
        print("   3. key对应的服务商不在上述测试列表中")
    else:
        print("\n✅ 有可用端点!")

    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
