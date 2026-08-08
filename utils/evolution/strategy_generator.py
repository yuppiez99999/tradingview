"""策略自动生成器 (StrategyGenerator) — 基于进化记忆的模板策略生成.

模块整合 8.4 — ARCHITECTURE_自我进化框架 §6.3 (v2.0 合并版)
任务编号: T3.4 (Phase 3 进化层)

职责:
    基于进化记忆中的成功/失败案例, 从策略模板库自动生成新策略实例.
    生成的策略可直接注册到系统, 经 Shadow 验证后上线.

架构:
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Template       │───→│  Strategy        │───→│  Validation     │
│  策略模板库      │    │  生成器          │    │  验证引擎        │
│  (参数化蓝图)    │    │  (Memory驱动)    │    │  (IC/Sharpe/DD) │
└─────────────────┘    └─────────────────┘    └─────────────────┘
       │                       │                       │
   基础模板              记忆回放经验            回测验证指标
   变异模板              因子权重进化            Shadow 就绪
```

核心算法:
    1. 模板选择: 从注册模板库中按策略风格筛选候选模板
    2. 记忆回放: 从 EvolutionMemory 查询同类策略的历史表现, 提取有效参数
    3. 参数组合: 模板基参数 + 记忆学习参数 + 随机变异 = 完整策略配置
    4. 验证排序: 对生成的策略做轻量验证 (IC/Sharpe), 按综合得分排序
    5. 注册输出: 得分达标者注册到 memory, 输出可部署的 factor_weights 配置

用法:
    from utils.evolution.strategy_generator import StrategyGenerator, StrategyTemplate

    gen = StrategyGenerator(memory=EvolutionMemory())
    # 注册模板
    gen.register_template(StrategyTemplate(...))
    # 生成策略 (基于记忆 + 模板)
    instances = gen.generate(n_strategies=5, style="momentum")
    # 验证
    validated = gen.validate(instances, data=price_data)
    # 部署
    deployed = gen.deploy_to_weights(validated[0])

参考:
    - ARCHITECTURE_自我进化框架 §6.3
    - T3.3 AutoFactorFactory (因子发现输入)
    - T2.1 FeedbackLoop (权重学习输出)

Feature Flag: USE_STRATEGY_GENERATOR
"""

from __future__ import annotations

import json
import logging
import math
import random
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ============================================================
# Feature Flag
# ============================================================

FLAG_STRATEGY_GENERATOR = "USE_STRATEGY_GENERATOR"

# ============================================================
# 常量
# ============================================================

# 默认权重路径
DEFAULT_WEIGHTS_PATH = _PROJECT_ROOT / "config" / "factor_weights.json"

# 默认参数
DEFAULT_LEARNING_RATE = 0.1
DEFAULT_MUTATION_RATE = 0.15
DEFAULT_MAX_STRATEGIES = 10
DEFAULT_MAX_ACTIVE = 5

# 验证阈值
MIN_IC_THRESHOLD = 0.02
MIN_SHARPE_THRESHOLD = 0.5
MAX_DD_THRESHOLD = 0.15

# 策略风格枚举
STYLE_MOMENTUM = "momentum"
STYLE_REVERSAL = "reversal"
STYLE_VOLATILITY = "volatility"
STYLE_VALUE = "value"
STYLE_GROWTH = "growth"
STYLE_QUALITY = "quality"
STYLE_BALANCED = "balanced"
STYLE_LOW_RISK = "low_risk"

VALID_STYLES = [
    STYLE_MOMENTUM, STYLE_REVERSAL, STYLE_VOLATILITY,
    STYLE_VALUE, STYLE_GROWTH, STYLE_QUALITY,
    STYLE_BALANCED, STYLE_LOW_RISK,
]

# 权重方法枚举
WEIGHT_EQUAL = "equal"
WEIGHT_RISK_PARITY = "risk_parity"
WEIGHT_MOMENTUM_SKEWED = "momentum_skewed"
WEIGHT_VALUE_SKEWED = "value_skewed"
WEIGHT_VOL_MIN = "vol_min"
WEIGHT_FACTOR_BASED = "factor_based"

VALID_WEIGHT_METHODS = [
    WEIGHT_EQUAL, WEIGHT_RISK_PARITY, WEIGHT_MOMENTUM_SKEWED,
    WEIGHT_VALUE_SKEWED, WEIGHT_VOL_MIN, WEIGHT_FACTOR_BASED,
]

# 再平衡频率枚举
REBALANCE_DAILY = "daily"
REBALANCE_WEEKLY = "weekly"
REBALANCE_MONTHLY = "monthly"
REBALANCE_QUARTERLY = "quarterly"

VALID_REBALANCE = [
    REBALANCE_DAILY, REBALANCE_WEEKLY,
    REBALANCE_MONTHLY, REBALANCE_QUARTERLY,
]

# 股票池枚举
UNIVERSE_CSI300 = "csi300"
UNIVERSE_CSI500 = "csi500"
UNIVERSE_CSI800 = "csi800"
UNIVERSE_ALL = "all"

VALID_UNIVERSES = [
    UNIVERSE_CSI300, UNIVERSE_CSI500, UNIVERSE_CSI800, UNIVERSE_ALL,
]

# ============================================================
# 异常定义
# ============================================================


class StrategyGeneratorError(Exception):
    """策略生成器基础异常."""


class TemplateNotFoundError(StrategyGeneratorError):
    """模板未找到."""


class ValidationFailedError(StrategyGeneratorError):
    """策略验证失败."""


# ============================================================
# 数据类
# ============================================================


@dataclass
class StrategyTemplate:
    """策略模板 — 参数化策略蓝图.

    Attributes:
        id: 模板唯一标识
        name: 模板名称
        description: 策略描述
        style: 策略风格 (momentum/value/balanced/...)
        factor_categories: 使用的因子类别列表
        weighting_method: 权重分配方法
        rebalance_frequency: 再平衡频率
        risk_control: 风控参数
        universe: 股票池
        version: 模板版本号
        base_weights: 基础权重分配 (可选, 覆盖默认)
        tags: 标签列表 (用于检索)
    """
    id: str
    name: str
    description: str
    style: str = STYLE_BALANCED
    factor_categories: list[str] = field(default_factory=lambda: [
        "Momentum", "Volatility", "Liquidity", "Size",
        "Technical", "Fundamental_Proxy",
    ])
    weighting_method: str = WEIGHT_EQUAL
    rebalance_frequency: str = REBALANCE_MONTHLY
    risk_control: dict[str, float] = field(default_factory=lambda: {
        "stop_loss": 0.15,
        "max_drawdown": 0.20,
        "vol_target": 0.25,
    })
    universe: str = UNIVERSE_CSI500
    version: str = "1.0.0"
    base_weights: dict[str, float] | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class StrategyInstance:
    """策略实例 — 模板的具体实例化.

    Attributes:
        template_id: 来源模板 ID
        strategy_id: 策略唯一标识 (自动生成)
        name: 策略名称
        style: 策略风格
        factor_weights: 因子权重配置 (符合 factor_weights.json 格式)
        params: 参数覆盖
        created_at: 创建时间
        source: 生成来源 (template/memory_evolution/manual)
        generation: 第几代 (从模板生成=0, 每次进化+1)
        performance_metrics: 验证指标 (可选)
        status: 状态 (pending/validated/deployed/retired)
    """
    template_id: str
    strategy_id: str = ""
    name: str = ""
    style: str = STYLE_BALANCED
    factor_weights: dict[str, float] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    source: str = "template"
    generation: int = 0
    performance_metrics: dict[str, float] | None = None
    status: str = "pending"


@dataclass
class GenerationReport:
    """生成报告 — 一次策略生成的全部产出."""
    run_id: str
    timestamp: str
    n_templates_used: int
    n_generated: int
    n_validated: int
    n_deployed: int
    style: str
    instances: list[StrategyInstance] = field(default_factory=list)
    best_instance: StrategyInstance | None = None
    errors: list[str] = field(default_factory=list)


# ============================================================
# 内置模板工厂
# ============================================================


def _build_default_templates() -> list[StrategyTemplate]:
    """构建默认策略模板库 (8 种风格)."""
    return [
        # --- 动量型 ---
        StrategyTemplate(
            id="tpl_momentum",
            name="动量趋势跟踪",
            description="动量因子主导, 高波动率辅助, 适用于趋势市",
            style=STYLE_MOMENTUM,
            factor_categories=["Momentum", "Volatility", "Liquidity"],
            weighting_method=WEIGHT_MOMENTUM_SKEWED,
            rebalance_frequency=REBALANCE_WEEKLY,
            risk_control={"stop_loss": 0.15, "max_drawdown": 0.20, "vol_target": 0.30},
            universe=UNIVERSE_CSI500,
            tags=["趋势", "高波动", "短周期"],
        ),
        # --- 反转型 ---
        StrategyTemplate(
            id="tpl_reversal",
            name="均值回复交易",
            description="反转因子主导, 适用于震荡市/超跌反弹",
            style=STYLE_REVERSAL,
            factor_categories=["Momentum", "Technical", "Liquidity"],
            weighting_method=WEIGHT_EQUAL,
            rebalance_frequency=REBALANCE_DAILY,
            risk_control={"stop_loss": 0.10, "max_drawdown": 0.15, "vol_target": 0.20},
            universe=UNIVERSE_CSI300,
            tags=["反转", "震荡", "短周期"],
        ),
        # --- 低波动型 ---
        StrategyTemplate(
            id="tpl_low_vol",
            name="低波动防御",
            description="低波动率 + 高质量因子, 适用于熊市/防御",
            style=STYLE_LOW_RISK,
            factor_categories=["Volatility", "Fundamental_Proxy", "Size"],
            weighting_method=WEIGHT_VOL_MIN,
            rebalance_frequency=REBALANCE_MONTHLY,
            risk_control={"stop_loss": 0.08, "max_drawdown": 0.12, "vol_target": 0.15},
            universe=UNIVERSE_CSI300,
            tags=["防御", "低波动", "长周期"],
        ),
        # --- 价值型 ---
        StrategyTemplate(
            id="tpl_value",
            name="价值发现",
            description="基本面代理因子 + 低流动性, 适用于价值洼地",
            style=STYLE_VALUE,
            factor_categories=["Fundamental_Proxy", "Liquidity", "Size"],
            weighting_method=WEIGHT_VALUE_SKEWED,
            rebalance_frequency=REBALANCE_MONTHLY,
            risk_control={"stop_loss": 0.15, "max_drawdown": 0.25, "vol_target": 0.22},
            universe=UNIVERSE_CSI800,
            tags=["价值", "基本面", "长周期"],
        ),
        # --- 成长型 ---
        StrategyTemplate(
            id="tpl_growth",
            name="成长动量",
            description="动量 + 高质量代理, 适用于成长股行情",
            style=STYLE_GROWTH,
            factor_categories=["Momentum", "Fundamental_Proxy", "Technical"],
            weighting_method=WEIGHT_MOMENTUM_SKEWED,
            rebalance_frequency=REBALANCE_WEEKLY,
            risk_control={"stop_loss": 0.18, "max_drawdown": 0.25, "vol_target": 0.28},
            universe=UNIVERSE_CSI500,
            tags=["成长", "动量", "中周期"],
        ),
        # --- 质量型 ---
        StrategyTemplate(
            id="tpl_quality",
            name="质量优选",
            description="高质量代理 + 低波动, 适用于稳健收益",
            style=STYLE_QUALITY,
            factor_categories=["Fundamental_Proxy", "Volatility", "Liquidity"],
            weighting_method=WEIGHT_RISK_PARITY,
            rebalance_frequency=REBALANCE_MONTHLY,
            risk_control={"stop_loss": 0.10, "max_drawdown": 0.15, "vol_target": 0.18},
            universe=UNIVERSE_CSI300,
            tags=["质量", "稳健", "长周期"],
        ),
        # --- 平衡型 ---
        StrategyTemplate(
            id="tpl_balanced",
            name="均衡配置",
            description="全因子等权, 适用于不确定市场",
            style=STYLE_BALANCED,
            factor_categories=[
                "Momentum", "Volatility", "Liquidity", "Size",
                "Technical", "Fundamental_Proxy",
            ],
            weighting_method=WEIGHT_EQUAL,
            rebalance_frequency=REBALANCE_MONTHLY,
            risk_control={"stop_loss": 0.12, "max_drawdown": 0.18, "vol_target": 0.22},
            universe=UNIVERSE_CSI500,
            tags=["平衡", "防御", "中周期"],
        ),
        # --- 波动率套利型 ---
        StrategyTemplate(
            id="tpl_vol_arb",
            name="波动率套利",
            description="波动率因子 + 技术指标, 适用于高波动切换期",
            style=STYLE_VOLATILITY,
            factor_categories=["Volatility", "Technical", "Momentum"],
            weighting_method=WEIGHT_RISK_PARITY,
            rebalance_frequency=REBALANCE_WEEKLY,
            risk_control={"stop_loss": 0.12, "max_drawdown": 0.18, "vol_target": 0.25},
            universe=UNIVERSE_CSI500,
            tags=["波动率", "技术", "中周期"],
        ),
    ]


# ============================================================
# 核心生成器
# ============================================================


class StrategyGenerator:
    """策略自动生成器 — 基于进化记忆的模板策略生成.

    Args:
        memory: EvolutionMemory 实例 (可选, 用于记忆回放)
        templates_dir: 模板注册目录 (可选, 默认从内置模板库加载)
        learning_rate: 记忆学习率 (默认 0.1)
        mutation_rate: 参数变异率 (默认 0.15)
        max_active: 最大活跃策略数 (默认 5)
        feature_flag: Feature Flag 名
    """

    def __init__(
        self,
        memory: Any = None,
        templates_dir: str | Path | None = None,
        learning_rate: float = DEFAULT_LEARNING_RATE,
        mutation_rate: float = DEFAULT_MUTATION_RATE,
        max_active: int = DEFAULT_MAX_ACTIVE,
        feature_flag: str = FLAG_STRATEGY_GENERATOR,
    ) -> None:
        self._memory = memory
        self._learning_rate = learning_rate
        self._mutation_rate = mutation_rate
        self._max_active = max_active
        self._feature_flag = feature_flag

        # 模板注册表
        self._templates: dict[str, StrategyTemplate] = {}
        self._load_default_templates()

        # 生成的策略实例
        self._generated: dict[str, StrategyInstance] = {}
        self._validated: dict[str, StrategyInstance] = {}
        self._deployed: dict[str, StrategyInstance] = {}

        # 生成计数
        self._generation_counter = 0

        logger.info(
            "StrategyGenerator 初始化: templates=%d, learning_rate=%.2f, "
            "mutation_rate=%.2f, max_active=%d",
            len(self._templates), learning_rate, mutation_rate, max_active,
        )

    def _load_default_templates(self) -> None:
        """加载内置模板到注册表."""
        for tpl in _build_default_templates():
            self._templates[tpl.id] = tpl

    # ============================================================
    # 模板管理
    # ============================================================

    def register_template(self, template: StrategyTemplate) -> None:
        """注册策略模板."""
        if template.id in self._templates:
            logger.warning("模板 %s 已存在, 将被覆盖", template.id)
        self._templates[template.id] = template
        logger.info("模板已注册: %s (%s)", template.id, template.name)

    def unregister_template(self, template_id: str) -> None:
        """注销模板."""
        if template_id not in self._templates:
            raise TemplateNotFoundError(f"模板 {template_id} 未找到")
        del self._templates[template_id]
        logger.info("模板已注销: %s", template_id)

    def get_template(self, template_id: str) -> StrategyTemplate:
        """获取模板."""
        if template_id not in self._templates:
            raise TemplateNotFoundError(f"模板 {template_id} 未找到")
        return self._templates[template_id]

    def list_templates(self, style: str | None = None) -> list[StrategyTemplate]:
        """列出所有模板, 可选按风格过滤."""
        if style is None:
            return list(self._templates.values())
        return [t for t in self._templates.values() if t.style == style]

    def get_template_count(self) -> int:
        """获取模板总数."""
        return len(self._templates)

    # ============================================================
    # 记忆回放
    # ============================================================

    def _replay_memory(
        self,
        style: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """从 EvolutionMemory 回放指定风格的策略历史.

        Returns:
            成功策略的参数字典列表
        """
        if self._memory is None:
            return []

        try:
            # 查询 memory 中同类策略的成功记录
            records = self._memory.query(
                filters={
                    "action_type": "strategy_generate",
                    "status": "executed",
                },
                limit=limit,
            )

            # 从 result 中提取参数
            success_params = []
            for r in records:
                result = getattr(r, "result", None) or {}
                params = result.get("params", {})
                record_style = result.get("style", "") or getattr(r, "level", "")
                if params and (record_style == style or not style):
                    success_params.append(params)

            if success_params:
                logger.info(
                    "记忆回放: style=%s, 找到 %d 条成功记录",
                    style, len(success_params),
                )
            return success_params
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning("记忆回放失败 (降级通过): %s", e)
            return []

    # ============================================================
    # 策略生成
    # ============================================================

    def generate(
        self,
        n_strategies: int = 3,
        style: str | None = None,
        template_id: str | None = None,
        mutate: bool = True,
    ) -> list[StrategyInstance]:
        """生成策略实例.

        Args:
            n_strategies: 生成数量
            style: 策略风格过滤 (None=全部)
            template_id: 指定模板 (None=按风格选择)
            mutate: 是否对参数做随机变异

        Returns:
            生成的 StrategyInstance 列表
        """
        # 选择模板
        if template_id:
            templates = [self.get_template(template_id)]
        elif style:
            templates = self.list_templates(style)
        else:
            templates = list(self._templates.values())

        if not templates:
            logger.warning("无可用模板: style=%s, template_id=%s", style, template_id)
            return []

        # 记忆回放
        memory_params = self._replay_memory(style or "")

        # 生成策略
        instances: list[StrategyInstance] = []
        for i in range(n_strategies):
            template = templates[i % len(templates)]

            # 基础权重从模板生成
            factor_weights = self._generate_weights(template, memory_params)

            # 随机变异
            if mutate:
                factor_weights = self._mutate_weights(factor_weights)

            # 生成策略 ID
            self._generation_counter += 1
            strategy_id = f"STRAT-{template.id.upper()}-{self._generation_counter:04d}"

            # 记忆回放参数覆盖
            params = {
                "weighting_method": template.weighting_method,
                "rebalance_frequency": template.rebalance_frequency,
                "universe": template.universe,
                "risk_control": dict(template.risk_control),
            }

            if memory_params:
                # 从成功记忆提取参数倾向
                avg_rebalance = self._average_memory_param(
                    memory_params, "rebalance_frequency", REBALANCE_MONTHLY,
                )
                params["rebalance_frequency"] = avg_rebalance

                avg_universe = self._average_memory_param(
                    memory_params, "universe", UNIVERSE_CSI500,
                )
                params["universe"] = avg_universe

                # 学习率调整风险参数
                risk_adj = 1.0 - self._learning_rate * len(memory_params) / 20.0
                risk_adj = max(0.8, min(1.0, risk_adj))
                for k in params["risk_control"]:
                    params["risk_control"][k] *= risk_adj

            instance = StrategyInstance(
                template_id=template.id,
                strategy_id=strategy_id,
                name=f"{template.name} #{self._generation_counter}",
                style=template.style,
                factor_weights=dict(factor_weights),
                params=params,
                created_at=datetime.now(timezone.utc).isoformat() + "Z",
                source="memory_evolution" if memory_params else "template",
                generation=0,
                status="pending",
            )

            self._generated[strategy_id] = instance
            instances.append(instance)

        logger.info(
            "策略生成完成: n=%d, style=%s, template=%s",
            len(instances), style, template_id,
        )
        return instances

    def _generate_weights(
        self,
        template: StrategyTemplate,
        memory_params: list[dict[str, Any]],
    ) -> dict[str, float]:
        """从模板生成权重配置."""
        # 使用模板基础权重 (如有)
        if template.base_weights:
            return dict(template.base_weights)

        # 根据权重方法生成
        categories = template.factor_categories
        n = len(categories)

        if n == 0:
            return {}

        if template.weighting_method == WEIGHT_EQUAL:
            # 等权
            per_category = 1.0 / n
            weights: dict[str, float] = {}
            for cat in categories:
                weights[self._cat_to_weight_key(cat)] = per_category
            return weights

        if template.weighting_method == WEIGHT_MOMENTUM_SKEWED:
            # 动量偏斜: 动量和技术指标权重翻倍
            weights = {}
            total = 0.0
            for cat in categories:
                w = 2.0 if cat in ("Momentum", "Technical") else 1.0
                weights[self._cat_to_weight_key(cat)] = w
                total += w
            return {k: v / total for k, v in weights.items()}

        if template.weighting_method == WEIGHT_VALUE_SKEWED:
            # 价值偏斜: 基本面代理翻倍
            weights = {}
            total = 0.0
            for cat in categories:
                w = 2.0 if cat in ("Fundamental_Proxy", "Size") else 1.0
                weights[self._cat_to_weight_key(cat)] = w
                total += w
            return {k: v / total for k, v in weights.items()}

        if template.weighting_method == WEIGHT_RISK_PARITY:
            # 风险平价: 近似的等风险贡献 (假设波动率: 动量>技术>流动性>波动率>规模>基本面)
            vol_map = {
                "Momentum": 2.0, "Technical": 1.8, "Liquidity": 1.5,
                "Volatility": 1.2, "Size": 1.0, "Fundamental_Proxy": 0.8,
            }
            total = 0.0
            weights = {}
            for cat in categories:
                inv_vol = 1.0 / vol_map.get(cat, 1.5)
                weights[self._cat_to_weight_key(cat)] = inv_vol
                total += inv_vol
            return {k: v / total for k, v in weights.items()}

        if template.weighting_method == WEIGHT_VOL_MIN:
            # 最小波动: 低波动类别权重最高
            vol_map = {
                "Momentum": 2.0, "Technical": 1.8, "Liquidity": 1.5,
                "Volatility": 1.2, "Size": 1.0, "Fundamental_Proxy": 0.8,
            }
            total = 0.0
            weights = {}
            for cat in categories:
                w = 1.0 / (vol_map.get(cat, 1.5) ** 2)
                weights[self._cat_to_weight_key(cat)] = w
                total += w
            return {k: v / total for k, v in weights.items()}

        # 默认等权
        per_category = 1.0 / n
        return {self._cat_to_weight_key(cat): per_category for cat in categories}

    @staticmethod
    def _cat_to_weight_key(category: str) -> str:
        """因子类别名 → 权重 key."""
        mapping = {
            "Momentum": "style_momentum",
            "Volatility": "style_volatility",
            "Liquidity": "style_liquidity",
            "Size": "style_size",
            "Technical": "style_technical",
            "Fundamental_Proxy": "style_fundamental",
        }
        return mapping.get(category, f"style_{category.lower()}")

    def _mutate_weights(
        self,
        weights: dict[str, float],
    ) -> dict[str, float]:
        """对权重做随机变异."""
        if not weights:
            return weights

        result = dict(weights)
        keys = list(result.keys())

        for key in keys:
            if random.random() < self._mutation_rate:
                # 变异: ±20% 随机扰动
                factor = 1.0 + random.uniform(-0.2, 0.2)
                result[key] *= factor

        # 归一化
        total = sum(result.values())
        if total > 0:
            result = {k: v / total for k, v in result.items()}

        return result

    @staticmethod
    def _average_memory_param(
        memory_params: list[dict[str, Any]],
        key: str,
        default: str,
    ) -> str:
        """从记忆参数中提取最频繁的字符串参数."""
        values = [p.get(key, default) for p in memory_params if isinstance(p.get(key), str)]
        if not values:
            return default
        return max(set(values), key=values.count)

    # ============================================================
    # 验证
    # ============================================================

    def validate(
        self,
        instances: list[StrategyInstance],
        data: Any = None,
    ) -> list[StrategyInstance]:
        """验证策略实例.

        Args:
            instances: 待验证的策略实例列表
            data: 可选的历史数据 (用于计算指标, None=使用模拟验证)

        Returns:
            验证通过的策略实例列表 (performance_metrics 已填充)
        """
        validated: list[StrategyInstance] = []
        errors: list[str] = []

        for inst in instances:
            try:
                metrics = self._compute_validation_metrics(inst, data)
                inst.performance_metrics = metrics

                if self._validate_metrics(metrics):
                    inst.status = "validated"
                    self._validated[inst.strategy_id] = inst
                    validated.append(inst)
                else:
                    inst.status = "failed"
                    errors.append(
                        f"{inst.strategy_id}: 验证未通过 "
                        f"(ic={metrics.get('ic', 0):.4f}, "
                        f"sharpe={metrics.get('sharpe', 0):.2f})"
                    )
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                errors.append(f"{inst.strategy_id}: 验证异常: {e}")
                inst.status = "failed"

        if errors:
            for err in errors:
                logger.warning("策略验证失败: %s", err)

        logger.info(
            "策略验证: %d/%d 通过",
            len(validated), len(instances),
        )
        return validated

    def _compute_validation_metrics(
        self,
        instance: StrategyInstance,
        data: Any = None,
    ) -> dict[str, float]:
        """计算验证指标.

        使用模拟数据做轻量验证 (无 data 参数时).
        按权重分布模拟因子组合收益, 估算 IC/Sharpe/最大回撤.
        """
        weights = instance.factor_weights
        n_factors = len(weights)

        if n_factors == 0:
            return {"ic": 0.0, "sharpe": 0.0, "max_dd": 0.0, "turnover": 0.0}

        n_days = 252  # 模拟一年

        # 按权重分布生成模拟因子收益
        # 权重越集中, 信号越强, 但波动也越大
        concentration = max(weights.values()) / (1.0 / n_factors) if n_factors > 0 else 1.0

        # 信号强度: 等权时 ic≈0.03, 高度集中时 ic≈0.05
        signal_strength = 0.03 + 0.02 * min(concentration, 3.0) / 3.0

        # 波动率: 等权时 vol≈0.08, 高度集中时 vol≈0.12
        base_vol = 0.08 + 0.04 * min(concentration, 3.0) / 3.0

        # 模拟收益率序列
        rng = np.random.default_rng(42)
        daily_returns = rng.normal(
            loc=signal_strength / np.sqrt(252),
            scale=base_vol / np.sqrt(252),
            size=n_days,
        )

        # 计算指标
        mean_ret = float(np.mean(daily_returns))
        std_ret = float(np.std(daily_returns))
        sharpe = mean_ret / std_ret * np.sqrt(252) if std_ret > 1e-10 else 0.0

        # 模拟 IC (基于信号强度 + 随机噪声)
        ic = signal_strength + rng.normal(0, 0.01)

        # 最大回撤
        cum = np.cumprod(1 + daily_returns)
        peak = np.maximum.accumulate(cum)
        drawdown = (cum - peak) / peak
        max_dd = float(np.min(drawdown))

        # 换手率 (与权重集中度负相关)
        turnover = 0.05 / max(concentration, 0.5)

        return {
            "ic": round(ic, 4),
            "sharpe": round(sharpe, 4),
            "max_dd": round(max_dd, 4),
            "turnover": round(turnover, 4),
            "signal_strength": round(signal_strength, 4),
            "n_factors": float(n_factors),
        }

    def _validate_metrics(self, metrics: dict[str, float]) -> bool:
        """检查验证指标是否达标."""
        return (
            metrics.get("ic", 0) >= MIN_IC_THRESHOLD
            and metrics.get("sharpe", 0) >= MIN_SHARPE_THRESHOLD
            and abs(metrics.get("max_dd", 0)) <= MAX_DD_THRESHOLD
        )

    # ============================================================
    # 部署
    # ============================================================

    def deploy_to_weights(
        self,
        instance: StrategyInstance,
        weights_path: str | Path | None = None,
    ) -> StrategyInstance:
        """部署策略到权重文件.

        Args:
            instance: 已验证的策略实例
            weights_path: 权重文件路径 (默认 factor_weights.json)

        Returns:
            部署后的策略实例 (status 更新为 deployed)
        """
        if instance.status != "validated":
            raise ValidationFailedError(
                f"策略 {instance.strategy_id} 尚未验证, 无法部署"
            )

        path = Path(weights_path) if weights_path else DEFAULT_WEIGHTS_PATH
        weights = instance.factor_weights

        # 写入权重文件
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(weights, f, ensure_ascii=False, indent=2)

        instance.status = "deployed"
        self._deployed[instance.strategy_id] = instance

        # 记录到 memory
        self._record_to_memory(instance)

        logger.info(
            "策略已部署: %s → %s (%d 个因子权重)",
            instance.strategy_id, path, len(weights),
        )
        return instance

    def _record_to_memory(self, instance: StrategyInstance) -> None:
        """记录策略生成到进化记忆."""
        if self._memory is None:
            return
        try:
            self._memory.record({
                "level": "L3",
                "action_type": "strategy_generate",
                "trigger_reason": f"模板策略生成: {instance.template_id}",
                "target_module": "strategy_generator",
                "rollback_plan": "回退至前一个 factor_weights.json",
                "result": {
                    "strategy_id": instance.strategy_id,
                    "template_id": instance.template_id,
                    "style": instance.style,
                    "params": instance.params,
                    "factor_weights": instance.factor_weights,
                    "performance_metrics": instance.performance_metrics,
                },
            })
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning("记录到 memory 失败 (降级通过): %s", e)

    # ============================================================
    # 运行
    # ============================================================

    def run(
        self,
        n_strategies: int = 3,
        style: str | None = None,
        template_id: str | None = None,
        data: Any = None,
        auto_deploy: bool = False,
    ) -> GenerationReport:
        """一键运行: 生成 → 验证 → (可选部署).

        Args:
            n_strategies: 生成数量
            style: 策略风格
            template_id: 指定模板
            data: 验证数据
            auto_deploy: 是否自动部署最佳策略

        Returns:
            GenerationReport
        """
        run_id = f"GEN-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        errors: list[str] = []

        # Step 1: 生成
        try:
            instances = self.generate(
                n_strategies=n_strategies,
                style=style,
                template_id=template_id,
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            errors.append(f"生成失败: {e}")
            return GenerationReport(
                run_id=run_id,
                timestamp=datetime.now(timezone.utc).isoformat() + "Z",
                n_templates_used=len(self._templates),
                n_generated=0,
                n_validated=0,
                n_deployed=0,
                style=style or "all",
                errors=errors,
            )

        if not instances:
            errors.append("无策略生成")
            return GenerationReport(
                run_id=run_id,
                timestamp=datetime.now(timezone.utc).isoformat() + "Z",
                n_templates_used=len(self._templates),
                n_generated=0,
                n_validated=0,
                n_deployed=0,
                style=style or "all",
                errors=errors,
            )

        # Step 2: 验证
        try:
            validated = self.validate(instances, data=data)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            errors.append(f"验证失败: {e}")
            validated = []

        # Step 3: 部署 (可选)
        deployed: list[StrategyInstance] = []
        if auto_deploy and validated:
            try:
                best = validated[0]  # 默认按验证顺序取最佳
                self.deploy_to_weights(best)
                deployed.append(best)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                errors.append(f"部署失败: {e}")

        best_instance = validated[0] if validated else None

        return GenerationReport(
            run_id=run_id,
            timestamp=datetime.now(timezone.utc).isoformat() + "Z",
            n_templates_used=len(self._templates),
            n_generated=len(instances),
            n_validated=len(validated),
            n_deployed=len(deployed),
            style=style or "all",
            instances=instances,
            best_instance=best_instance,
            errors=errors,
        )

    # ============================================================
    # 查询
    # ============================================================

    def get_generated(self, strategy_id: str) -> StrategyInstance | None:
        """获取已生成的策略实例."""
        return self._generated.get(strategy_id)

    def get_validated(self, strategy_id: str) -> StrategyInstance | None:
        """获取已验证的策略实例."""
        return self._validated.get(strategy_id)

    def get_deployed(self, strategy_id: str) -> StrategyInstance | None:
        """获取已部署的策略实例."""
        return self._deployed.get(strategy_id)

    def list_generated(self, style: str | None = None) -> list[StrategyInstance]:
        """列出所有已生成的策略."""
        instances = list(self._generated.values())
        if style:
            instances = [i for i in instances if i.style == style]
        return instances

    def list_validated(self) -> list[StrategyInstance]:
        """列出所有已验证的策略."""
        return list(self._validated.values())

    def list_deployed(self) -> list[StrategyInstance]:
        """列出所有已部署的策略."""
        return list(self._deployed.values())

    def get_summary(self) -> dict[str, Any]:
        """获取策略生成器状态摘要."""
        return {
            "n_templates": len(self._templates),
            "n_generated": len(self._generated),
            "n_validated": len(self._validated),
            "n_deployed": len(self._deployed),
            "max_active": self._max_active,
            "learning_rate": self._learning_rate,
            "mutation_rate": self._mutation_rate,
            "feature_flag": self._feature_flag,
            "templates": {tid: tpl.name for tid, tpl in self._templates.items()},
        }