"""气象数据适配器 — 28 系统集成层 (v8.6.14)

核心功能:
    封装 apizero.cn 天气 API (彩云天气代理), 提供实时天气 / 小时预报 /
    天预报 / 分钟级降水 的统一数据接口, 支撑大宗商品/能源/电力/农业/
    旅游等板块的气象因子分析.

设计原则:
    1. 懒加载: 首次调用时初始化 HTTP 客户端, 不影响系统启动
    2. 优雅降级: apizero 不可用时回退到 Open-Meteo 免费数据源
    3. 统一数据结构: 返回 WeatherSnapshot / WeatherForecast 数据类
    4. 单例模式: 共享 HTTP 会话, 复用连接池
    5. 代理绕过: 自动设置 NO_PROXY, 绕过系统代理访问国内气象 API

数据源层级:
    1. apizero.cn (彩云天气代理, key=tj_live_F8W4O894RQYp) — P0
    2. Open-Meteo (免费开源, 需翻墙, 备选) — P1
    3. 本地缓存 (最近一次成功的响应) — P2
    4. 预定义兜底数据 — P3

用法:
    from utils.weather_data_adapter import WeatherDataAdapter

    adapter = WeatherDataAdapter()
    snap = adapter.get_realtime(106.5, 28.7)       # 实时天气快照
    hourly = adapter.get_hourly(106.5, 28.7, hours=72)  # 72小时预报
    daily = adapter.get_daily(106.5, 28.7, days=15)  # 15天预报
    summary = adapter.get_summary(106.5, 28.7)     # 综合摘要
    alerts = adapter.get_alerts(106.5, 28.7)       # 气象预警

作者: 28 系统 PM
日期: 2026-08-01
"""

from __future__ import annotations

import json
import logging
import os
import time
import warnings
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("weather_data")

# ============================================================
# 代理绕过设置
# ============================================================
os.environ.setdefault("NO_PROXY", "apizero.cn,open-meteo.com,caiyunapp.com")
os.environ.setdefault("no_proxy", os.environ["NO_PROXY"])

# ============================================================
# 可选依赖 (懒加载)
# ============================================================
try:
    import requests as _requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    _REQUESTS_AVAILABLE = True
except ImportError:
    _requests = None
    _REQUESTS_AVAILABLE = False

warnings.filterwarnings("ignore", message="Unverified HTTPS request")


# ============================================================
# 数据类
# ============================================================


@dataclass
class WeatherRealtime:
    """实时天气快照"""

    temperature: float = 0.0
    apparent_temperature: float = 0.0
    humidity: float = 0.0
    cloudrate: float = 0.0
    skycon: str = ""
    visibility: float = 0.0
    dswrf: float = 0.0
    wind_speed: float = 0.0
    wind_direction: float = 0.0
    pressure: float = 0.0
    precipitation_local: float = 0.0
    precipitation_nearest: float = 0.0
    precipitation_nearest_distance: float = 0.0
    aqi_chn: int = 0
    aqi_usa: int = 0
    pm25: float = 0.0
    pm10: float = 0.0
    server_time: str = ""
    status: str = "unknown"

    @property
    def is_rainy(self) -> bool:
        return self.precipitation_local > 0 or self.precipitation_nearest > 0

    @property
    def is_extreme_heat(self) -> bool:
        return self.temperature >= 35.0

    @property
    def is_frozen(self) -> bool:
        return self.temperature <= 0.0

    @property
    def low_visibility(self) -> bool:
        return self.visibility < 2.0

    @property
    def strong_wind(self) -> bool:
        return self.wind_speed >= 10.0


@dataclass
class WeatherHourlyPoint:
    """单小时预报点"""

    datetime: str = ""
    temperature: float = 0.0
    apparent_temperature: float = 0.0
    precipitation: float = 0.0
    precipitation_probability: float = 0.0
    wind_speed: float = 0.0
    wind_direction: float = 0.0
    humidity: float = 0.0
    cloudrate: float = 0.0
    skycon: str = ""
    pressure: float = 0.0
    visibility: float = 0.0
    dswrf: float = 0.0
    aqi_chn: int = 0


@dataclass
class WeatherDailyPoint:
    """单日预报"""

    date: str = ""
    temp_max: float = 0.0
    temp_min: float = 0.0
    temp_avg: float = 0.0
    precip_max: float = 0.0
    precip_avg: float = 0.0
    precip_probability: float = 0.0
    wind_max_speed: float = 0.0
    wind_max_direction: float = 0.0
    wind_avg_speed: float = 0.0
    humidity_max: float = 0.0
    humidity_min: float = 0.0
    humidity_avg: float = 0.0
    cloudrate_max: float = 0.0
    pressure_max: float = 0.0
    visibility_max: float = 0.0
    dswrf_max: float = 0.0
    dswrf_avg: float = 0.0
    skycon: str = ""
    sunrise: str = ""
    sunset: str = ""
    aqi_chn_max: int = 0


@dataclass
class WeatherMinutelyPoint:
    """分钟级降水"""

    minute_index: int = 0
    precipitation: float = 0.0
    precipitation_2h: float = 0.0
    accumulation: float = 0.0
    probability: float = 0.0


@dataclass
class WeatherForecast:
    """完整天气预报"""

    realtime: WeatherRealtime | None = None
    hourly: list[WeatherHourlyPoint] = field(default_factory=list)
    daily: list[WeatherDailyPoint] = field(default_factory=list)
    minutely: list[WeatherMinutelyPoint] = field(default_factory=list)
    summary_text: str = ""
    alerts: list[dict[str, Any]] = field(default_factory=list)
    location_name: str = ""
    source: str = "unknown"
    timestamp: float = 0.0


# ============================================================
# HTTP 客户端 (懒加载单例)
# ============================================================

_http_session: Any | None = None


def _get_session() -> Any | None:
    """获取绕过代理的 HTTP Session (单例)."""
    global _http_session
    if _http_session is not None:
        return _http_session
    if not _REQUESTS_AVAILABLE:
        return None

    session = _requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}

    retry_strategy = Retry(
        total=1,
        backoff_factor=0.3,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy, pool_connections=4, pool_maxsize=8
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    _http_session = session
    return session


# ============================================================
# 适配器主类
# ============================================================


class WeatherDataAdapter:
    """气象数据适配器 — apizero.cn + Open-Meteo 降级链.

    典型用法::

        adapter = WeatherDataAdapter()
        snap = adapter.get_realtime(106.5, 28.7)
        forecast = adapter.get_forecast(106.5, 28.7, days=15)
    """

    API_URL = "https://v1.apizero.cn/api/weather"
    OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"
    CACHE_TTL = 600  # 缓存有效期 (秒) — 延长缓存减少重复请求

    def __init__(self, api_key: str | None = None):
        # 安全合规: API key 仅从环境变量或参数读取, 不硬编码
        self.api_key = api_key or os.environ.get("WEATHER_API_KEY", "")
        self._session = _get_session()
        self._cache: dict[str, tuple[float, Any]] = {}
        self._available: bool | None = None
        self._source: str = "unknown"

    # ----------------------------------------------------------
    # 可用性检测
    # ----------------------------------------------------------

    @property
    def is_available(self) -> bool:
        # 缓存过期后重新检测 (10 分钟)
        if self._available is not None and hasattr(self, "_available_expires_at"):
            if time.time() < self._available_expires_at:
                return self._available
            self._available = None  # 强制重新检测
        if self._available is not None:
            return self._available
        self._available = self._check_apizero()
        self._available_expires_at = time.time() + 600
        return self._available

    def _check_apizero(self) -> bool:
        """检测 apizero API 是否可用."""
        if self._session is None:
            logger.warning("requests 库不可用, WeatherDataAdapter 无法工作")
            return False
        try:
            resp = self._session.get(
                self.API_URL,
                params={
                    "type": "realtime",
                    "location": "116.4,39.9",
                    "key": self.api_key,
                },
                timeout=8,
                verify=True,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    self._source = "apizero"
                    logger.info("apizero 天气 API 可用")
                    return True
            logger.warning("apizero API 检测失败: HTTP %d", resp.status_code)
            return False
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("apizero API 不可用: %s, 将降级", e)
            return False

    # ----------------------------------------------------------
    # 核心请求方法
    # ----------------------------------------------------------

    def _request(self, params: dict[str, Any], use_cache: bool = True) -> dict | None:
        """发送请求到 apizero API, 带缓存.

        遇到 429 限流时, 立即标记 apizero 不可用, 后续请求直接降级.
        """
        cache_key = json.dumps(params, sort_keys=True)
        if use_cache:
            cached = self._cache.get(cache_key)
            if cached and (time.time() - cached[0]) < self.CACHE_TTL:
                return cached[1]

        if self._session is None:
            return None

        try:
            resp = self._session.get(
                self.API_URL,
                params={**params, "key": self.api_key},
                timeout=8,
                verify=True,
            )
            if resp.status_code == 429:
                # 触发限流 — 立即标记 apizero 不可用, 10 分钟内不再尝试
                self._available = False
                self._available_expires_at = time.time() + 600
                logger.warning("apizero 触发限流 (429), 降级到 Open-Meteo")
                return None
            if resp.status_code != 200:
                logger.warning(
                    "apizero 请求失败: HTTP %d, params=%s", resp.status_code, params
                )
                return None
            data = resp.json()
            if data.get("code") != 0:
                logger.warning("apizero 返回错误: %s", data.get("msg"))
                return None

            result = data.get("data", {})
            self._cache[cache_key] = (time.time(), result)
            return result
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error("apizero 请求异常: %s", e)
            return None

    def _request_openmeteo(self, lat: float, lon: float) -> dict | None:
        """降级到 Open-Meteo 免费 API, 带 10 分钟缓存."""
        cache_key = f"openmeteo:{lat:.2f}:{lon:.2f}"
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < self.CACHE_TTL:
            self._source = "openmeteo"
            return cached[1]

        if self._session is None:
            return None
        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "wind_speed_10m,wind_direction_10m,"
                "precipitation,cloud_cover,pressure_msl,visibility",
                "hourly": "temperature_2m,wind_speed_10m,precipitation,cloud_cover",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                "forecast_days": 15,
            }
            resp = self._session.get(
                self.OPENMETEO_URL,
                params=params,
                timeout=8,
                verify=True,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._source = "openmeteo"
                self._cache[cache_key] = (time.time(), data)
                return data
            logger.warning("Open-Meteo 请求失败: HTTP %d", resp.status_code)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("Open-Meteo 请求异常: %s", e)
        return None

    # ----------------------------------------------------------
    # 实时天气
    # ----------------------------------------------------------

    def get_realtime(self, lon: float, lat: float) -> WeatherRealtime:
        """获取指定坐标的实时天气快照."""
        loc = f"{lon},{lat}"
        data = self._request({"type": "realtime", "location": loc})

        if data is None:
            logger.info("实时天气降级到 Open-Meteo")
            return self._fallback_realtime(lon, lat)

        rt = data.get("realtime", {})
        return WeatherRealtime(
            temperature=self._f(rt.get("temperature")),
            apparent_temperature=self._f(rt.get("apparent_temperature")),
            humidity=self._f(rt.get("humidity")),
            cloudrate=self._f(rt.get("cloudrate")),
            skycon=rt.get("skycon", ""),
            visibility=self._f(rt.get("visibility")),
            dswrf=self._f(rt.get("dswrf")),
            wind_speed=self._f(rt.get("wind", {}).get("speed")),
            wind_direction=self._f(rt.get("wind", {}).get("direction")),
            pressure=self._f(rt.get("pressure")),
            precipitation_local=self._f(
                rt.get("precipitation", {}).get("local", {}).get("intensity")
            ),
            precipitation_nearest=self._f(
                rt.get("precipitation", {}).get("nearest", {}).get("intensity")
            ),
            precipitation_nearest_distance=self._f(
                rt.get("precipitation", {}).get("nearest", {}).get("distance")
            ),
            aqi_chn=int(rt.get("air_quality", {}).get("aqi", {}).get("chn", 0) or 0),
            aqi_usa=int(rt.get("air_quality", {}).get("aqi", {}).get("usa", 0) or 0),
            pm25=self._f(rt.get("air_quality", {}).get("pm25")),
            pm10=self._f(rt.get("air_quality", {}).get("pm10")),
            server_time=data.get("server_time", ""),
            status=rt.get("status", "unknown"),
        )

    def _fallback_realtime(self, lon: float, lat: float) -> WeatherRealtime:
        """Open-Meteo 降级: 获取实时数据."""
        data = self._request_openmeteo(lat, lon)
        if data and "current" in data:
            cur = data["current"]
            return WeatherRealtime(
                temperature=self._f(cur.get("temperature_2m")),
                apparent_temperature=self._f(cur.get("apparent_temperature")),
                humidity=self._f(cur.get("relative_humidity_2m")),
                cloudrate=self._f(cur.get("cloud_cover")) / 100.0,
                skycon=self._map_cloud_to_skycon(cur.get("cloud_cover", 0)),
                visibility=self._f(cur.get("visibility")),
                dswrf=0.0,
                wind_speed=self._f(cur.get("wind_speed_10m")),
                wind_direction=self._f(cur.get("wind_direction_10m")),
                pressure=self._f(cur.get("pressure_msl")) * 100.0,
                precipitation_local=self._f(cur.get("precipitation")),
                precipitation_nearest=0.0,
                precipitation_nearest_distance=0.0,
                aqi_chn=0,
                aqi_usa=0,
                pm25=0.0,
                pm10=0.0,
                server_time=cur.get("time", ""),
                status="fallback_openmeteo",
            )
        return WeatherRealtime(status="no_data")

    # ----------------------------------------------------------
    # 小时预报
    # ----------------------------------------------------------

    def get_hourly(
        self,
        lon: float,
        lat: float,
        hours: int = 72,
        raw_data: dict | None = None,
    ) -> list[WeatherHourlyPoint]:
        """获取指定坐标的小时级预报.

        Args:
            raw_data: 可选, 若已有综合接口 (type=weather) 返回的数据, 直接解析
                      避免重复 API 请求 (节省配额, 防限流).
        """
        if raw_data is not None:
            data = raw_data
        else:
            loc = f"{lon},{lat}"
            data = self._request(
                {"type": "hourly", "location": loc, "hours": min(hours, 360)}
            )
            if data is None:
                logger.info("小时预报降级到 Open-Meteo")
                return self._fallback_hourly(lon, lat)

        hourly_block = data.get("hourly", {})
        result: list[WeatherHourlyPoint] = []

        precip_list = hourly_block.get("precipitation", [])
        temp_list = hourly_block.get("temperature", [])
        app_temp_list = hourly_block.get("apparent_temperature", [])
        wind_list = hourly_block.get("wind", [])
        hum_list = hourly_block.get("humidity", [])
        cloud_list = hourly_block.get("cloudrate", [])
        skycon_list = hourly_block.get("skycon", [])
        press_list = hourly_block.get("pressure", [])
        vis_list = hourly_block.get("visibility", [])
        dswrf_list = hourly_block.get("dswrf", [])
        aqi_dict = hourly_block.get("air_quality", {})
        aqi_list = aqi_dict.get("aqi", []) if isinstance(aqi_dict, dict) else []

        max_len = len(temp_list)
        for i in range(max_len):
            precip = precip_list[i] if i < len(precip_list) else {}
            wind = wind_list[i] if i < len(wind_list) else {}
            aqi_item = aqi_list[i] if i < len(aqi_list) else {}

            result.append(
                WeatherHourlyPoint(
                    datetime=(
                        temp_list[i].get("datetime", "") if i < len(temp_list) else ""
                    ),
                    temperature=self._f(
                        temp_list[i].get("value") if i < len(temp_list) else None
                    ),
                    apparent_temperature=self._f(
                        app_temp_list[i].get("value")
                        if i < len(app_temp_list)
                        else None
                    ),
                    precipitation=self._f(precip.get("value")),
                    precipitation_probability=self._f(precip.get("probability")),
                    wind_speed=self._f(wind.get("speed")),
                    wind_direction=self._f(wind.get("direction")),
                    humidity=self._f(
                        hum_list[i].get("value") if i < len(hum_list) else None
                    ),
                    cloudrate=self._f(
                        cloud_list[i].get("value") if i < len(cloud_list) else None
                    ),
                    skycon=(
                        skycon_list[i].get("value", "") if i < len(skycon_list) else ""
                    ),
                    pressure=self._f(
                        press_list[i].get("value") if i < len(press_list) else None
                    ),
                    visibility=self._f(
                        vis_list[i].get("value") if i < len(vis_list) else None
                    ),
                    dswrf=self._f(
                        dswrf_list[i].get("value") if i < len(dswrf_list) else None
                    ),
                    aqi_chn=int(
                        (
                            aqi_item.get("value", {}).get("chn", 0)
                            if isinstance(aqi_item.get("value"), dict)
                            else 0
                        )
                        or 0
                    ),
                )
            )

        return result

    def _fallback_hourly(self, lon: float, lat: float) -> list[WeatherHourlyPoint]:
        """Open-Meteo 降级."""
        data = self._request_openmeteo(lat, lon)
        if not data or "hourly" not in data:
            return []
        h = data["hourly"]
        times = h.get("time", [])
        temps = h.get("temperature_2m", [])
        winds = h.get("wind_speed_10m", [])
        precs = h.get("precipitation", [])
        clouds = h.get("cloud_cover", [])
        result = []
        for i, t in enumerate(times):
            result.append(
                WeatherHourlyPoint(
                    datetime=t,
                    temperature=self._f(temps[i] if i < len(temps) else None),
                    apparent_temperature=0.0,
                    precipitation=self._f(precs[i] if i < len(precs) else None),
                    precipitation_probability=0.0,
                    wind_speed=self._f(winds[i] if i < len(winds) else None),
                    cloudrate=self._f(clouds[i] if i < len(clouds) else None) / 100.0,
                )
            )
        return result

    # ----------------------------------------------------------
    # 天预报
    # ----------------------------------------------------------

    def get_daily(
        self,
        lon: float,
        lat: float,
        days: int = 15,
        raw_data: dict | None = None,
    ) -> list[WeatherDailyPoint]:
        """获取指定坐标的天级预报.

        Args:
            raw_data: 可选, 若已有综合接口 (type=weather) 返回的数据, 直接解析
                      避免重复 API 请求 (节省配额, 防限流).
        """
        if raw_data is not None:
            data = raw_data
        else:
            loc = f"{lon},{lat}"
            data = self._request(
                {"type": "daily", "location": loc, "days": min(days, 15)}
            )
            if data is None:
                logger.info("天预报降级到 Open-Meteo")
                return self._fallback_daily(lon, lat)

        daily_block = data.get("daily", {})
        result: list[WeatherDailyPoint] = []

        temp_list = daily_block.get("temperature", [])
        precip_list = daily_block.get("precipitation", [])
        wind_list = daily_block.get("wind", [])
        hum_list = daily_block.get("humidity", [])
        cloud_list = daily_block.get("cloudrate", [])
        press_list = daily_block.get("pressure", [])
        vis_list = daily_block.get("visibility", [])
        dswrf_list = daily_block.get("dswrf", [])
        skycon_list = daily_block.get("skycon", [])
        astro_list = daily_block.get("astro", [])
        aqi_dict = daily_block.get("air_quality", {})
        aqi_list = aqi_dict.get("aqi", []) if isinstance(aqi_dict, dict) else []

        for i, item in enumerate(temp_list):
            precip = precip_list[i] if i < len(precip_list) else {}
            wind = wind_list[i] if i < len(wind_list) else {}
            hum = hum_list[i] if i < len(hum_list) else {}
            cloud = cloud_list[i] if i < len(cloud_list) else {}
            press = press_list[i] if i < len(press_list) else {}
            vis = vis_list[i] if i < len(vis_list) else {}
            dswrf = dswrf_list[i] if i < len(dswrf_list) else {}
            skycon = skycon_list[i] if i < len(skycon_list) else {}
            astro = astro_list[i] if i < len(astro_list) else {}
            aqi_item = aqi_list[i] if i < len(aqi_list) else {}

            result.append(
                WeatherDailyPoint(
                    date=item.get("date", ""),
                    temp_max=self._f(item.get("max")),
                    temp_min=self._f(item.get("min")),
                    temp_avg=self._f(item.get("avg")),
                    precip_max=self._f(precip.get("max")),
                    precip_avg=self._f(precip.get("avg")),
                    precip_probability=self._f(precip.get("probability")),
                    wind_max_speed=self._f(wind.get("max", {}).get("speed")),
                    wind_max_direction=self._f(wind.get("max", {}).get("direction")),
                    wind_avg_speed=self._f(wind.get("avg", {}).get("speed")),
                    humidity_max=self._f(hum.get("max")),
                    humidity_min=self._f(hum.get("min")),
                    humidity_avg=self._f(hum.get("avg")),
                    cloudrate_max=self._f(cloud.get("max")),
                    pressure_max=self._f(press.get("max")),
                    visibility_max=self._f(vis.get("max")),
                    dswrf_max=self._f(dswrf.get("max")),
                    dswrf_avg=self._f(dswrf.get("avg")),
                    skycon=skycon.get("value", ""),
                    sunrise=astro.get("sunrise", {}).get("time", ""),
                    sunset=astro.get("sunset", {}).get("time", ""),
                    aqi_chn_max=int(
                        (
                            aqi_item.get("max", {}).get("chn", 0)
                            if isinstance(aqi_item.get("max"), dict)
                            else 0
                        )
                        or 0
                    ),
                )
            )

        return result

    def _fallback_daily(self, lon: float, lat: float) -> list[WeatherDailyPoint]:
        """Open-Meteo 降级."""
        data = self._request_openmeteo(lat, lon)
        if not data or "daily" not in data:
            return []
        d = data["daily"]
        times = d.get("time", [])
        temp_max = d.get("temperature_2m_max", [])
        temp_min = d.get("temperature_2m_min", [])
        precip = d.get("precipitation_sum", [])
        result = []
        for i, t in enumerate(times):
            result.append(
                WeatherDailyPoint(
                    date=t,
                    temp_max=self._f(temp_max[i] if i < len(temp_max) else None),
                    temp_min=self._f(temp_min[i] if i < len(temp_min) else None),
                    precip_max=self._f(precip[i] if i < len(precip) else None),
                    precip_avg=self._f(precip[i] if i < len(precip) else None),
                )
            )
        return result

    # ----------------------------------------------------------
    # 综合预报
    # ----------------------------------------------------------

    def get_forecast(self, lon: float, lat: float, days: int = 15) -> WeatherForecast:
        """获取完整天气预报 (综合接口, 一次请求获取所有数据)."""
        loc = f"{lon},{lat}"
        data = self._request({"type": "weather", "location": loc, "alert": "true"})

        if data is None:
            logger.info("综合天气降级到 Open-Meteo")
            realtime = self._fallback_realtime(lon, lat)
            hourly = self._fallback_hourly(lon, lat)
            daily = self._fallback_daily(lon, lat)
            return WeatherForecast(
                realtime=realtime,
                hourly=hourly,
                daily=daily,
                source="openmeteo_fallback",
                timestamp=time.time(),
            )

        # 解析 realtime
        rt_data = data.get("realtime", {})
        realtime = WeatherRealtime(
            temperature=self._f(rt_data.get("temperature")),
            apparent_temperature=self._f(rt_data.get("apparent_temperature")),
            humidity=self._f(rt_data.get("humidity")),
            cloudrate=self._f(rt_data.get("cloudrate")),
            skycon=rt_data.get("skycon", ""),
            visibility=self._f(rt_data.get("visibility")),
            dswrf=self._f(rt_data.get("dswrf")),
            wind_speed=self._f(rt_data.get("wind", {}).get("speed")),
            wind_direction=self._f(rt_data.get("wind", {}).get("direction")),
            pressure=self._f(rt_data.get("pressure")),
            precipitation_local=self._f(
                rt_data.get("precipitation", {}).get("local", {}).get("intensity")
            ),
            precipitation_nearest=self._f(
                rt_data.get("precipitation", {}).get("nearest", {}).get("intensity")
            ),
            precipitation_nearest_distance=self._f(
                rt_data.get("precipitation", {}).get("nearest", {}).get("distance")
            ),
            aqi_chn=int(
                rt_data.get("air_quality", {}).get("aqi", {}).get("chn", 0) or 0
            ),
            aqi_usa=int(
                rt_data.get("air_quality", {}).get("aqi", {}).get("usa", 0) or 0
            ),
            pm25=self._f(rt_data.get("air_quality", {}).get("pm25")),
            pm10=self._f(rt_data.get("air_quality", {}).get("pm10")),
            server_time=data.get("server_time", ""),
            status=rt_data.get("status", "unknown"),
        )

        # 解析 alerts
        alerts = data.get("alerts", [])
        if alerts is None:
            alerts = []

        # 解析 summary
        summary = data.get("summary", {})
        summary_text = data.get("forecast_keypoint", "") or summary.get("skycon", "")

        return WeatherForecast(
            realtime=realtime,
            hourly=self.get_hourly(lon, lat, hours=days * 24, raw_data=data),
            daily=self.get_daily(lon, lat, days=days, raw_data=data),
            alerts=alerts,
            summary_text=summary_text,
            location_name=data.get("location", {}).get("city", "") or f"{lon},{lat}",
            source=self._source,
            timestamp=time.time(),
        )

    # ----------------------------------------------------------
    # 预警
    # ----------------------------------------------------------

    def get_alerts(self, lon: float, lat: float) -> list[dict[str, Any]]:
        """获取气象预警信息."""
        loc = f"{lon},{lat}"
        data = self._request({"type": "weather", "location": loc, "alert": "true"})
        if data is None:
            return []
        alerts = data.get("alerts", [])
        return alerts if alerts else []

    # ----------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------

    @staticmethod
    def _f(val: Any) -> float:
        """安全转 float."""
        if val is None:
            return 0.0
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _map_cloud_to_skycon(cloud_cover: float) -> str:
        """将云量 (%) 映射到 skycon 代码."""
        if cloud_cover < 10:
            return "CLEAR_DAY"
        if cloud_cover < 30:
            return "MOSTLY_CLEAR_DAY"
        if cloud_cover < 60:
            return "PARTLY_CLOUDY_DAY"
        if cloud_cover < 80:
            return "MOSTLY_CLOUDY_DAY"
        return "CLOUDY"

    def clear_cache(self) -> None:
        """清空本地缓存."""
        self._cache.clear()
        logger.info("天气数据缓存已清空")

    @property
    def source(self) -> str:
        """当前数据源标识."""
        return self._source


# ============================================================
# 便捷函数
# ============================================================

_adapter_instance: WeatherDataAdapter | None = None


def get_adapter() -> WeatherDataAdapter:
    """获取全局 WeatherDataAdapter 实例 (单例)."""
    global _adapter_instance
    if _adapter_instance is None:
        _adapter_instance = WeatherDataAdapter()
    return _adapter_instance
