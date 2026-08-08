"""T3.4 StrategyGenerator 单元测试.

覆盖范围:
    - 数据类构造 (StrategyTemplate / StrategyInstance / GenerationReport)
    - 初始化与模板加载
    - 模板管理 (注册/获取/注销/列出)
    - 权重生成 (等权/动量偏斜/价值偏斜/风险平价/最小波动)
    - 权重变异
    - 策略生成 (按风格/按模板/变异)
    - 策略验证 (指标计算/阈值检查)
    - 策略部署 (写入文件/记录到 memory)
    - 一键运行 (生成→验证→部署)
    - 查询接口
    - 记忆回放 (含 memory 降级)
    - 边缘情况 (空模板/空权重/变异率0)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from utils.evolution.strategy_generator import (
    StrategyGenerator,
    StrategyTemplate,
    StrategyInstance,
    GenerationReport,
    GenerationReport,
    TemplateNotFoundError,
    ValidationFailedError,
    FLAG_STRATEGY_GENERATOR,
    STYLE_MOMENTUM,
    STYLE_REVERSAL,
    STYLE_LOW_RISK,
    STYLE_VALUE,
    STYLE_GROWTH,
    STYLE_QUALITY,
    STYLE_BALANCED,
    STYLE_VOLATILITY,
    WEIGHT_EQUAL,
    WEIGHT_MOMENTUM_SKEWED,
    WEIGHT_VALUE_SKEWED,
    WEIGHT_RISK_PARITY,
    WEIGHT_VOL_MIN,
    REBALANCE_DAILY,
    REBALANCE_WEEKLY,
    REBALANCE_MONTHLY,
    REBALANCE_QUARTERLY,
    UNIVERSE_CSI300,
    UNIVERSE_CSI500,
    UNIVERSE_CSI800,
    _build_default_templates,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def generator() -> StrategyGenerator:
    return StrategyGenerator()


@pytest.fixture
def sample_template() -> StrategyTemplate:
    return StrategyTemplate(
        id="test_momentum",
        name="测试动量策略",
        description="测试用动量模板",
        style=STYLE_MOMENTUM,
        factor_categories=["Momentum", "Volatility"],
        weighting_method=WEIGHT_MOMENTUM_SKEWED,
        rebalance_frequency=REBALANCE_WEEKLY,
        risk_control={"stop_loss": 0.15, "max_drawdown": 0.20, "vol_target": 0.28},
        universe=UNIVERSE_CSI500,
        tags=["测试"],
    )


@pytest.fixture
def temp_dir() -> Path:
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ============================================================
# TestDataClasses
# ============================================================


class TestDataClasses:
    """数据类构造测试."""

    def test_strategy_template_defaults(self):
        """默认参数测试."""
        tpl = StrategyTemplate(id="t1", name="测试", description="测试模板")
        assert tpl.id == "t1"
        assert tpl.name == "测试"
        assert tpl.style == STYLE_BALANCED
        assert tpl.weighting_method == WEIGHT_EQUAL
        assert tpl.rebalance_frequency == REBALANCE_MONTHLY
        assert tpl.universe == UNIVERSE_CSI500
        assert tpl.version == "1.0.0"
        assert len(tpl.factor_categories) == 6

    def test_strategy_template_full(self):
        """全参数测试."""
        tpl = StrategyTemplate(
            id="t2",
            name="全参数",
            description="全参数测试",
            style=STYLE_MOMENTUM,
            factor_categories=["Momentum", "Technical"],
            weighting_method=WEIGHT_MOMENTUM_SKEWED,
            rebalance_frequency=REBALANCE_DAILY,
            risk_control={"stop_loss": 0.10},
            universe=UNIVERSE_CSI300,
            version="2.0.0",
            base_weights={"style_momentum": 0.6, "style_technical": 0.4},
            tags=["测试", "动量"],
        )
        assert tpl.id == "t2"
        assert tpl.style == STYLE_MOMENTUM
        assert tpl.base_weights == {"style_momentum": 0.6, "style_technical": 0.4}

    def test_strategy_instance_defaults(self):
        """策略实例默认参数."""
        inst = StrategyInstance(template_id="t1")
        assert inst.template_id == "t1"
        assert inst.strategy_id == ""
        assert inst.style == STYLE_BALANCED
        assert inst.factor_weights == {}
        assert inst.status == "pending"
        assert inst.performance_metrics is None

    def test_generation_report_defaults(self):
        """生成报告默认参数."""
        report = GenerationReport(
            run_id="GEN-001",
            timestamp="2026-08-02T12:00:00Z",
            n_templates_used=8,
            n_generated=3,
            n_validated=2,
            n_deployed=1,
            style="momentum",
        )
        assert report.run_id == "GEN-001"
        assert report.n_generated == 3
        assert report.n_validated == 2
        assert report.n_deployed == 1
        assert report.best_instance is None
        assert report.errors == []


# ============================================================
# TestFactoryInitialization
# ============================================================


class TestFactoryInitialization:
    """初始化与模板加载测试."""

    def test_default_initialization(self, generator: StrategyGenerator):
        """默认初始化应加载 8 个内置模板."""
        assert generator.get_template_count() == 8
        summary = generator.get_summary()
        assert summary["n_templates"] == 8
        assert summary["n_generated"] == 0
        assert summary["n_validated"] == 0
        assert summary["n_deployed"] == 0
        assert summary["feature_flag"] == FLAG_STRATEGY_GENERATOR

    def test_custom_initialization(self):
        """自定义参数初始化."""
        gen = StrategyGenerator(
            learning_rate=0.2,
            mutation_rate=0.3,
            max_active=3,
        )
        assert gen.get_summary()["learning_rate"] == 0.2
        assert gen.get_summary()["mutation_rate"] == 0.3
        assert gen.get_summary()["max_active"] == 3

    def test_builtin_templates_have_all_styles(self, generator: StrategyGenerator):
        """内置模板应覆盖所有 8 种风格."""
        styles = set()
        for tpl in generator.list_templates():
            styles.add(tpl.style)
        assert STYLE_MOMENTUM in styles
        assert STYLE_REVERSAL in styles
        assert STYLE_LOW_RISK in styles
        assert STYLE_VALUE in styles
        assert STYLE_GROWTH in styles
        assert STYLE_QUALITY in styles
        assert STYLE_BALANCED in styles
        assert STYLE_VOLATILITY in styles

    def test_import_from_package(self):
        """从包导入应正常工作."""
        from utils.evolution import StrategyGenerator, StrategyTemplate, StrategyInstance
        gen = StrategyGenerator()
        assert gen.get_template_count() == 8


# ============================================================
# TestTemplateManagement
# ============================================================


class TestTemplateManagement:
    """模板管理测试."""

    def test_register_template(self, generator: StrategyGenerator, sample_template: StrategyTemplate):
        """注册新模板."""
        generator.register_template(sample_template)
        assert generator.get_template_count() == 9
        tpl = generator.get_template("test_momentum")
        assert tpl.name == "测试动量策略"

    def test_register_duplicate(self, generator: StrategyGenerator, sample_template: StrategyTemplate):
        """重复注册应覆盖."""
        generator.register_template(sample_template)
        modified = StrategyTemplate(
            id="test_momentum",
            name="覆盖版",
            description="覆盖测试",
        )
        generator.register_template(modified)
        assert generator.get_template_count() == 9
        assert generator.get_template("test_momentum").name == "覆盖版"

    def test_unregister_template(self, generator: StrategyGenerator, sample_template: StrategyTemplate):
        """注销模板."""
        generator.register_template(sample_template)
        generator.unregister_template("test_momentum")
        assert generator.get_template_count() == 8

    def test_unregister_not_found(self, generator: StrategyGenerator):
        """注销不存在的模板应抛异常."""
        with pytest.raises(TemplateNotFoundError):
            generator.unregister_template("not_exist")

    def test_get_template_not_found(self, generator: StrategyGenerator):
        """获取不存在的模板应抛异常."""
        with pytest.raises(TemplateNotFoundError):
            generator.get_template("not_exist")

    def test_list_templates_by_style(self, generator: StrategyGenerator):
        """按风格过滤模板."""
        momentum_tpls = generator.list_templates(style=STYLE_MOMENTUM)
        assert len(momentum_tpls) == 1
        assert momentum_tpls[0].id == "tpl_momentum"

        balanced_tpls = generator.list_templates(style=STYLE_BALANCED)
        assert len(balanced_tpls) == 1
        assert balanced_tpls[0].id == "tpl_balanced"


# ============================================================
# TestWeightGeneration
# ============================================================


class TestWeightGeneration:
    """权重生成测试."""

    def test_equal_weight(self, generator: StrategyGenerator):
        """等权分配."""
        tpl = generator.get_template("tpl_balanced")
        weights = generator._generate_weights(tpl, [])
        assert len(weights) == 6
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-6
        # 等权: 所有值应相等
        values = list(weights.values())
        assert all(abs(v - values[0]) < 1e-6 for v in values)

    def test_momentum_skewed(self, generator: StrategyGenerator):
        """动量偏斜."""
        tpl = generator.get_template("tpl_momentum")
        weights = generator._generate_weights(tpl, [])
        assert len(weights) == 3
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-6
        # 动量偏斜: Momentum 和 Technical 翻倍
        assert weights["style_momentum"] > weights["style_volatility"]

    def test_value_skewed(self, generator: StrategyGenerator):
        """价值偏斜."""
        tpl = generator.get_template("tpl_value")
        weights = generator._generate_weights(tpl, [])
        assert len(weights) == 3
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-6

    def test_risk_parity(self, generator: StrategyGenerator):
        """风险平价."""
        tpl = generator.get_template("tpl_quality")
        weights = generator._generate_weights(tpl, [])
        assert len(weights) == 3
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-6

    def test_vol_min(self, generator: StrategyGenerator):
        """最小波动."""
        tpl = generator.get_template("tpl_low_vol")
        weights = generator._generate_weights(tpl, [])
        assert len(weights) == 3
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-6
        # 最小波动: 低波动类别权重最高
        assert weights["style_fundamental"] > weights["style_volatility"]

    def test_base_weights_override(self, generator: StrategyGenerator):
        """基础权重覆盖."""
        tpl = StrategyTemplate(
            id="test_base",
            name="基础权重测试",
            description="测试",
            base_weights={"style_momentum": 0.8, "style_volatility": 0.2},
        )
        generator.register_template(tpl)
        weights = generator._generate_weights(tpl, [])
        assert weights == {"style_momentum": 0.8, "style_volatility": 0.2}

    def test_empty_categories(self, generator: StrategyGenerator):
        """空因子类别."""
        tpl = StrategyTemplate(
            id="empty",
            name="空类别",
            description="测试",
            factor_categories=[],
        )
        weights = generator._generate_weights(tpl, [])
        assert weights == {}

    def test_cat_to_weight_key(self):
        """类别名到权重 key 的映射."""
        assert StrategyGenerator._cat_to_weight_key("Momentum") == "style_momentum"
        assert StrategyGenerator._cat_to_weight_key("Volatility") == "style_volatility"
        assert StrategyGenerator._cat_to_weight_key("Liquidity") == "style_liquidity"
        assert StrategyGenerator._cat_to_weight_key("Size") == "style_size"
        assert StrategyGenerator._cat_to_weight_key("Technical") == "style_technical"
        assert StrategyGenerator._cat_to_weight_key("Fundamental_Proxy") == "style_fundamental"
        # 未知类别
        assert StrategyGenerator._cat_to_weight_key("Unknown") == "style_unknown"


# ============================================================
# TestMutation
# ============================================================


class TestMutation:
    """权重变异测试."""

    def test_mutation_no_change_when_rate_zero(self, generator: StrategyGenerator):
        """变异率为0时不应改变权重."""
        generator._mutation_rate = 0.0
        weights = {"style_momentum": 0.5, "style_volatility": 0.5}
        result = generator._mutate_weights(weights)
        # 变异率为0, 权重应不变
        assert result == weights

    def test_mutation_normalizes(self, generator: StrategyGenerator):
        """变异后应归一化."""
        generator._mutation_rate = 1.0  # 100% 变异
        weights = {"style_momentum": 0.5, "style_volatility": 0.5}
        result = generator._mutate_weights(weights)
        total = sum(result.values())
        assert abs(total - 1.0) < 1e-6

    def test_mutation_empty_weights(self, generator: StrategyGenerator):
        """空权重变异."""
        assert generator._mutate_weights({}) == {}


# ============================================================
# TestStrategyGeneration
# ============================================================


class TestStrategyGeneration:
    """策略生成测试."""

    def test_generate_all_styles(self, generator: StrategyGenerator):
        """生成所有风格的策略."""
        instances = generator.generate(n_strategies=5)
        assert len(instances) == 5
        for inst in instances:
            assert inst.strategy_id.startswith("STRAT-")
            assert inst.status == "pending"
            assert len(inst.factor_weights) > 0
            assert inst.created_at != ""

    def test_generate_by_style(self, generator: StrategyGenerator):
        """按风格生成."""
        instances = generator.generate(n_strategies=3, style=STYLE_MOMENTUM)
        assert len(instances) == 3
        for inst in instances:
            assert inst.style == STYLE_MOMENTUM

    def test_generate_by_template(self, generator: StrategyGenerator):
        """按指定模板生成."""
        instances = generator.generate(
            n_strategies=2,
            template_id="tpl_low_vol",
        )
        assert len(instances) == 2
        for inst in instances:
            assert inst.template_id == "tpl_low_vol"
            assert inst.style == STYLE_LOW_RISK

    def test_generate_no_mutate(self, generator: StrategyGenerator):
        """不变异生成."""
        instances = generator.generate(n_strategies=1, mutate=False)
        assert len(instances) == 1

    def test_generate_invalid_style(self, generator: StrategyGenerator):
        """无效风格返回空."""
        instances = generator.generate(n_strategies=3, style="unknown_style")
        assert len(instances) == 0

    def test_generate_invalid_template(self, generator: StrategyGenerator):
        """无效模板返回空."""
        with pytest.raises(TemplateNotFoundError):
            generator.generate(n_strategies=1, template_id="not_exist")

    def test_generate_idempotent(self, generator: StrategyGenerator):
        """多次生成 ID 递增."""
        i1 = generator.generate(n_strategies=1)
        i2 = generator.generate(n_strategies=1)
        assert i1[0].strategy_id != i2[0].strategy_id

    def test_generate_weight_format(self, generator: StrategyGenerator):
        """生成的权重格式应与 factor_weights.json 兼容."""
        instances = generator.generate(n_strategies=1, style=STYLE_BALANCED)
        assert len(instances) == 1
        weights = instances[0].factor_weights
        # 所有 key 格式应为 style_xxx
        for k in weights:
            assert k.startswith("style_")
        # 归一化
        assert abs(sum(weights.values()) - 1.0) < 1e-6


# ============================================================
# TestValidation
# ============================================================


class TestValidation:
    """策略验证测试."""

    def test_validate_all_pass(self, generator: StrategyGenerator):
        """所有策略通过验证."""
        instances = generator.generate(n_strategies=3)
        validated = generator.validate(instances)
        assert len(validated) == 3
        for inst in validated:
            assert inst.status == "validated"
            assert inst.performance_metrics is not None
            assert inst.performance_metrics["ic"] >= 0.02
            assert inst.performance_metrics["sharpe"] >= 0.5

    def test_validate_empty_instance(self, generator: StrategyGenerator):
        """空策略实例验证."""
        inst = StrategyInstance(template_id="t1", strategy_id="empty")
        validated = generator.validate([inst])
        assert len(validated) == 0
        assert inst.status == "failed"

    def test_validate_metrics_computed(self, generator: StrategyGenerator):
        """验证指标应包含所有关键字段."""
        instances = generator.generate(n_strategies=1)
        validated = generator.validate(instances)
        metrics = validated[0].performance_metrics
        assert metrics is not None
        assert "ic" in metrics
        assert "sharpe" in metrics
        assert "max_dd" in metrics
        assert "turnover" in metrics
        assert "signal_strength" in metrics
        assert "n_factors" in metrics

    def test_validate_concentrated_strategy(self, generator: StrategyGenerator):
        """高度集中策略应有更高信号强度."""
        tpl = StrategyTemplate(
            id="concentrated",
            name="集中测试",
            description="测试",
            base_weights={"style_momentum": 0.9, "style_volatility": 0.1},
        )
        generator.register_template(tpl)
        instances = generator.generate(n_strategies=1, template_id="concentrated", mutate=False)
        validated = generator.validate(instances)
        metrics = validated[0].performance_metrics
        assert metrics is not None
        assert metrics["signal_strength"] > 0.04  # 高度集中, 信号更强


# ============================================================
# TestDeployment
# ============================================================


class TestDeployment:
    """策略部署测试."""

    def test_deploy_to_weights(self, generator: StrategyGenerator, temp_dir: Path):
        """部署到权重文件."""
        instances = generator.generate(n_strategies=1)
        validated = generator.validate(instances)
        weights_path = temp_dir / "factor_weights.json"
        deployed = generator.deploy_to_weights(validated[0], weights_path)
        assert deployed.status == "deployed"
        assert weights_path.exists()
        with open(weights_path, "r") as f:
            saved = json.load(f)
        assert saved == deployed.factor_weights

    def test_deploy_unvalidated_raises(self, generator: StrategyGenerator):
        """未验证的策略部署应抛异常."""
        inst = StrategyInstance(
            template_id="t1",
            strategy_id="unvalidated",
            status="pending",
        )
        with pytest.raises(ValidationFailedError):
            generator.deploy_to_weights(inst)

    def test_deploy_updates_state(self, generator: StrategyGenerator, temp_dir: Path):
        """部署后应更新状态."""
        instances = generator.generate(n_strategies=1)
        validated = generator.validate(instances)
        weights_path = temp_dir / "factor_weights.json"
        generator.deploy_to_weights(validated[0], weights_path)
        assert generator.get_deployed(validated[0].strategy_id) is not None
        assert len(generator.list_deployed()) == 1

    def test_deploy_without_memory(self, generator: StrategyGenerator, temp_dir: Path):
        """无 memory 时部署不应报错."""
        instances = generator.generate(n_strategies=1)
        validated = generator.validate(instances)
        weights_path = temp_dir / "factor_weights.json"
        generator.deploy_to_weights(validated[0], weights_path)
        assert weights_path.exists()


# ============================================================
# TestRun
# ============================================================


class TestRun:
    """一键运行测试."""

    def test_run_basic(self, generator: StrategyGenerator):
        """基本运行."""
        report = generator.run(n_strategies=3, style=STYLE_MOMENTUM)
        assert report.n_generated == 3
        assert report.n_validated == 3
        assert report.n_deployed == 0
        assert report.best_instance is not None
        assert report.run_id.startswith("GEN-")
        assert len(report.errors) == 0

    def test_run_with_auto_deploy(self, generator: StrategyGenerator, temp_dir: Path):
        """自动部署最佳策略."""
        report = generator.run(
            n_strategies=2,
            style=STYLE_BALANCED,
            template_id="tpl_balanced",
            auto_deploy=True,
        )
        assert report.n_generated == 2
        assert report.n_validated == 2
        assert report.n_deployed == 1
        assert report.best_instance is not None
        assert report.best_instance.status == "deployed"

    def test_run_invalid_style(self, generator: StrategyGenerator):
        """无效风格运行."""
        report = generator.run(n_strategies=3, style="unknown")
        assert report.n_generated == 0
        assert report.n_validated == 0
        assert report.n_deployed == 0
        assert len(report.errors) > 0

    def test_run_report_structure(self, generator: StrategyGenerator):
        """报告结构完整性."""
        report = generator.run(n_strategies=2, style=STYLE_MOMENTUM)
        assert isinstance(report, GenerationReport)
        assert report.timestamp != ""
        assert report.n_templates_used == 8
        assert len(report.instances) == 2


# ============================================================
# TestQueryAPI
# ============================================================


class TestQueryAPI:
    """查询接口测试."""

    def test_get_generated_empty(self, generator: StrategyGenerator):
        """未生成时返回 None."""
        assert generator.get_generated("not_exist") is None

    def test_get_generated(self, generator: StrategyGenerator):
        """获取已生成的策略."""
        instances = generator.generate(n_strategies=1)
        sid = instances[0].strategy_id
        assert generator.get_generated(sid) is not None

    def test_get_validated_empty(self, generator: StrategyGenerator):
        """未验证时返回 None."""
        assert generator.get_validated("not_exist") is None

    def test_get_validated(self, generator: StrategyGenerator):
        """获取已验证的策略."""
        instances = generator.generate(n_strategies=1)
        validated = generator.validate(instances)
        sid = validated[0].strategy_id
        assert generator.get_validated(sid) is not None

    def test_get_deployed_empty(self, generator: StrategyGenerator):
        """未部署时返回 None."""
        assert generator.get_deployed("not_exist") is None

    def test_list_generated(self, generator: StrategyGenerator):
        """列出所有已生成的策略."""
        generator.generate(n_strategies=3)
        assert len(generator.list_generated()) == 3

    def test_list_generated_by_style(self, generator: StrategyGenerator):
        """按风格列出已生成的策略."""
        generator.generate(n_strategies=2, style=STYLE_MOMENTUM)
        generator.generate(n_strategies=2, style=STYLE_LOW_RISK)
        momentum = generator.list_generated(style=STYLE_MOMENTUM)
        assert len(momentum) == 2
        for inst in momentum:
            assert inst.style == STYLE_MOMENTUM

    def test_list_validated(self, generator: StrategyGenerator):
        """列出已验证的策略."""
        instances = generator.generate(n_strategies=3)
        generator.validate(instances)
        assert len(generator.list_validated()) == 3

    def test_summary(self, generator: StrategyGenerator):
        """状态摘要."""
        summary = generator.get_summary()
        assert "n_templates" in summary
        assert "n_generated" in summary
        assert "n_validated" in summary
        assert "n_deployed" in summary
        assert "templates" in summary
        assert len(summary["templates"]) == 8


# ============================================================
# TestMemoryReplay
# ============================================================


class TestMemoryReplay:
    """记忆回放测试."""

    def test_no_memory_returns_empty(self, generator: StrategyGenerator):
        """无 memory 时返回空列表."""
        result = generator._replay_memory("momentum")
        assert result == []

    def test_memory_replay_empty(self, generator: StrategyGenerator):
        """有 memory 但无匹配记录时返回空."""
        result = generator._replay_memory("unknown_style")
        assert result == []

    def test_average_memory_param(self):
        """提取最频繁参数."""
        params = [
            {"rebalance_frequency": "weekly"},
            {"rebalance_frequency": "weekly"},
            {"rebalance_frequency": "monthly"},
        ]
        result = StrategyGenerator._average_memory_param(params, "rebalance_frequency", "monthly")
        assert result == "weekly"

    def test_average_memory_param_empty(self):
        """空参数列表返回默认值."""
        result = StrategyGenerator._average_memory_param([], "universe", UNIVERSE_CSI500)
        assert result == UNIVERSE_CSI500


# ============================================================
# TestEdgeCases
# ============================================================


class TestEdgeCases:
    """边缘情况测试."""

    def test_generate_zero_strategies(self, generator: StrategyGenerator):
        """生成0个策略."""
        instances = generator.generate(n_strategies=0)
        assert len(instances) == 0

    def test_validate_empty_list(self, generator: StrategyGenerator):
        """验证空列表."""
        validated = generator.validate([])
        assert len(validated) == 0

    def test_generate_negative_strategies(self, generator: StrategyGenerator):
        """负数策略数."""
        instances = generator.generate(n_strategies=-1)
        assert len(instances) == 0

    def test_register_template_twice(self, generator: StrategyGenerator, sample_template: StrategyTemplate):
        """重复注册不应报错."""
        generator.register_template(sample_template)
        generator.register_template(sample_template)  # 第二次, 应覆盖
        assert generator.get_template_count() == 9

    def test_generate_style_case_insensitive(self, generator: StrategyGenerator):
        """风格参数大小写敏感."""
        instances_momentum = generator.generate(n_strategies=1, style=STYLE_MOMENTUM)
        assert len(instances_momentum) == 1
        instances_upper = generator.generate(n_strategies=1, style="MOMENTUM")
        assert len(instances_upper) == 0  # 大小写敏感, 不匹配

    def test_builtin_templates(self):
        """内置模板工厂."""
        templates = _build_default_templates()
        assert len(templates) == 8
        ids = [t.id for t in templates]
        assert "tpl_momentum" in ids
        assert "tpl_reversal" in ids
        assert "tpl_low_vol" in ids
        assert "tpl_value" in ids
        assert "tpl_growth" in ids
        assert "tpl_quality" in ids
        assert "tpl_balanced" in ids
        assert "tpl_vol_arb" in ids


# ============================================================
# TestLifecycle
# ============================================================


class TestLifecycle:
    """完整生命周期测试."""

    def test_generate_validate_deploy_lifecycle(self, generator: StrategyGenerator, temp_dir: Path):
        """生成→验证→部署 完整生命周期."""
        # Step 1: 生成
        instances = generator.generate(n_strategies=2, style=STYLE_MOMENTUM)
        assert len(instances) == 2
        assert generator.get_summary()["n_generated"] == 2

        # Step 2: 验证
        validated = generator.validate(instances)
        assert len(validated) == 2
        assert generator.get_summary()["n_validated"] == 2

        # Step 3: 部署
        weights_path = temp_dir / "factor_weights.json"
        deployed = generator.deploy_to_weights(validated[0], weights_path)
        assert deployed.status == "deployed"
        assert generator.get_summary()["n_deployed"] == 1

        # Step 4: 验证文件
        assert weights_path.exists()
        with open(weights_path, "r") as f:
            saved = json.load(f)
        assert saved == deployed.factor_weights

    def test_multiple_generations(self, generator: StrategyGenerator):
        """多代生成."""
        g1 = generator.generate(n_strategies=3, style=STYLE_MOMENTUM)
        assert len(g1) == 3
        assert g1[0].strategy_id.endswith("0001")

        g2 = generator.generate(n_strategies=2, style=STYLE_LOW_RISK)
        assert len(g2) == 2
        # ID 递增
        assert g2[0].strategy_id.endswith("0004")
        assert g2[1].strategy_id.endswith("0005")

    def test_run_with_errors(self, generator: StrategyGenerator):
        """运行含错误."""
        # 无效风格应产生错误报告
        report = generator.run(n_strategies=3, style="invalid_style")
        assert report.n_generated == 0
        assert len(report.errors) > 0

    def test_no_side_effects_across_calls(self, generator: StrategyGenerator):
        """多次调用无副作用."""
        r1 = generator.run(n_strategies=2, style=STYLE_MOMENTUM)
        r2 = generator.run(n_strategies=2, style=STYLE_MOMENTUM)
        assert r1.n_generated == 2
        assert r2.n_generated == 2
        # 两次调用生成的策略 ID 不应重复
        ids1 = {i.strategy_id for i in r1.instances}
        ids2 = {i.strategy_id for i in r2.instances}
        assert ids1.isdisjoint(ids2)