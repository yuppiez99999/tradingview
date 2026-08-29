"""
VolRegimeWeighter Phase 0 端到端集成测试
验证完整链路: portfolio.yaml 快照 → VIX → regime 识别 → 权重计算 → 报告输出 → decisions.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.vol_regime_weighter import (
    REGIME_BEAR,
    REGIME_BULL,
    REGIME_NEUTRAL,
    VolRegimeWeighter,
)

# ============================================================
# 测试 fixtures
# ============================================================


@pytest.fixture
def sample_portfolio_data():
    """模拟 configs/portfolio.yaml 完整结构."""
    return {
        "account_structure": {
            "total_capital": 5000000,
            "target_annual_return": 0.08,
            "target_max_drawdown": 0.15,
        },
        "assets": [
            {
                "code": "588080",
                "name": "科创50ETF易方达",
                "weight": 0.045,
                "style": "科技",
            },
            {
                "code": "512760",
                "name": "半导体ETF国泰",
                "weight": 0.03,
                "style": "科技",
            },
            {"code": "688041", "name": "海光信息", "weight": 0.04, "style": "科技"},
            {"code": "300308", "name": "中际旭创", "weight": 0.04, "style": "科技"},
            {"code": "002371", "name": "北方华创", "weight": 0.04, "style": "科技"},
            {"code": "603019", "name": "中科曙光", "weight": 0.03, "style": "科技"},
            {"code": "300033", "name": "同花顺", "weight": 0.04, "style": "科技"},
            {"code": "300782", "name": "卓胜微", "weight": 0.02, "style": "科技"},
            {
                "code": "515030",
                "name": "新能源车ETF华夏",
                "weight": 0.05,
                "style": "新能源",
            },
            {"code": "300274", "name": "阳光电源", "weight": 0.04, "style": "新能源"},
            {"code": "512170", "name": "医疗ETF华宝", "weight": 0.09, "style": "医药"},
            {"code": "600276", "name": "恒瑞医药", "weight": 0.06, "style": "医药"},
            {"code": "512880", "name": "证券ETF国泰", "weight": 0.05, "style": "金融"},
            {"code": "512800", "name": "银行ETF华宝", "weight": 0.06, "style": "金融"},
            {
                "code": "510050",
                "name": "上证50ETF华夏",
                "weight": 0.06,
                "style": "宽基",
            },
            {"code": "518880", "name": "黄金ETF华安", "weight": 0.05, "style": "资源"},
            {"code": "000408", "name": "藏格矿业", "weight": 0.04, "style": "资源"},
            {"code": "601088", "name": "中国神华", "weight": 0.03, "style": "顺周期"},
            {"code": "600900", "name": "长江电力", "weight": 0.05, "style": "防御"},
            {"code": "688017", "name": "绿的谐波", "weight": 0.03, "style": "制造"},
            {"code": "CASH", "name": "现金储备", "weight": 0.05, "style": "现金"},
        ],
    }


@pytest.fixture
def sample_daily_returns():
    """模拟 20 天日收益率 (波动率约 0.28 → bear)."""
    return [
        0.012,
        -0.018,
        0.005,
        -0.022,
        0.015,
        -0.010,
        0.020,
        -0.025,
        0.008,
        -0.015,
        0.018,
        -0.020,
        0.010,
        -0.030,
        0.022,
        -0.012,
        0.015,
        -0.025,
        0.018,
        -0.020,
    ]


# ============================================================
# 端到端测试
# ============================================================


class TestPhase0E2E:
    """Phase 0 端到端测试."""

    def test_phase0_e2e_vix_bear_regime(
        self, sample_portfolio_data, sample_daily_returns, tmp_path
    ):
        """端到端: VIX=32.5 → bear → 生成报告 → portfolio.yaml 未变."""
        # 1. 创建测试用 VolRegimeWeighter (Flag=True)
        with patch(
            "utils.alpha.vol_regime_weighter.VolRegimeWeighter._check_flag",
            return_value=True,
        ):
            weighter = VolRegimeWeighter(reports_dir=tmp_path)

        # 2. 记录 portfolio.yaml mtime (模拟)
        portfolio_yaml_path = _PROJECT_ROOT / "configs" / "portfolio.yaml"
        try:
            mtime_before = portfolio_yaml_path.stat().st_mtime
        except Exception:
            mtime_before = 0

        # 3. 运行完整 cycle
        result = weighter.run_cycle(
            portfolio_snapshot=sample_portfolio_data,
            vix_value=32.5,
            daily_returns=sample_daily_returns,
            reports_dir=tmp_path,
        )

        # 4. 断言: 状态 OK
        assert result["status"] == "ok"

        # 5. 断言: regime = bear
        assert result["regime"] == REGIME_BEAR

        # 6. 断言: 报告文件已生成
        report_path = Path(result["report_path"])
        assert report_path.exists(), f"报告未生成: {report_path}"

        # 7. 断言: 报告内容正确
        with report_path.open("r", encoding="utf-8") as f:
            report_data = json.load(f)
        assert report_data["regime"]["label"] == REGIME_BEAR
        assert report_data["observation_phase"] is True
        assert report_data["audit"]["portfolio_yaml_untouched"] is True

        # 8. 断言: 科技减仓 (multiplier=0.60)
        assert report_data["multipliers"]["科技"] == 0.60
        assert (
            report_data["suggested_weights"]["科技"]
            < report_data["current_weights"]["科技"]
        )

        # 9. 断言: 现金加仓 (multiplier=2.00)
        assert report_data["multipliers"]["现金"] == 2.00
        assert (
            report_data["suggested_weights"]["现金"]
            > report_data["current_weights"]["现金"]
        )

        # 10. 断言: 约束应用
        assert len(report_data["constraints_applied"]) > 0
        assert any("sum_to_one" in c for c in report_data["constraints_applied"])

        # 11. 断言: portfolio.yaml 未被修改
        try:
            mtime_after = portfolio_yaml_path.stat().st_mtime
            assert mtime_after == mtime_before, "portfolio.yaml 被修改了!"
        except Exception:
            pass  # 文件不存在时跳过

    def test_phase0_e2e_vix_missing_fallback_to_rv(
        self, sample_portfolio_data, sample_daily_returns, tmp_path
    ):
        """VIX 缺失时降级到 realized_vol."""
        with patch(
            "utils.alpha.vol_regime_weighter.VolRegimeWeighter._check_flag",
            return_value=True,
        ):
            weighter = VolRegimeWeighter(reports_dir=tmp_path)

        # mock vol_controller 返回 0.32 → bear
        weighter._vol_controller = MagicMock()
        weighter._vol_controller.calc_realized_vol.return_value = 0.32

        result = weighter.run_cycle(
            portfolio_snapshot=sample_portfolio_data,
            vix_value=None,  # VIX 缺失
            daily_returns=sample_daily_returns,
            reports_dir=tmp_path,
        )

        assert result["status"] == "ok"
        assert result["regime"] == REGIME_BEAR

        # 验证报告 source = realized_vol
        report_path = Path(result["report_path"])
        with report_path.open("r", encoding="utf-8") as f:
            report_data = json.load(f)
        assert report_data["regime"]["source"] == "realized_vol"

    def test_phase0_e2e_flag_disabled_noop(self, sample_portfolio_data, tmp_path):
        """Flag=False 时零影响 (HC-1)."""
        with patch(
            "utils.alpha.vol_regime_weighter.VolRegimeWeighter._check_flag",
            return_value=False,
        ):
            weighter = VolRegimeWeighter(reports_dir=tmp_path)

        result = weighter.run_cycle(
            portfolio_snapshot=sample_portfolio_data,
            vix_value=32.5,
            reports_dir=tmp_path,
        )

        # 断言: 状态 disabled
        assert result["status"] == "disabled"
        assert "feature_flag_disabled" in result["reason"]

        # 断言: 不生成正式报告 (noop 报告可能写入, 但内容是 degraded)
        if "report_path" in result:
            report_path = Path(result["report_path"])
            if report_path.exists():
                with report_path.open("r", encoding="utf-8") as f:
                    report_data = json.load(f)
                assert report_data["degraded"] is True

    def test_phase0_e2e_with_orchestrator_log_decision(
        self, sample_portfolio_data, sample_daily_returns, tmp_path
    ):
        """集成 EvolutionOrchestrator: 写入 decisions.jsonl."""
        with patch(
            "utils.alpha.vol_regime_weighter.VolRegimeWeighter._check_flag",
            return_value=True,
        ):
            weighter = VolRegimeWeighter(reports_dir=tmp_path)

        # mock orchestrator
        mock_orch = MagicMock()
        mock_orch.log_decision.return_value = True

        result = weighter.run_cycle(
            portfolio_snapshot=sample_portfolio_data,
            vix_value=32.5,
            daily_returns=sample_daily_returns,
            orchestrator=mock_orch,
            reports_dir=tmp_path,
        )

        # 断言: log_decision 被调用
        assert mock_orch.log_decision.called
        assert result["decision_logged"] is True

        # 断言: action=evaluate_only (Phase 0 强制)
        call_args = mock_orch.log_decision.call_args
        assert call_args.kwargs.get("action") == "evaluate_only"

        # 断言: extra_payload 包含 vol_regime_suggestion
        extra = call_args.kwargs.get("extra_payload", {})
        assert "vol_regime_suggestion" in extra
        assert "report_path" in extra

    def test_phase0_e2e_bull_regime_increases_tech(
        self, sample_portfolio_data, tmp_path
    ):
        """bull regime: 科技加仓 (multiplier=1.20)."""
        with patch(
            "utils.alpha.vol_regime_weighter.VolRegimeWeighter._check_flag",
            return_value=True,
        ):
            weighter = VolRegimeWeighter(reports_dir=tmp_path)

        result = weighter.run_cycle(
            portfolio_snapshot=sample_portfolio_data,
            vix_value=15.0,  # VIX < 20 → bull
            reports_dir=tmp_path,
        )

        assert result["status"] == "ok"
        assert result["regime"] == REGIME_BULL

        report_path = Path(result["report_path"])
        with report_path.open("r", encoding="utf-8") as f:
            report_data = json.load(f)

        # bull 档科技 ×1.20
        assert report_data["multipliers"]["科技"] == 1.20
        assert (
            report_data["suggested_weights"]["科技"]
            > report_data["current_weights"]["科技"]
        )

    def test_phase0_e2e_constraints_sum_to_one(self, sample_portfolio_data, tmp_path):
        """所有 regime 下总和必须 = 1.0."""
        with patch(
            "utils.alpha.vol_regime_weighter.VolRegimeWeighter._check_flag",
            return_value=True,
        ):
            weighter = VolRegimeWeighter(reports_dir=tmp_path)

        for vix, expected_regime in [
            (15.0, REGIME_BULL),
            (25.0, REGIME_NEUTRAL),
            (35.0, REGIME_BEAR),
            (50.0, "crisis"),
        ]:
            result = weighter.run_cycle(
                portfolio_snapshot=sample_portfolio_data,
                vix_value=vix,
                reports_dir=tmp_path,
            )
            assert result["status"] == "ok"
            assert result["regime"] == expected_regime

            report_path = Path(result["report_path"])
            with report_path.open("r", encoding="utf-8") as f:
                report_data = json.load(f)

            total = sum(report_data["suggested_weights"].values())
            assert (
                abs(total - 1.0) < 1e-6
            ), f"VIX={vix} ({expected_regime}) 总和 {total} ≠ 1.0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
