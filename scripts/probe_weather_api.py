"""彩云天气 API 探测脚本

验证 tj_live API key 的可用性、返回字段结构和覆盖范围.
仅发送只读请求, 不修改任何状态.
"""

import os
import sys
import time

import requests

API_KEY = os.environ.get("APIZERO_API_KEY", "")
BASE_URL = "https://api.caiyunapp.com/v2.6"
if not API_KEY:
    print("[WARN] 未设置 APIZERO_API_KEY 环境变量, 探测将失败. 请先 export/set APIZERO_API_KEY=<key>")

# 关键标的地理位置 (经度, 纬度)
LOCATIONS = {
    "长江电力 (宜宾-宜昌流域)": "106.5,28.7",
    "中国神华 (鄂尔多斯)": "109.7,39.6",
    "盐湖股份 (察尔汗盐湖)": "94.7,36.9",
    "上海 (大盘参考)": "121.47,31.23",
    "北京 (华北参考)": "116.4,39.9",
}


def probe_realtime(location: str, name: str) -> dict:
    """探测实时天气接口"""
    url = f"{BASE_URL}/{API_KEY}/{location}/realtime.json"
    print(f"\n{'='*60}")
    print(f"[探测] {name}")
    print(f"  URL: {url}")

    t0 = time.time()
    try:
        resp = requests.get(url, timeout=15)
        elapsed = (time.time() - t0) * 1000
        print(f"  状态码: {resp.status_code}, 耗时: {elapsed:.0f}ms")

        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result", {})
            realtime = result.get("realtime", {})

            print("  ✅ 可用")
            print(f"  实时数据字段: {list(realtime.keys())}")

            # 核心变量
            print("  --- 核心指标 ---")
            print(f"  温度: {realtime.get('temperature')} ℃")
            print(f"  湿度: {realtime.get('humidity')} %")
            print(f"  风向: {realtime.get('windDirection')} °")
            print(f"  风速: {realtime.get('windSpeed')} m/s")
            print(f"  能见度: {realtime.get('visibility')} km")
            print(f"  气压: {realtime.get('pressure')} hPa")
            print(f"  云量: {realtime.get('cloud')} %")
            print(f"  天气现象: {realtime.get('skycon')}")
            print(f"  AQI: {realtime.get('aqi')}")

            # 降水
            precip = realtime.get("precipitation", {})
            if precip:
                print(f"  降水量: {precip}")

            return {"ok": True, "data": data, "fields": list(realtime.keys())}
        else:
            print(f"  ❌ 失败: {resp.text[:200]}")
            return {"ok": False, "error": resp.text}
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        print(f"  ❌ 异常: {e}")
        return {"ok": False, "error": str(e)}


def probe_hourly(location: str, name: str) -> dict:
    """探测小时预报 (360h = 15天)"""
    url = f"{BASE_URL}/{API_KEY}/{location}/hourly.json"
    print(f"\n[探测] {name} - 15天小时预报")

    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result", {})
            hourly = result.get("hourly", {})

            # 检查可用的预报类型
            available = [k for k in hourly.keys() if isinstance(hourly[k], list) and hourly[k]]
            print(f"  ✅ 可用, 预报类型: {available}")

            # 抽样第一个温度点
            temp_data = hourly.get("temperature", [])
            if temp_data:
                first = temp_data[0]
                last = temp_data[-1]
                print(f"  温度预报点数: {len(temp_data)}")
                print(f"  起点: {first.get('datetime', '?')} → {first.get('value', '?')}℃")
                print(f"  终点: {last.get('datetime', '?')} → {last.get('value', '?')}℃")

                # 检查时间覆盖范围
                times = [d.get("datetime", "") for d in temp_data if "datetime" in d]
                if times:
                    print(f"  时间范围: {times[0]} ~ {times[-1]}")

            # 检查风速
            wind_data = hourly.get("windSpeed", [])
            if wind_data:
                print(f"  风速预报点数: {len(wind_data)}")

            # 检查降水
            precip_data = hourly.get("precipitation", [])
            if precip_data:
                print(f"  降水预报点数: {len(precip_data)}")

            return {"ok": True, "forecast_types": available, "points": len(temp_data)}
        else:
            print(f"  ❌ 失败: {resp.status_code}")
            return {"ok": False}
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        print(f"  ❌ 异常: {e}")
        return {"ok": False}


def probe_daily(location: str, name: str) -> dict:
    """探测天预报 (15天)"""
    url = f"{BASE_URL}/{API_KEY}/{location}/daily.json"
    print(f"\n[探测] {name} - 15天天预报")

    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result", {})
            daily = result.get("daily", {})

            available = [k for k in daily.keys() if isinstance(daily[k], list) and daily[k]]
            print(f"  ✅ 可用, 预报类型: {available}")

            # 抽样
            temp_15 = daily.get("temperature", [])
            if temp_15:
                print(f"  15天温度: {len(temp_15)} 天")
                for d in temp_15[:3]:
                    print(f"    {d.get('date', '?')}: {d.get('max', '?')}℃ / {d.get('min', '?')}℃")

            # 风力
            wind_15 = daily.get("wind", [])
            if wind_15:
                print(f"  15天风力: {len(wind_15)} 天")

            # 生活指数
            life_index = daily.get("lifeIndex", [])
            if life_index:
                print(f"  生活指数: {len(life_index)} 天")

            # 空气质量
            aqi_15 = daily.get("aqi", [])
            if aqi_15:
                print(f"  AQI预报: {len(aqi_15)} 天")

            # 紫外线
            uv_15 = daily.get("ultraviolet", [])
            if uv_15:
                print(f"  紫外线预报: {len(uv_15)} 天")

            return {"ok": True, "forecast_types": available, "days": len(temp_15) if temp_15 else 0}
        else:
            return {"ok": False}
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return {"ok": False, "error": str(e)}


def probe_minutely(location: str, name: str) -> dict:
    """探测分钟级降水预报 (未来2小时)"""
    url = f"{BASE_URL}/{API_KEY}/{location}/minutely.json"
    print(f"\n[探测] {name} - 分钟级降水 (2h)")

    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result", {})
            minutely = result.get("minutely", {})

            available = list(minutely.keys())
            print(f"  ✅ 可用, 字段: {available}")

            # 降水网格
            precip = minutely.get("precipitation", {})
            if precip:
                print(f"  降水网格字段: {list(precip.keys())}")

            # 概要
            summary = minutely.get("summary", [])
            if summary:
                print(f"  分钟级摘要: {len(summary)} 条")
                print(f"  首条: {summary[0] if summary else 'N/A'}")

            return {"ok": True, "fields": available}
        else:
            return {"ok": False}
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return {"ok": False, "error": str(e)}


def main():
    print("=" * 60)
    print("彩云天气 API 探测报告")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Key: {API_KEY[:12]}...{API_KEY[-6:]}")
    print(f"探测地点: {len(LOCATIONS)} 个")
    print("=" * 60)

    results = {}

    for name, loc in list(LOCATIONS.items())[:2]:  # 只测前2个, 避免限流
        print(f"\n{'#'*60}")
        print(f"# 地点: {name} ({loc})")
        print(f"{'#'*60}")

        results[name] = {}
        results[name]["realtime"] = probe_realtime(loc, name)
        results[name]["hourly"] = probe_hourly(loc, name)
        results[name]["daily"] = probe_daily(loc, name)
        results[name]["minutely"] = probe_minutely(loc, name)

        time.sleep(1)  # 避免触发限流

    # 总结
    print(f"\n\n{'='*60}")
    print("探测总结")
    print(f"{'='*60}")

    all_ok = True
    for name, res in results.items():
        realtime_ok = res["realtime"].get("ok", False)
        hourly_ok = res["hourly"].get("ok", False)
        daily_ok = res["daily"].get("ok", False)
        minutely_ok = res["minutely"].get("ok", False)

        status = "✅" if all([realtime_ok, hourly_ok, daily_ok, minutely_ok]) else "⚠️"
        print(f"  {status} {name}: 实时={realtime_ok}, 小时={hourly_ok}, 天={daily_ok}, 分钟={minutely_ok}")

        if not all([realtime_ok, hourly_ok, daily_ok, minutely_ok]):
            all_ok = False

    print(f"\n{'✅ 全部可用' if all_ok else '⚠️ 部分失败, 需排查'}")

    # 输出 JSON 结构摘要供后续开发参考
    print(f"\n\n{'='*60}")
    print("数据结构摘要 (供开发参考)")
    print(f"{'='*60}")

    if results:
        first_result = next(iter(results.values()))
        rt_fields = first_result.get("realtime", {}).get("fields", [])
        print(f"实时天气字段: {rt_fields}")

        hourly_types = first_result.get("hourly", {}).get("forecast_types", [])
        print(f"小时预报类型: {hourly_types}")

        daily_types = first_result.get("daily", {}).get("forecast_types", [])
        print(f"天预报类型: {daily_types}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
