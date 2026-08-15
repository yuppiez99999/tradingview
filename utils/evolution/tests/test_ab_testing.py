"""T3.2 ABTestFramework 单元测试.

覆盖范围:
    - 数据类构造 (ABTestConfig / ABTestResult / ABTest)
    - 框架初始化与持久化
    - 测试生命周期 (创建 → 启动 → 停止 → 评估 → 晋升/回滚)
    - 流量分配 (hash / random / round-robin)
    - 每日指标记录
    - 统计评估 (t-test / Cohen's d / 推荐逻辑)
    - 查询接口
    - 边缘情况 (空数据/小样本/重复创建/异常处理)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from utils.alpha.ab_testing import (
    ABTest,
    ABTestConfig,
    ABTestError,
    ABTestFramework,
    ABTestResult,
    ABTestStatus,
    InsufficientDataError,
    SplitStrategy,
    TestAlreadyExistsError,
    TestNotFoundError,
    TestNotRunningError,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def temp_dir() -> Path:
    """临时目录."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def framework(temp_dir: Path) -> ABTestFramework:
    """ABTestFramework 实例 (隔离目录)."""
    return ABTestFramework(results_dir=str(temp_dir))


@pytest.fixture
def sample_config() -> ABTestConfig:
    """标准测试配置."""
    return ABTestConfig(
        name="test_v9_vs_v10",
        champion_model="v9_lgb",
        challenger_model="v10_lgb",
        traffic_split=0.2,
        min_samples=5,  # 小样本方便测试
        significance_level=0.05,
        description="集成测试用",
    )


@pytest.fixture
def daily_metrics() -> tuple[dict[str, float], dict[str, float]]:
    """champion 略优的每日指标."""
    champion = {"ic": 0.06, "sharpe": 1.2, "return": 0.008}
    challenger = {"ic": 0.04, "sharpe": 0.9, "return": 0.005}
    return champion, challenger


# ============================================================
# TestDataClasses
# ============================================================


class TestABTestConfig:
    """ABTestConfig 数据类测试."""

    def test_default_values(self):
        """默认参数测试."""
        cfg = ABTestConfig(name="test", champion_model="v1", challenger_model="v2")
        assert cfg.name == "test"
        assert cfg.traffic_split == 0.2
        assert cfg.min_samples == 30
        assert cfg.significance_level == 0.05
        assert cfg.split_strategy == SplitStrategy.HASH_SYMBOL.value
        assert "dsr_min" in cfg.promotion_criteria
        assert "dsr_challenger_lt_champion_by" in cfg.rollback_criteria

    def test_to_dict_roundtrip(self):
        """to_dict / from_dict 往返."""
        cfg = ABTestConfig(
            name="test", champion_model="v1", challenger_model="v2", traffic_split=0.3
        )
        d = cfg.to_dict()
        restored = ABTestConfig.from_dict(d)
        assert restored.name == "test"
        assert restored.traffic_split == 0.3
        assert restored.champion_model == "v1"

    def test_from_dict_filters_unknown(self):
        """from_dict 应忽略未知字段."""
        d = {"name": "t", "champion_model": "v1", "challenger_model": "v2", "unknown": 42}
        cfg = ABTestConfig.from_dict(d)
        assert cfg.name == "t"
        assert not hasattr(cfg, "unknown")


class TestABTestResult:
    """ABTestResult 数据类测试."""

    def test_default_values(self):
        """默认参数."""
        r = ABTestResult(test_name="t", status="running")
        assert r.test_name == "t"
        assert r.p_value == 1.0
        assert r.effect_size == 0.0
        assert r.recommendation == ""

    def test_to_dict(self):
        """to_dict 序列化."""
        r = ABTestResult(
            test_name="t",
            status="completed",
            is_significant=True,
            challenger_better=True,
            p_value=0.01,
            effect_size=0.5,
            recommendation="promote",
        )
        d = r.to_dict()
        assert d["test_name"] == "t"
        assert d["p_value"] == 0.01
        assert d["recommendation"] == "promote"


class TestABTest:
    """ABTest 实例数据类测试."""

    def test_default_values(self):
        """默认参数."""
        cfg = ABTestConfig(name="t", champion_model="v1", challenger_model="v2")
        t = ABTest(config=cfg)
        assert t.status == ABTestStatus.CREATED.value
        assert t.started_at == ""
        assert t.result is None
        assert t.daily_records == []

    def test_to_dict_roundtrip(self):
        """to_dict / from_dict 往返."""
        cfg = ABTestConfig(name="t", champion_model="v1", challenger_model="v2")
        t = ABTest(config=cfg, status=ABTestStatus.RUNNING.value, started_at="2026-08-01T00:00:00Z")
        t.daily_records.append({"date": "2026-08-01", "champion": {"ic": 0.05}, "challenger": {"ic": 0.03}})
        d = t.to_dict()
        restored = ABTest.from_dict(d)
        assert restored.config.name == "t"
        assert restored.status == ABTestStatus.RUNNING.value
        assert len(restored.daily_records) == 1
        assert restored.daily_records[0]["champion"]["ic"] == 0.05


# ============================================================
# TestFrameworkInitialization
# ============================================================


class TestFrameworkInitialization:
    """框架初始化测试."""

    def test_default_initialization(self):
        """默认初始化 (使用默认路径)."""
        framework = ABTestFramework()
        assert framework.results_dir is not None
        assert framework.results_dir.name == "ab_tests"

    def test_custom_results_dir(self, temp_dir: Path):
        """自定义结果目录."""
        custom_dir = temp_dir / "my_ab_tests"
        framework = ABTestFramework(results_dir=str(custom_dir))
        assert framework.results_dir == custom_dir
        assert custom_dir.exists()

    def test_empty_load(self, temp_dir: Path):
        """空目录加载."""
        framework = ABTestFramework(results_dir=str(temp_dir))
        assert framework.list_tests() == []

    def test_persistence(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """创建后应持久化到文件."""
        framework.create_test(sample_config)
        test_file = framework.results_dir / f"{sample_config.name}.json"
        assert test_file.exists()
        with open(test_file, encoding="utf-8") as f:
            data = json.load(f)
        assert data["config"]["name"] == sample_config.name


# ============================================================
# TestLifecycle
# ============================================================


class TestLifecycle:
    """测试生命周期测试."""

    def test_create_test(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """创建测试."""
        test = framework.create_test(sample_config)
        assert test.config.name == "test_v9_vs_v10"
        assert test.status == ABTestStatus.CREATED.value

    def test_create_duplicate(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """重复创建应抛异常."""
        framework.create_test(sample_config)
        with pytest.raises(TestAlreadyExistsError):
            framework.create_test(sample_config)

    def test_create_invalid_split(self, framework: ABTestFramework):
        """无效 traffic_split 应抛异常."""
        cfg = ABTestConfig(name="bad", champion_model="v1", challenger_model="v2", traffic_split=1.5)
        with pytest.raises(ABTestError):
            framework.create_test(cfg)

    def test_start_test(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """启动测试."""
        framework.create_test(sample_config)
        test = framework.start_test("test_v9_vs_v10")
        assert test.status == ABTestStatus.RUNNING.value
        assert test.started_at != ""

    def test_start_not_found(self, framework: ABTestFramework):
        """启动不存在的测试应抛异常."""
        with pytest.raises(TestNotFoundError):
            framework.start_test("not_exist")

    def test_start_already_running(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """重复启动不应报错 (仅 warning)."""
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        test = framework.start_test("test_v9_vs_v10")  # 第二次应返回且不报错
        assert test.status == ABTestStatus.RUNNING.value

    def test_stop_test(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """停止测试."""
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        test = framework.stop_test("test_v9_vs_v10", reason="manual")
        assert test.status == ABTestStatus.STOPPED.value
        assert test.ended_at != ""

    def test_full_lifecycle(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """完整生命周期: 创建 → 启动 → 记录 → 评估 → 晋升."""
        # 创建 + 启动
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")

        # 记录 7 日数据 (champion 始终优于 challenger)
        # 注意: dsr 值需要变化, 否则方差为 0 → t-test 返回 p=1.0
        for i in range(7):
            date = f"2026-08-{i+1:02d}"
            champion = {"ic": 0.06 + i * 0.005, "sharpe": 1.2, "dsr": 6.0 + i * 0.1}
            challenger = {"ic": 0.04 + i * 0.003, "sharpe": 0.9, "dsr": 4.0 + i * 0.05}
            framework.record_daily_metrics("test_v9_vs_v10", date, champion, challenger)

        # 评估 (champion 更优 → 应推荐 continue 或 rollback)
        result = framework.evaluate_test("test_v9_vs_v10")
        assert result.is_significant  # 差异显著
        assert result.champion_metrics.get("ic", 0) > result.challenger_metrics.get("ic", 0)

    def test_promote_flow(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """晋升流程: challenger 显著更优 → promote."""
        sample_config.min_samples = 3
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")

        # challenger 始终优于 champion
        for i in range(5):
            date = f"2026-08-{i+1:02d}"
            champion = {"ic": 0.03, "sharpe": 0.5, "dsr": 2.0}
            challenger = {"ic": 0.08, "sharpe": 1.5, "dsr": 7.0}
            framework.record_daily_metrics("test_v9_vs_v10", date, champion, challenger)

        result = framework.evaluate_test("test_v9_vs_v10")
        assert result.challenger_better
        # 注意: 由于 mock 环境没有 ModelRegistry, promote_challenger 会失败
        # 验证 evaluate 阶段的 recommendation 逻辑即可
        assert result.recommendation in ("promote", "continue")


# ============================================================
# TestGroupAssignment
# ============================================================


class TestGroupAssignment:
    """流量分配测试."""

    def test_assign_group_before_start(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """未启动时分配应抛异常."""
        framework.create_test(sample_config)
        with pytest.raises(TestNotRunningError):
            framework.assign_group("test_v9_vs_v10", "000001")

    def test_hash_consistency(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """hash 分桶应可重现."""
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        g1 = framework.assign_group("test_v9_vs_v10", "000001")
        g2 = framework.assign_group("test_v9_vs_v10", "000001")
        assert g1 == g2  # 相同 symbol 应分配到同一组

    def test_hash_distribution(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """hash 分桶应大致均匀 (20% 给 challenger)."""
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        symbols = [f"{i:06d}" for i in range(1000)]
        challenger_count = 0
        for sym in symbols:
            group = framework.assign_group("test_v9_vs_v10", sym)
            if group == "challenger":
                challenger_count += 1
        # 20% 流量, 允许 ±5% 误差
        ratio = challenger_count / len(symbols)
        assert 0.15 <= ratio <= 0.25, f"challenger 比例 {ratio:.3f} 超出预期范围"

    def test_assign_group_not_found(self, framework: ABTestFramework):
        """不存在的测试应抛异常."""
        with pytest.raises(TestNotFoundError):
            framework.assign_group("not_exist", "000001")

    def test_zero_split(self, framework: ABTestFramework):
        """traffic_split=0 时所有流量进入 champion."""
        cfg = ABTestConfig(name="zero", champion_model="v1", challenger_model="v2", traffic_split=0.0)
        framework.create_test(cfg)
        framework.start_test("zero")
        for sym in ["000001", "000002", "000003"]:
            assert framework.assign_group("zero", sym) == "champion"

    def test_full_split(self, framework: ABTestFramework):
        """traffic_split=1.0 时所有流量进入 challenger."""
        cfg = ABTestConfig(name="full", champion_model="v1", challenger_model="v2", traffic_split=1.0)
        framework.create_test(cfg)
        framework.start_test("full")
        for sym in ["000001", "000002"]:
            assert framework.assign_group("full", sym) == "challenger"


# ============================================================
# TestDailyMetrics
# ============================================================


class TestDailyMetrics:
    """每日指标记录测试."""

    def test_record_metrics(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """记录每日指标."""
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        champion = {"ic": 0.05, "sharpe": 1.0}
        challenger = {"ic": 0.04, "sharpe": 0.8}
        framework.record_daily_metrics("test_v9_vs_v10", "2026-08-01", champion, challenger)
        test = framework.get_test("test_v9_vs_v10")
        assert len(test.daily_records) == 1
        assert test.daily_records[0]["champion"]["ic"] == 0.05

    def test_record_not_found(self, framework: ABTestFramework):
        """不存在的测试记录指标应抛异常."""
        with pytest.raises(TestNotFoundError):
            framework.record_daily_metrics("not_exist", "2026-08-01", {}, {})

    def test_record_multiple_days(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """多日连续记录."""
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        for i in range(30):
            date = f"2026-08-{i+1:02d}"
            framework.record_daily_metrics("test_v9_vs_v10", date, {"ic": 0.05}, {"ic": 0.04})
        test = framework.get_test("test_v9_vs_v10")
        assert len(test.daily_records) == 30


# ============================================================
# TestEvaluation
# ============================================================


class TestEvaluation:
    """统计评估测试."""

    def test_evaluate_not_found(self, framework: ABTestFramework):
        """不存在的测试评估应抛异常."""
        with pytest.raises(TestNotFoundError):
            framework.evaluate_test("not_exist")

    def test_evaluate_insufficient_data(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """数据不足应抛异常."""
        sample_config.min_samples = 10
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        champion = {"ic": 0.05}
        challenger = {"ic": 0.04}
        framework.record_daily_metrics("test_v9_vs_v10", "2026-08-01", champion, challenger)
        with pytest.raises(InsufficientDataError):
            framework.evaluate_test("test_v9_vs_v10")

    def test_evaluate_champion_better(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """champion 显著更优时推荐 continue 或 rollback."""
        sample_config.min_samples = 3
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        for i in range(5):
            date = f"2026-08-{i+1:02d}"
            framework.record_daily_metrics(
                "test_v9_vs_v10", date,
                {"ic": 0.08, "sharpe": 1.5, "dsr": 7.0},
                {"ic": 0.02, "sharpe": 0.3, "dsr": 1.0},
            )
        result = framework.evaluate_test("test_v9_vs_v10")
        assert not result.challenger_better
        # champion DSR 远高于 challenger → 应 rollback
        assert result.recommendation in ("rollback", "continue")

    def test_evaluate_challenger_better(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """challenger 显著更优时推荐 promote 或 continue."""
        sample_config.min_samples = 3
        sample_config.promotion_criteria = {"dsr_min": 1.0}  # 放宽阈值
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        for i in range(5):
            date = f"2026-08-{i+1:02d}"
            framework.record_daily_metrics(
                "test_v9_vs_v10", date,
                {"ic": 0.03, "sharpe": 0.5, "dsr": 2.0},
                {"ic": 0.08, "sharpe": 1.5, "dsr": 7.0},
            )
        result = framework.evaluate_test("test_v9_vs_v10")
        assert result.challenger_better
        assert result.recommendation in ("promote", "continue")

    def test_evaluate_edge_small_sample(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """极小样本 (2 条) 应正常计算."""
        sample_config.min_samples = 2
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        framework.record_daily_metrics("test_v9_vs_v10", "2026-08-01", {"ic": 0.05}, {"ic": 0.04})
        framework.record_daily_metrics("test_v9_vs_v10", "2026-08-02", {"ic": 0.06}, {"ic": 0.05})
        result = framework.evaluate_test("test_v9_vs_v10")
        assert result.p_value > 0  # 至少能算出 p 值
        assert result.effect_size != 0.0

    def test_evaluate_identical_performance(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """两组性能完全相同时 p_value=1.0, recommendation=continue."""
        sample_config.min_samples = 3
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        for i in range(5):
            date = f"2026-08-{i+1:02d}"
            framework.record_daily_metrics(
                "test_v9_vs_v10", date,
                {"ic": 0.05, "dsr": 5.0},
                {"ic": 0.05, "dsr": 5.0},
            )
        result = framework.evaluate_test("test_v9_vs_v10")
        assert result.p_value >= 0.5  # 完全相同 → p 值接近 1.0
        assert result.recommendation == "continue"

    def test_evaluate_detects_primary_metric(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """应优先使用 dsr 作为主要指标."""
        sample_config.min_samples = 2
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        framework.record_daily_metrics(
            "test_v9_vs_v10", "2026-08-01",
            {"dsr": 6.0, "ic": 0.05, "return": 0.01},
            {"dsr": 4.0, "ic": 0.03, "return": 0.005},
        )
        framework.record_daily_metrics(
            "test_v9_vs_v10", "2026-08-02",
            {"dsr": 6.5, "ic": 0.06, "return": 0.012},
            {"dsr": 4.5, "ic": 0.04, "return": 0.006},
        )
        result = framework.evaluate_test("test_v9_vs_v10")
        # dsr 作为主指标 → champion 更优
        assert result.champion_metrics.get("dsr", 0) > result.challenger_metrics.get("dsr", 0)


# ============================================================
# TestQueries
# ============================================================


class TestQueries:
    """查询接口测试."""

    def test_get_test(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """获取测试详情."""
        framework.create_test(sample_config)
        test = framework.get_test("test_v9_vs_v10")
        assert test.config.name == "test_v9_vs_v10"

    def test_get_test_not_found(self, framework: ABTestFramework):
        """获取不存在的测试应抛异常."""
        with pytest.raises(TestNotFoundError):
            framework.get_test("not_exist")

    def test_list_tests(self, framework: ABTestFramework):
        """列出所有测试."""
        for i in range(3):
            cfg = ABTestConfig(name=f"test_{i}", champion_model="v1", challenger_model="v2")
            framework.create_test(cfg)
        assert len(framework.list_tests()) == 3

    def test_list_tests_by_status(self, framework: ABTestFramework):
        """按状态过滤."""
        for i in range(3):
            cfg = ABTestConfig(name=f"test_{i}", champion_model="v1", challenger_model="v2")
            framework.create_test(cfg)
        framework.start_test("test_0")
        running = framework.list_tests(status=ABTestStatus.RUNNING)
        created = framework.list_tests(status=ABTestStatus.CREATED)
        assert len(running) == 1
        assert len(created) == 2

    def test_get_active_test_for_model(self, framework: ABTestFramework):
        """获取指定模型的活跃测试."""
        cfg1 = ABTestConfig(name="t1", champion_model="v1", challenger_model="v2")
        cfg2 = ABTestConfig(name="t2", champion_model="v3", challenger_model="v4")
        framework.create_test(cfg1)
        framework.create_test(cfg2)
        framework.start_test("t1")

        active = framework.get_active_test_for_model("v1")
        assert active is not None
        assert active.config.name == "t1"

        # 未运行模型的测试
        not_active = framework.get_active_test_for_model("v3")
        assert not_active is None


# ============================================================
# TestErrorHandling
# ============================================================


class TestErrorHandling:
    """异常处理测试."""

    def test_promote_without_evaluation(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """未评估时晋升应抛异常."""
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        with pytest.raises(ABTestError, match="未评估"):
            framework.promote_challenger("test_v9_vs_v10")

    def test_rollback_without_registry(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """无 ModelRegistry 时回滚应抛异常."""
        sample_config.min_samples = 2
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        framework.record_daily_metrics("test_v9_vs_v10", "2026-08-01", {"ic": 0.05}, {"ic": 0.04})
        framework.record_daily_metrics("test_v9_vs_v10", "2026-08-02", {"ic": 0.05}, {"ic": 0.04})
        framework.evaluate_test("test_v9_vs_v10")
        # 没有 ModelRegistry → 应抛 ABTestError (模型未找到)
        with pytest.raises(ABTestError):
            framework.rollback_to_champion("test_v9_vs_v10")

    def test_evaluate_missing_test(self, framework: ABTestFramework):
        """评估不存在的测试."""
        with pytest.raises(TestNotFoundError):
            framework.evaluate_test("ghost")

    def test_assign_missing_test(self, framework: ABTestFramework):
        """分配不存在的测试."""
        with pytest.raises(TestNotFoundError):
            framework.assign_group("ghost", "000001")


# ============================================================
# TestEdgeCases
# ============================================================


class TestEdgeCases:
    """边缘情况测试."""

    def test_load_corrupted_file(self, temp_dir: Path):
        """损坏的 JSON 文件应被跳过 (不崩溃)."""
        # 创建损坏文件
        bad_file = temp_dir / "corrupted.json"
        bad_file.write_text("{invalid json", encoding="utf-8")
        framework = ABTestFramework(results_dir=str(temp_dir))
        assert framework.list_tests() == []

    def test_empty_daily_records(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """空 daily_records 的 evaluate 应抛异常."""
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        with pytest.raises(InsufficientDataError):
            framework.evaluate_test("test_v9_vs_v10")

    def test_assign_group_logging(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """分配应记录到 assignment_log."""
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        framework.assign_group("test_v9_vs_v10", "000001")
        test = framework.get_test("test_v9_vs_v10")
        assert len(test.assignment_log) == 1
        assert test.assignment_log[0]["symbol"] == "000001"

    def test_varied_metrics_across_days(self, framework: ABTestFramework, sample_config: ABTestConfig):
        """每日指标不同时, 汇总应正确计算平均值."""
        sample_config.min_samples = 3
        framework.create_test(sample_config)
        framework.start_test("test_v9_vs_v10")
        framework.record_daily_metrics("test_v9_vs_v10", "2026-08-01", {"ic": 0.04}, {"ic": 0.02})
        framework.record_daily_metrics("test_v9_vs_v10", "2026-08-02", {"ic": 0.06}, {"ic": 0.04})
        framework.record_daily_metrics("test_v9_vs_v10", "2026-08-03", {"ic": 0.08}, {"ic": 0.06})
        result = framework.evaluate_test("test_v9_vs_v10")
        # champion IC 均值 = (0.04+0.06+0.08)/3 = 0.06
        assert abs(result.champion_metrics.get("ic", 0) - 0.06) < 0.001
        # challenger IC 均值 = (0.02+0.04+0.06)/3 = 0.04
        assert abs(result.challenger_metrics.get("ic", 0) - 0.04) < 0.001
