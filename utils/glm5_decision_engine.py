# -*- coding: utf-8 -*-
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

import os
import sys
import json
import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.glm5_client import GLM5Client
from utils.multi_model_router import ModelRouter, RoutingResult, get_model_router
from utils.wind_data_provider import WindDataProvider, get_wind_provider

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
            "use_wind_mcp": True,          # v5.8: 使用 Wind MCP 动态数据
            "use_fundamental_rag": True,    # v5.8: 再平衡时启用基本面 RAG
        }
        
        # 合并用户配置
        if kwargs:
            self.config.update(kwargs)
        
        # v5.8: 初始化多模型路由器 (替代旧的 GLM5Client 优先模式)
        try:
            self.router = get_model_router()
            logger.info("✓ 多模型路由器初始化成功")
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.warning(f"多模型路由器初始化失败: {e}, 降级到 GLM5Client")
            self.router = None
        
        # v5.8: 初始化 Wind 数据供应器
        try:
            self.wind_provider = get_wind_provider()
            logger.info(f"✓ Wind 数据供应器初始化成功 (Wind MCP: {'可用' if self.wind_provider._wind_available else '不可用'})")
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
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
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"GLM-5 客户端初始化失败: {e}")
            self.client = None
        
        # v5.8 场景专用系统提示词 (2026-08-07 升级: 注入系统约束 + few-shot + 温度/Token 建议)
        self._scene_prompts = {
            "intraday_decision": """你是资深量化交易决策官，负责A股盘中实时决策。
你的500万实盘组合有严格约束，所有建议必须在约束内。
**建议温度: 0.15 | max_tokens: 800**

## 系统硬约束 (必须遵守)
- 现货账户 400万 + 对冲账户 100万，双账户独立
- 单标的 ≤ 10% 仓位，单板块 ≤ 25%
- 组合日度 VaR95 ≤ 1.5%
- 止损线: 个股亏损 -8% 触发减仓，-10% 强制清仓
- 止盈: 盈利 +20% 考虑分批止盈
- 现金缓冲 ≥ 5%
- 期货对冲: IF/IC/IM/IH 空头，动态调整 Beta 敞口

## 决策框架 (30秒快判)
1. 识别当前最大风险源 (哪只标的/哪个板块/哪类风险)
2. 判断是否需要立即行动 (止损触发？仓位偏离 >3%？市场情绪突变？)
3. 给出不超过 3 条最高优先级操作指令

{rag_context}

## 输出格式 (30秒卡片，极简)
```
## ⚡ 盘中快判 (HH:MM)
**最大风险**: [一句话]
**建议操作**:
1. [动作] [标的代码] [方向] [仓位变化] — [理由, ≤20字] 置信度: X%
2. ...
**风险预警**: [若有: 级别 + 标的 + 原因]
```

## few-shot 示例
```
## ⚡ 盘中快判 (14:32)
**最大风险**: 中国神华亏损 -7.8%，距止损线仅 0.2%
**建议操作**:
1. [HOLD] 继续持有，若 14:50 前未回升至 -6% 则减半仓 — MACD日线仍多头 置信度: 65%
2. [REDUCE] 中际旭创 减仓 2% — 板块轮动转向消费，科技承压 置信度: 72%
**风险预警**: [MEDIUM] 中国神华 距止损线 0.2%，建议 14:50 前决策
```""",

            "rebalancing_analysis": """你是资深量化投资组合经理，负责每日盘后再平衡。
已实盘部署500万（现货400万+对冲100万），目标年化 ≥8%，最大回撤 <15%。
**建议温度: 0.2 | max_tokens: 2000**

## 系统硬约束 (必须遵守)
- 单标的 ≤ 10%，单板块 ≤ 25%，现金缓冲 ≥ 5%
- 止损: 个股 -8% 触发减仓，-10% 强制清仓
- 止盈: +20% 考虑分批止盈
- 再平衡触发: 权重偏离目标 >3% 或 VaR95 > 1.5%
- 组合日度 VaR95 ≤ 1.5%
- 对冲: IF/IC/IM/IH 空头 + 可选 50ETF/300ETF Put 尾部保护

{rag_context}

## 分析框架
1. 组合风险分解: 行业集中度、个股相关性、尾部风险、Beta 敞口
2. 多因子评估: 估值(PE/PB)、成长性(ROE/营收增速)、质量(负债率)、动量(20D/60D)
3. 对冲有效性: 当前 Beta 敞口是否在目标区间（≤0.30）
4. 调仓方案: 需增减标的 + 目标权重 + 执行优先级

## 输出格式
```
## 再平衡分析 (YYYY-MM-DD)
**组合总风险**: [一句话 + VaR95值]
**对冲状态**: Beta敞口=X.XX, 有效性=XX%, [是否需调整]
**调仓建议**:
| 标的 | 动作 | 当前权重 | 目标权重 | 变化 | 优先级 | 理由 |
|------|------|---------|---------|------|--------|------|
**风险矩阵**:
- 行业集中度: [最大行业] = XX%, [是否超标]
- 个股相关性: [最大相关系数] 对
- 尾部风险: [情景] 最大回撤 = XX%
**后续观察**: 3天内需跟踪的关键信号
```""",

            "macro_analysis": """你是宏观策略分析师，关注A股中期趋势和配置方向。
**建议温度: 0.3 | max_tokens: 1500**

{rag_context}

## 分析维度
1. 当前位置: 大盘处于什么阶段（反弹/震荡/趋势/筑顶/筑底），关键支撑/压力位
2. 风格判断: 大盘/小盘、价值/成长、防御/进攻 当前谁占优，持续性如何
3. 行业轮动: 哪些行业资金流入/流出，是否有切换信号
4. 关键风险: 未来1-4周最重要的事件风险
5. 配置建议: 大类资产方向（股票/债券/商品/现金）建议比例

## 输出格式
```
## 宏观判断 (YYYY-MM-DD)
**当前位置**: [阶段] + [关键位]
**风格判断**: [方向] 置信度: X%
**行业轮动**: 流入 Top3 / 流出 Top3
**风险事件**: [事件] [概率] [影响]
**配置建议**: 股票XX% / 债券XX% / 商品XX% / 现金XX%
```""",

            "report_generation": """你是专业金融报告撰写专家，生成结构清晰、数据准确的报告。
**建议温度: 0.5 | max_tokens: 3000**
要求: 数据准确、格式规范、结论明确、风险提示完整。
报告类型: {report_type}""",

            "light_analysis": """你是金融数据分类专家。快速分类和情感判断，仅输出结论。
**建议温度: 0.1 | max_tokens: 300**
输出格式: JSON {{"sentiment":"positive|negative|neutral","confidence":0.0-1.0,"category":"类别","keywords":["词1"]}}""",
        }

        # 通用系统提示词 (向后兼容，非场景路由时使用)
        self.system_prompt = """你是资深量化交易分析师。
要求: 1) 专业客观 2) 数据支撑 3) 风险提示 4) 格式清晰。
系统约束: 单标 ≤10%，板块 ≤25%，止损 -8%，止盈 +20%，现金 ≥5%。"""

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
                for holding in portfolio_data.get('持仓', []):
                    code = holding.get('代码', '')
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
                    if '指数行情' in wind_market:
                        existing_indices = market_data.get('指数行情', {})
                        for k, v in wind_market['指数行情'].items():
                            if k not in existing_indices:
                                existing_indices[k] = v
                        market_data['指数行情'] = existing_indices
                        market_data['数据来源'] = wind_market.get('数据来源', 'Wind MCP')
                    
                    # 如果有基本面数据，注入 RAG
                    if '基本面数据' in wind_market and include_fundamentals:
                        market_data['基本面数据'] = wind_market['基本面数据']
                        logger.info(f"[Wind MCP] 已加载 {len(wind_market.get('基本面数据', {}))} 只标的基本面数据")
                    
                    logger.info(f"[Wind MCP] 指数行情已更新: {list(wind_market.get('指数行情', {}).keys())}")
            except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
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
            fundamental_data = market_data.get('基本面数据', {})
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
            report_type="综合"  # 用于 report_generation
        )
        
        # 构建额外上下文给路由器
        extra_context = {
            "fundamental_data": market_data.get('基本面数据', {}),
            "index_data": market_data.get('指数行情', {}),
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
            
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
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
            
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"旧版模式分析失败: {e}")
            return self._create_error_result(str(e))
    
    def _build_decision_prompt(
        self,
        market_data: Dict,
        portfolio_data: Dict,
        risk_rules: Optional[Dict],
        macro_indicators: Optional[Dict],
    ) -> str:
        """构建决策提示词 (v5.8+ 2026-08-07 升级: 摘要化 + few-shot + 精简表格)"""

        # 提取关键指标 (摘要化，避免 JSON dump 过长)
        index_data = market_data.get('指数行情', {})
        sector_data = market_data.get('板块表现', {})
        flow_data = market_data.get('资金流向', {})

        # 指数摘要 (一行)
        index_lines = []
        for name, info in index_data.items():
            if isinstance(info, dict):
                chg = info.get('涨跌幅', info.get('change', ''))
                close = info.get('收盘', info.get('close', ''))
                index_lines.append(f"{name}: {close} ({chg})")
            else:
                index_lines.append(f"{name}: {info}")
        index_summary = " | ".join(index_lines) if index_lines else "N/A"

        # 板块摘要 (Top 5)
        sector_items = list(sector_data.items())[:5] if isinstance(sector_data, dict) else []
        sector_lines = [f"{k}: {v}" for k, v in sector_items]

        # 资金流向 (一行)
        flow_lines = [f"{k}: {v}" for k, v in flow_data.items()] if flow_data else []
        flow_summary = " | ".join(flow_lines) if flow_lines else "N/A"

        # 持仓摘要 (每只一行，最多 15 只)
        holdings = portfolio_data.get('持仓', [])
        holding_lines = []
        for h in holdings[:15]:
            code = h.get('代码', h.get('code', ''))
            name = h.get('名称', h.get('name', ''))
            weight = h.get('仓位', h.get('weight', h.get('current_weight', '')))
            pnl = h.get('盈亏', h.get('pnl', ''))
            holding_lines.append(
                f"| {code} | {name} | {weight} | {pnl} |"
            )

        prompt = f"""# A股量化组合 — {market_data.get('日期', datetime.now().strftime('%Y-%m-%d'))}

## 一、大盘快照
{index_summary}

## 二、板块表现
{chr(10).join(sector_lines) if sector_lines else 'N/A'}

## 三、资金
{flow_summary}

## 四、账户
- 总值: {portfolio_data.get('账户总值', 'N/A')}
- 当日: {portfolio_data.get('当日盈亏', 'N/A')}
- 累计: {portfolio_data.get('累计收益', 'N/A')}

## 五、持仓明细
| 代码 | 名称 | 仓位 | 盈亏 |
|------|------|------|------|
{chr(10).join(holding_lines) if holding_lines else '| - | 无持仓 | - | - |'}

## 六、风控约束
- 单标 ≤ {risk_rules.get('max_single_position', 0.10) * 100:.0f}% | 板块 ≤ 25%
- 止损: 个股 -8% | 止盈 +20%
- 现金 ≥ 5% | VaR95 ≤ 1.5%

"""
        if macro_indicators:
            prompt += f"""
## 宏观指标
{json.dumps(macro_indicators, ensure_ascii=False, indent=2)}
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
        """从文本中提取交易信号 (v5.8+ 兼容简表格式)"""
        signals = []

        lines = text.split('\n')
        in_table = False

        for line in lines:
            # 检测表格头: "| 代码 | 名称 |" 或 "| 标的 | 动作 |"
            if '|' in line and ('代码' in line or '标的' in line):
                in_table = True
                continue
            if in_table:
                if line.strip().startswith('|') and '---' not in line:
                    cells = [c.strip() for c in line.split('|')[1:-1]]
                    if len(cells) >= 6:  # 至少 6 列: 代码|名称|动作|当前权重|目标权重|理由
                        try:
                            # 提取动作 (在 cells 中查找 BUY/SELL/HOLD/REDUCE)
                            action = "HOLD"
                            action_idx = -1
                            for i, c in enumerate(cells):
                                if c.upper() in ("BUY", "SELL", "HOLD", "REDUCE"):
                                    action = c.upper()
                                    action_idx = i
                                    break

                            # 权重组: 通常紧跟在动作后面
                            current_w = 0.0
                            target_w = 0.0
                            weight_change = 0.0
                            conf = 0.5
                            reason = ""
                            urgency = "MEDIUM"

                            # 尝试按位置解析
                            if action_idx >= 0:
                                for i, c in enumerate(cells):
                                    c_stripped = c.rstrip('%')
                                    try:
                                        val = float(c_stripped)
                                        if val <= 1.0 and current_w == 0.0:
                                            current_w = val
                                        elif val <= 1.0:
                                            target_w = val
                                    except ValueError:
                                        pass
                                # 置信度在倒数第 2-3 列
                                for c in reversed(cells):
                                    try:
                                        v = float(c)
                                        if 0 < v <= 1.0:
                                            conf = v
                                            break
                                    except ValueError:
                                        pass
                                # 找到非空的最长文本作为理由
                                texts = [c for c in cells if len(c) > 5 and not c[0].isdigit()]
                                if texts:
                                    reason = texts[-1]

                            signal = TradingSignal(
                                action=action,
                                code=cells[0],
                                name=cells[1] if len(cells) > 1 else "",
                                current_weight=current_w,
                                target_weight=target_w,
                                weight_change=weight_change,
                                quantity=0,
                                price=0,
                                confidence=conf,
                                reason=reason,
                                urgency=urgency,
                            )
                            signals.append(signal)
                        except (ValueError, IndexError):
                            continue
                else:
                    in_table = False

        # 回退: 文本提取
        if not signals:
            signals = self._extract_signals_from_text(text)

        return signals
    
    def _extract_signals_from_text(self, text: str) -> List[TradingSignal]:
        """从纯文本中提取交易信号（备用方案）"""
        signals = []
        
        # 简单正则匹配
        import re
        patterns = [
            r'(BUY|SELL|HOLD|REDUCE)\s+([A-Z0-9]+)\s+([^\s,]+)',
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                signals.append(TradingSignal(
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
                ))
        
        return signals
    
    def _extract_risk_alerts(self, text: str) -> List[RiskAlert]:
        """从文本中提取风险预警"""
        alerts = []
        
        import re
        # 匹配 [CRITICAL]/[HIGH]/[MEDIUM]/[LOW] 格式
        pattern = r'\[(CRITICAL|HIGH|MEDIUM|LOW)\]\s+(.+?)(?=\n\[-|\n##|$)'
        matches = re.finditer(pattern, text, re.DOTALL)
        
        for match in matches:
            severity = match.group(1)
            message = match.group(2).strip()
            
            # 简单提取股票代码
            code_match = re.search(r'([A-Z0-9]{6})', message)
            code = code_match.group(1) if code_match else "UNKNOWN"
            
            alerts.append(RiskAlert(
                alert_type="RISK_WARNING",
                severity=severity,
                code=code,
                message=message,
                action_required="请人工审核"
            ))
        
        return alerts
    
    def _extract_section(self, text: str, section_name: str) -> str:
        """提取指定章节内容"""
        import re
        pattern = rf'##\s*{section_name}\s*\n(.*?)(?=\n##|\n#|$)'
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
            risk_alerts=[RiskAlert(
                alert_type="SYSTEM_ERROR",
                severity="CRITICAL",
                code="SYSTEM",
                message=error_msg,
                action_required="检查系统配置和网络连接"
            )],
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
            }
        )
    
    def export_decisions(self, decision: DecisionResult, output_dir: str = None) -> str:
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
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"# AI 交易决策报告\n\n")
            f.write(f"**生成时间**: {decision.timestamp}\n")
            f.write(f"**AI 置信度**: {decision.ai_confidence:.2%}\n\n")
            
            # 市场概况
            f.write(f"## 市场概况\n\n{decision.market_summary}\n\n")
            
            # 交易信号
            if decision.trading_signals:
                f.write(f"## 交易信号\n\n")
                f.write("| 代码 | 名称 | 动作 | 当前仓位 | 目标仓位 | 数量 | 置信度 | 紧急程度 |\n")
                f.write("|------|------|------|---------|---------|------|--------|----------|\n")
                for sig in decision.trading_signals:
                    f.write(f"| {sig.code} | {sig.name} | {sig.action} | "
                           f"{sig.current_weight:.1%} | {sig.target_weight:.1%} | "
                           f"{sig.quantity} | {sig.confidence:.2f} | {sig.urgency} |\n")
                f.write("\n")
            
            # 风险预警
            if decision.risk_alerts:
                f.write(f"## 风险预警\n\n")
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
            f.write(f"---\n\n*以上决策由 GLM-5 AI 自动生成，仅供参考，不构成投资建议*\n")
            f.write("*请人工审核后再执行交易*\n")
        
        logger.info(f"决策报告已保存: {output_file}")
        return str(output_file)


# ==================== 快捷函数 ====================

def auto_trade_decision(
    market_data: Dict,
    portfolio_data: Dict,
    **kwargs
) -> DecisionResult:
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
    logger.info("=" * 60)
    logger.info("GLM-5 自动决策引擎测试")
    logger.info("=" * 60)
    
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
        logger.info("\n正在生成交易决策...\n")
        decision = auto_trade_decision(
            market_data=market_data,
            portfolio_data=portfolio_data,
            risk_rules=risk_rules,
        )
        
        # 打印结果
        logger.info("=" * 60)
        logger.info("决策结果")
        logger.info("=" * 60)
        logger.info(f"\n市场概况: {decision.market_summary}")
        
        if decision.trading_signals:
            logger.info(f"\n交易信号 ({len(decision.trading_signals)} 条):")
            for sig in decision.trading_signals:
                logger.info(f"  [{sig.action}] {sig.code} {sig.name} "
                      f"(仓位: {sig.current_weight:.1%} → {sig.target_weight:.1%}, "
                      f"置信度: {sig.confidence:.2f})")
                logger.info(f"    理由: {sig.reason}")
        
        if decision.risk_alerts:
            logger.info(f"\n风险预警 ({len(decision.risk_alerts)} 条):")
            for alert in decision.risk_alerts:
                logger.info(f"  [{alert.severity}] {alert.message}")
        
        if decision.portfolio_advice:
            logger.info(f"\n组合建议: {decision.portfolio_advice[:200]}...")
        
        logger.info(f"\nAI 整体置信度: {decision.ai_confidence:.2%}")
        
        # 导出报告
        output_file = auto_trade_decision.__globals__['GLM5DecisionEngine']().__class__.__module__
        engine = GLM5DecisionEngine()
        file_path = engine.export_decisions(decision)
        logger.info(f"\n报告已保存: {file_path}")
        
        logger.info("\n" + "=" * 60)
        logger.info("✅ 决策引擎测试完成!")
        
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"\n❌ 决策引擎测试失败: {e}")
        import traceback
        traceback.print_exc()
