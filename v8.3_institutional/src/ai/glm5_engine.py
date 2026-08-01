"""
AI 自动决策引擎 v5.8 — 量化交易系统 AI 决策模块 (多模型场景路由升级)
根据调研推荐方案，实现场景路由 + 并行对冲 + 交叉验证

架构变更 (v5.7 → v5.8):
- 旧: 单一 GLM5Client + 豆包优先 + 顺序降级 + 硬编码指数数据
- 新: ModelRouter 场景路由 + Wind MCP 动态数据 + 并行对冲 + 交叉验证
  - 盘中决策: GLM-4.7-Flash + Qwen3.5 Flash 并行对冲 (<12秒)
  - 再平衡: DeepSeek V4 Pro + Qwen-Plus 交叉验证 (深度推理)
  - 宏观分析: DeepSeek V4 Pro + GLM-5.2 交叉验证
  - 报告生成: Qwen-Plus (创意结构化)
  - 轻量分析: 豆包Speed (情感/分类)

使用方式 (向后兼容):
    from utils.glm5_decision_engine import GLM5DecisionEngine

    engine = GLM5DecisionEngine()
    # 盘中决策
    decisions = engine.make_decisions(market_data, portfolio_data, scene="intraday_decision")
    # 或再平衡分析
    decisions = engine.make_decisions(market_data, portfolio_data, scene="rebalancing_analysis")
"""

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.glm5_client import GLM5Client
from utils.multi_model_router import get_model_router
from utils.wind_data_provider import get_wind_provider

logger = logging.getLogger(__name__)


@dataclass
class TradingSignal:
    """交易信号"""

    action: str  # "BUY" / "SELL" / "HOLD" / "REDUCE"
    code: str
    name: str
    current_weight: float  # 当前仓位占比
    target_weight: float  # 目标仓位占比
    weight_change: float  # 仓位变化
    quantity: int  # 建议数量（股/手）
    price: float  # 参考价格
    confidence: float  # 置信度 (0-1)
    reason: str  # 决策理由
    urgency: str  # "LOW" / "MEDIUM" / "HIGH" / "URGENT"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class RiskAlert:
    """风险预警"""

    alert_type: str  # "STOP_LOSS" / "TAKE_PROFIT" / "OVERWEIGHT" / "UNDERWEIGHT"
    severity: str  # "LOW" / "MEDIUM" / "HIGH" / "CRITICAL"
    code: str
    message: str
    action_required: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class DecisionResult:
    """决策结果"""

    timestamp: str
    market_summary: str  # 市场概况
    trading_signals: List[TradingSignal] = field(default_factory=list)
    risk_alerts: List[RiskAlert] = field(default_factory=list)
    portfolio_advice: str = ""  # 组合调整建议
    macro_outlook: str = ""  # 宏观展望
    ai_confidence: float = 0.0  # AI 整体置信度
    raw_analysis: str = ""  # 原始分析文本


class GLM5DecisionEngine:
    """
    AI 自动决策引擎 v5.8 — 多模型场景路由

    功能:
    1. 自动分析市场数据（Wind MCP 动态指数、板块、资金流）
    2. 评估持仓风险（止损/止盈/仓位偏离）
    3. 生成交易信号（买卖建议）
    4. 风险预警（异常波动/极端行情）
    5. 组合再平衡建议 — 含 Wind MCP 基本面 RAG

    v5.8 升级要点:
    - 场景路由代替固定模型优先级
    - Wind MCP 动态指数数据代替硬编码
    - 再平衡场景自动加载基本面 RAG
    """

    # 支持的决策场景
    SCENES = {
        "intraday_decision": "盘中实时决策 (低延迟优先)",
        "rebalancing_analysis": "再平衡深度分析 (推理质量优先)",
        "macro_analysis": "宏观综合分析 (三大分析)",
        "report_generation": "报告生成 (结构化输出)",
        "light_analysis": "轻量分析 (情感/分类)",
    }

    def __init__(self, config: Optional[Dict] = None, **kwargs):
        """
        初始化决策引擎

        Args:
            config: 配置字典
            **kwargs: 可覆盖 scene/model 等参数
        """
        # 默认配置
        self.config = config or {
            "mode": "api",
            "default_scene": "intraday_decision",
            "temperature": 0.3,
            "max_tokens": 3000,
            "enable_risk_check": True,
            "enable_signal_generation": True,
            "enable_rebalance": True,
            "use_wind_mcp": True,  # v5.8: 使用 Wind MCP 动态数据
            "use_fundamental_rag": True,  # v5.8: 再平衡时启用基本面 RAG
        }

        # 合并用户配置
        if kwargs:
            self.config.update(kwargs)

        # v5.8: 初始化多模型路由器 (替代旧的 GLM5Client 优先模式)
        try:
            self.router = get_model_router()
            logger.info("✓ 多模型路由器初始化成功")
        except Exception as e:
            logger.warning(f"多模型路由器初始化失败: {e}, 降级到 GLM5Client")
            self.router = None

        # v5.8: 初始化 Wind 数据供应器
        try:
            self.wind_provider = get_wind_provider()
            logger.info(
                f"✓ Wind 数据供应器初始化成功 (Wind MCP: {'可用' if self.wind_provider._wind_available else '不可用'})"
            )
        except Exception as e:
            logger.warning(f"Wind 数据供应器初始化失败: {e}")
            self.wind_provider = None

        # 保留 GLM5Client 作为降级方案 (向后兼容)
        try:
            self.client = GLM5Client(
                mode=self.config.get("mode", "api"),
                api_model=self.config.get("api_model", "doubao-seed-1-6-251015"),
                temperature=self.config.get("temperature", 0.3),
                max_new_tokens=self.config.get("max_tokens", 3000),
            )
            logger.info("✓ GLM-5 客户端 (降级方案) 初始化成功")
        except Exception as e:
            logger.error(f"GLM-5 客户端初始化失败: {e}")
            self.client = None

        # v5.8 场景专用系统提示词
        self._scene_prompts = {
            "intraday_decision": """你是一位资深的量化交易决策官，拥有20年以上的A股盘中交易经验。
你的职责是在盘中实时根据市场数据和持仓信息，做出快速、客观、理性的交易决策。

决策原则:
1. 严格遵循风控优先原则，任何交易建议必须包含风险控制
2. 基于数据说话，不凭感觉，不追涨杀跌
3. 仓位管理要科学，单只标的不超过10%，单一行业不超过30%
4. 止损纪律严格执行，亏损达到10%必须减仓，达到12%必须清仓
5. 止盈策略灵活，盈利超过20%可考虑分批止盈

{rag_context}

输出格式要求:
- 使用清晰的 Markdown 格式
- 每个交易信号必须包含：代码、名称、动作、仓位变化、理由、置信度
- 风险预警必须标注严重程度（LOW/MEDIUM/HIGH/CRITICAL）
- 给出明确的数字建议
- 如果你对某个判断的信心低于70%，请明确标注"需人工确认"

语气要求:
- 专业、冷静、客观
- 简洁精炼，直奔主题（盘中时间宝贵）
- 数据支撑结论，引用具体数值""",
            "rebalancing_analysis": """你是一位资深的量化投资组合经理，拥有15年以上的A股资产配置经验。
你的职责是对投资组合进行全面深度分析，制定科学严谨的再平衡方案。

{rag_context}

分析框架:
1. 康波周期定位: 判断当前宏观周期阶段，确定大类资产配置方向
2. 十五五规划对齐: 评估持仓与国家战略方向的匹配度
3. 社保基金风格: 模拟国家队配置逻辑，评估防御/进攻比例
4. 多因子评估: 估值(PE/PB)、成长性(ROE/营收增速)、质量(负债率)、动量
5. 风险分解: 行业集中度、个股相关性、尾部风险

决策原则:
1. 长期配置为主，不因短期波动剧烈调整
2. 再平衡触发条件: 权重偏离目标>3% 或 风险指标破位
3. 调整时考虑交易成本(佣金+印花税+冲击成本)
4. 保持5%以上的现金缓冲
5. 止损-10%/止盈+20%

输出格式:
- 详细分析报告，包含多情景模拟
- 每个调仓建议附带逻辑推理链
- 风险矩阵完整性检查""",
            "macro_analysis": """你是一位宏观策略分析师，精通康波周期、政策分析和产业趋势。
请基于提供的市场数据和宏观指标体系，输出结构化的宏观分析报告。

{rag_context}

分析维度:
1. 康波周期阶段判断与资产配置建议
2. 十五五规划政策对齐度评估
3. 社保基金/国家队风格信号解读
4. 行业轮动与风格切换预判
5. 关键风险事件与情景分析""",
            "report_generation": """你是一位专业的金融报告撰写专家。
请基于提供的数据，生成结构清晰、内容专业的{report_type}报告。
要求: 数据准确、格式规范、结论明确、风险提示完整。""",
            "light_analysis": """你是一位金融数据分类专家。
请对以下内容进行快速分类和情感判断，仅输出结论。""",
        }

        # 通用系统提示词 (向后兼容，非场景路由时使用)
        self.system_prompt = """你是一位资深的量化交易分析师和投资顾问。请根据用户提供的信息：
1. 用专业、客观的语言进行分析
2. 给出数据支撑的结论，避免主观臆断
3. 明确风险提示和操作建议
4. 回复格式清晰，适合直接用于交易报告"""

    def make_decisions(
        self,
        market_data: Dict[str, Any],
        portfolio_data: Dict[str, Any],
        risk_rules: Optional[Dict[str, Any]] = None,
        macro_indicators: Optional[Dict[str, Any]] = None,
        scene: str = "intraday_decision",
        include_fundamentals: Optional[bool] = None,
    ) -> DecisionResult:
        """
        生成综合交易决策 (v5.8 场景路由升级)

        Args:
            market_data: 市场数据 (支持 Wind MCP 动态获取)
                {
                    "日期": "2026-06-29",
                    "指数行情": {"上证指数": {"收盘": 3950, "涨跌幅": "+0.85%"}},
                    "板块表现": {"高端制造": "+2.1%"},
                    "资金流向": {"北向资金": "净流入 +85亿"},
                }
            portfolio_data: 持仓数据
            risk_rules: 风控规则
            macro_indicators: 宏观指标
            scene: 决策场景 (v5.8 新增)
                - "intraday_decision": 盘中决策 (并行对冲)
                - "rebalancing_analysis": 再平衡分析 (交叉验证+基本面RAG)
                - "macro_analysis": 宏观综合分析
                - "report_generation": 报告生成
                - "light_analysis": 轻量分析
            include_fundamentals: 是否包含基本面 RAG (None 则根据场景自动决定)

        Returns:
            DecisionResult 对象，包含所有交易信号和风险预警
        """
        logger.info(f"[决策引擎] 开始生成交易决策, 场景={scene}")

        # v5.8: 自动决定是否包含基本面 RAG
        if include_fundamentals is None:
            include_fundamentals = scene in ("rebalancing_analysis", "macro_analysis")

        # v5.8: 尝试用 Wind MCP 增强市场数据 (替代硬编码)
        if self.config.get("use_wind_mcp", True) and self.wind_provider:
            try:
                # 从 portfolio_data 中提取持仓代码
                holdings_codes = []
                positions_dict = {}
                for holding in portfolio_data.get("持仓", []):
                    code = holding.get("代码", "")
                    if code:
                        holdings_codes.append(code)
                        positions_dict[code] = holding

                if holdings_codes:
                    # 用 Wind MCP 动态获取指数行情
                    wind_market = self.wind_provider.build_market_data(
                        positions=positions_dict,
                        include_fundamentals=include_fundamentals,
                    )

                    # 合并到 market_data (Wind 数据优先)
                    if "指数行情" in wind_market:
                        existing_indices = market_data.get("指数行情", {})
                        for k, v in wind_market["指数行情"].items():
                            if k not in existing_indices:
                                existing_indices[k] = v
                        market_data["指数行情"] = existing_indices
                        market_data["数据来源"] = wind_market.get("数据来源", "Wind MCP")

                    # 如果有基本面数据，注入 RAG
                    if "基本面数据" in wind_market and include_fundamentals:
                        market_data["基本面数据"] = wind_market["基本面数据"]
                        logger.info(f"[Wind MCP] 已加载 {len(wind_market.get('基本面数据', {}))} 只标的基本面数据")

                    logger.info(f"[Wind MCP] 指数行情已更新: {list(wind_market.get('指数行情', {}).keys())}")
            except Exception as e:
                logger.warning(f"[Wind MCP] 数据增强失败: {e}, 使用原始 market_data")

        # 构建决策提示词
        prompt = self._build_decision_prompt(
            market_data=market_data,
            portfolio_data=portfolio_data,
            risk_rules=risk_rules,
            macro_indicators=macro_indicators,
        )

        # v5.8: 使用多模型路由器 (替代旧的单模型调用)
        if self.router:
            return self._make_decision_v58(prompt, scene, market_data, portfolio_data, risk_rules)
        else:
            # 降级到旧版 GLM5Client 模式 (向后兼容)
            return self._make_decision_legacy(prompt, market_data, portfolio_data, risk_rules)

    def _make_decision_v58(
        self,
        prompt: str,
        scene: str,
        market_data: Dict,
        portfolio_data: Dict,
        risk_rules: Optional[Dict],
    ) -> DecisionResult:
        """v5.8 多模型场景路由决策"""
        system_prompt_template = self._scene_prompts.get(scene, self.system_prompt)

        # 构建 RAG 上下文
        rag_context = ""
        if scene in ("rebalancing_analysis", "macro_analysis"):
            fundamental_data = market_data.get("基本面数据", {})
            if fundamental_data:
                rag_context = "\n\n【可用的基本面数据 (Wind MCP)】\n请充分利用以下财务数据进行分析：\n"
                for code, info in fundamental_data.items():
                    rag_context += (
                        f"- {code} {info.get('名称', '')}: PE={info.get('市盈率TTM', 'N/A')}, "
                        f"PB={info.get('市净率', 'N/A')}, ROE={info.get('ROE(%)', 'N/A')}%, "
                        f"营收同比={info.get('营收同比(%)', 'N/A')}%, "
                        f"利润同比={info.get('利润同比(%)', 'N/A')}%, "
                        f"负债率={info.get('资产负债率(%)', 'N/A')}%, "
                        f"市值={info.get('总市值(亿)', 'N/A')}亿, "
                        f"股息率={info.get('股息率(%)', 'N/A')}%\n"
                    )

        system_prompt = system_prompt_template.format(
            rag_context=rag_context,
            report_type="综合",  # 用于 report_generation
        )

        # 构建额外上下文给路由器
        extra_context = {
            "fundamental_data": market_data.get("基本面数据", {}),
            "index_data": market_data.get("指数行情", {}),
            "macro_indicators": {},
        }

        try:
            routing_result = self.router.route(
                scene=scene,
                prompt=prompt,
                system_prompt=system_prompt,
                extra_context=extra_context,
            )

            raw_analysis = routing_result.merged_content

            logger.info(
                f"[v5.8路由] 场景={scene}, 模型路径={routing_result.model_path}, "
                f"延迟={routing_result.latency_ms:.0f}ms, "
                f"置信度={routing_result.confidence:.2f}, "
                f"一致性={routing_result.agreement}, "
                f"成本≈${routing_result.cost_estimate:.6f}"
            )

            # 如果有分歧点，追加到原始分析
            if routing_result.divergent_points:
                raw_analysis += "\n\n## ⚠️ AI 分歧警告\n"
                for point in routing_result.divergent_points:
                    raw_analysis += f"- {point}\n"

            # 解析决策结果
            decision = self._parse_decision_result(
                raw_analysis=raw_analysis,
                market_data=market_data,
                portfolio_data=portfolio_data,
                risk_rules=risk_rules,
            )

            # 补充路由元数据
            decision.ai_confidence = max(decision.ai_confidence, routing_result.confidence)

            return decision

        except Exception as e:
            logger.error(f"[v5.8路由] 调用失败: {e}, 降级到旧版模式")
            return self._make_decision_legacy(prompt, market_data, portfolio_data, risk_rules)

    def _make_decision_legacy(
        self,
        prompt: str,
        market_data: Dict,
        portfolio_data: Dict,
        risk_rules: Optional[Dict],
    ) -> DecisionResult:
        """v5.7 旧版决策模式 (向后兼容降级)"""
        if not self.client:
            return self._create_error_result("无可用模型客户端")

        try:
            result = self.client.chat(
                message=prompt,
                system_prompt=self.system_prompt,
                temperature=self.config.get("temperature", 0.3),
                max_tokens=self.config.get("max_tokens", 3000),
            )

            raw_analysis = result.get("content", "")
            logger.info(f"旧版模式分析完成，输出 {len(raw_analysis)} 字")

            return self._parse_decision_result(
                raw_analysis=raw_analysis,
                market_data=market_data,
                portfolio_data=portfolio_data,
                risk_rules=risk_rules,
            )

        except Exception as e:
            logger.error(f"旧版模式分析失败: {e}")
            return self._create_error_result(str(e))

    def _build_decision_prompt(
        self,
        market_data: Dict,
        portfolio_data: Dict,
        risk_rules: Optional[Dict],
        macro_indicators: Optional[Dict],
    ) -> str:
        """构建决策提示词"""

        prompt = f"""# 交易决策请求

【日期】: {market_data.get("日期", datetime.now().strftime("%Y-%m-%d"))}

## 一、市场数据

### 1. 指数行情
{json.dumps(market_data.get("指数行情", {}), ensure_ascii=False, indent=2)}

### 2. 板块表现
{json.dumps(market_data.get("板块表现", {}), ensure_ascii=False, indent=2)}

### 3. 资金流向
{json.dumps(market_data.get("资金流向", {}), ensure_ascii=False, indent=2)}

### 4. 技术指标
{json.dumps(market_data.get("技术指标", []), ensure_ascii=False, indent=2)}

"""

        # 宏观指标
        if macro_indicators:
            prompt += f"""
## 二、宏观指标

{json.dumps(macro_indicators, ensure_ascii=False, indent=2)}

"""

        # 持仓数据
        prompt += f"""
## 三、持仓数据

### 1. 账户概况
- 账户总值: {portfolio_data.get("账户总值", "N/A")}
- 当日盈亏: {portfolio_data.get("当日盈亏", "N/A")}
- 累计收益: {portfolio_data.get("累计收益", "N/A")}

### 2. 持仓明细
{json.dumps(portfolio_data.get("持仓", []), ensure_ascii=False, indent=2)}

### 3. 目标仓位
{json.dumps(portfolio_data.get("目标仓位", {}), ensure_ascii=False, indent=2)}

"""

        # 风控规则
        if risk_rules:
            prompt += f"""
## 四、风控规则

- 单只标的最大仓位: {risk_rules.get("max_single_position", 0.10) * 100:.0f}%
- 止损线: {risk_rules.get("stop_loss_pct", -0.08) * 100:.0f}%
- 止盈线: {risk_rules.get("take_profit_pct", 0.15) * 100:.0f}%

"""

        # 决策要求
        prompt += """
## 五、决策要求

请根据以上数据，做出以下决策：

### 1. 交易信号（必须给出明确建议）
对每只持仓标的，给出：
- 动作: BUY（买入）/ SELL（卖出）/ HOLD（持有）/ REDUCE（减仓）
- 目标仓位: 建议的目标占比
- 仓位变化: 需要增减的百分点
- 建议数量: 建议买卖的股数
- 置信度: 0-1 之间的数值
- 理由: 基于数据的分析
- 紧急程度: LOW / MEDIUM / HIGH / URGENT

### 2. 风险预警
检查以下风险：
- 止损触发: 是否有标的亏损超过止损线
- 止盈机会: 是否有标的盈利超过止盈线
- 仓位超标: 是否有标的超过最大仓位限制
- 行业集中: 是否有行业过度集中
- 市场风险: 大盘是否出现危险信号

### 3. 组合调整建议
- 是否需要再平衡
- 建议调仓方向
- 现金比例建议

### 4. 宏观展望
- 短期（1-3天）市场走势判断
- 中期（1-4周）趋势预判
- 重点关注的事件和风险

请严格按照以下格式输出：

## 交易信号
| 代码 | 名称 | 动作 | 当前仓位 | 目标仓位 | 变化 | 数量 | 置信度 | 紧急程度 | 理由 |
|------|------|------|---------|---------|------|------|--------|---------|------|
| 300308 | 中际旭创 | BUY | 4.5% | 5.0% | +0.5% | 100股 | 0.85 | MEDIUM | MACD金叉确认，资金流入 |

## 风险预警
- [CRITICAL] 中国神华亏损-9.2%，触发止损线，建议立即减仓50%
- [HIGH] 科技板块仓位已达35%，接近上限，建议控制新增买入

## 组合调整建议
- 建议将现金比例从5%提升至8%
- 减持中国神华2%，增持中际旭创1%

## 宏观展望
- 短期：震荡上行，关注3050点压力位
- 中期：结构性行情，科技板块占优
- 风险：美联储议息会议结果不确定

## AI 决策总结
（用3-5句话总结最重要的决策建议）
"""

        return prompt

    def _parse_decision_result(
        self,
        raw_analysis: str,
        market_data: Dict,
        portfolio_data: Dict,
        risk_rules: Optional[Dict],
    ) -> DecisionResult:
        """解析 GLM-5 的输出结果"""

        # 提取交易信号（从 Markdown 表格中解析）
        trading_signals = self._extract_trading_signals(raw_analysis)

        # 提取风险预警
        risk_alerts = self._extract_risk_alerts(raw_analysis)

        # 提取组合建议
        portfolio_advice = self._extract_section(raw_analysis, "组合调整建议")

        # 提取宏观展望
        macro_outlook = self._extract_section(raw_analysis, "宏观展望")

        # 提取市场概况
        market_summary = self._extract_section(raw_analysis, "AI 决策总结") or "AI 分析完成"

        # 计算整体置信度
        if trading_signals:
            avg_confidence = sum(s.confidence for s in trading_signals) / len(trading_signals)
        else:
            avg_confidence = 0.0

        return DecisionResult(
            timestamp=datetime.now().isoformat(),
            market_summary=market_summary,
            trading_signals=trading_signals,
            risk_alerts=risk_alerts,
            portfolio_advice=portfolio_advice,
            macro_outlook=macro_outlook,
            ai_confidence=avg_confidence,
            raw_analysis=raw_analysis,
        )

    def _extract_trading_signals(self, text: str) -> List[TradingSignal]:
        """从文本中提取交易信号"""
        signals = []

        # 尝试从 Markdown 表格中提取
        lines = text.split("\n")
        in_table = False
        table_rows = []

        for line in lines:
            if "|" in line and ("代码" in line or "标的" in line):
                in_table = True
                continue
            if in_table:
                if line.strip().startswith("|") and "---" not in line:
                    table_rows.append(line)
                else:
                    in_table = False

        # 解析表格行
        for row in table_rows:
            cells = [c.strip() for c in row.split("|")[1:-1]]
            if len(cells) >= 8:
                try:
                    signal = TradingSignal(
                        action=cells[2],
                        code=cells[0],
                        name=cells[1],
                        current_weight=float(cells[3].replace("%", "")) / 100,
                        target_weight=float(cells[4].replace("%", "")) / 100,
                        weight_change=0,  # 可以从 cells[5] 解析
                        quantity=int(cells[6].replace("股", "").replace("手", "")) if cells[6] else 0,
                        price=0,
                        confidence=float(cells[7]),
                        reason=cells[8] if len(cells) > 8 else "",
                        urgency=cells[6]
                        if len(cells) > 6 and cells[6] in ["LOW", "MEDIUM", "HIGH", "URGENT"]
                        else "MEDIUM",
                    )
                    signals.append(signal)
                except (ValueError, IndexError):
                    continue

        # 如果没有表格，尝试从文本中提取
        if not signals:
            signals = self._extract_signals_from_text(text)

        return signals

    def _extract_signals_from_text(self, text: str) -> List[TradingSignal]:
        """从纯文本中提取交易信号（备用方案）"""
        signals = []

        # 简单正则匹配
        import re

        patterns = [
            r"(BUY|SELL|HOLD|REDUCE)\s+([A-Z0-9]+)\s+([^\s,]+)",
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                signals.append(
                    TradingSignal(
                        action=match.group(1).upper(),
                        code=match.group(2),
                        name=match.group(3),
                        current_weight=0,
                        target_weight=0,
                        weight_change=0,
                        quantity=0,
                        price=0,
                        confidence=0.7,
                        reason="AI 自动判断",
                        urgency="MEDIUM",
                    )
                )

        return signals

    def _extract_risk_alerts(self, text: str) -> List[RiskAlert]:
        """从文本中提取风险预警"""
        alerts = []

        import re

        # 匹配 [CRITICAL]/[HIGH]/[MEDIUM]/[LOW] 格式
        pattern = r"\[(CRITICAL|HIGH|MEDIUM|LOW)\]\s+(.+?)(?=\n\[-|\n##|$)"
        matches = re.finditer(pattern, text, re.DOTALL)

        for match in matches:
            severity = match.group(1)
            message = match.group(2).strip()

            # 简单提取股票代码
            code_match = re.search(r"([A-Z0-9]{6})", message)
            code = code_match.group(1) if code_match else "UNKNOWN"

            alerts.append(
                RiskAlert(
                    alert_type="RISK_WARNING",
                    severity=severity,
                    code=code,
                    message=message,
                    action_required="请人工审核",
                )
            )

        return alerts

    def _extract_section(self, text: str, section_name: str) -> str:
        """提取指定章节内容"""
        import re

        pattern = rf"##\s*{section_name}\s*\n(.*?)(?=\n##|\n#|$)"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""

    def _create_error_result(self, error_msg: str) -> DecisionResult:
        """创建错误结果"""
        return DecisionResult(
            timestamp=datetime.now().isoformat(),
            market_summary=f"决策生成失败: {error_msg}",
            trading_signals=[],
            risk_alerts=[
                RiskAlert(
                    alert_type="SYSTEM_ERROR",
                    severity="CRITICAL",
                    code="SYSTEM",
                    message=error_msg,
                    action_required="检查系统配置和网络连接",
                )
            ],
            ai_confidence=0.0,
        )

    def quick_check(self, portfolio_data: Dict) -> DecisionResult:
        """
        快速检查（简化版，仅检查持仓风险）

        Args:
            portfolio_data: 持仓数据

        Returns:
            DecisionResult
        """
        market_data = {
            "日期": datetime.now().strftime("%Y-%m-%d"),
            "指数行情": {},
            "板块表现": {},
            "资金流向": {},
            "技术指标": [],
        }

        return self.make_decisions(
            market_data=market_data,
            portfolio_data=portfolio_data,
            risk_rules={
                "max_single_position": 0.10,
                "stop_loss_pct": -0.08,
                "take_profit_pct": 0.15,
            },
        )

    def export_decisions(self, decision: DecisionResult, output_dir: Optional[str] = None) -> str:
        """
        导出决策结果为 Markdown 文件

        Args:
            decision: 决策结果
            output_dir: 输出目录（默认: 每日报告归档/YYYY-MM-DD/）

        Returns:
            输出文件路径
        """
        if output_dir is None:
            base_dir = Path(__file__).parent.parent.parent / "每日报告归档"
            date_str = datetime.now().strftime("%Y-%m-%d")
            output_dir = base_dir / date_str
        else:
            output_dir = Path(output_dir)

        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = output_dir / f"AI决策_{timestamp}.md"

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("# AI 交易决策报告\n\n")
            f.write(f"**生成时间**: {decision.timestamp}\n")
            f.write(f"**AI 置信度**: {decision.ai_confidence:.2%}\n\n")

            # 市场概况
            f.write(f"## 市场概况\n\n{decision.market_summary}\n\n")

            # 交易信号
            if decision.trading_signals:
                f.write("## 交易信号\n\n")
                f.write("| 代码 | 名称 | 动作 | 当前仓位 | 目标仓位 | 数量 | 置信度 | 紧急程度 |\n")
                f.write("|------|------|------|---------|---------|------|--------|----------|\n")
                for sig in decision.trading_signals:
                    f.write(
                        f"| {sig.code} | {sig.name} | {sig.action} | "
                        f"{sig.current_weight:.1%} | {sig.target_weight:.1%} | "
                        f"{sig.quantity} | {sig.confidence:.2f} | {sig.urgency} |\n"
                    )
                f.write("\n")

            # 风险预警
            if decision.risk_alerts:
                f.write("## 风险预警\n\n")
                for alert in decision.risk_alerts:
                    f.write(f"- **[{alert.severity}]** {alert.message}\n")
                f.write("\n")

            # 组合建议
            if decision.portfolio_advice:
                f.write(f"## 组合调整建议\n\n{decision.portfolio_advice}\n\n")

            # 宏观展望
            if decision.macro_outlook:
                f.write(f"## 宏观展望\n\n{decision.macro_outlook}\n\n")

            # 原始分析
            f.write("---\n\n*以上决策由 GLM-5 AI 自动生成，仅供参考，不构成投资建议*\n")
            f.write("*请人工审核后再执行交易*\n")

        logger.info(f"决策报告已保存: {output_file}")
        return str(output_file)


# ==================== 快捷函数 ====================


def auto_trade_decision(market_data: Dict, portfolio_data: Dict, **kwargs) -> DecisionResult:
    """
    一键生成交易决策

    Args:
        market_data: 市场数据
        portfolio_data: 持仓数据
        **kwargs: 传递给 GLM5DecisionEngine 的参数

    Returns:
        DecisionResult
    """
    engine = GLM5DecisionEngine(**kwargs)
    return engine.make_decisions(market_data, portfolio_data)


if __name__ == "__main__":
    # 测试代码
    print("=" * 60)
    print("GLM-5 自动决策引擎测试")
    print("=" * 60)

    # 模拟市场数据
    market_data = {
        "日期": "2026-06-23",
        "指数行情": {
            "上证指数": {"收盘": 3050.12, "涨跌幅": "+0.85%"},
            "深证成指": {"收盘": 9800.45, "涨跌幅": "+1.20%"},
            "创业板指": {"收盘": 1920.33, "涨跌幅": "+1.50%"},
        },
        "板块表现": {
            "科技": "+2.1%",
            "消费": "-0.5%",
            "能源": "+0.3%",
        },
        "资金流向": {
            "北向资金": "净流入 +85亿",
            "主力资金": "净流入 +120亿",
        },
        "技术指标": [
            "上证指数突破3000点关键位",
            "MACD金叉确认",
            "RSI处于中性区域",
        ],
    }

    # 模拟持仓数据
    portfolio_data = {
        "账户总值": "1,052,340元",
        "当日盈亏": "+12,850元 (+1.24%)",
        "累计收益": "+52,340元 (+5.23%)",
        "持仓": [
            {
                "代码": "300308",
                "名称": "中际旭创",
                "仓位": "5.2%",
                "成本价": 128.50,
                "现价": 145.30,
                "盈亏": "+13.1%",
            },
            {
                "代码": "601088",
                "名称": "中国神华",
                "仓位": "4.8%",
                "成本价": 38.20,
                "现价": 37.90,
                "盈亏": "-0.8%",
            },
            {
                "代码": "518880",
                "名称": "华安黄金ETF",
                "仓位": "4.1%",
                "成本价": 5.12,
                "现价": 5.18,
                "盈亏": "+1.2%",
            },
        ],
        "目标仓位": {
            "中际旭创": "5%",
            "中国神华": "4%",
            "华安黄金ETF": "4%",
        },
    }

    # 风控规则
    risk_rules = {
        "max_single_position": 0.10,
        "stop_loss_pct": -0.08,
        "take_profit_pct": 0.15,
    }

    try:
        # 生成决策
        print("\n正在生成交易决策...\n")
        decision = auto_trade_decision(
            market_data=market_data,
            portfolio_data=portfolio_data,
            risk_rules=risk_rules,
        )

        # 打印结果
        print("=" * 60)
        print("决策结果")
        print("=" * 60)
        print(f"\n市场概况: {decision.market_summary}")

        if decision.trading_signals:
            print(f"\n交易信号 ({len(decision.trading_signals)} 条):")
            for sig in decision.trading_signals:
                print(
                    f"  [{sig.action}] {sig.code} {sig.name} "
                    f"(仓位: {sig.current_weight:.1%} → {sig.target_weight:.1%}, "
                    f"置信度: {sig.confidence:.2f})"
                )
                print(f"    理由: {sig.reason}")

        if decision.risk_alerts:
            print(f"\n风险预警 ({len(decision.risk_alerts)} 条):")
            for alert in decision.risk_alerts:
                print(f"  [{alert.severity}] {alert.message}")

        if decision.portfolio_advice:
            print(f"\n组合建议: {decision.portfolio_advice[:200]}...")

        print(f"\nAI 整体置信度: {decision.ai_confidence:.2%}")

        # 导出报告
        output_file = auto_trade_decision.__globals__["GLM5DecisionEngine"]().__class__.__module__
        engine = GLM5DecisionEngine()
        file_path = engine.export_decisions(decision)
        print(f"\n报告已保存: {file_path}")

        print("\n" + "=" * 60)
        print("✅ 决策引擎测试完成!")

    except Exception as e:
        print(f"\n❌ 决策引擎测试失败: {e}")
        import traceback

        traceback.print_exc()
