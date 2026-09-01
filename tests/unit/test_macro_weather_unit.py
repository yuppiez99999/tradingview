"""macro_weather 单元测试 (Wave 12-A #2).

被测模块: utils/macro_weather.py
验收门禁: 免费无需key / 温度降水ENSO可查 / 单测覆盖 / 降级不崩溃
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.macro_weather import MacroWeatherFetcher  # noqa: E402


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests import HTTPError

        resp.raise_for_status.side_effect = HTTPError(response=resp)
    return resp


class TestGetHistoricalWeather:
    """get_historical_weather 方法测试."""

    @patch("utils.macro_weather.requests.get")
    def test_success(self, mock_get):
        mock_get.return_value = _mock_response({"daily": {"time": ["2026-01-01"], "temperature_2m_mean": [25.0]}})
        f = MacroWeatherFetcher()
        result = f.get_historical_weather(30.0, 120.0, "2026-01-01", "2026-01-02")
        assert "daily" in result
        mock_get.assert_called_once()

    @patch("utils.macro_weather.requests.get")
    def test_default_daily_vars(self, mock_get):
        mock_get.return_value = _mock_response({"daily": {}})
        f = MacroWeatherFetcher()
        f.get_historical_weather(30.0, 120.0, "2026-01-01", "2026-01-02")
        call_params = mock_get.call_args[1]["params"]
        assert "temperature_2m_mean" in call_params["daily"]
        assert "precipitation_sum" in call_params["daily"]

    @patch("utils.macro_weather.requests.get")
    def test_custom_daily_vars(self, mock_get):
        mock_get.return_value = _mock_response({"daily": {}})
        f = MacroWeatherFetcher()
        f.get_historical_weather(30.0, 120.0, "2026-01-01", "2026-01-02", ["wind_speed_10m_max"])
        call_params = mock_get.call_args[1]["params"]
        assert call_params["daily"] == "wind_speed_10m_max"


class TestGetTemperature:
    """get_temperature 方法测试."""

    @patch("utils.macro_weather.requests.get")
    def test_success_structure(self, mock_get):
        mock_get.return_value = _mock_response(
            {
                "daily": {
                    "time": ["2026-01-01", "2026-01-02"],
                    "temperature_2m_mean": [25.0, 26.0],
                },
                "daily_units": {"temperature_2m_mean": "°C"},
            }
        )
        f = MacroWeatherFetcher()
        result = f.get_temperature(30.0, 120.0, "2026-01-01", "2026-01-02")
        assert result["dates"] == ["2026-01-01", "2026-01-02"]
        assert result["temperatures"] == [25.0, 26.0]
        assert result["unit"] == "°C"
        assert result["location"]["latitude"] == 30.0

    @patch("utils.macro_weather.requests.get")
    def test_error_propagation(self, mock_get):
        from requests import ConnectionError

        mock_get.side_effect = ConnectionError("network down")
        f = MacroWeatherFetcher()
        result = f.get_temperature(30.0, 120.0, "2026-01-01", "2026-01-02")
        assert "error" in result


class TestGetPrecipitation:
    """get_precipitation 方法测试."""

    @patch("utils.macro_weather.requests.get")
    def test_success_structure(self, mock_get):
        mock_get.return_value = _mock_response(
            {
                "daily": {
                    "time": ["2026-01-01"],
                    "precipitation_sum": [5.0],
                },
                "daily_units": {"precipitation_sum": "mm"},
            }
        )
        f = MacroWeatherFetcher()
        result = f.get_precipitation(30.0, 120.0, "2026-01-01", "2026-01-02")
        assert result["dates"] == ["2026-01-01"]
        assert result["precipitations"] == [5.0]
        assert result["unit"] == "mm"

    @patch("utils.macro_weather.requests.get")
    def test_error_propagation(self, mock_get):
        from requests import Timeout

        mock_get.side_effect = Timeout("timeout")
        f = MacroWeatherFetcher()
        result = f.get_precipitation(30.0, 120.0, "2026-01-01", "2026-01-02")
        assert "error" in result


class TestGetEnsoIndicator:
    """get_enso_indicator 方法测试."""

    @patch("utils.macro_weather.requests.get")
    def test_success_structure(self, mock_get):
        mock_get.return_value = _mock_response(
            {
                "daily": {
                    "time": ["2026-01-01", "2026-01-02", "2026-01-03"],
                    "temperature_2m_mean": [27.0, 27.5, 28.0],
                },
                "daily_units": {"temperature_2m_mean": "°C"},
            }
        )
        f = MacroWeatherFetcher()
        result = f.get_enso_indicator(lookback_days=3)
        assert "anomaly" in result
        assert "phase" in result
        assert "locations" in result
        assert result["phase"] in (
            "厄尔尼诺 (El Niño)",
            "拉尼娜 (La Niña)",
            "中性 (Neutral)",
        )

    @patch("utils.macro_weather.requests.get")
    def test_all_locations_failed(self, mock_get):
        from requests import ConnectionError

        mock_get.side_effect = ConnectionError("network down")
        f = MacroWeatherFetcher()
        result = f.get_enso_indicator()
        assert "error" in result


class TestGetCommodityWeatherSignals:
    """get_commodity_weather_signals 方法测试."""

    @patch("utils.macro_weather.requests.get")
    def test_returns_list(self, mock_get):
        mock_get.return_value = _mock_response(
            {
                "daily": {
                    "time": ["2026-01-01"],
                    "temperature_2m_mean": [25.0],
                    "precipitation_sum": [10.0],
                },
                "daily_units": {
                    "temperature_2m_mean": "°C",
                    "precipitation_sum": "mm",
                },
            }
        )
        f = MacroWeatherFetcher()
        result = f.get_commodity_weather_signals()
        assert isinstance(result, list)
        assert len(result) == 6

    @patch("utils.macro_weather.requests.get")
    def test_signal_structure(self, mock_get):
        mock_get.return_value = _mock_response(
            {
                "daily": {
                    "time": ["2026-01-01"],
                    "temperature_2m_mean": [25.0],
                    "precipitation_sum": [10.0],
                },
                "daily_units": {
                    "temperature_2m_mean": "°C",
                    "precipitation_sum": "mm",
                },
            }
        )
        f = MacroWeatherFetcher()
        result = f.get_commodity_weather_signals()
        for item in result:
            assert "commodity" in item
            assert "location" in item
            assert "temp_avg" in item
            assert "precip_sum" in item
            assert "signal" in item
            assert "risk_level" in item
            assert "status" in item

    @patch("utils.macro_weather.requests.get")
    def test_commodity_names(self, mock_get):
        mock_get.return_value = _mock_response(
            {
                "daily": {
                    "time": ["2026-01-01"],
                    "temperature_2m_mean": [25.0],
                    "precipitation_sum": [10.0],
                },
                "daily_units": {
                    "temperature_2m_mean": "°C",
                    "precipitation_sum": "mm",
                },
            }
        )
        f = MacroWeatherFetcher()
        result = f.get_commodity_weather_signals()
        names = {item["commodity"] for item in result}
        assert names == {"原油", "天然气", "大豆", "玉米", "小麦", "铜"}

    @patch("utils.macro_weather.requests.get")
    def test_network_error_no_crash(self, mock_get):
        from requests import ConnectionError

        mock_get.side_effect = ConnectionError("network down")
        f = MacroWeatherFetcher()
        result = f.get_commodity_weather_signals()
        assert isinstance(result, list)
        for item in result:
            assert item["status"] == "no_data"
            assert item["temp_avg"] is None


class TestClassifyEnsoPhase:
    """_classify_enso_phase 静态方法测试."""

    def test_el_nino(self):
        assert MacroWeatherFetcher._classify_enso_phase(1.0) == "厄尔尼诺 (El Niño)"

    def test_la_nina(self):
        assert MacroWeatherFetcher._classify_enso_phase(-1.0) == "拉尼娜 (La Niña)"

    def test_neutral(self):
        assert MacroWeatherFetcher._classify_enso_phase(0.0) == "中性 (Neutral)"

    def test_boundary_high(self):
        assert MacroWeatherFetcher._classify_enso_phase(0.5) == "中性 (Neutral)"

    def test_boundary_low(self):
        assert MacroWeatherFetcher._classify_enso_phase(-0.5) == "中性 (Neutral)"


class TestAssessCommodityRisk:
    """_assess_commodity_risk 静态方法测试."""

    def test_no_data(self):
        signal, risk = MacroWeatherFetcher._assess_commodity_risk("大豆", None, None)
        assert signal == "数据不足"
        assert risk == "未知"

    def test_agriculture_drought(self):
        signal, risk = MacroWeatherFetcher._assess_commodity_risk("大豆", 25.0, 5.0)
        assert "干旱" in signal
        assert risk == "高"

    def test_agriculture_heat(self):
        signal, risk = MacroWeatherFetcher._assess_commodity_risk("玉米", 35.0, 50.0)
        assert "高温" in signal
        assert risk == "高"

    def test_agriculture_normal(self):
        signal, risk = MacroWeatherFetcher._assess_commodity_risk("小麦", 20.0, 50.0)
        assert "正常" in signal
        assert risk == "低"

    def test_energy_high_temp(self):
        signal, risk = MacroWeatherFetcher._assess_commodity_risk("原油", 35.0, 10.0)
        assert "看多" in signal

    def test_energy_low_temp(self):
        signal, risk = MacroWeatherFetcher._assess_commodity_risk("天然气", -5.0, 10.0)
        assert "看多" in signal

    def test_copper_heavy_rain(self):
        signal, risk = MacroWeatherFetcher._assess_commodity_risk("铜", 20.0, 150.0)
        assert "偏多" in signal


class TestDegradation:
    """降级不崩溃测试."""

    @patch("utils.macro_weather.requests.get")
    def test_timeout_no_crash(self, mock_get):
        from requests import Timeout

        mock_get.side_effect = Timeout("timeout")
        f = MacroWeatherFetcher()
        result = f.get_historical_weather(30.0, 120.0, "2026-01-01", "2026-01-02")
        assert result["error"] == "timeout"

    @patch("utils.macro_weather.requests.get")
    def test_http_error_no_crash(self, mock_get):
        mock_get.return_value = _mock_response({"error": "server error"}, 500)
        f = MacroWeatherFetcher()
        result = f.get_historical_weather(30.0, 120.0, "2026-01-01", "2026-01-02")
        assert result["error"] == "http_error"

    @patch("utils.macro_weather.requests.get")
    def test_parse_error_no_crash(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.side_effect = ValueError("invalid json")
        mock_get.return_value = resp
        f = MacroWeatherFetcher()
        result = f.get_historical_weather(30.0, 120.0, "2026-01-01", "2026-01-02")
        assert result["error"] == "parse_error"
