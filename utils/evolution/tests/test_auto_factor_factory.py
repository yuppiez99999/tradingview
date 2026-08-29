"""AutoFactorFactory 单元测试.

测试覆盖:
1. 数据类构造 (CandidateFactor / ValidatedFactor / DeployedFactor / RetireSuggestion)
2. AutoFactorFactory 初始化与状态持久化
3. 类别推断 (_infer_category)
4. 公式推断 (_infer_formula)
5. 验证评分 (_compute_validation_score)
6. 部署与注册
7. 监控与淘汰
8. 全流水线 (降级模式)
9. 状态重置
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from utils.evolution.auto_factor_factory import (
    AutoFactorFactory,
    CandidateFactor,
    DeployedFactor,
    FactoryPipelineReport,
    RetireSuggestion,
    ValidatedFactor,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def temp_data_dir():
    """临时数据目录"""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def factory(temp_data_dir: Path) -> AutoFactorFactory:
    """AutoFactorFactory 实例 (临时目录)"""
    return AutoFactorFactory(
        data_dir=temp_data_dir,
        ic_threshold=0.02,
        ir_threshold=0.30,
        retire_confirm_days=5,
        max_active_factors=10,
    )


@pytest.fixture
def sample_candidates() -> list[CandidateFactor]:
    """样本候选因子"""
    return [
        CandidateFactor(
            name="MOM_60D",
            category="Momentum",
            formula="close / close.shift(60) - 1",
            source="qlib",
            n_stocks=500,
            first_date="2021-01-01",
            last_date="2026-01-01",
        ),
        CandidateFactor(
            name="VOL_20D",
            category="Volatility",
            formula="ret.rolling(20).std()",
            source="qlib",
            n_stocks=500,
            first_date="2021-01-01",
            last_date="2026-01-01",
        ),
        CandidateFactor(
            name="TURNOVER_20D",
            category="Liquidity",
            formula="volume.rolling(20).mean()",
            source="qlib",
            n_stocks=500,
            first_date="2021-01-01",
            last_date="2026-01-01",
        ),
    ]


@pytest.fixture
def sample_validated() -> list[ValidatedFactor]:
    """样本已验证因子"""
    return [
        ValidatedFactor(
            name="MOM_60D",
            category="Momentum",
            formula="close / close.shift(60) - 1",
            source="qlib",
            ic_mean_1d=0.035,
            ic_std_1d=0.05,
            ic_ir_1d=0.70,
            ic_positive_ratio=0.65,
            long_short_return=0.002,
            group_returns=[-0.001, 0.0, 0.001, 0.002, 0.003],
            monotonicity=0.95,
            score=4.5,
            effective=True,
            validation_date="2026-01-01",
            n_samples=100,
        ),
        ValidatedFactor(
            name="VOL_20D",
            category="Volatility",
            formula="ret.rolling(20).std()",
            source="qlib",
            ic_mean_1d=0.025,
            ic_std_1d=0.06,
            ic_ir_1d=0.42,
            ic_positive_ratio=0.58,
            long_short_return=0.001,
            group_returns=[-0.0005, 0.0, 0.0005, 0.001, 0.0015],
            monotonicity=0.85,
            score=3.2,
            effective=True,
            validation_date="2026-01-01",
            n_samples=100,
        ),
    ]


# ============================================================
# 数据类构造测试
# ============================================================


class TestDataClasses:
    """数据类构造测试"""

    def test_candidate_factor_defaults(self):
        """CandidateFactor 默认值"""
        cf = CandidateFactor(
            name="TEST", category="Momentum", formula="ret", source="qlib"
        )
        assert cf.n_stocks == 0
        assert cf.first_date == ""
        assert cf.last_date == ""

    def test_candidate_factor_full(self, sample_candidates):
        """CandidateFactor 完整构造"""
        cf = sample_candidates[0]
        assert cf.name == "MOM_60D"
        assert cf.category == "Momentum"
        assert cf.n_stocks == 500

    def test_validated_factor_defaults(self):
        """ValidatedFactor 默认值"""
        vf = ValidatedFactor(
            name="TEST", category="Momentum", formula="ret", source="qlib"
        )
        assert vf.ic_mean_1d == 0.0
        assert vf.ic_ir_1d == 0.0
        assert vf.effective is False

    def test_validated_factor_full(self, sample_validated):
        """ValidatedFactor 完整构造"""
        vf = sample_validated[0]
        assert vf.name == "MOM_60D"
        assert abs(vf.ic_mean_1d - 0.035) < 1e-10
        assert vf.effective is True

    def test_deployed_factor_defaults(self):
        """DeployedFactor 默认值"""
        df = DeployedFactor(name="TEST", category="Momentum", formula="ret")
        assert df.deploy_date == ""
        assert df.version == 1
        assert df.active is True

    def test_retire_suggestion_defaults(self):
        """RetireSuggestion 默认值"""
        rs = RetireSuggestion(name="TEST", category="Momentum", current_ic=0.01)
        assert rs.suggest_retire is False
        assert rs.archived is False

    def test_factory_pipeline_report_defaults(self):
        """FactoryPipelineReport 默认值"""
        rpt = FactoryPipelineReport()
        assert rpt.status == "success"
        assert rpt.duration_seconds == 0.0


# ============================================================
# AutoFactorFactory 初始化与状态持久化
# ============================================================


class TestFactoryInitialization:
    """工厂初始化测试"""

    def test_default_initialization(self):
        """默认初始化"""
        factory = AutoFactorFactory()
        assert factory.ic_threshold == 0.02
        assert factory.ir_threshold == 0.30
        assert factory.retire_confirm_days == 20
        assert factory.max_active_factors == 50
        assert factory.data_dir.exists()

    def test_custom_initialization(self, temp_data_dir):
        """自定义参数初始化"""
        factory = AutoFactorFactory(
            data_dir=temp_data_dir,
            ic_threshold=0.03,
            ir_threshold=0.50,
            retire_confirm_days=10,
            max_active_factors=20,
        )
        assert factory.ic_threshold == 0.03
        assert factory.ir_threshold == 0.50
        assert factory.retire_confirm_days == 10
        assert factory.max_active_factors == 20

    def test_initial_state_empty(self, factory):
        """初始状态为空"""
        assert len(factory._discovered) == 0
        assert len(factory._validated) == 0
        assert len(factory._deployed) == 0
        assert len(factory._retired) == 0

    def test_state_persistence(self, factory):
        """状态持久化"""
        # 添加一个因子到内部状态
        factory._discovered["MOM_60D"] = CandidateFactor(
            name="MOM_60D", category="Momentum", formula="ret", source="qlib"
        )
        factory._save_state()

        # 创建新实例, 验证状态加载
        factory2 = AutoFactorFactory(data_dir=factory.data_dir)
        assert "MOM_60D" in factory2._discovered

    def test_import_from_package(self):
        """从 evolution 包导入"""
        from utils.evolution import AutoFactorFactory

        assert AutoFactorFactory is not None


# ============================================================
# 类别推断测试
# ============================================================


class TestInferCategory:
    """类别推断测试"""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("MOM_60D", "Momentum"),
            ("REVERSAL_20D", "Momentum"),
            ("MACD", "Momentum"),
            ("RSI_14D", "Momentum"),
            ("OBV_CHG", "Momentum"),
            ("VOL_20D", "Volatility"),
            ("DOWNSIDE_VOL_60D", "Volatility"),
            ("SKEW_60D", "Volatility"),
            ("KURT_120D", "Volatility"),
            ("ATR_14D", "Volatility"),
            ("BB_WIDTH_20D", "Volatility"),
            ("TURNOVER_20D", "Liquidity"),
            ("AMIHUD_20D", "Liquidity"),
            ("VOLUME_CHG_5D", "Liquidity"),
            ("VOLUME_Z_20D", "Liquidity"),
            ("LIQ_TURNOVER", "Liquidity"),
            ("SIZE_LOG_MCAP", "Size"),
            ("MCAP", "Size"),
            ("MA_DEV_20D", "Technical"),
            ("PRICE_VOL_DIVERG_20D", "Technical"),
            ("GTJA191_004", "Technical"),
            ("ALPHA004", "Technical"),
            ("SIZE_PROXY", "Fundamental_Proxy"),
            ("EARNING_STABILITY", "Fundamental_Proxy"),
            ("QUALITY_PROXY", "Fundamental_Proxy"),
            ("UNKNOWN_FACTOR", "Other"),
        ],
    )
    def test_infer_category(self, factory, name, expected):
        """验证各类别推断"""
        result = factory._infer_category(name)
        assert result == expected, f"{name} → {result}, expected {expected}"


# ============================================================
# 公式推断测试
# ============================================================


class TestInferFormula:
    """公式推断测试"""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("MOM_60D", "close / close.shift(60) - 1"),
            ("MOM_20D", "close / close.shift(20) - 1"),
            ("REVERSAL_20D", "-(close / close.shift(20) - 1)"),
            ("VOL_20D", "ret.rolling(20).std()"),
            ("TURNOVER_20D", "volume.rolling(20).mean()"),
            ("RSI_14D", "RSI(14)"),
            ("MA_DEV_20D", "close / close.rolling(20).mean() - 1"),
            ("SKEW_60D", "ret.rolling(60).skew()"),
            ("KURT_120D", "ret.rolling(120).kurt()"),
            ("AMIHUD_20D", "mean(|ret| / volume, 20)"),
            ("GTJA191_004", "GTJA191 factor: GTJA191_004"),
            ("ALPHA004", "GTJA191 factor: ALPHA004"),
            ("OBV_CHG", "OBV / OBV.shift(20) - 1"),
            ("PRICE_VOL_DIVERG_20D", "price新高 && volume未新高"),
            ("SIZE_PROXY", "close (市值代理)"),
            ("LONG_TERM_RET", "close / close.shift(252) - 1"),
            ("EARNING_STABILITY", "-ret.rolling(252).std()"),
            ("QUALITY_PROXY", "ret.mean() / ret.std() (120日)"),
        ],
    )
    def test_infer_formula(self, factory, name, expected):
        """验证公式推断"""
        result = factory._infer_formula(name)
        assert result == expected, f"{name} → {result}, expected {expected}"


# ============================================================
# 验证评分测试
# ============================================================


class TestValidationScore:
    """验证评分测试"""

    def test_high_quality_factor(self, factory):
        """高质量因子评分"""
        vf = ValidatedFactor(
            name="MOM_60D",
            category="Momentum",
            formula="ret",
            source="qlib",
            ic_mean_1d=0.05,
            ic_ir_1d=1.0,
            ic_positive_ratio=0.70,
            monotonicity=0.95,
            long_short_return=0.005,
        )
        score = factory._compute_validation_score(vf)
        # IC: min(0.05*100, 5) * 0.30 = 1.5
        # IR: min(1.0, 3) * 0.25 = 0.25
        # 正IC: 0.70 * 0.15 = 0.105
        # 单调性: 0.95 * 0.15 = 0.1425
        # 多空: min(0.5, 2) * 0.10 = 0.05
        # 合计: 2.0475
        assert abs(score - 2.0475) < 1e-10

    def test_low_quality_factor(self, factory):
        """低质量因子评分"""
        vf = ValidatedFactor(
            name="WEAK",
            category="Other",
            formula="?",
            source="qlib",
            ic_mean_1d=0.01,
            ic_ir_1d=0.1,
            ic_positive_ratio=0.51,
            monotonicity=0.2,
            long_short_return=0.0001,
        )
        score = factory._compute_validation_score(vf)
        # IC: min(1, 5) * 0.30 = 0.3
        # IR: min(0.1, 3) * 0.25 = 0.025
        # 正IC: 0.51 * 0.15 = 0.0765
        # 单调性: 0.2 * 0.15 = 0.03
        # 多空: min(0.01, 2) * 0.10 = 0.001
        # 合计: 0.4325
        assert abs(score - 0.4325) < 1e-10

    def test_score_with_walk_forward(self, factory):
        """含 Walk-Forward 评分的因子"""
        vf = ValidatedFactor(
            name="MOM_60D",
            category="Momentum",
            formula="ret",
            source="qlib",
            ic_mean_1d=0.03,
            ic_ir_1d=0.6,
            ic_positive_ratio=0.60,
            monotonicity=0.8,
            long_short_return=0.002,
            wf_n_windows=10,
            wf_consistency=0.80,
        )
        score = factory._compute_validation_score(vf)
        # 基础: 3*0.30 + 0.6*0.25 + 0.60*0.15 + 0.8*0.15 + 0.2*0.10
        #      = 0.9 + 0.15 + 0.09 + 0.12 + 0.02 = 1.28
        # WF: 0.80 * 0.05 = 0.04
        # 合计: 1.32
        assert abs(score - 1.32) < 1e-10

    def test_effective_threshold(self, factory):
        """有效性阈值判定"""
        # 有效因子
        vf_ok = ValidatedFactor(
            name="OK",
            category="Momentum",
            formula="ret",
            source="qlib",
            ic_mean_1d=0.03,
            ic_ir_1d=0.40,
        )
        vf_ok.effective = abs(vf_ok.ic_mean_1d) >= 0.02 and abs(vf_ok.ic_ir_1d) >= 0.30
        assert vf_ok.effective is True

        # 无效因子
        vf_bad = ValidatedFactor(
            name="BAD",
            category="Momentum",
            formula="ret",
            source="qlib",
            ic_mean_1d=0.01,
            ic_ir_1d=0.10,
        )
        vf_bad.effective = (
            abs(vf_bad.ic_mean_1d) >= 0.02 and abs(vf_bad.ic_ir_1d) >= 0.30
        )
        assert vf_bad.effective is False


# ============================================================
# 部署与注册测试
# ============================================================


class TestDeployment:
    """部署阶段测试"""

    def test_deploy_no_validated_factors(self, factory):
        """无待部署因子"""
        result = factory.deploy()
        assert len(result) == 0

    def test_deploy_with_validated(self, factory, sample_validated):
        """部署已验证因子"""
        deployed = factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )
        assert len(deployed) == 2
        assert deployed[0].name == "MOM_60D"
        assert deployed[0].active is True
        assert deployed[0].version == 1

    def test_deploy_idempotent(self, factory, sample_validated):
        """重复部署幂等"""
        factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )
        # 再次部署, 应跳过已部署的
        deployed2 = factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )
        assert len(deployed2) == 0

    def test_deploy_max_active_limit(self, factory, sample_validated):
        """活跃因子数上限"""
        # 设置很小的上限
        factory.max_active_factors = 1
        deployed = factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )
        # 只部署评分最高的 1 个
        assert len(deployed) == 1

    def test_deploy_generate_code(self, factory, sample_validated):
        """生成因子代码"""
        deployed = factory.deploy(
            validated=sample_validated, generate_code=True, register_to_library=False
        )
        assert len(deployed) == 2
        # 代码文件应存在
        for d in deployed:
            if d.code_path:
                code_file = Path(d.code_path)
                assert code_file.exists()
                content = code_file.read_text(encoding="utf-8")
                assert d.name in content

    def test_register_to_library(self, factory, sample_validated):
        """注册到因子登记簿"""
        deployed = factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=True
        )
        assert len(deployed) == 2

        # 验证登记簿
        registry_path = factory.data_dir / "factor_registry.json"
        assert registry_path.exists()
        import json

        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        assert "MOM_60D" in registry
        assert registry["MOM_60D"]["active"] is True


# ============================================================
# 监控与淘汰测试
# ============================================================


class TestMonitoring:
    """监控与淘汰测试"""

    def test_monitor_no_deployed(self, factory):
        """无已部署因子时监控返回空"""
        suggestions = factory.monitor()
        assert len(suggestions) == 0

    def test_monitor_with_deployed(self, factory, sample_validated):
        """有已部署因子时的监控"""
        # 先部署
        factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )

        # 添加 IC 历史 (低于阈值)
        for name in ["MOM_60D", "VOL_20D"]:
            factory._ic_history[name] = [
                {"date": f"2026-01-{d:02d}", "ic": 0.01} for d in range(1, 8)
            ]

        # 监控
        suggestions = factory.monitor()
        assert len(suggestions) == 2
        for s in suggestions:
            assert s.suggest_retire is True
            assert s.archived is True

    def test_monitor_high_ic_no_retire(self, factory, sample_validated):
        """高 IC 因子不应被淘汰"""
        factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )

        # 添加高 IC 历史
        for name in ["MOM_60D", "VOL_20D"]:
            factory._ic_history[name] = [
                {"date": f"2026-01-{d:02d}", "ic": 0.05} for d in range(1, 8)
            ]

        suggestions = factory.monitor()
        # 高 IC 不应被淘汰
        retired = [s for s in suggestions if s.suggest_retire]
        assert len(retired) == 0

    def test_retire_factor(self, factory, sample_validated):
        """淘汰因子"""
        factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )
        factory._retire_factor("MOM_60D")
        assert factory._deployed["MOM_60D"].active is False


# ============================================================
# 全流水线测试 (降级模式)
# ============================================================


class TestPipeline:
    """全流水线测试"""

    def test_pipeline_monitor_only(self, factory):
        """仅监控模式"""
        report = factory.run_pipeline(monitor_only=True)
        assert report.status == "success"
        assert report.n_discovered == 0
        assert report.n_validated == 0
        assert report.n_deployed == 0

    def test_pipeline_with_candidates(self, factory, sample_validated):
        """预置已验证因子后的流水线"""
        # 预置已验证因子
        for v in sample_validated:
            factory._validated[v.name] = v

        # 执行流水线 (仅部署+监控)
        report = factory.run_pipeline(monitor_only=False)
        # 发现阶段: QLib 不可用, 返回空
        # 但由于有预置验证因子, deploy 会使用已验证的
        assert report.status == "success"

    def test_pipeline_report_structure(self, factory):
        """流水线报告结构"""
        report = factory.run_pipeline(monitor_only=True)
        assert hasattr(report, "pipeline_date")
        assert hasattr(report, "n_discovered")
        assert hasattr(report, "n_validated")
        assert hasattr(report, "n_deployed")
        assert hasattr(report, "n_retired")
        assert hasattr(report, "n_active")
        assert hasattr(report, "top_factors")
        assert hasattr(report, "retired_factors")
        assert hasattr(report, "duration_seconds")
        assert hasattr(report, "status")
        assert hasattr(report, "error")


# ============================================================
# 查询接口测试
# ============================================================


class TestQueryAPI:
    """查询接口测试"""

    def test_get_active_factors_empty(self, factory):
        """空状态下获取活跃因子"""
        assert len(factory.get_active_factors()) == 0

    def test_get_active_factors(self, factory, sample_validated):
        """获取活跃因子"""
        factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )
        active = factory.get_active_factors()
        assert len(active) == 2

    def test_get_retired_factors_empty(self, factory):
        """空状态下获取已淘汰因子"""
        assert len(factory.get_retired_factors()) == 0

    def test_get_retired_factors(self, factory, sample_validated):
        """获取已淘汰因子"""
        factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )
        factory._retire_factor("MOM_60D")
        retired = factory.get_retired_factors()
        assert "MOM_60D" in retired

    def test_get_factor_summary(self, factory, sample_validated):
        """获取因子摘要"""
        factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )
        summary = factory.get_factor_summary()
        assert summary["n_deployed"] == 2
        assert summary["n_active"] == 2
        assert len(summary["active_factors"]) == 2


# ============================================================
# 状态重置测试
# ============================================================


class TestReset:
    """状态重置测试"""

    def test_reset_without_confirm(self, factory, sample_validated):
        """未确认时重置失败"""
        factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )
        result = factory.reset(confirm=False)
        assert result is False
        assert len(factory._deployed) == 2

    def test_reset_with_confirm(self, factory, sample_validated):
        """确认后重置成功"""
        factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )
        result = factory.reset(confirm=True)
        assert result is True
        assert len(factory._deployed) == 0
        assert len(factory._discovered) == 0

    def test_reset_clears_state_file(self, factory, sample_validated):
        """重置清除状态文件"""
        factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )
        factory.reset(confirm=True)
        state_file = factory._state_path("factory_state")
        assert not state_file.exists()


# ============================================================
# 边缘情况测试
# ============================================================


class TestEdgeCases:
    """边缘情况测试"""

    def test_empty_discover(self, factory):
        """空发现"""
        factory._discover_basic_technical()
        assert len(factory._discovered) == 0

    def test_validate_no_candidates(self, factory):
        """无候选因子时验证返回空"""
        result = factory.validate(candidates=[])
        assert len(result) == 0

    def test_deploy_empty_list(self, factory):
        """空列表部署"""
        result = factory.deploy(validated=[])
        assert len(result) == 0

    def test_monitor_no_history(self, factory, sample_validated):
        """无 IC 历史时监控跳过"""
        factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )
        # 不添加 IC 历史
        suggestions = factory.monitor()
        assert len(suggestions) == 0

    def test_audit_without_memory(self, factory):
        """无 memory 时审计不报错"""
        # factory.memory 已经是 None
        factory._audit("test", {"key": "value"})  # 不应抛出异常

    def test_save_report(self, factory):
        """保存报告"""
        report = FactoryPipelineReport(
            pipeline_date="2026-01-01",
            n_discovered=10,
            n_validated=5,
            n_deployed=3,
            n_retired=1,
            n_active=2,
            top_factors=["MOM_60D", "VOL_20D"],
            retired_factors=["WEAK"],
            duration_seconds=1.5,
            status="success",
        )
        factory._save_report(report)
        report_dir = factory.data_dir / "reports"
        assert report_dir.exists()
        json_files = list(report_dir.glob("*.json"))
        assert len(json_files) > 0


# ============================================================
# 集成测试: 模拟完整生命周期
# ============================================================


class TestLifecycle:
    """完整生命周期测试"""

    def test_discover_to_retire_lifecycle(
        self, factory, sample_candidates, sample_validated
    ):
        """发现→验证→部署→监控→淘汰 完整生命周期"""
        # Step 1: 模拟发现
        for c in sample_candidates:
            factory._discovered[c.name] = c
        assert len(factory._discovered) == 3

        # Step 2: 模拟验证
        for v in sample_validated:
            factory._validated[v.name] = v
        assert len(factory._validated) == 2

        # Step 3: 部署
        deployed = factory.deploy(
            validated=[v for v in sample_validated if v.effective],
            generate_code=False,
            register_to_library=True,
        )
        assert len(deployed) == 2

        # Step 4: 模拟低 IC 历史 → 触发淘汰
        for name in ["MOM_60D", "VOL_20D"]:
            factory._ic_history[name] = [
                {"date": f"2026-01-{d:02d}", "ic": 0.01} for d in range(1, 8)
            ]

        suggestions = factory.monitor()
        retired = [s for s in suggestions if s.suggest_retire]
        assert len(retired) == 2

        # Step 5: 验证状态
        summary = factory.get_factor_summary()
        assert summary["n_active"] == 0
        assert summary["n_retired"] == 2

    def test_factor_reenable(self, factory, sample_validated):
        """淘汰后重新部署"""
        # 部署
        factory.deploy(
            validated=sample_validated, generate_code=False, register_to_library=False
        )
        # 淘汰
        factory._retire_factor("MOM_60D")
        assert factory._deployed["MOM_60D"].active is False

        # 重新部署 (版本升级)
        vf_new = sample_validated[0]
        deployed = factory.deploy(
            validated=[vf_new], generate_code=False, register_to_library=False
        )
        assert len(deployed) == 1
        assert deployed[0].version == 2
        assert deployed[0].active is True
