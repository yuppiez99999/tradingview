"""动力煤基本面 (tools/coal_fundamentals.py) + 接线 单元测试

全部离线: Wind EDB 调用 monkeypatch 打桩, 不发网络。
覆盖: 单次批量取数 / 派生(港口合计, 焦煤焦炭价差) / 陈旧标记 / 顺序 / 当日缓存 /
      fail-open / shim 兼容 / 日报四组接线 (ok 与 按组失败)。
"""

from __future__ import annotations

import sys
from utils.datetime_utils import now_bj
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import coal_fundamentals as cf  # noqa: E402
from tools import coal_port_inventory as cpi  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """隔离缓存目录, 避免真实 data_cache 串扰 (命中旧数据导致用例误判)"""
    monkeypatch.setattr(cf, "DEFAULT_CACHE_DIR", tmp_path)


def _metric(code, dates, values, freq="日", unit="万吨", name="X"):
    return {
        "meta": {
            "code": code,
            "name": name,
            "unit": unit,
            "source": "测试来源",
            "magnitude": "万",
            "updateDate": dates[-1],
            "freq": freq,
        },
        "date": list(dates),
        "value": list(values),
    }


# 真实 code (与 INDICATOR_GROUPS 角色映射一致, 便于派生)
QHD = _metric("S5103725", ["20260908", "20260909"], [595.0, 605.0], name="秦皇岛港")
CFD = _metric("S5118163", ["20260908", "20260909"], [459.0, 452.0], name="曹妃甸港")
BOHAI = _metric(
    "Z8948284", ["20260907", "20260909"], [2344.6, 2347.9], name="环渤海港", unit="万吨"
)
POWER = _metric("Q0149884", ["20260903"], [463.0], freq="周", unit="万吨", name="重点电厂")
REBAR = _metric("S5707798", ["20260908", "20260909"], [3300.0, 3352.0], unit="元/吨", name="螺纹钢")
COKE = _metric("T2987959", ["20260909", "20260910"], [2103.0, 2112.0], unit="元/吨", name="冶金焦炭")
COAL = _metric("U4421912", ["20260909", "20260910"], [1945.0, 1957.0], unit="元/吨", name="焦煤")

ALL_CODES = ["S5103725", "S5118163", "Z8948284", "Q0149884", "S5707798", "T2987959", "U4421912"]


def _fake_query(code_to_metric: dict):
    """打桩 wind_query_economic_indicator: 批量(逗号分隔)或单码均按 code 查表"""
    calls = {"n": 0}

    def _call(question, begin_date=None, end_date=None, observation=None):
        calls["n"] += 1
        out = []
        for c in str(question).split(","):
            c = c.strip()
            if c in code_to_metric:
                out.append(code_to_metric[c])
        return out

    _call.calls = calls
    return _call


ALL_METRICS = {
    "S5103725": QHD,
    "S5118163": CFD,
    "Z8948284": BOHAI,
    "Q0149884": POWER,
    "S5707798": REBAR,
    "T2987959": COKE,
    "U4421912": COAL,
}


class TestFetchCoalFundamentals:
    @pytest.mark.unit
    def test_single_batch_call(self, monkeypatch):
        """全部指标一次批量取数, 不重复往返"""
        fake = _fake_query(ALL_METRICS)
        monkeypatch.setattr(cf, "wind_query_economic_indicator", fake)
        res = cf.fetch_coal_fundamentals(days=30, use_cache=False)

        assert res["ok"] is True
        assert fake.calls["n"] == 1  # 只调用一次
        assert set(res["groups"].keys()) == {
            "port_inventory",
            "power_daily_use",
            "rebar_price",
            "coke_spread",
        }
        # 港口组按注册顺序 (秦皇岛先于曹妃甸)
        items = res["groups"]["port_inventory"]["items"]
        assert items[0]["label"] == "秦皇岛港"

    @pytest.mark.unit
    def test_derived_port_total_excludes_aggregate(self, monkeypatch):
        monkeypatch.setattr(cf, "wind_query_economic_indicator", _fake_query(ALL_METRICS))
        res = cf.fetch_coal_fundamentals(days=30, use_cache=False)

        d = res["groups"]["port_inventory"]["derived"]
        # 仅秦皇岛 605 + 曹妃甸 452, 不含环渤海/沿海 (合计口径)
        assert d["total_value"] == 1057.0
        assert d["total_ports"] == ["秦皇岛港", "曹妃甸港"]

    @pytest.mark.unit
    def test_derived_coke_spread(self, monkeypatch):
        monkeypatch.setattr(cf, "wind_query_economic_indicator", _fake_query(ALL_METRICS))
        res = cf.fetch_coal_fundamentals(days=30, use_cache=False)

        d = res["groups"]["coke_spread"]["derived"]
        assert d["coke_price"] == 2112.0
        assert d["coking_coal_price"] == 1957.0
        assert d["spread"] == 155.0
        assert d["ratio"] == pytest.approx(2112 / 1957, abs=0.001)

    @pytest.mark.unit
    def test_stale_flag_by_freq(self, monkeypatch):
        """周频阈值 21 天, 日频 7 天; 落后则 stale=True"""
        stale_daily = _metric("S5707798", ["20260801", "20260802"], [3300.0, 3352.0],
                              unit="元/吨", name="螺纹钢")
        fake = _fake_query({**ALL_METRICS, "S5707798": stale_daily})
        monkeypatch.setattr(cf, "wind_query_economic_indicator", fake)
        res = cf.fetch_coal_fundamentals(days=30, use_cache=False)

        # 螺纹钢 (日频, 落后 >7 天) 应被标 stale
        rebar = next(i for i in res["groups"]["rebar_price"]["items"] if i["code"] == "S5707798")
        assert rebar["stale"] is True
        # 电厂日耗 (周频, 20260903 vs 今日约 09-10, 落后 <21 天) 不 stale
        power = res["groups"]["power_daily_use"]["items"][0]
        assert power["stale"] is False

    @pytest.mark.unit
    def test_cache_hit_avoid_refetch(self, monkeypatch, tmp_path):
        fake = _fake_query(ALL_METRICS)
        monkeypatch.setattr(cf, "wind_query_economic_indicator", fake)
        monkeypatch.setattr(cf, "DEFAULT_CACHE_DIR", tmp_path)

        cf.fetch_coal_fundamentals(days=30, use_cache=True)
        cf.fetch_coal_fundamentals(days=30, use_cache=True)

        assert fake.calls["n"] == 1  # 第二次命中缓存
        cache_file = tmp_path / f"coal_fundamentals_{now_bj().strftime('%Y%m%d')}.json"
        assert cache_file.exists()

    @pytest.mark.unit
    def test_fail_open_on_exception(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("wind down")

        monkeypatch.setattr(cf, "wind_query_economic_indicator", _boom)
        res = cf.fetch_coal_fundamentals()

        assert res["ok"] is False
        assert "fetch_failed" in res["error"]

    @pytest.mark.unit
    def test_empty_metrics_not_ok(self, monkeypatch):
        monkeypatch.setattr(cf, "wind_query_economic_indicator", _fake_query({}))
        res = cf.fetch_coal_fundamentals(days=30, use_cache=False)
        assert res["ok"] is False
        assert res["error"] == "no_metrics_returned"


class TestShim:
    @pytest.mark.unit
    def test_shim_returns_port_group(self, monkeypatch):
        """coal_port_inventory.fetch_port_coal_inventory 委托 fundamentals 的港口组"""
        monkeypatch.setattr(cpi, "fetch_coal_fundamentals", lambda **kw: {
            "ok": True,
            "as_of": "20260909",
            "groups": {
                "port_inventory": {
                    "items": [{"label": "秦皇岛港", "value": 605.0, "unit": "万吨",
                               "date": "20260909", "change": 10.0, "freq": "日",
                               "source": "x", "aggregate": False}],
                    "derived": {"total_value": 605.0, "total_change": 10.0,
                                "total_ports": ["秦皇岛港"], "total_date": "20260909"},
                }
            },
        })
        res = cpi.fetch_port_coal_inventory(observation=6)

        assert res["ok"] is True
        assert res["items"][0]["label"] == "秦皇岛港"
        assert res["total"]["value"] == 605.0


class TestReportWiring:
    @pytest.mark.unit
    def test_report_all_four_groups_ok(self, monkeypatch, tmp_path):
        import nlp.sentiment_hub as sh

        monkeypatch.setattr(sh, "_fetch_coal_news_judged", lambda positions: [])
        monkeypatch.setattr(cf, "wind_query_economic_indicator", _fake_query(ALL_METRICS))

        md = sh._generate_coal_report(
            "2026-09-10",
            {"positions": {"600900.SH": {"name": "长江电力", "sector": "防御"}}},
            output_dir=tmp_path,
        )

        assert md.count("已接入 (Wind EDB") == 4
        assert "焦炭-焦煤价差: **155.0" in md
        assert "待接入数据源" not in md
        assert (tmp_path / "coal_fundamentals_20260910.json").exists()

    @pytest.mark.unit
    def test_report_partial_fail_keeps_placeholder(self, monkeypatch, tmp_path):
        import nlp.sentiment_hub as sh

        monkeypatch.setattr(sh, "_fetch_coal_news_judged", lambda positions: [])
        monkeypatch.setattr(cf, "wind_query_economic_indicator", _fake_query({}))  # 取数失败

        md = sh._generate_coal_report(
            "2026-09-10", {"positions": {}}, output_dir=tmp_path
        )

        # 取数失败 → 四个监控项均保留待接入占位
        assert md.count("待接入数据源") >= 4
        assert "已接入 (Wind EDB" not in md
