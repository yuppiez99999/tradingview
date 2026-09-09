"""气象因子计算引擎 — 28 系统集成层 (v8.6.14)

核心功能:
    基于 weather_data_adapter 获取的气象数据 + weather_symbols_mapping.yaml
    的标的映射配置, 计算每个标的/板块的气象因子得分, 输出交易信号.

因子体系:
    1. 温度因子 (TemperatureFactor) — 极端温度对能源/农业/医药的影响
    2. 降水因子 (PrecipitationFactor) — 干旱/洪涝对农业/水电/采矿的影响
    3. 风速因子 (WindFactor) — 强风对风电/运输/采矿的影响
    4. 辐照因子 (IrradianceFactor) — 日照对光伏/农业/电力的影响
    5. 气压因子 (PressureFactor) — 高压/低压系统对能源/医药的影响
    6. 空气质量因子 (AirQualityFactor) — AQI 对制造/医药的影响
    7. 综合气象因子 (CompositeWeatherFactor) — 加权汇总

信号等级:
    STRONG_BULL (+2.0)  气象条件极度利好
    BULLISH     (+1.0)  气象条件利好
    NEUTRAL     ( 0.0)  气象条件中性
    BEARISH     (-1.0)  气象条件利空
    STRONG_BEAR (-2.0)  气象条件极度利空

用法:
    from utils.weather_factor_engine import WeatherFactorEngine

    engine = WeatherFactorEngine()
    results = engine.evaluate_all()           # 评估所有标的
    signal = engine.evaluate_symbol("600900.SH")  # 评估单个标的
    sector_signals = engine.evaluate_sector("power_energy")  # 板块评估

作者: 28 系统 PM
日期: 2026-08-01
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, cast

logger = logging.getLogger("weather_factor")

# ============================================================
# 配置加载
# ============================================================

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "weather_symbols_mapping.yaml",
)

_stocks_cache: list[dict[str, Any]] | None = None
_etfs_cache: list[dict[str, Any]] | None = None
_futures_cache: list[dict[str, Any]] | None = None
_sectors_cache: dict[str, Any] | None = None
_factor_config: dict[str, Any] | None = None


def _load_yaml(path: str) -> dict | None:
    """加载 YAML 配置文件."""
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            # yaml.safe_load 无 stub 返回 Any, cast 收窄为 dict
            return cast("dict[str, Any] | None", yaml.safe_load(f))
    except ImportError:
        try:
            # 备选: 简单解析 (不支持嵌套 YAML)
            logger.warning("PyYAML 不可用, 尝试 JSON 解析")
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
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
        logger.error("加载 YAML 配置失败: %s", e)
    return None


def _ensure_config() -> bool:
    """确保配置已加载 (懒加载)."""
    global _stocks_cache, _etfs_cache, _futures_cache, _sectors_cache, _factor_config
    if _stocks_cache is not None:
        return True

    cfg = _load_yaml(_CONFIG_PATH)
    if cfg is None:
        logger.warning("weather_symbols_mapping.yaml 加载失败, 使用默认配置")
        _stocks_cache = []
        _etfs_cache = []
        _futures_cache = []
        _sectors_cache = {}
        _factor_config = {}
        return False

    _stocks_cache = cfg.get("stocks", []) or []
    _etfs_cache = cfg.get("etfs", []) or []
    _futures_cache = cfg.get("futures", []) or []
    _sectors_cache = cfg.get("sector_aggregates", {}) or {}
    _factor_config = cfg.get("factor_config", {}) or {}
    logger.info(
        "气象映射配置加载: %d 股票, %d ETF, %d 期货, %d 板块",
        len(_stocks_cache),
        len(_etfs_cache),
        len(_futures_cache),
        len(_sectors_cache),
    )
    return True


# ============================================================
# 因子计算数据类
# ============================================================


@dataclass
class FactorScore:
    """单个因子得分"""

    name: str = ""
    value: float = 0.0  # 原始值
    score: float = 0.0  # 标准化得分 (-1.0 ~ +1.0)
    weight: float = 1.0  # 权重
    contribution: float = 0.0  # 加权贡献 = score * weight
    description: str = ""  # 描述

    def __post_init__(self):
        self.contribution = self.score * self.weight


@dataclass
class WeatherFactorResult:
    """气象因子综合评估结果"""

    symbol: str = ""
    name: str = ""
    category: str = ""
    weather_sensitivity: float = 0.0
    composite_score: float = 0.0  # 综合得分 (-2.0 ~ +2.0)
    signal: str = "NEUTRAL"  # STRONG_BULL/BULLISH/NEUTRAL/BEARISH/STRONG_BEAR
    confidence: float = 0.0  # 置信度 (0-1)
    factors: list[FactorScore] = field(default_factory=list)
    key_drivers: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    data_source: str = ""
    timestamp: float = 0.0
    reasoning: str = ""

    @property
    def signal_numeric(self) -> float:
        return self.composite_score


@dataclass
class SectorWeatherResult:
    """板块级气象评估"""

    sector: str = ""
    name: str = ""
    composite_score: float = 0.0
    signal: str = "NEUTRAL"
    symbol_results: list[WeatherFactorResult] = field(default_factory=list)
    summary: str = ""


# ============================================================
# 因子计算引擎
# ============================================================


class WeatherFactorEngine:
    """气象因子计算引擎.

    计算流程:
        1. 加载标的映射配置 (weather_symbols_mapping.yaml)
        2. 对每个标的, 拉取关联地理坐标的天气数据
        3. 计算各因子得分 (温度/降水/风速/辐照/气压/AQI)
        4. 按行业类别加权汇总
        5. 输出信号 (STRONG_BULL → STRONG_BEAR)
    """

    SIGNAL_STRONG_BULL = "STRONG_BULL"
    SIGNAL_BULLISH = "BULLISH"
    SIGNAL_NEUTRAL = "NEUTRAL"
    SIGNAL_BEARISH = "BEARISH"
    SIGNAL_STRONG_BEAR = "STRONG_BEAR"

    def __init__(self, adapter: Any | None = None) -> None:
        _ensure_config()
        self._adapter: Any | None = adapter
        self._available: bool | None = None
        self._factor_cfg: dict[str, Any] = _factor_config or {}

    # ----------------------------------------------------------
    # 懒加载适配器
    # ----------------------------------------------------------

    @property
    def adapter(self):
        if self._adapter is None:
            try:
                from utils.weather_data_adapter import get_adapter

                self._adapter = get_adapter()
            except ImportError:
                logger.warning("WeatherDataAdapter 不可用")
                self._adapter = None
        return self._adapter

    @property
    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        if self.adapter is None:
            self._available = False
        else:
            # 即使 apizero 被限流, 只要 adapter 本身可实例化,
            # 就可以通过 Open-Meteo 降级链获取数据, 标记为可用
            self._available = True
        return self._available

    # ----------------------------------------------------------
    # 核心评估方法
    # ----------------------------------------------------------

    def evaluate_all(self) -> list[WeatherFactorResult]:
        """评估所有标的 (股票 + ETF + 期货)."""
        results: list[WeatherFactorResult] = []

        for item in _stocks_cache or []:
            result = self.evaluate_symbol(item.get("symbol", ""), item)
            if result:
                results.append(result)

        for item in _etfs_cache or []:
            result = self.evaluate_symbol(item.get("symbol", ""), item)
            if result:
                results.append(result)

        for item in _futures_cache or []:
            result = self.evaluate_symbol(item.get("symbol", ""), item)
            if result:
                results.append(result)

        results.sort(key=lambda r: abs(r.composite_score), reverse=True)
        logger.info(
            "气象因子评估完成: %d 个标的, 平均得分=%.3f",
            len(results),
            sum(r.composite_score for r in results) / max(len(results), 1),
        )
        return results

    def evaluate_symbol(
        self, symbol: str, meta: dict | None = None
    ) -> WeatherFactorResult | None:
        """评估单个标的的气象因子.

        Args:
            symbol: 标的代码 (如 "600900.SH")
            meta: 标的元数据 (可选, 从缓存查找如未提供)

        Returns:
            WeatherFactorResult 或 None (无数据)
        """
        if meta is None:
            meta = self._find_meta(symbol)
        if meta is None:
            logger.debug("标的 %s 未在映射配置中找到", symbol)
            return None

        if not self.is_available:
            return self._neutral_result(meta, "天气数据不可用")

        locations = meta.get("locations", [])
        if not locations:
            return self._neutral_result(meta, "无地理坐标映射")

        # 加权获取多地点天气
        weather_data = self._get_weighted_weather(locations)
        if weather_data is None:
            return self._neutral_result(meta, "天气数据获取失败")

        # 计算因子
        category = meta.get("category", "manufacturing")
        factors = self._compute_factors(weather_data, category, meta)

        # 综合得分
        composite = self._compute_composite_score(factors, meta)

        # 信号判定
        signal = self._score_to_signal(composite)

        # 置信度
        confidence = self._compute_confidence(factors, meta, weather_data)

        # 关键驱动因子
        key_drivers = sorted(
            [f for f in factors if abs(f.score) > 0.3],
            key=lambda f: abs(f.contribution),
            reverse=True,
        )
        driver_names = [f"{f.name}({f.score:+.2f})" for f in key_drivers[:3]]

        # 预警
        alerts = self._check_alerts(weather_data, meta)

        return WeatherFactorResult(
            symbol=symbol,
            name=meta.get("name", symbol),
            category=category,
            weather_sensitivity=meta.get("weather_sensitivity", 0.3),
            composite_score=max(-2.0, min(2.0, composite)),
            signal=signal,
            confidence=confidence,
            factors=factors,
            key_drivers=driver_names,
            alerts=alerts,
            data_source=self.adapter.source if self.adapter else "unknown",
            timestamp=weather_data.get("timestamp", 0),
            reasoning=self._generate_reasoning(meta, factors, composite, signal),
        )

    def evaluate_sector(self, sector: str) -> SectorWeatherResult | None:
        """评估板块级气象因子."""
        _ensure_config()
        sector_cfg = (_sectors_cache or {}).get(sector)
        if not sector_cfg:
            logger.warning("板块 %s 未在配置中定义", sector)
            return None

        locations = sector_cfg.get("locations", [])
        if not self.is_available or not locations:
            return SectorWeatherResult(
                sector=sector,
                name=sector_cfg.get("name", sector),
                composite_score=0.0,
                signal=self.SIGNAL_NEUTRAL,
                summary="天气数据不可用",
            )

        weather = self._get_weighted_weather(locations)
        if weather is None:
            return SectorWeatherResult(
                sector=sector,
                name=sector_cfg.get("name", sector),
                composite_score=0.0,
                signal=self.SIGNAL_NEUTRAL,
                summary="天气数据获取失败",
            )

        # 计算板块级因子
        factors = self._compute_factors(weather, sector, {"weather_sensitivity": 0.7})
        composite = self._compute_composite_score(factors, {"weather_sensitivity": 0.7})
        signal = self._score_to_signal(composite)

        # 收集该板块下的标的结果
        symbol_results = []
        for item in _stocks_cache or []:
            if item.get("category") == sector:
                r = self.evaluate_symbol(item.get("symbol", ""), item)
                if r:
                    symbol_results.append(r)

        return SectorWeatherResult(
            sector=sector,
            name=sector_cfg.get("name", sector),
            composite_score=max(-2.0, min(2.0, composite)),
            signal=signal,
            symbol_results=symbol_results,
            summary=self._generate_reasoning(
                {"name": sector_cfg.get("name", sector)}, factors, composite, signal
            ),
        )

    # ----------------------------------------------------------
    # 天气数据获取
    # ----------------------------------------------------------

    def _get_weighted_weather(
        self, locations: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """获取多地点加权天气数据."""
        if not self.adapter:
            return None

        # 计算加权平均
        total_weight = sum(loc.get("weight", 1.0) for loc in locations)
        if total_weight <= 0:
            total_weight = len(locations)

        weighted: dict[str, Any] = {
            "temperature": 0.0,
            "apparent_temperature": 0.0,
            "humidity": 0.0,
            "cloudrate": 0.0,
            "visibility": 0.0,
            "dswrf": 0.0,
            "wind_speed": 0.0,
            "wind_direction": 0.0,
            "pressure": 0.0,
            "precipitation_local": 0.0,
            "precipitation_nearest": 0.0,
            "aqi_chn": 0.0,
            "pm25": 0.0,
            "count": 0.0,
        }

        hourly_temps: list[float] = []
        hourly_precips: list[float] = []
        daily_max_temps: list[float] = []
        daily_min_temps: list[float] = []
        daily_precips: list[float] = []
        daily_wind_speeds: list[float] = []
        all_alerts: list[dict] = []

        for loc in locations:
            lon = loc.get("lon", 0)
            lat = loc.get("lat", 0)
            w = loc.get("weight", 1.0) / total_weight

            try:
                snap = self.adapter.get_realtime(lon, lat)
                if snap.status == "no_data":
                    continue

                weighted["temperature"] += snap.temperature * w
                weighted["apparent_temperature"] += snap.apparent_temperature * w
                weighted["humidity"] += snap.humidity * w
                weighted["cloudrate"] += snap.cloudrate * w
                weighted["visibility"] += snap.visibility * w
                weighted["dswrf"] += snap.dswrf * w
                weighted["wind_speed"] += snap.wind_speed * w
                weighted["wind_direction"] += snap.wind_direction * w
                weighted["pressure"] += snap.pressure * w
                weighted["precipitation_local"] += snap.precipitation_local * w
                weighted["precipitation_nearest"] += snap.precipitation_nearest * w
                weighted["aqi_chn"] += snap.aqi_chn * w
                weighted["pm25"] += snap.pm25 * w
                weighted["count"] += w

                # 获取小时预报 (72h)
                hourly = self.adapter.get_hourly(lon, lat, hours=72)
                for h in hourly:
                    hourly_temps.append(h.temperature)
                    hourly_precips.append(h.precipitation)

                # 获取天预报 (7d)
                daily = self.adapter.get_daily(lon, lat, days=7)
                for d in daily:
                    daily_max_temps.append(d.temp_max)
                    daily_min_temps.append(d.temp_min)
                    daily_precips.append(d.precip_max)
                    daily_wind_speeds.append(d.wind_max_speed)

                # 获取预警
                alerts = self.adapter.get_alerts(lon, lat)
                all_alerts.extend(alerts or [])

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
                logger.warning("获取 %s 天气异常: %s", loc.get("name"), e)
                continue

        if weighted["count"] < 0.3:
            return None

        # 聚合附加数据
        weighted["hourly_temp_mean"] = self._mean(hourly_temps)
        weighted["hourly_temp_std"] = self._std(hourly_temps)
        weighted["hourly_precip_max"] = max(hourly_precips) if hourly_precips else 0.0
        weighted["daily_max_temp"] = (
            max(daily_max_temps) if daily_max_temps else weighted["temperature"]
        )
        weighted["daily_min_temp"] = (
            min(daily_min_temps) if daily_min_temps else weighted["temperature"]
        )
        weighted["daily_precip_sum"] = sum(daily_precips) if daily_precips else 0.0
        weighted["daily_wind_max"] = (
            max(daily_wind_speeds) if daily_wind_speeds else weighted["wind_speed"]
        )
        weighted["alerts"] = all_alerts
        weighted["timestamp"] = self._now_ts()

        return weighted

    # ----------------------------------------------------------
    # 因子计算
    # ----------------------------------------------------------

    def _compute_factors(
        self,
        weather: dict[str, Any],
        category: str,
        meta: dict[str, Any],
    ) -> list[FactorScore]:
        """计算所有因子得分."""
        factors: list[FactorScore] = []

        # 温度因子
        factors.append(self._calc_temperature_factor(weather, category))

        # 降水因子
        factors.append(self._calc_precipitation_factor(weather, category))

        # 风速因子
        factors.append(self._calc_wind_factor(weather, category))

        # 辐照因子
        factors.append(self._calc_dswrf_factor(weather, category))

        # 气压因子
        factors.append(self._calc_pressure_factor(weather, category))

        # 空气质量因子
        factors.append(self._calc_aqi_factor(weather, category))

        # 能见度因子
        factors.append(self._calc_visibility_factor(weather, category))

        return factors

    def _calc_temperature_factor(self, weather: dict, category: str) -> FactorScore:
        """温度因子.

        逻辑:
        - 电力: 高温→空调需求→利好; 低温→供暖需求→利好; 最适温度中性
        - 矿业: 极端温度→停工影响→利空
        - 农业: 适宜温度→利好; 极端温度→利空
        - 医药: 极端温度→疾病高发→利好 (冬季流感/夏季中暑)
        - 制造: 适宜温度→利好生产效率
        """
        temp = weather.get("temperature", 20.0)
        max_temp = weather.get("daily_max_temp", temp)
        min_temp = weather.get("daily_min_temp", temp)
        cfg = self._factor_cfg.get("temperature", {})

        extreme_heat = cfg.get("extreme_heat_threshold", 35.0)
        extreme_cold = cfg.get("extreme_cold_threshold", 0.0)

        weight_map = {
            "power_energy": 1.5,
            "mining": 1.2,
            "smelting": 1.0,
            "pharma": 0.8,
            "agriculture": 1.3,
            "manufacturing": 0.6,
            "financial": 0.2,
            "commodity": 0.8,
        }
        weight = weight_map.get(category, 0.5)

        # 基础分: -1 (极端) → 0 (正常) → +1 (适宜)
        if category == "power_energy":
            # 电力: 两端高负荷利好
            if temp >= extreme_heat:
                score = 0.8  # 高温→制冷需求
                desc = f"高温预警 {temp:.0f}℃ → 空调负荷↑"
            elif temp <= extreme_cold:
                score = 0.8  # 低温→供暖需求
                desc = f"低温预警 {temp:.0f}℃ → 供暖负荷↑"
            elif 18 <= temp <= 28:
                score = 0.3  # 温和
                desc = f"温度适宜 {temp:.0f}℃"
            else:
                score = -0.2
                desc = f"温度偏离负荷中心 {temp:.0f}℃"
        elif category == "mining":
            if temp >= extreme_heat:
                score = -0.6
                desc = f"高温 {temp:.0f}℃ → 采矿作业受限"
            elif temp <= extreme_cold:
                score = -0.4
                desc = f"低温 {temp:.0f}℃ → 采矿效率降低"
            else:
                score = 0.2
                desc = f"温度适宜采矿 {temp:.0f}℃"
        elif category == "agriculture":
            if max_temp >= extreme_heat:
                score = -0.5
                desc = f"高温热害 {max_temp:.0f}℃ → 农业受损"
            elif min_temp <= extreme_cold:
                score = -0.6
                desc = f"低温冻害 {min_temp:.0f}℃ → 作物受损"
            elif 15 <= temp <= 28:
                score = 0.6
                desc = f"温度适宜生长 {temp:.0f}℃"
            else:
                score = 0.0
                desc = f"温度一般 {temp:.0f}℃"
        elif category == "pharma":
            if temp >= 33:
                score = 0.5
                desc = f"高温 {temp:.0f}℃ → 夏季用药需求↑"
            elif temp <= 5:
                score = 0.6
                desc = f"低温 {temp:.0f}℃ → 冬季流感用药↑"
            else:
                score = 0.0
                desc = f"温度中性 {temp:.0f}℃"
        else:
            if 18 <= temp <= 28:
                score = 0.3
                desc = f"温度适宜生产 {temp:.0f}℃"
            elif temp >= extreme_heat or temp <= extreme_cold:
                score = -0.3
                desc = f"温度极值 {temp:.0f}℃ → 效率降低"
            else:
                score = 0.0
                desc = f"温度中性 {temp:.0f}℃"

        return FactorScore(
            name="temperature",
            value=temp,
            score=score,
            weight=weight,
            description=desc,
        )

    def _calc_precipitation_factor(self, weather: dict, category: str) -> FactorScore:
        """降水因子.

        逻辑:
        - 水电: 降水→水库蓄水→利好
        - 采矿: 暴雨→安全隐患→利空; 干旱→采矿顺利→利好
        - 农业: 适度降水→利好; 暴雨/干旱→利空
        - 冶炼: 降水影响小
        """
        precip = weather.get("precipitation_local", 0.0)
        precip_sum = weather.get("daily_precip_sum", 0.0)
        max_precip = weather.get("hourly_precip_max", 0.0)
        cfg = self._factor_cfg.get("precipitation", {})

        heavy = cfg.get("heavy_rain_threshold", 50.0)
        moderate = cfg.get("moderate_rain_threshold", 25.0)

        weight_map = {
            "power_energy": 1.3,
            "mining": 1.4,
            "agriculture": 1.5,
            "smelting": 0.6,
            "pharma": 0.4,
            "manufacturing": 0.3,
            "financial": 0.1,
            "commodity": 1.0,
        }
        weight = weight_map.get(category, 0.5)

        if category == "power_energy":
            if precip_sum > 100:
                score = 0.7
                desc = f"丰沛降水 {precip_sum:.0f}mm → 水电蓄水↑"
            elif precip_sum > 30:
                score = 0.4
                desc = f"适度降水 {precip_sum:.0f}mm → 水电蓄水改善"
            elif precip < 0.5:
                score = -0.3
                desc = "降水偏少 → 水电蓄水压力"
            else:
                score = 0.1
                desc = "降水正常"
        elif category == "mining":
            if max_precip >= heavy:
                score = -0.8
                desc = f"暴雨红色预警 {max_precip:.0f}mm → 采矿安全!"
            elif max_precip >= moderate:
                score = -0.4
                desc = f"中到大雨 {max_precip:.0f}mm → 采矿受限"
            elif precip < 0.5:
                score = 0.3
                desc = "无雨 → 采矿作业顺利"
            else:
                score = 0.0
                desc = "降水中性"
        elif category == "agriculture":
            if precip_sum > 150:
                score = -0.6
                desc = f"洪涝 {precip_sum:.0f}mm → 农业受灾"
            elif precip_sum > 30:
                score = 0.5
                desc = f"适宜降水 {precip_sum:.0f}mm → 作物生长"
            elif precip < 0.1:
                score = -0.5
                desc = "干旱 → 作物受损"
            else:
                score = 0.2
                desc = "降水正常"
        else:
            if max_precip >= heavy:
                score = -0.3
                desc = "暴雨影响运输/作业"
            else:
                score = 0.0
                desc = "降水中性"

        return FactorScore(
            name="precipitation",
            value=max(precip, precip_sum / 7.0),
            score=score,
            weight=weight,
            description=desc,
        )

    def _calc_wind_factor(self, weather: dict, category: str) -> FactorScore:
        """风速因子.

        逻辑:
        - 风电: 强风→利好; 微风→利空
        - 采矿: 强风→安全隐患
        - 航运: 强风→影响运输
        """
        wind = weather.get("wind_speed", 0.0)
        max_wind = weather.get("daily_wind_max", wind)
        cfg = self._factor_cfg.get("wind", {})

        strong = cfg.get("strong_wind_threshold", 17.2)
        extreme = cfg.get("extreme_wind_threshold", 24.5)

        weight_map = {
            "power_energy": 1.4,
            "mining": 0.8,
            "manufacturing": 0.4,
            "smelting": 0.3,
            "agriculture": 0.5,
            "financial": 0.1,
            "commodity": 0.6,
        }
        weight = weight_map.get(category, 0.5)

        if category == "power_energy":
            if max_wind >= 12:
                score = 0.6
                desc = f"风力较强 {max_wind:.1f}m/s → 风电出力↑"
            elif max_wind >= 5:
                score = 0.3
                desc = f"风力适宜 {max_wind:.1f}m/s"
            elif max_wind < 2:
                score = -0.3
                desc = f"静风 {max_wind:.1f}m/s → 风电出力↓"
            else:
                score = 0.0
                desc = "风力一般"
        elif category == "mining":
            if max_wind >= strong:
                score = -0.6
                desc = f"大风 {max_wind:.1f}m/s → 露天矿安全!"
            else:
                score = 0.1
                desc = "风力适宜"
        else:
            if max_wind >= extreme:
                score = -0.5
                desc = f"极端大风 {max_wind:.1f}m/s → 影响作业"
            elif max_wind >= strong:
                score = -0.2
                desc = f"较强风 {max_wind:.1f}m/s"
            else:
                score = 0.0
                desc = "风力正常"

        return FactorScore(
            name="wind",
            value=max_wind,
            score=score,
            weight=weight,
            description=desc,
        )

    def _calc_dswrf_factor(self, weather: dict, category: str) -> FactorScore:
        """辐照因子 (Downward Surface Shortwave Flux).

        逻辑:
        - 光伏: 高辐照→利好; 低辐照→利空
        - 农业: 充足日照→利好
        """
        dswrf = weather.get("dswrf", 0.0)
        cfg = self._factor_cfg.get("dswrf", {})

        high = cfg.get("high_irradiance", 800.0)
        low = cfg.get("low_irradiance", 100.0)

        weight_map = {
            "power_energy": 1.2,
            "agriculture": 1.0,
            "manufacturing": 0.2,
            "financial": 0.1,
        }
        weight = weight_map.get(category, 0.3)

        if category == "power_energy":
            if dswrf >= high:
                score = 0.7
                desc = f"强辐照 {dswrf:.0f}W/m² → 光伏出力↑"
            elif dswrf >= 400:
                score = 0.4
                desc = f"充足日照 {dswrf:.0f}W/m²"
            elif dswrf < low:
                score = -0.5
                desc = f"弱辐照 {dswrf:.0f}W/m² → 光伏出力↓"
            else:
                score = 0.1
                desc = "日照正常"
        elif category == "agriculture":
            if dswrf >= 400:
                score = 0.3
                desc = "充足日照 → 光合作用↑"
            else:
                score = 0.0
                desc = "日照一般"
        else:
            score = 0.0
            desc = "辐照中性"

        return FactorScore(
            name="dswrf",
            value=dswrf,
            score=score,
            weight=weight,
            description=desc,
        )

    def _calc_pressure_factor(self, weather: dict, category: str) -> FactorScore:
        """气压因子."""
        press = weather.get("pressure", 101325.0)
        press_hpa = press / 100.0  # Pa → hPa

        cfg = self._factor_cfg.get("pressure", {})
        high = cfg.get("high_pressure", 1020.0)
        low = cfg.get("low_pressure", 1000.0)

        weight_map = {"power_energy": 0.6, "pharma": 0.4, "manufacturing": 0.3}
        weight = weight_map.get(category, 0.2)

        if press_hpa >= high:
            score = 0.2
            desc = f"高压 {press_hpa:.0f}hPa → 天气稳定"
        elif press_hpa <= low:
            score = -0.2
            desc = f"低压 {press_hpa:.0f}hPa → 天气多变"
        else:
            score = 0.0
            desc = f"气压正常 {press_hpa:.0f}hPa"

        return FactorScore(
            name="pressure",
            value=press_hpa,
            score=score,
            weight=weight,
            description=desc,
        )

    def _calc_aqi_factor(self, weather: dict, category: str) -> FactorScore:
        """空气质量因子."""
        aqi = weather.get("aqi_chn", 50)
        cfg = self._factor_cfg.get("aqi", {})

        good = cfg.get("good_threshold", 50)
        moderate = cfg.get("moderate_threshold", 100)
        heavy = cfg.get("heavy_pollution", 200)

        weight_map = {
            "manufacturing": 0.8,
            "pharma": 1.0,
            "power_energy": 0.6,
            "mining": 0.5,
            "financial": 0.2,
        }
        weight = weight_map.get(category, 0.4)

        if aqi <= good:
            score = 0.3
            desc = f"空气质量优 AQI={aqi}"
        elif aqi <= moderate:
            score = 0.0
            desc = f"空气质量良 AQI={aqi}"
        elif aqi <= heavy:
            score = -0.4
            desc = f"轻度污染 AQI={aqi} → 生产受限"
        else:
            score = -0.7
            desc = f"重度污染 AQI={aqi} → 停工!"

        return FactorScore(
            name="aqi",
            value=aqi,
            score=score,
            weight=weight,
            description=desc,
        )

    def _calc_visibility_factor(self, weather: dict, category: str) -> FactorScore:
        """能见度因子."""
        vis = weather.get("visibility", 20.0)
        cfg = self._factor_cfg.get("visibility", {})
        low = cfg.get("low_visibility", 2.0)

        weight_map = {"mining": 1.0, "manufacturing": 0.6, "transport": 0.8}
        weight = weight_map.get(category, 0.3)

        if vis < low:
            score = -0.5
            desc = f"低能见度 {vis:.1f}km → 作业受限"
        elif vis < 5:
            score = -0.2
            desc = f"能见度偏低 {vis:.1f}km"
        else:
            score = 0.1
            desc = f"能见度良好 {vis:.1f}km"

        return FactorScore(
            name="visibility",
            value=vis,
            score=score,
            weight=weight,
            description=desc,
        )

    # ----------------------------------------------------------
    # 综合评分与信号
    # ----------------------------------------------------------

    def _compute_composite_score(
        self, factors: list[FactorScore], meta: dict[str, Any]
    ) -> float:
        """计算综合得分 (-2.0 ~ +2.0)."""
        # meta 值类型 Any: float() 收窄, 消除 no-any-return
        sensitivity = float(meta.get("weather_sensitivity", 0.3))

        # 加权平均
        total_contribution = sum(f.contribution for f in factors)
        total_weight = sum(f.weight for f in factors)

        if total_weight == 0:
            return 0.0

        raw_score = total_contribution / total_weight

        # 乘以敏感度
        adjusted = raw_score * sensitivity * 3.0

        # 裁剪到 [-2.0, +2.0]
        return max(-2.0, min(2.0, adjusted))

    @staticmethod
    def _score_to_signal(score: float) -> str:
        """将得分映射到信号等级."""
        if score >= 1.2:
            return WeatherFactorEngine.SIGNAL_STRONG_BULL
        if score >= 0.4:
            return WeatherFactorEngine.SIGNAL_BULLISH
        if score <= -1.2:
            return WeatherFactorEngine.SIGNAL_STRONG_BEAR
        if score <= -0.4:
            return WeatherFactorEngine.SIGNAL_BEARISH
        return WeatherFactorEngine.SIGNAL_NEUTRAL

    def _compute_confidence(
        self, factors: list[FactorScore], meta: dict, weather: dict
    ) -> float:
        """计算置信度 (0-1)."""
        non_zero = [f for f in factors if abs(f.score) > 0.05]
        if not non_zero:
            return 0.3

        # 因子一致性
        avg_score = sum(f.score for f in non_zero) / len(non_zero)
        variance = sum((f.score - avg_score) ** 2 for f in non_zero) / len(non_zero)
        consistency = max(0.0, 1.0 - math.sqrt(variance))

        # 数据完整性: weather.get 值类型 Any, float() 收窄
        data_count = float(weather.get("count", 0))
        completeness = min(1.0, data_count)

        # 敏感度
        sensitivity = float(meta.get("weather_sensitivity", 0.3))

        return min(1.0, consistency * 0.5 + completeness * 0.3 + sensitivity * 0.2)

    def _check_alerts(self, weather: dict, meta: dict) -> list[str]:
        """检查气象预警."""
        alerts: list[str] = []
        temp = weather.get("temperature", 20.0)
        aqi = weather.get("aqi_chn", 50)
        wind = weather.get("wind_speed", 3.0)

        if temp >= 38:
            alerts.append(f"高温预警: {temp:.0f}℃")
        if temp <= -5:
            alerts.append(f"寒潮预警: {temp:.0f}℃")
        if aqi >= 200:
            alerts.append(f"重度污染: AQI={aqi}")
        if wind >= 17.2:
            alerts.append(f"大风预警: {wind:.1f}m/s")

        raw_alerts = weather.get("alerts", [])
        if raw_alerts:
            for alert in raw_alerts:
                if isinstance(alert, dict):
                    title = alert.get("title", "") or alert.get("description", "")
                    if title:
                        alerts.append(title[:50])

        return alerts[:5]

    def _generate_reasoning(
        self,
        meta: dict,
        factors: list[FactorScore],
        composite: float,
        signal: str,
    ) -> str:
        """生成人类可读的推理解释."""
        symbol_name = meta.get("name", "")
        category = meta.get("category", "")
        sens = meta.get("weather_sensitivity", 0.3)

        strong = [f for f in factors if abs(f.score) > 0.3]
        if not strong:
            return f"{symbol_name}({category}): 气象条件中性, 无显著驱动因子"

        drivers = "; ".join(
            f"{f.name}={f.score:+.2f}({f.description[:30]})" for f in strong[:3]
        )

        direction = "利好" if composite > 0 else ("利空" if composite < 0 else "中性")

        return (
            f"{symbol_name}({category}): {direction} "
            f"[得分={composite:+.2f}, 敏感度={sens:.0%}]. "
            f"主要驱动: {drivers}"
        )

    def _find_meta(self, symbol: str) -> dict[str, Any] | None:
        """在缓存中查找标的元数据."""
        for list_name in ("_stocks_cache", "_etfs_cache", "_futures_cache"):
            lst = globals().get(list_name, []) or []
            for item in lst:
                if item.get("symbol") == symbol:
                    return cast("dict[str, Any]", item)
        return None

    def _neutral_result(self, meta: dict, reason: str) -> WeatherFactorResult:
        """返回中性结果 (降级)."""
        return WeatherFactorResult(
            symbol=meta.get("symbol", ""),
            name=meta.get("name", ""),
            category=meta.get("category", ""),
            weather_sensitivity=meta.get("weather_sensitivity", 0.3),
            composite_score=0.0,
            signal=self.SIGNAL_NEUTRAL,
            confidence=0.2,
            reasoning=f"[降级] {reason}",
            data_source="none",
        )

    # ----------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------

    @staticmethod
    def _mean(values: list[float]) -> float:
        if not values:
            return 0.0
        return sum(values) / len(values)

    @staticmethod
    def _std(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        m = WeatherFactorEngine._mean(values)
        variance = sum((x - m) ** 2 for x in values) / len(values)
        return math.sqrt(variance)

    @staticmethod
    def _now_ts() -> float:
        import time as _t

        return _t.time()


# ============================================================
# 便捷函数
# ============================================================

_engine_instance: WeatherFactorEngine | None = None


def get_engine() -> WeatherFactorEngine:
    """获取全局 WeatherFactorEngine 实例 (单例)."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = WeatherFactorEngine()
    return _engine_instance
