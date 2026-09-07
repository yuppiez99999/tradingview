"""
PluginRegistry + 插件化协调器 单元测试
=====================================

测试 W.C.3 deepseek-harness 插件化重构:
- PluginRegistry 注册/卸载/列举/优先级排序
- 路由插件 (BudgetGuard/Intraday/DeepResearch/MacroAnalysis/Default)
- 冲突检测插件 (MajorityVote/WeightedVote)
- 向后兼容: USE_PLUGIN_COORDINATOR=false 走旧路径
- 插件路径: USE_PLUGIN_COORDINATOR=true 走 PluginRegistry
- shadow 比对: 插件路径与旧路径决策一致性
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.ai_coordinator import (  # noqa: E402
    AICoordinator,
    Priority,
    TaskType,
)
from utils.ai_coordinator_plugins.base import (  # noqa: E402
    ConflictContext,
    RoutingContext,
)
from utils.ai_coordinator_plugins.conflict_detection_plugin import (  # noqa: E402
    MajorityVotePlugin,
    WeightedVotePlugin,
    create_default_conflict_plugins,
)
from utils.ai_coordinator_plugins.registry import (  # noqa: E402
    PluginRegistry,
    PluginRegistryError,
    get_registry,
    reset_registry,
)
from utils.ai_coordinator_plugins.routing_plugin import (  # noqa: E402
    BudgetGuardRoutingPlugin,
    DeepResearchRoutingPlugin,
    DefaultRoutingPlugin,
    IntradayRoutingPlugin,
    MacroAnalysisRoutingPlugin,
    create_default_routing_plugins,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        Path(path).unlink(missing_ok=True)
    except (ValueError, TypeError, OSError):
        pass


@pytest.fixture
def registry():
    r = PluginRegistry()
    yield r
    r.clear()


def _make_routing_context(
    task_type=TaskType.DAILY_REPORT, priority=Priority.MEDIUM, budget_ratio=0.1
):
    return RoutingContext(
        task_type=task_type,
        priority=priority,
        budget_ratio=budget_ratio,
        token_used_today=int(budget_ratio * 500000),
        daily_token_budget=500000,
    )


def _make_conflict_context():
    return ConflictContext(
        decisions_by_source={
            "ai_hedge": {"600519": "BUY", "000001": "HOLD"},
            "glm5": {"600519": "SELL", "000001": "HOLD"},
            "ml": {"600519": "BUY", "000001": "SELL"},
        }
    )


# ============================================================
# 1. PluginRegistry 注册/卸载/列举
# ============================================================


class TestPluginRegistry:
    def test_register_routing_plugin(self, registry):
        plugin = IntradayRoutingPlugin()
        registry.register(plugin)
        assert len(registry) == 1
        assert registry.get_plugin("intraday") is plugin

    def test_register_conflict_plugin(self, registry):
        plugin = MajorityVotePlugin()
        registry.register(plugin)
        assert len(registry) == 1
        assert registry.get_plugin("majority_vote") is plugin

    def test_register_duplicate_raises(self, registry):
        registry.register(IntradayRoutingPlugin())
        with pytest.raises(PluginRegistryError, match="插件已存在"):
            registry.register(IntradayRoutingPlugin())

    def test_unregister(self, registry):
        registry.register(IntradayRoutingPlugin())
        removed = registry.unregister("intraday")
        assert removed is not None
        assert removed.name == "intraday"
        assert len(registry) == 0

    def test_unregister_nonexistent(self, registry):
        result = registry.unregister("nonexistent")
        assert result is None

    def test_priority_sorting(self, registry):
        registry.register(DefaultRoutingPlugin())
        registry.register(IntradayRoutingPlugin())
        registry.register(BudgetGuardRoutingPlugin())
        names = [p["name"] for p in registry.list_routing_plugins()]
        assert names == ["budget_guard", "intraday", "default"]

    def test_list_routing_plugins(self, registry):
        for p in create_default_routing_plugins():
            registry.register(p)
        listed = registry.list_routing_plugins()
        assert len(listed) == 5
        assert listed[0]["name"] == "budget_guard"
        assert listed[0]["priority"] == 100

    def test_list_conflict_plugins(self, registry):
        for p in create_default_conflict_plugins():
            registry.register(p)
        listed = registry.list_conflict_plugins()
        assert len(listed) == 2
        assert listed[0]["name"] == "weighted_vote"

    def test_clear(self, registry):
        registry.register(IntradayRoutingPlugin())
        registry.register(MajorityVotePlugin())
        registry.clear()
        assert len(registry) == 0

    def test_get_registry_singleton(self):
        reset_registry()
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2
        reset_registry()


# ============================================================
# 2. 路由插件
# ============================================================


class TestRoutingPlugins:
    def test_budget_guard_high_budget(self):
        plugin = BudgetGuardRoutingPlugin()
        ctx = _make_routing_context(budget_ratio=0.85, priority=Priority.MEDIUM)
        assert plugin.can_handle(ctx) is True
        result = plugin.handle(ctx)
        assert result.model == "mlx_qwen3_8b"

    def test_budget_guard_low_budget(self):
        plugin = BudgetGuardRoutingPlugin()
        ctx = _make_routing_context(budget_ratio=0.5, priority=Priority.MEDIUM)
        assert plugin.can_handle(ctx) is False

    def test_budget_guard_critical_priority(self):
        plugin = BudgetGuardRoutingPlugin()
        ctx = _make_routing_context(budget_ratio=0.95, priority=Priority.CRITICAL)
        assert plugin.can_handle(ctx) is False

    def test_intraday(self):
        plugin = IntradayRoutingPlugin()
        ctx = _make_routing_context(task_type=TaskType.INTRADAY_DECISION)
        assert plugin.can_handle(ctx) is True
        assert plugin.handle(ctx).model == "mlx_qwen3_8b"

    def test_intraday_not_match(self):
        plugin = IntradayRoutingPlugin()
        ctx = _make_routing_context(task_type=TaskType.DEEP_RESEARCH)
        assert plugin.can_handle(ctx) is False

    def test_deep_research_low_budget(self):
        plugin = DeepResearchRoutingPlugin()
        ctx = _make_routing_context(task_type=TaskType.DEEP_RESEARCH, budget_ratio=0.3)
        assert plugin.can_handle(ctx) is True
        assert plugin.handle(ctx).model == "deepseek"

    def test_deep_research_high_budget(self):
        plugin = DeepResearchRoutingPlugin()
        ctx = _make_routing_context(task_type=TaskType.DEEP_RESEARCH, budget_ratio=0.6)
        assert plugin.handle(ctx).model == "mlx_qwen3_8b"

    def test_macro_analysis_low_budget(self):
        plugin = MacroAnalysisRoutingPlugin()
        ctx = _make_routing_context(task_type=TaskType.MACRO_ANALYSIS, budget_ratio=0.4)
        assert plugin.can_handle(ctx) is True
        assert plugin.handle(ctx).model == "glm5"

    def test_macro_analysis_high_budget(self):
        plugin = MacroAnalysisRoutingPlugin()
        ctx = _make_routing_context(task_type=TaskType.MACRO_ANALYSIS, budget_ratio=0.7)
        assert plugin.handle(ctx).model == "mlx_qwen3_8b"

    def test_default_always_handles(self):
        plugin = DefaultRoutingPlugin()
        ctx = _make_routing_context(task_type=TaskType.SENTIMENT)
        assert plugin.can_handle(ctx) is True
        assert plugin.handle(ctx).model == "mlx_qwen3_8b"

    def test_create_default_routing_plugins(self):
        plugins = create_default_routing_plugins()
        assert len(plugins) == 5
        priorities = [p.priority for p in plugins]
        assert priorities == sorted(priorities, reverse=True)


# ============================================================
# 3. 冲突检测插件
# ============================================================


class TestConflictPlugins:
    def test_majority_vote_basic(self):
        plugin = MajorityVotePlugin()
        ctx = _make_conflict_context()
        assert plugin.can_handle(ctx) is True
        result = plugin.handle(ctx)
        assert "600519" in result.resolved
        assert "000001" in result.resolved

    def test_majority_vote_conflict_detection(self):
        plugin = MajorityVotePlugin()
        ctx = _make_conflict_context()
        result = plugin.handle(ctx)
        assert result.resolved["600519"]["conflict"] is True
        assert result.resolved["600519"]["resolved_action"] == "BUY"
        assert result.resolved["600519"]["confidence"] == 0.67

    def test_majority_vote_no_conflict(self):
        plugin = MajorityVotePlugin()
        ctx = ConflictContext(
            decisions_by_source={
                "a": {"600519": "BUY"},
                "b": {"600519": "BUY"},
            }
        )
        result = plugin.handle(ctx)
        assert result.resolved["600519"]["conflict"] is False
        assert result.resolved["600519"]["resolved_action"] == "BUY"

    def test_majority_vote_empty(self):
        plugin = MajorityVotePlugin()
        ctx = ConflictContext(decisions_by_source={})
        assert plugin.can_handle(ctx) is False

    def test_weighted_vote(self):
        plugin = WeightedVotePlugin()
        ctx = ConflictContext(
            decisions_by_source={
                "a": {"600519": "BUY"},
                "b": {"600519": "SELL"},
            },
            source_weights={"a": 3.0, "b": 1.0},
        )
        assert plugin.can_handle(ctx) is True
        result = plugin.handle(ctx)
        assert result.resolved["600519"]["resolved_action"] == "BUY"

    def test_weighted_vote_no_weights_falls_back(self):
        plugin = WeightedVotePlugin()
        ctx = _make_conflict_context()
        assert plugin.can_handle(ctx) is False

    def test_create_default_conflict_plugins(self):
        plugins = create_default_conflict_plugins()
        assert len(plugins) == 2
        assert plugins[0].priority > plugins[1].priority


# ============================================================
# 4. PluginRegistry 执行
# ============================================================


class TestRegistryExecution:
    def test_resolve_routing_full_set(self, registry):
        for p in create_default_routing_plugins():
            registry.register(p)
        ctx = _make_routing_context(task_type=TaskType.INTRADAY_DECISION)
        result = registry.resolve_routing(ctx)
        assert result is not None
        assert result.model == "mlx_qwen3_8b"
        assert result.plugin_name == "intraday"

    def test_resolve_routing_budget_guard_wins(self, registry):
        for p in create_default_routing_plugins():
            registry.register(p)
        ctx = _make_routing_context(
            task_type=TaskType.DEEP_RESEARCH,
            priority=Priority.MEDIUM,
            budget_ratio=0.85,
        )
        result = registry.resolve_routing(ctx)
        assert result.model == "mlx_qwen3_8b"
        assert result.plugin_name == "budget_guard"

    def test_resolve_routing_no_plugin(self, registry):
        ctx = _make_routing_context()
        result = registry.resolve_routing(ctx)
        assert result is None

    def test_resolve_conflict_full_set(self, registry):
        for p in create_default_conflict_plugins():
            registry.register(p)
        ctx = _make_conflict_context()
        result = registry.resolve_conflict(ctx)
        assert result is not None
        assert "600519" in result.resolved
        assert result.plugin_name == "majority_vote"


# ============================================================
# 5. 向后兼容: USE_PLUGIN_COORDINATOR=false 走旧路径
# ============================================================


class TestBackwardCompatibility:
    def test_route_legacy_path(self, tmp_db):
        with patch("utils.ai_coordinator._is_flag_enabled", return_value=False):
            coord = AICoordinator(daily_token_budget=500000, db_path=tmp_db)
        assert coord._use_plugin_coordinator is False
        assert coord.route(TaskType.INTRADAY_DECISION) == "mlx_qwen3_8b"
        assert coord.route(TaskType.DEEP_RESEARCH) == "deepseek"
        assert coord.route(TaskType.MACRO_ANALYSIS) == "glm5"
        assert coord.route(TaskType.DAILY_REPORT) == "mlx_qwen3_8b"

    def test_resolve_conflicts_legacy_path(self, tmp_db):
        with patch("utils.ai_coordinator._is_flag_enabled", return_value=False):
            coord = AICoordinator(daily_token_budget=500000, db_path=tmp_db)
        decisions = {
            "a": {"600519": "BUY"},
            "b": {"600519": "SELL"},
            "c": {"600519": "BUY"},
        }
        result = coord.resolve_conflicts(decisions)
        assert result["600519"]["resolved_action"] == "BUY"
        assert result["600519"]["conflict"] is True
        assert result["600519"]["confidence"] == 0.67

    def test_legacy_route_budget_guard(self, tmp_db):
        with patch("utils.ai_coordinator._is_flag_enabled", return_value=False):
            coord = AICoordinator(daily_token_budget=500000, db_path=tmp_db)
        coord._token_used_today = 450000
        result = coord.route(TaskType.DEEP_RESEARCH, Priority.MEDIUM)
        assert result == "mlx_qwen3_8b"


# ============================================================
# 6. 插件路径: USE_PLUGIN_COORDINATOR=true
# ============================================================


class TestPluginPath:
    def test_route_plugin_path(self, tmp_db):
        with patch("utils.ai_coordinator._is_flag_enabled", return_value=True):
            coord = AICoordinator(daily_token_budget=500000, db_path=tmp_db)
        assert coord._use_plugin_coordinator is True
        assert coord._plugin_registry is not None
        assert coord.route(TaskType.INTRADAY_DECISION) == "mlx_qwen3_8b"
        assert coord.route(TaskType.DEEP_RESEARCH) == "deepseek"
        assert coord.route(TaskType.MACRO_ANALYSIS) == "glm5"
        assert coord.route(TaskType.DAILY_REPORT) == "mlx_qwen3_8b"

    def test_resolve_conflicts_plugin_path(self, tmp_db):
        with patch("utils.ai_coordinator._is_flag_enabled", return_value=True):
            coord = AICoordinator(daily_token_budget=500000, db_path=tmp_db)
        decisions = {
            "a": {"600519": "BUY"},
            "b": {"600519": "SELL"},
            "c": {"600519": "BUY"},
        }
        result = coord.resolve_conflicts(decisions)
        assert result["600519"]["resolved_action"] == "BUY"
        assert result["600519"]["conflict"] is True

    def test_plugin_path_budget_guard(self, tmp_db):
        with patch("utils.ai_coordinator._is_flag_enabled", return_value=True):
            coord = AICoordinator(daily_token_budget=500000, db_path=tmp_db)
        coord._token_used_today = 450000
        result = coord.route(TaskType.DEEP_RESEARCH, Priority.MEDIUM)
        assert result == "mlx_qwen3_8b"


# ============================================================
# 7. shadow 比对: 插件路径与旧路径决策一致性
# ============================================================


class TestShadowConsistency:
    @pytest.mark.parametrize(
        "task_type,priority,budget_ratio",
        [
            (TaskType.INTRADAY_DECISION, Priority.LOW, 0.1),
            (TaskType.DEEP_RESEARCH, Priority.MEDIUM, 0.3),
            (TaskType.DEEP_RESEARCH, Priority.MEDIUM, 0.6),
            (TaskType.MACRO_ANALYSIS, Priority.HIGH, 0.4),
            (TaskType.MACRO_ANALYSIS, Priority.HIGH, 0.7),
            (TaskType.DAILY_REPORT, Priority.MEDIUM, 0.1),
            (TaskType.SENTIMENT, Priority.LOW, 0.5),
            (TaskType.INTRADAY_DECISION, Priority.MEDIUM, 0.85),
            (TaskType.DEEP_RESEARCH, Priority.CRITICAL, 0.95),
        ],
    )
    def test_route_shadow_consistency(self, tmp_db, task_type, priority, budget_ratio):
        with patch("utils.ai_coordinator._is_flag_enabled", return_value=False):
            legacy_coord = AICoordinator(daily_token_budget=500000, db_path=tmp_db)
        with patch("utils.ai_coordinator._is_flag_enabled", return_value=True):
            plugin_coord = AICoordinator(daily_token_budget=500000, db_path=tmp_db)

        legacy_coord._token_used_today = int(budget_ratio * 500000)
        plugin_coord._token_used_today = int(budget_ratio * 500000)

        legacy_result = legacy_coord.route(task_type, priority)
        plugin_result = plugin_coord.route(task_type, priority)
        assert legacy_result == plugin_result, (
            f"shadow 不一致: task={task_type} priority={priority} budget={budget_ratio} "
            f"legacy={legacy_result} plugin={plugin_result}"
        )

    def test_resolve_conflicts_shadow_consistency(self, tmp_db):
        decisions = {
            "ai_hedge": {"600519": "BUY", "000001": "HOLD", "300750": "SELL"},
            "glm5": {"600519": "SELL", "000001": "HOLD", "300750": "SELL"},
            "ml": {"600519": "BUY", "000001": "SELL", "300750": "BUY"},
        }
        with patch("utils.ai_coordinator._is_flag_enabled", return_value=False):
            legacy_coord = AICoordinator(daily_token_budget=500000, db_path=tmp_db)
        with patch("utils.ai_coordinator._is_flag_enabled", return_value=True):
            plugin_coord = AICoordinator(daily_token_budget=500000, db_path=tmp_db)

        legacy_result = legacy_coord.resolve_conflicts(decisions)
        plugin_result = plugin_coord.resolve_conflicts(decisions)
        assert legacy_result == plugin_result


# ============================================================
# 8. 从 YAML 配置加载
# ============================================================


class TestConfigLoad:
    def test_load_from_config(self, registry):
        count = registry.load_from_config()
        assert count >= 5
        listed = registry.list_routing_plugins()
        assert any(p["name"] == "intraday" for p in listed)

    def test_load_from_config_nonexistent_path(self, registry):
        count = registry.load_from_config("/nonexistent/path/to/plugins.yaml")
        assert count == 0
