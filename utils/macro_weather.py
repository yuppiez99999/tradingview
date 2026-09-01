"""宏观气象数据获取器 (Open-Meteo, 免费, Wave 12-A #2).

数据源: Open-Meteo REST API (https://open-meteo.com), 免费无需 key.
功能:
  1. 历史温度/降水查询 (archive-api.open-meteo.com)
  2. ENSO 指标代理 (赤道太平洋陆地温度异常)
  3. 商品气象信号 (能源/农产品/工业金属)

降级原则: 网络失败返回空 dict/list, 不崩溃.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

_OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_DEFAULT_TIMEOUT = 10

_COMMODITY_LOCATIONS = {
    "原油": {"name": "Houston (WTI)", "lat": 29.76, "lon": -95.37},
    "天然气": {"name": "Henry Hub", "lat": 30.02, "lon": -93.98},
    "大豆": {"name": "Iowa", "lat": 42.03, "lon": -93.58},
    "玉米": {"name": "Iowa", "lat": 42.03, "lon": -93.58},
    "小麦": {"name": "Kansas", "lat": 38.5, "lon": -96.5},
    "铜": {"name": "Chile", "lat": -33.45, "lon": -70.66},
}

_NINO34_PROXY_LOCATIONS = [
    {"name": "Nino34-West", "lat": 0.0, "lon": -150.0},
    {"name": "Nino34-Center", "lat": 0.0, "lon": -135.0},
    {"name": "Nino34-East", "lat": 0.0, "lon": -120.0},
]


class MacroWeatherFetcher:
    """宏观气象数据获取器 (Open-Meteo, 免费, Wave 12-A #2)."""

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT):
        self.timeout = timeout

    def get_historical_weather(
        self,
        latitude: float,
        longitude: float,
        start_date: str,
        end_date: str,
        daily_vars: list[str] | None = None,
    ) -> dict[str, Any]:
        """获取历史天气数据.

        Args:
            latitude: 纬度.
            longitude: 经度.
            start_date: 起始日期 YYYY-MM-DD.
            end_date: 结束日期 YYYY-MM-DD.
            daily_vars: 日变量列表 (默认温度+降水).

        Returns:
            dict: Open-Meteo API 响应, 失败返回 {"error": ...}.
        """
        if daily_vars is None:
            daily_vars = ["temperature_2m_mean", "precipitation_sum"]

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start_date,
            "end_date": end_date,
            "daily": ",".join(daily_vars),
            "timezone": "UTC",
        }

        return self._fetch(_OPEN_METEO_ARCHIVE_URL, params)

    def get_temperature(
        self,
        latitude: float,
        longitude: float,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        """获取历史温度.

        Returns:
            dict: {"dates": [...], "temperatures": [...], "unit": "°C"} 或 {"error": ...}.
        """
        raw = self.get_historical_weather(latitude, longitude, start_date, end_date, ["temperature_2m_mean"])
        if "error" in raw:
            return raw

        daily = raw.get("daily", {})
        return {
            "dates": daily.get("time", []),
            "temperatures": daily.get("temperature_2m_mean", []),
            "unit": raw.get("daily_units", {}).get("temperature_2m_mean", "°C"),
            "location": {"latitude": latitude, "longitude": longitude},
        }

    def get_precipitation(
        self,
        latitude: float,
        longitude: float,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        """获取历史降水.

        Returns:
            dict: {"dates": [...], "precipitations": [...], "unit": "mm"} 或 {"error": ...}.
        """
        raw = self.get_historical_weather(latitude, longitude, start_date, end_date, ["precipitation_sum"])
        if "error" in raw:
            return raw

        daily = raw.get("daily", {})
        return {
            "dates": daily.get("time", []),
            "precipitations": daily.get("precipitation_sum", []),
            "unit": raw.get("daily_units", {}).get("precipitation_sum", "mm"),
            "location": {"latitude": latitude, "longitude": longitude},
        }

    def get_enso_indicator(self, lookback_days: int = 90) -> dict[str, Any]:
        """获取 ENSO 指标代理 (赤道太平洋陆地温度异常).

        使用 Niño 3.4 区域 (5N-5S, 170W-120W) 附近陆地温度
        作为 ENSO 的间接代理指标.

        Returns:
            dict: {"anomaly": float, "phase": str, "locations": [...]} 或 {"error": ...}.
        """
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        anomalies = []
        location_results = []
        for loc in _NINO34_PROXY_LOCATIONS:
            temp_data = self.get_temperature(loc["lat"], loc["lon"], start_date, end_date)
            if "error" in temp_data:
                location_results.append({"name": loc["name"], "status": "failed"})
                continue

            temps = [t for t in temp_data.get("temperatures", []) if t is not None]
            if not temps:
                location_results.append({"name": loc["name"], "status": "no_data"})
                continue

            avg_temp = sum(temps) / len(temps)
            baseline = sum(temps[: max(1, len(temps) // 3)]) / max(1, len(temps[: max(1, len(temps) // 3)]))
            anomaly = avg_temp - baseline
            anomalies.append(anomaly)
            location_results.append(
                {
                    "name": loc["name"],
                    "status": "ok",
                    "avg_temp": round(avg_temp, 2),
                    "anomaly": round(anomaly, 2),
                }
            )

        if not anomalies:
            return {"error": "all_locations_failed", "locations": location_results}

        avg_anomaly = sum(anomalies) / len(anomalies)
        phase = self._classify_enso_phase(avg_anomaly)

        return {
            "anomaly": round(avg_anomaly, 2),
            "phase": phase,
            "lookback_days": lookback_days,
            "locations": location_results,
        }

    def get_commodity_weather_signals(self, lookback_days: int = 30) -> list[dict]:
        """生成商品气象信号.

        基于温度/降水偏离度生成商品交易辅助信号.

        Returns:
            list[dict]: 每个商品包含 name/signal/temp_avg/precip_sum/risk_level.
        """
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        signals = []
        for commodity, loc in _COMMODITY_LOCATIONS.items():
            temp_data = self.get_temperature(loc["lat"], loc["lon"], start_date, end_date)
            precip_data = self.get_precipitation(loc["lat"], loc["lon"], start_date, end_date)

            temps = [t for t in temp_data.get("temperatures", []) if t is not None]
            precips = [p for p in precip_data.get("precipitations", []) if p is not None]

            temp_avg = round(sum(temps) / len(temps), 1) if temps else None
            precip_sum = round(sum(precips), 1) if precips else None

            signal, risk = self._assess_commodity_risk(commodity, temp_avg, precip_sum)

            signals.append(
                {
                    "commodity": commodity,
                    "location": loc["name"],
                    "temp_avg": temp_avg,
                    "precip_sum": precip_sum,
                    "signal": signal,
                    "risk_level": risk,
                    "status": "ok" if temp_avg is not None else "no_data",
                }
            )

        return signals

    def _fetch(self, url: str, params: dict) -> dict[str, Any]:
        """执行 HTTP 请求, 降级不崩溃."""
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.Timeout:
            logger.warning("[MacroWeather] 请求超时: %s", url)
            return {"error": "timeout", "url": url}
        except requests.ConnectionError:
            logger.warning("[MacroWeather] 连接失败: %s", url)
            return {"error": "connection_error", "url": url}
        except requests.HTTPError as e:
            logger.warning("[MacroWeather] HTTP 错误: %s -> %s", url, e)
            return {"error": "http_error", "status_code": e.response.status_code}
        except (ValueError, KeyError) as e:
            logger.warning("[MacroWeather] 解析错误: %s -> %s", url, e)
            return {"error": "parse_error", "detail": str(e)}

    @staticmethod
    def _classify_enso_phase(anomaly: float) -> str:
        """根据温度异常分类 ENSO 阶段."""
        if anomaly > 0.5:
            return "厄尔尼诺 (El Niño)"
        if anomaly < -0.5:
            return "拉尼娜 (La Niña)"
        return "中性 (Neutral)"

    @staticmethod
    def _assess_commodity_risk(commodity: str, temp_avg: float | None, precip_sum: float | None) -> tuple[str, str]:
        """评估商品气象风险."""
        if temp_avg is None:
            return "数据不足", "未知"

        if commodity in ("大豆", "玉米", "小麦"):
            if precip_sum is not None and precip_sum < 10:
                return "干旱风险，看多", "高"
            if temp_avg > 30:
                return "高温风险，看多", "高"
            return "气象正常", "低"

        if commodity in ("原油", "天然气"):
            if temp_avg > 30:
                return "高温需求增，看多", "中"
            if temp_avg < 0:
                return "低温需求增，看多", "中"
            return "气象正常", "低"

        if commodity == "铜":
            if precip_sum is not None and precip_sum > 100:
                return "降水过多采矿风险，偏多", "中"
            return "气象正常", "低"

        return "气象正常", "低"
