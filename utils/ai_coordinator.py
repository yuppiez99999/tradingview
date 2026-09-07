"""
AI 协调器 — v5.7 Phase 1 优化

统一管理多AI系统的任务路由、结果协调和成本管控。
三个AI系统（AI Hedge Fund、GLM-5、豆包Speed）各自独立调用LLM，
可能产生矛盾建议。协调器职责：

1. 任务路由：根据任务类型自动选择最合适的AI模型
2. 结果协调：汇总多个AI系统的结论，检测矛盾并标注
3. 成本管控：每日Token预算、用量追踪、超预算自动切换便宜模型
4. 冲突解决：基于各AI系统历史胜率加权决策
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

try:
    from .logging_manager import get_logger

    logger = get_logger("ai_coordinator")
except ImportError:
    import logging

    logger = logging.getLogger("ai_coordinator")

# W.C.3 插件化: feature-flag + PluginRegistry (可选依赖, 缺失时走旧路径)
try:
    from .infra.feature_flags import is_enabled as _is_flag_enabled
except ImportError:

    def _is_flag_enabled(_name: str) -> bool:
        return False


try:
    from .ai_coordinator_plugins.base import ConflictContext, RoutingContext
    from .ai_coordinator_plugins.conflict_detection_plugin import (
        create_default_conflict_plugins,
    )
    from .ai_coordinator_plugins.registry import PluginRegistry
    from .ai_coordinator_plugins.routing_plugin import create_default_routing_plugins

    _PLUGINS_AVAILABLE = True
except ImportError:
    _PLUGINS_AVAILABLE = False

# LIT-2.2: TradingGroup 自反思机制 (可选依赖, 缺失时降级)
try:
    from .trading_group_reflector import TradingGroupReflector

    _REFLECTOR_AVAILABLE = True
except ImportError:
    _REFLECTOR_AVAILABLE = False

# LIT-2.6: 对抗新闻攻击防护 (可选依赖, 缺失时降级)
try:
    from .adversarial_news_guard import AdversarialNewsGuard

    _NEWS_GUARD_AVAILABLE = True
except ImportError:
    _NEWS_GUARD_AVAILABLE = False


class TaskType(Enum):
    """AI任务类型"""

    DAILY_REPORT = "daily_report"  # 日报告生成（质量要求中等，量大）
    INTRADAY_DECISION = "intraday_decision"  # 盘中实时决策（速度要求高）
    DEEP_RESEARCH = "deep_research"  # 深度研究（质量要求极高，量少）
    SENTIMENT = "sentiment"  # 情绪分析（量中等）
    MACRO_ANALYSIS = "macro_analysis"  # 宏观分析（质量要求高）


class Priority(Enum):
    """任务优先级"""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class AICoordinator:
    """AI 协调器 — 统一管理多AI系统

    使用方式:
        coordinator = AICoordinator(daily_token_budget=500000)
        model = coordinator.route(TaskType.INTRADAY_DECISION, Priority.HIGH)
    """

    # 模型配置（价格按 2024-2025 市场行情估算，单位：元/1K tokens）
    MODEL_CONFIG = {
        "mlx_qwen3_8b": {
            "cost_per_1k_input": 0.0,
            "cost_per_1k_output": 0.0,
            "max_tokens": 32000,
            "roles": [
                TaskType.INTRADAY_DECISION,
                TaskType.DAILY_REPORT,
                TaskType.SENTIMENT,
            ],
        },
        "glm5": {
            "cost_per_1k_input": 0.015,
            "cost_per_1k_output": 0.015,
            "max_tokens": 128000,
            "roles": [TaskType.MACRO_ANALYSIS],
        },
        "deepseek": {
            "cost_per_1k_input": 0.002,
            "cost_per_1k_output": 0.008,
            "max_tokens": 64000,
            "roles": [TaskType.DEEP_RESEARCH],
        },
    }

    def __init__(
        self,
        daily_token_budget: int = 500000,
        db_path: str | None = None,
        pricing_path: str | None = None,
    ):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(base_dir, "data", "ai_coordinator.db")

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        # P2: 价格表外置到 config/llm_pricing.yaml, 缺失回退内置默认值
        self.pricing = self._load_pricing(pricing_path)
        if pricing_path is None:
            budget = self.pricing.get("daily_token_budget")
            if budget:
                daily_token_budget = budget
        self.daily_token_budget = daily_token_budget
        self._token_used_today = 0
        self._today = datetime.now().strftime("%Y-%m-%d")
        self._init_db()
        # W.C.3 插件化: 初始化 PluginRegistry (feature-flag 控制是否启用)
        self._plugin_registry: Any | None = None
        self._use_plugin_coordinator = _PLUGINS_AVAILABLE and _is_flag_enabled(
            "USE_PLUGIN_COORDINATOR"
        )
        if self._use_plugin_coordinator:
            self._init_plugin_registry()
        # LIT-2.2: TradingGroup 自反思引擎 (延迟初始化)
        self._reflector: Any | None = None

    @staticmethod
    def _load_pricing(pricing_path: str | None = None) -> dict:
        """从外置 YAML 加载价格表, 失败回退内置默认值。"""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = []
        if pricing_path:
            candidates.append(pricing_path)
        candidates.append(os.path.join(base_dir, "config", "llm_pricing.yaml"))
        for path in candidates:
            try:
                import yaml  # type: ignore

                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if "models" in data:
                    return data
            except FileNotFoundError:
                continue
            except (
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                OSError,
                RuntimeError,
            ):
                continue
        # 回退: 内置默认价格表 (与 MODEL_CONFIG 保持一致)
        return {
            "models": {
                "mlx_qwen3_8b": {
                    "cost_per_1k_input": 0.0,
                    "cost_per_1k_output": 0.0,
                    "max_tokens": 32000,
                },
                "glm5": {
                    "cost_per_1k_input": 0.015,
                    "cost_per_1k_output": 0.015,
                    "max_tokens": 128000,
                },
                "deepseek": {
                    "cost_per_1k_input": 0.002,
                    "cost_per_1k_output": 0.008,
                    "max_tokens": 64000,
                },
                "default": {
                    "cost_per_1k_input": 0.01,
                    "cost_per_1k_output": 0.03,
                    "max_tokens": 8000,
                },
            },
            "daily_token_budget": 500000,
        }

    def _price_for(self, model: str) -> dict:
        """获取某模型价格, 缺失回退 default。"""
        models = self.pricing.get("models", {})
        return (
            models.get(model)
            or models.get("default")
            or {"cost_per_1k_input": 0.01, "cost_per_1k_output": 0.03}
        )

    def _init_db(self) -> None:
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                model TEXT NOT NULL,
                task_type TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cost_estimate REAL DEFAULT 0.0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                source TEXT NOT NULL,
                ticker TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL,
                reasoning TEXT,
                model_used TEXT,
                tokens_consumed INTEGER,
                task_type TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_token_usage_date
            ON token_usage(date)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_decision_accuracy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id INTEGER NOT NULL,
                predicted_action TEXT NOT NULL,
                actual_outcome TEXT,
                pnl_if_followed REAL,
                evaluated_at TEXT NOT NULL,
                FOREIGN KEY (decision_id) REFERENCES ai_decisions(id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_decisions_source_ticker
            ON ai_decisions(source, ticker)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_decision_accuracy_id
            ON ai_decision_accuracy(decision_id)
        """)
        conn.commit()
        conn.close()

    # ── W.C.3 插件化 ──

    def _init_plugin_registry(self) -> None:
        """初始化 PluginRegistry: 先从 YAML 加载, 失败则用默认插件集"""
        try:
            self._plugin_registry = PluginRegistry()
            loaded = self._plugin_registry.load_from_config()
            if loaded == 0:
                for p in create_default_routing_plugins():
                    self._plugin_registry.register(p)
                for p in create_default_conflict_plugins():
                    self._plugin_registry.register(p)
                logger.info(
                    "插件化协调器: 已加载默认插件集 (%d 个)", len(self._plugin_registry)
                )
            else:
                logger.info("插件化协调器: 从 YAML 加载 %d 个插件", loaded)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            logger.warning("插件化协调器初始化失败, 回退旧路径: %s", e)
            self._plugin_registry = None
            self._use_plugin_coordinator = False

    # ── 任务路由 ──

    def route(self, task_type: TaskType, priority: Priority = Priority.MEDIUM) -> str:
        """根据任务类型和优先级选择最合适的模型。

        Returns:
            str: 模型名称 ('mlx_qwen3_8b' / 'glm5' / 'deepseek')
        """
        # 检查每日预算
        self._refresh_daily_budget()
        budget_ratio = (
            self._token_used_today / self.daily_token_budget
            if self.daily_token_budget > 0
            else 0
        )

        # W.C.3 插件化: feature-flag 启用时走 PluginRegistry, 否则走旧路径
        if self._use_plugin_coordinator and self._plugin_registry is not None:
            return self._route_via_plugins(task_type, priority, budget_ratio)
        return self._route_legacy(task_type, priority, budget_ratio)

    def _route_legacy(
        self, task_type: TaskType, priority: Priority, budget_ratio: float
    ) -> str:
        """旧路径路由 — 原硬编码 if-else (保留向后兼容)"""
        # 预算快用完 (>80%) → 强制切换便宜模型
        if budget_ratio > 0.8 and priority != Priority.CRITICAL:
            logger.warning(
                f"Token预算已使用 {budget_ratio:.0%}，强制切换到 mlx_qwen3_8b"
            )
            return "mlx_qwen3_8b"

        # 按任务类型匹配
        if task_type == TaskType.INTRADAY_DECISION:
            return "mlx_qwen3_8b"  # 盘中决策：豆包 Speed 速度快成本低

        if task_type == TaskType.DEEP_RESEARCH:
            return "deepseek" if budget_ratio < 0.5 else "mlx_qwen3_8b"

        if task_type == TaskType.MACRO_ANALYSIS:
            return "glm5" if budget_ratio < 0.6 else "mlx_qwen3_8b"

        # 默认：日报/情绪分析用便宜模型
        return "mlx_qwen3_8b"

    def _route_via_plugins(
        self, task_type: TaskType, priority: Priority, budget_ratio: float
    ) -> str:
        """插件路径路由 — 委托 PluginRegistry.resolve_routing

        含 shadow 比对: 同时跑旧路径, 比较决策一致性, 不一致时记日志 (不影响插件路径结果).
        """
        ctx = RoutingContext(
            task_type=task_type,
            priority=priority,
            budget_ratio=budget_ratio,
            token_used_today=self._token_used_today,
            daily_token_budget=self.daily_token_budget,
        )
        result = self._plugin_registry.resolve_routing(ctx)
        if result is None:
            logger.warning("插件路径无插件可处理, 回退旧路径")
            return self._route_legacy(task_type, priority, budget_ratio)

        # shadow 比对 (不阻塞, 仅日志)
        try:
            legacy_model = self._route_legacy(task_type, priority, budget_ratio)
            if legacy_model != result.model:
                logger.warning(
                    "shadow 比对不一致: task=%s priority=%s plugin=%s legacy=%s (plugin=%s)",
                    task_type,
                    priority,
                    result.model,
                    legacy_model,
                    result.plugin_name,
                )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
            logger.warning("shadow 比对异常: %s", e)

        return result.model

    def _refresh_daily_budget(self) -> None:
        """刷新每日预算（跨天重置）"""
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._today:
            self._token_used_today = 0
            self._today = today

    def can_proceed(self, estimated_tokens: int = 1000) -> tuple[bool, str]:
        """检查是否可以继续调用AI（预算内）"""
        self._refresh_daily_budget()
        after = self._token_used_today + estimated_tokens
        ratio = after / self.daily_token_budget if self.daily_token_budget > 0 else 0

        if ratio > 1.0:
            return False, f"超出每日预算 ({self.daily_token_budget:,} tokens)"
        if ratio > 0.9:
            return True, f"预算仅剩 {100 - ratio * 100:.0f}%，请谨慎使用"

        return True, ""

    # ── Token 用量追踪 ──

    def record_usage(
        self, model: str, task_type: str, input_tokens: int, output_tokens: int
    ) -> None:
        """记录Token用量 (P2: 价格取自外置价格表)"""
        config = self._price_for(model)
        cost = (
            input_tokens * config.get("cost_per_1k_input", 0)
            + output_tokens * config.get("cost_per_1k_output", 0)
        ) / 1000

        self._token_used_today += input_tokens + output_tokens

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                INSERT INTO token_usage (date, model, task_type,
                    input_tokens, output_tokens, cost_estimate)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    datetime.now().strftime("%Y-%m-%d"),
                    model,
                    task_type,
                    input_tokens,
                    output_tokens,
                    cost,
                ),
            )
            conn.commit()
            conn.close()
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.debug(f"记录Token用量失败: {e}")

    # ── AI决策持久化 ──

    def record_decision(
        self,
        source: str,
        ticker: str,
        action: str,
        confidence: float = 0.0,
        reasoning: str = "",
        model_used: str = "",
        tokens: int = 0,
        task_type: str = "",
    ) -> None:
        """记录AI决策"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                INSERT INTO ai_decisions (timestamp, source, ticker, action,
                    confidence, reasoning, model_used, tokens_consumed, task_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    datetime.now().isoformat(),
                    source,
                    ticker,
                    action,
                    confidence,
                    reasoning[:2000],
                    model_used,
                    tokens,
                    task_type,
                ),
            )
            conn.commit()
            conn.close()
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.debug(f"记录AI决策失败: {e}")

    def record_decision_with_impact(
        self,
        source: str,
        ticker: str,
        action: str,
        change_path: str = "",
        confidence: float = 0.0,
        reasoning: str = "",
        model_used: str = "",
        tokens: int = 0,
        task_type: str = "",
    ) -> dict[str, Any]:
        """记录 AI 决策 + 代码影响半径 (W.A.2 code-graph-rag 接入)

        当 change_path 非空时, 调用 CodeGraphRAG.impact_analysis 计算影响半径,
        附加到 reasoning 并返回影响信息. 不传 change_path 时退化为 record_decision.

        Returns:
            {"impact_radius": int, "impacted_files": list} 或空字典 (无 change_path / 失败)
        """
        impact_info: dict[str, Any] = {}
        enriched_reasoning = reasoning

        if change_path:
            rag = None
            try:
                from .ai_tools.code_graph_rag import CodeGraphRAG

                rag = CodeGraphRAG()
                impact = rag.impact_analysis(change_path)
                impact_info = {
                    "impact_radius": impact.impact_radius,
                    "impacted_files_count": len(impact.impacted_files),
                    "impacted_files": impact.impacted_files[:20],
                }
                enriched_reasoning = (
                    f"{reasoning}\n[影响半径] radius={impact.impact_radius}, "
                    f"impacted_files={len(impact.impacted_files)}"
                )
            except (
                ImportError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
            ) as e:
                logger.debug(f"影响半径计算失败 change_path={change_path}: {e}")
            finally:
                if rag is not None:
                    try:
                        rag.close()
                    except (OSError, RuntimeError, ValueError):
                        pass

        self.record_decision(
            source=source,
            ticker=ticker,
            action=action,
            confidence=confidence,
            reasoning=enriched_reasoning,
            model_used=model_used,
            tokens=tokens,
            task_type=task_type,
        )
        return impact_info

    # ── 冲突检测 ──

    def resolve_conflicts(
        self, decisions_by_source: dict[str, dict[str, str]]
    ) -> dict[str, dict[str, Any]]:
        """检测并解决多AI系统对同一标的的矛盾信号。

        Args:
            decisions_by_source: {
                'ai_hedge': {'600519': 'BUY', '000001': 'HOLD'},
                'glm5': {'600519': 'SELL', '000001': 'HOLD'},
                'ml': {'600519': 'BUY', '000001': 'SELL'},
            }

        Returns:
            {
                '600519': {
                    'actions': {'ai_hedge': 'BUY', 'glm5': 'SELL', 'ml': 'BUY'},
                    'conflict': True,
                    'resolved_action': 'BUY',
                    'confidence': 0.67,
                },
            }
        """
        # W.C.3 插件化: feature-flag 启用时走 PluginRegistry, 否则走旧路径
        if self._use_plugin_coordinator and self._plugin_registry is not None:
            return self._resolve_conflicts_via_plugins(decisions_by_source)
        return self._resolve_conflicts_legacy(decisions_by_source)

    def _resolve_conflicts_legacy(
        self, decisions_by_source: dict[str, dict[str, str]]
    ) -> dict[str, dict[str, Any]]:
        """旧路径冲突检测 — 原多数投票 (保留向后兼容)"""
        # 收集所有标的
        all_tickers: set[Any] = set()
        for decisions in decisions_by_source.values():
            all_tickers.update(decisions.keys())

        resolved = {}
        for ticker in all_tickers:
            actions = {}
            for source, decisions in decisions_by_source.items():
                if ticker in decisions:
                    actions[source] = decisions[ticker]

            buy_count = sum(1 for a in actions.values() if a == "BUY")
            sell_count = sum(1 for a in actions.values() if a == "SELL")
            hold_count = sum(1 for a in actions.values() if a == "HOLD")

            # 是否有冲突（同时存在买入和卖出）
            has_conflict = buy_count > 0 and sell_count > 0

            # 多数投票
            if buy_count > sell_count and buy_count > hold_count:
                resolved_action = "BUY"
                confidence = buy_count / len(actions)
            elif sell_count > buy_count and sell_count > hold_count:
                resolved_action = "SELL"
                confidence = sell_count / len(actions)
            else:
                resolved_action = "HOLD"
                confidence = max(buy_count, sell_count, hold_count) / len(actions)

            resolved[ticker] = {
                "actions": actions,
                "conflict": has_conflict,
                "resolved_action": resolved_action,
                "confidence": round(confidence, 2),
                "buy_votes": buy_count,
                "sell_votes": sell_count,
                "hold_votes": hold_count,
            }

        return resolved

    def _resolve_conflicts_via_plugins(
        self, decisions_by_source: dict[str, dict[str, str]]
    ) -> dict[str, dict[str, Any]]:
        """插件路径冲突检测 — 委托 PluginRegistry.resolve_conflict

        含 shadow 比对: 同时跑旧路径, 比较结果一致性, 不一致时记日志.
        """
        ctx = ConflictContext(decisions_by_source=decisions_by_source)
        result = self._plugin_registry.resolve_conflict(ctx)
        if result is None:
            logger.warning("插件路径无冲突检测插件可处理, 回退旧路径")
            return self._resolve_conflicts_legacy(decisions_by_source)

        # shadow 比对 (不阻塞, 仅日志)
        try:
            legacy_resolved = self._resolve_conflicts_legacy(decisions_by_source)
            if legacy_resolved != result.resolved:
                diff_tickers = [
                    t
                    for t in legacy_resolved
                    if t not in result.resolved
                    or legacy_resolved[t] != result.resolved[t]
                ]
                logger.warning(
                    "shadow 比对不一致: %d/%d 标的决策不同 (plugin=%s, tickers=%s)",
                    len(diff_tickers),
                    len(legacy_resolved),
                    result.plugin_name,
                    diff_tickers[:10],
                )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
            logger.warning("shadow 比对异常: %s", e)

        return result.resolved

    # ── 统计与查询 ──

    def get_daily_usage(self, date: str | None = None) -> dict[str, Any]:
        """获取指定日期的Token使用统计"""
        date = date or datetime.now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            """
            SELECT model, task_type,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cost_estimate) as total_cost
            FROM token_usage
            WHERE date = ?
            GROUP BY model, task_type
        """,
            (date,),
        ).fetchall()
        conn.close()

        breakdown = []
        total_input = total_output = total_cost = 0
        for r in rows:
            total_input += r[2]
            total_output += r[3]
            total_cost += r[4]
            breakdown.append(
                {
                    "model": r[0],
                    "task_type": r[1],
                    "input_tokens": r[2],
                    "output_tokens": r[3],
                    "cost": round(r[4], 4),
                }
            )

        return {
            "date": date,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "total_cost": round(total_cost, 4),
            "budget": self.daily_token_budget,
            "budget_used_pct": (
                round((total_input + total_output) / self.daily_token_budget * 100, 1)
                if self.daily_token_budget > 0
                else 0
            ),
            "breakdown": breakdown,
        }

    def get_decision_history(
        self, ticker: str | None = None, source: str | None = None, days: int = 7
    ) -> list[dict[str, Any]]:
        """查询AI决策历史"""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)

        query = """
            SELECT timestamp, source, ticker, action, confidence,
                   reasoning, model_used, tokens_consumed, task_type
            FROM ai_decisions
            WHERE date(timestamp) >= ?
        """
        params = [since]
        if ticker:
            query += " AND ticker = ?"
            params.append(ticker)
        if source:
            query += " AND source = ?"
            params.append(source)
        query += " ORDER BY timestamp DESC LIMIT 200"

        rows = conn.execute(query, params).fetchall()
        conn.close()

        return [
            {
                "timestamp": r[0],
                "source": r[1],
                "ticker": r[2],
                "action": r[3],
                "confidence": r[4],
                "reasoning": (r[5] or "")[:200],
                "model_used": r[6],
                "tokens": r[7],
                "task_type": r[8],
            }
            for r in rows
        ]

    def get_stats(self) -> dict[str, Any]:
        """获取协调器统计"""
        self._refresh_daily_budget()
        daily = self.get_daily_usage()
        return {
            "daily_token_used": self._token_used_today,
            "daily_budget": self.daily_token_budget,
            "daily_cost": daily["total_cost"],
            "models_available": list(self.MODEL_CONFIG.keys()),
        }

    # ── v5.7 Phase 2: AI决策准确率评估 ──

    def record_accuracy(
        self,
        decision_id: int,
        predicted_action: str,
        actual_outcome: str = "",
        pnl_if_followed: float = 0.0,
    ) -> None:
        """记录AI决策的5日验证结果。"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                INSERT INTO ai_decision_accuracy
                (decision_id, predicted_action, actual_outcome, pnl_if_followed, evaluated_at)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    decision_id,
                    predicted_action,
                    actual_outcome,
                    pnl_if_followed,
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
            conn.close()
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.debug(f"记录AI决策准确率失败: {e}")

    def get_source_accuracy(self, days: int = 30) -> dict[str, Any]:
        """统计各AI信号源的近期准确率（有验证数据的）。

        Returns:
            {'glm5': {'accuracy': 0.62, 'count': 25, 'avg_pnl': 0.012}, ...}
        """
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)

        rows = conn.execute(
            """
            SELECT d.source,
                   COUNT(*) as total,
                   SUM(CASE
                       WHEN a.actual_outcome = 'WIN' THEN 1
                       WHEN d.action = 'BUY' AND a.pnl_if_followed > 0 THEN 1
                       WHEN d.action = 'SELL' AND a.pnl_if_followed > 0 THEN 1
                       ELSE 0
                   END) as wins,
                   AVG(a.pnl_if_followed) as avg_pnl
            FROM ai_decisions d
            JOIN ai_decision_accuracy a ON d.id = a.decision_id
            WHERE d.timestamp >= ?
            GROUP BY d.source
        """,
            (since,),
        ).fetchall()
        conn.close()

        result = {}
        for r in rows:
            source, total, wins, avg_pnl = r
            result[source] = {
                "accuracy": round(wins / total, 3) if total > 0 else 0,
                "count": total,
                "avg_pnl": round(avg_pnl or 0, 4),
            }
        return result

    # ============================================================
    # LIT-2.2: TradingGroup 自反思集成
    # ============================================================

    def get_reflector(self) -> Any:
        """获取 TradingGroup 自反思引擎 (延迟初始化)。"""
        if self._reflector is None and _REFLECTOR_AVAILABLE:
            self._reflector = TradingGroupReflector()
        return self._reflector

    def reflect_decision(
        self,
        decision: dict[str, Any],
        outcome: dict[str, Any],
        market_state: dict[str, Any] | None = None,
    ) -> Any | None:
        """对 AI 决策进行自反思评估。

        Args:
            decision: 决策信息 {decision_id, ticker, action, entry_price}
            outcome: 结果信息 {exit_price, return, timestamp}
            market_state: 决策时市场状态

        Returns:
            ReflectionRecord 或 None (reflector 不可用时)
        """
        reflector = self.get_reflector()
        if reflector is None:
            logger.debug("TradingGroup 反思器不可用, 跳过自反思")
            return None
        return reflector.reflect(decision, outcome, market_state)

    def synthesize_training_data(self) -> list[Any]:
        """从历史反思合成训练数据。"""
        reflector = self.get_reflector()
        if reflector is None:
            return []
        return reflector.synthesize_data()

    def compute_dynamic_stops(
        self,
        entry_price: float,
        atr: float,
        trend_strength: float = 0.0,
        holding_days: int = 0,
        action: str = "buy",
    ) -> Any | None:
        """计算动态止盈止损。"""
        reflector = self.get_reflector()
        if reflector is None:
            return None
        return reflector.compute_dynamic_stops(
            entry_price=entry_price,
            atr=atr,
            trend_strength=trend_strength,
            holding_days=holding_days,
            action=action,
        )

    # ============================================================
    # LIT-2.6: 对抗新闻攻击防护集成
    # ============================================================

    def sanitize_news_input(self, text: str) -> dict[str, Any]:
        """净化新闻输入 (对抗攻击防护)。

        Args:
            text: 原始新闻文本

        Returns:
            {clean_text, is_safe, severity, threats} 或 {error} (guard 不可用时)
        """
        if not _NEWS_GUARD_AVAILABLE:
            return {"clean_text": text, "is_safe": True, "error": "guard_unavailable"}
        guard = AdversarialNewsGuard()
        result = guard.sanitize(text)
        return {
            "clean_text": result.clean_text,
            "is_safe": result.is_safe,
            "severity": result.report.severity.value,
            "threats": [t.value for t in result.report.threat_types],
            "modifications": result.modifications,
        }


# ── 全局单例 ──

_coordinator: AICoordinator | None = None


def get_ai_coordinator() -> AICoordinator:
    """获取全局AI协调器单例"""
    global _coordinator
    if _coordinator is None:
        budget = int(os.environ.get("AI_TOKEN_BUDGET", "500000"))
        _coordinator = AICoordinator(daily_token_budget=budget)
    return _coordinator
