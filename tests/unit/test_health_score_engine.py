"""Health Score 聚合引擎单测 (Production Edition T2, 2026-09-02)."""
from __future__ import annotations

import json
from pathlib import Path

from utils.health.score_engine import (
    DEGRADED_NEUTRAL,
    WEIGHTS,
    DimensionScore,
    compute_health_score,
    score_capital,
    score_data,
    score_model,
    score_risk,
    score_trading,
    status_for,
)


class TestEngineSkeleton:
    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
        assert set(WEIGHTS) == {"model", "data", "trading", "risk", "capital"}

    def test_status_thresholds(self):
        assert status_for(85.0) == "GREEN"
        assert status_for(100.0) == "GREEN"
        assert status_for(84.9) == "YELLOW"
        assert status_for(70.0) == "YELLOW"
        assert status_for(69.9) == "RED"
        assert status_for(0.0) == "RED"

    def test_degraded_neutral_is_60(self):
        assert DEGRADED_NEUTRAL == 60.0

    def test_dimension_score_fields(self):
        d = DimensionScore(score=80.0, weight=0.2, degraded=True, detail={"k": 1})
        assert d.score == 80.0
        assert d.weight == 0.2
        assert d.degraded is True
        assert d.detail == {"k": 1}


def _write_drift(root: Path, date: str, payload: dict) -> None:
    d = root / "reports" / "drift"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"integration_{date}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


class TestScoreModel:
    DATE = "2026-09-01"

    def _good(self, **over):
        p = {
            "date": self.DATE,
            "skipped": False,
            "error": None,
            "ic_degradation": 0.1,
            "alerts": [],
            "delayed_metrics": {"ic": 0.05, "rank_ic": 0.06, "ic_ir": 0.8},
        }
        p.update(over)
        return p

    def test_missing_file_degraded(self, tmp_path):
        d = score_model(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.25

    def test_low_degradation_no_alerts_full_score(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good())
        d = score_model(tmp_path, self.DATE)
        assert d.degraded is False
        assert d.score == 100.0
        assert d.detail["ic"] == 0.05

    def test_medium_degradation(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(ic_degradation=0.5))
        assert score_model(tmp_path, self.DATE).score == 85.0

    def test_high_degradation(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(ic_degradation=0.88))
        assert score_model(tmp_path, self.DATE).score == 70.0

    def test_alerts_deduct_with_floor(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(alerts=["a", "b", "c"]))
        assert score_model(tmp_path, self.DATE).score == 70.0  # 100-30

    def test_alerts_floor_zero(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(alerts=[str(i) for i in range(12)]))
        assert score_model(tmp_path, self.DATE).score == 0.0

    def test_skipped_is_degraded(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(skipped=True))
        d = score_model(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0

    def test_error_is_degraded(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(error="boom"))
        assert score_model(tmp_path, self.DATE).degraded is True

    def test_corrupt_json_is_degraded(self, tmp_path):
        d = tmp_path / "reports" / "drift"
        d.mkdir(parents=True)
        (d / f"integration_{self.DATE}.json").write_text("{bad", encoding="utf-8")
        assert score_model(tmp_path, self.DATE).degraded is True


def _write_degradation_log(root: Path, records: list[dict]) -> None:
    d = root / "reports"
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    (d / "degradation_log.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _deg(ts: str, scope: str = "mod", key: str = "k") -> dict:
    return {"ts": ts, "scope": scope, "key": key, "default": "d", "reason": "r"}


class TestScoreData:
    DATE = "2026-09-01"

    def test_missing_log_degraded(self, tmp_path):
        d = score_data(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.20

    def test_zero_entries_full_score(self, tmp_path):
        _write_degradation_log(tmp_path, [])
        d = score_data(tmp_path, self.DATE)
        assert d.degraded is False
        assert d.score == 100.0

    def test_other_dates_ignored(self, tmp_path):
        _write_degradation_log(tmp_path, [_deg("2026-08-31T10:00:00")])
        assert score_data(tmp_path, self.DATE).score == 100.0

    def test_one_or_two_entries_80(self, tmp_path):
        _write_degradation_log(
            tmp_path,
            [_deg(f"{self.DATE}T10:00:00"), _deg(f"{self.DATE}T11:00:00")],
        )
        assert score_data(tmp_path, self.DATE).score == 80.0

    def test_three_to_five_distinct_events_60(self, tmp_path):
        # 3 个不同的 (scope, key) 事件 → 60
        _write_degradation_log(
            tmp_path,
            [
                _deg(f"{self.DATE}T10:0{i}:00", scope=f"mod{i}", key=f"k{i}")
                for i in range(3)
            ],
        )
        d = score_data(tmp_path, self.DATE)
        assert d.detail["distinct_events"] == 3
        assert d.score == 60.0

    def test_six_plus_distinct_events_40(self, tmp_path):
        # 6 个不同的 (scope, key) 事件 → 40
        _write_degradation_log(
            tmp_path,
            [
                _deg(f"{self.DATE}T10:0{i}:00", scope=f"mod{i}", key=f"k{i}")
                for i in range(6)
            ],
        )
        assert score_data(tmp_path, self.DATE).score == 40.0

    def test_duplicate_pairs_collapse_v2(self, tmp_path):
        # 同一 (scope,key) 跨进程重复刷屏 → 去重后仅 1 个事件, 不应打成假 RED
        _write_degradation_log(
            tmp_path,
            [_deg(f"{self.DATE}T10:0{i}:00") for i in range(30)],  # 30 条同事件
        )
        d = score_data(tmp_path, self.DATE)
        assert d.detail["entries"] == 30          # 原始条目仍透明可见
        assert d.detail["distinct_events"] == 1   # 但计分用去重数
        assert d.score == 80.0

    def test_chaos_scope_ignored_v2(self, tmp_path):
        # 演练/测试产生的降级不计入生产健康度
        _write_degradation_log(
            tmp_path,
            [_deg(f"{self.DATE}T10:00:00", scope="chaos_data", key="src"),
             _deg(f"{self.DATE}T10:01:00", scope="chaos_exec", key="s1"),
             _deg(f"{self.DATE}T10:02:00", scope="chaos_exec", key="s2")],
        )
        d = score_data(tmp_path, self.DATE)
        assert d.detail["entries"] == 3
        assert d.detail["distinct_events"] == 0   # 全部为 chaos_ 前缀
        assert d.score == 100.0

    def test_ignore_prefixes_customizable(self, tmp_path):
        # 自定义忽略前缀: 关闭忽略后 chaos 事件计入
        _write_degradation_log(
            tmp_path,
            [_deg(f"{self.DATE}T10:00:00", scope="chaos_data", key="src")],
        )
        d = score_data(tmp_path, self.DATE, ignore_scope_prefixes=())
        assert d.detail["distinct_events"] == 1
        assert d.score == 80.0

    def test_config_manager_is_chronic_detail_only_v2(self, tmp_path):
        # 慢性配置债 (config_manager 缺配置) 只入明细, 不把数据维打成 RED
        _write_degradation_log(
            tmp_path,
            [
                _deg(f"{self.DATE}T10:0{i}:00", scope="config_manager", key=f"cfg{i}")
                for i in range(20)  # 20 个不同的缺失配置
            ],
        )
        d = score_data(tmp_path, self.DATE)
        assert d.detail["entries"] == 20
        assert d.detail["distinct_events"] == 0      # 全部为慢性配置债
        assert len(d.detail["config_missing_keys"]) == 20
        assert d.score == 100.0

    def test_runtime_plus_chronic_mix_v2(self, tmp_path):
        # 运行时降级计分, 慢性配置债仍只入明细
        _write_degradation_log(
            tmp_path,
            [
                _deg(f"{self.DATE}T10:00:00", scope="config_manager", key="trade_execution.yaml"),
                _deg(f"{self.DATE}T10:01:00", scope="config_manager", key="kill_switch"),
                _deg(f"{self.DATE}T10:02:00", scope="data_layer", key="wind_mcp"),
                _deg(f"{self.DATE}T10:03:00", scope="data_layer", key="akshare"),
            ],
        )
        d = score_data(tmp_path, self.DATE)
        assert d.detail["distinct_events"] == 2      # 2 个运行时事件
        assert d.detail["config_missing_keys"] == ["kill_switch", "trade_execution.yaml"]
        assert d.score == 80.0

    def test_detail_has_scopes(self, tmp_path):
        _write_degradation_log(
            tmp_path, [_deg(f"{self.DATE}T10:00:00", scope="z_mod"), _deg(f"{self.DATE}T10:01:00", scope="a_mod")]
        )
        d = score_data(tmp_path, self.DATE)
        assert d.detail["entries"] == 2
        assert d.detail["scopes"] == ["a_mod", "z_mod"]

    def test_corrupt_lines_skipped(self, tmp_path):
        d = tmp_path / "reports"
        d.mkdir(parents=True)
        (d / "degradation_log.jsonl").write_text(
            "{bad\n" + json.dumps(_deg(f"{self.DATE}T10:00:00")) + "\n", encoding="utf-8"
        )
        assert score_data(tmp_path, self.DATE).score == 80.0

    def test_backup_stale_deducts(self, tmp_path):
        _write_degradation_log(tmp_path, [])
        backup_root = tmp_path / "bak"
        backup_root.mkdir()
        (backup_root / "2026-08-01").mkdir()  # 32 天前 > 4 天阈值
        d = score_data(tmp_path, self.DATE, backup_root=backup_root)
        assert d.score == 80.0  # 100 - 20
        assert d.detail["backup_stale"] is True

    def test_backup_fresh_no_deduction(self, tmp_path):
        _write_degradation_log(tmp_path, [])
        backup_root = tmp_path / "bak"
        backup_root.mkdir()
        (backup_root / "2026-09-01").mkdir()  # 1 天前
        d = score_data(tmp_path, self.DATE, backup_root=backup_root)
        assert d.score == 100.0
        assert d.detail["backup_stale"] is False

    def test_backup_root_none_skips_check(self, tmp_path):
        _write_degradation_log(tmp_path, [])
        d = score_data(tmp_path, self.DATE)  # 不传 backup_root: 向后兼容
        assert d.score == 100.0
        assert "backup_stale" not in d.detail


def _write_tca(root: Path, date: str, records: list[dict]) -> None:
    d = root / "reports" / "tca"
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    (d / f"fills_{date}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fill(estimate) -> dict:
    return {
        "type": "fill",
        "fill": {"symbol": "510300.SH", "side": "BUY", "shares": 100},
        "estimate": estimate,
    }


class TestScoreTrading:
    DATE = "2026-09-01"

    def test_missing_file_degraded(self, tmp_path):
        d = score_trading(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.15

    def test_no_fills_degraded(self, tmp_path):
        _write_tca(tmp_path, self.DATE, [])
        assert score_trading(tmp_path, self.DATE).degraded is True

    def test_full_estimate_coverage(self, tmp_path):
        _write_tca(
            tmp_path, self.DATE,
            [_fill({"cost_bps": 5.0}), _fill({"cost_bps": 6.0})],
        )
        d = score_trading(tmp_path, self.DATE)
        assert d.score == 100.0
        assert d.detail == {"fills": 2, "estimate_coverage": 1.0}

    def test_zero_estimate_coverage(self, tmp_path):
        _write_tca(tmp_path, self.DATE, [_fill(None), _fill(None)])
        assert score_trading(tmp_path, self.DATE).score == 70.0

    def test_mixed_coverage_below_half(self, tmp_path):
        _write_tca(tmp_path, self.DATE, [_fill({"cost_bps": 5.0}), _fill(None), _fill(None)])
        d = score_trading(tmp_path, self.DATE)
        assert d.score == 70.0
        assert d.detail["estimate_coverage"] == round(1 / 3, 4)


def _write_vol_regime(root: Path, date: str, payload: dict) -> None:
    d = root / "reports" / "evolution"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"vol_regime_weights_{date}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _vol(label: str, **over) -> dict:
    p = {
        "regime": {"label": label, "confidence": 0.85},
        "degraded": False,
        "observation_phase": False,
    }
    p.update(over)
    return p


class TestScoreRisk:
    DATE = "2026-09-01"

    def test_missing_file_degraded(self, tmp_path):
        d = score_risk(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.20

    def test_bull_100(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("bull"))
        assert score_risk(tmp_path, self.DATE).score == 100.0

    def test_bear_80(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("bear"))
        assert score_risk(tmp_path, self.DATE).score == 80.0

    def test_sideways_95(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("sideways"))
        assert score_risk(tmp_path, self.DATE).score == 95.0

    def test_unknown_label_90(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("turbulent"))
        assert score_risk(tmp_path, self.DATE).score == 90.0

    def test_regime_degraded_flag_50(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("bull", degraded=True))
        assert score_risk(tmp_path, self.DATE).score == 50.0

    def test_corrupt_json_degraded(self, tmp_path):
        d = tmp_path / "reports" / "evolution"
        d.mkdir(parents=True)
        (d / f"vol_regime_weights_{self.DATE}.json").write_text("{bad", encoding="utf-8")
        assert score_risk(tmp_path, self.DATE).degraded is True


def _write_shadow_state(root: Path, payload: dict) -> None:
    d = root / "output" / "shadow_account"
    d.mkdir(parents=True, exist_ok=True)
    (d / "s12_shadow_state.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _shadow(**over) -> dict:
    p = {
        "account_id": "S12_SHADOW_P3",
        "nav": 1.0,
        "trading_day_count": 0,
        "fail_fast_triggered": False,
    }
    p.update(over)
    return p


class TestScoreCapital:
    def test_missing_state_degraded(self, tmp_path):
        d = score_capital(tmp_path, "2026-09-01")
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.20

    def test_normal_nav_100(self, tmp_path):
        _write_shadow_state(tmp_path, _shadow())
        d = score_capital(tmp_path, "2026-09-01")
        assert d.score == 100.0
        assert d.detail["nav"] == 1.0

    def test_fail_fast_zero(self, tmp_path):
        _write_shadow_state(tmp_path, _shadow(fail_fast_triggered=True))
        assert score_capital(tmp_path, "2026-09-01").score == 0.0

    def test_nav_out_of_sane_range_50(self, tmp_path):
        _write_shadow_state(tmp_path, _shadow(nav=3.0))
        assert score_capital(tmp_path, "2026-09-01").score == 50.0

    def test_corrupt_json_degraded(self, tmp_path):
        d = tmp_path / "output" / "shadow_account"
        d.mkdir(parents=True)
        (d / "s12_shadow_state.json").write_text("{bad", encoding="utf-8")
        assert score_capital(tmp_path, "2026-09-01").degraded is True


class TestShadowExemption:
    """v3 (2026-09-02): shadow 阶段主链产物豁免 — model/trading/risk 三维的
    数据源 (drift integration / TCA fills / vol_regime) 属实盘链产物, 纯
    shadow 阶段不会生成 → 标记 exempted, 不计 degraded, 权重从总分剔除
    (归一化)。实盘启动 (reports/tca 出现历史 fills) 后豁免自动失效。"""

    DATE = "2026-09-02"

    def test_exempt_when_shadow_phase(self, tmp_path):
        _write_shadow_state(tmp_path, _shadow())
        r = compute_health_score(tmp_path, self.DATE)
        assert set(r["exempted_dimensions"]) == {"model", "trading", "risk"}
        # degradation_log 不存在 → data degraded 60 (中性), capital 100
        # 归一化: (60*0.20 + 100*0.20) / (0.20+0.20) = 80.0
        assert r["total_score"] == 80.0
        assert r["status"] == "YELLOW"
        assert r["degraded_dimensions"] == ["data"]

    def test_exempted_dimension_fields(self, tmp_path):
        _write_shadow_state(tmp_path, _shadow())
        d = score_model(tmp_path, self.DATE)
        assert d.exempted is True
        assert d.degraded is False
        assert d.score == 100.0
        assert d.detail["exemption"] == "shadow_phase"

    def test_exempt_off_when_live_fills_exist(self, tmp_path):
        _write_shadow_state(tmp_path, _shadow())
        # 历史实盘成交 (QMT broker) → 实盘链已启动, 豁免失效
        _write_tca(tmp_path, "2026-08-01", [
            {"type": "fill", "estimate": True,
             "fill": {"symbol": "510300", "broker": "QMT"}}])
        r = compute_health_score(tmp_path, self.DATE)
        assert r["exempted_dimensions"] == []
        assert "model" in r["degraded_dimensions"]

    def test_sim_fills_do_not_disable_exemption(self, tmp_path):
        """期权对冲仿真链 (OptionsSimBroker) 的 fills 不算实盘启动."""
        _write_shadow_state(tmp_path, _shadow())
        _write_tca(tmp_path, "2026-09-01", [
            {"type": "fill", "estimate": None,
             "fill": {"symbol": "510050 Put", "broker": "OptionsSimBroker"}}])
        r = compute_health_score(tmp_path, self.DATE)
        assert set(r["exempted_dimensions"]) == {"model", "trading", "risk"}

    def test_exempt_off_without_shadow_state(self, tmp_path):
        r = compute_health_score(tmp_path, self.DATE)
        assert r["exempted_dimensions"] == []
        assert set(r["degraded_dimensions"]) == {
            "model", "data", "trading", "risk", "capital"}

    def test_schema_has_exempted_dimensions_field(self, tmp_path):
        _write_shadow_state(tmp_path, _shadow())
        r = compute_health_score(tmp_path, self.DATE)
        assert "exempted_dimensions" in r
        assert r["dimensions"]["model"]["exempted"] is True


class TestAggregate:
    DATE = "2026-09-01"

    def test_all_missing_all_degraded_total_60(self, tmp_path):
        r = compute_health_score(tmp_path, self.DATE)
        assert r["date"] == self.DATE
        assert r["total_score"] == 60.0
        # 60.0 < 70 → RED (计划评分规则表; 与 Task 1 status_for 单测一致)
        assert r["status"] == "RED"
        assert set(r["degraded_dimensions"]) == {"model", "data", "trading", "risk", "capital"}
        assert set(r["dimensions"]) == {"model", "data", "trading", "risk", "capital"}

    def test_mixed_fixture_exact_weighted_total(self, tmp_path):
        # model 100 (ic_deg 0.1 无告警) / data 100 (0 条) / trading 60 (缺文件)
        # risk 100 (bull) / capital 100 (nav 1.0)
        # 历史实盘 fills (QMT) 标记实盘已启动 → trading 缺文件按降级计 (非豁免)
        _write_tca(tmp_path, "2026-08-01", [
            {"type": "fill", "estimate": True,
             "fill": {"symbol": "510300", "broker": "QMT"}}])
        _write_drift(tmp_path, self.DATE, {
            "date": self.DATE, "skipped": False, "error": None,
            "ic_degradation": 0.1, "alerts": [],
            "delayed_metrics": {"ic": 0.05, "rank_ic": 0.06, "ic_ir": 0.8},
        })
        _write_degradation_log(tmp_path, [])
        _write_vol_regime(tmp_path, self.DATE, _vol("bull"))
        _write_shadow_state(tmp_path, _shadow())
        r = compute_health_score(tmp_path, self.DATE)
        # 100*0.25 + 100*0.20 + 60*0.15 + 100*0.20 + 100*0.20 = 94.0
        assert r["total_score"] == 94.0
        assert r["status"] == "GREEN"
        assert r["degraded_dimensions"] == ["trading"]
        assert r["dimensions"]["trading"]["degraded"] is True

    def test_output_schema_fields(self, tmp_path):
        r = compute_health_score(tmp_path, self.DATE)
        assert "generated_at" in r
        assert r["dimensions"]["model"]["weight"] == 0.25
        assert isinstance(r["dimensions"]["data"]["detail"], dict)
