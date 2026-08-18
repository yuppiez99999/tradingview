"""
ETF资金流向盘前/盘中决策模块 v1.0
=====================================

功能:
- 盘前(09:15-09:25): 基于隔夜资金流信号生成预配置建议
- 盘中(09:30-15:00): 实时资金流监控 + LLM辅助决策
- 盘后(15:00-15:30): 资金流复盘 + 信号归档

数据源优先级: Wind MCP > 东财push2 > 新浪 > 价格动量代理
LLM降级链: DeepSeek (V3/R1, 主 LLM) → Ollama本地 → GLM-5 → 豆包 → 规则引擎

集成点:
- daily_build_and_hedge.py (盘前报告第七节/第八节)
- utils/etf_flow_monitor.py (资金流获取)
- utils/astock_realtime.py (实时行情)
- 15_每日工作流/llm_client.py (本地LLM调用)

用法:
    from utils.etf_flow_decision import ETFFlowDecisionEngine
    engine = ETFFlowDecisionEngine()

    # 盘前决策 (09:15-09:25)
    pre_market_plan = engine.pre_market_decision()

    # 盘中决策 (09:30-15:00)
    intraday_signal = engine.intraday_decision()

    # 盘后复盘 (15:00-15:30)
    post_review = engine.post_market_review()
"""

import argparse
import json
import logging
import sys
import threading
import time
import types
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict, cast

logger = logging.getLogger(__name__)

# 支持直接运行: python utils/etf_flow_decision.py --phase pre_market
if __package__ is None:
    _pkg_root = str(Path(__file__).resolve().parent.parent)
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)

# 导入28系统核心模块
from utils.astock_realtime import get_realtime_quotes
from utils.etf_flow_monitor import NATIONAL_TEAM_ETFS, ETFRealTimeTracker
from utils.logger import get_logger
from utils.signal_fusion import SignalFusionEngine

logger = get_logger("etf_flow_decision")

# ============================================================
# 决策时间窗口定义
# ============================================================

PRE_MARKET_START = "09:15"  # 盘前开始
PRE_MARKET_END = "09:25"  # 盘前结束 (集合竞价)
INTRADAY_START = "09:30"  # 盘中开始
INTRADAY_END = "15:00"  # 盘中结束
POST_MARKET_START = "15:00"  # 盘后开始
POST_MARKET_END = "15:30"  # 盘后结束

# ============================================================
# 信号阈值配置
# ============================================================

SIGNAL_CONFIG = {
    # 资金流信号阈值 (亿元)
    "strong_inflow": 5.0,  # 强流入
    "medium_inflow": 2.0,  # 中流入
    "weak_inflow": 0.5,  # 弱流入
    "strong_outflow": -5.0,  # 强流出
    "medium_outflow": -2.0,  # 中流出
    "weak_outflow": -0.5,  # 弱流出
    # LLM决策权重
    "llm_weight": 0.3,  # LLM占30%权重
    "flow_weight": 0.5,  # 资金流占50%权重
    "price_weight": 0.2,  # 价格动量占20%权重
    # 置信度过滤
    "min_confidence": 0.4,  # 最低置信度
    "high_confidence": 0.7,  # 高置信度阈值
}

# ============================================================
# 决策结果 TypedDict (根除 logger 内 decision_result["summary"]["xxx"] [index] 报错)
# ============================================================

class DecisionSummary(TypedDict, total=False):
    total_etfs: int
    strong_signals: int
    medium_signals: int
    total_inflow: float
    sudden_changes: int

class DecisionResult(TypedDict, total=False):
    status: str
    phase: str
    timestamp: str
    elapsed_seconds: float
    summary: DecisionSummary
    signals: Dict[str, Dict]
    sudden_changes: List[Dict]
    fused_signals: List[Dict[str, Any]]
    recommendations: List[Dict]
    llm_analysis: Optional[str]
    realtime_snapshot: Dict[str, Any]


# ============================================================
# ETF资金流决策引擎
# ============================================================


class ETFFlowDecisionEngine:
    """ETF资金流向盘前/盘中/盘后决策引擎"""

    # 可选懒加载属性 — 根除 None 单例推断触发的 [union-attr]
    _local_llm_client: Optional[Any]
    _decision_cache: Dict[str, Dict[str, Any]]
    _cache_ttl: int
    tracker: Any
    fusion_engine: Any

    def __init__(self):
        self.tracker = ETFRealTimeTracker()
        self.fusion_engine = SignalFusionEngine()
        self._local_llm_client = None
        self._decision_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = 300  # 5分钟缓存

    def _get_llm_client(self):
        """延迟加载LLM客户端"""
        if self._local_llm_client is None:
            try:
                llm_path = Path(__file__).parent.parent / "15_每日工作流" / "llm_client.py"
                llm_path = llm_path.resolve()
                if llm_path.exists():
                    import importlib.util

                    spec = importlib.util.spec_from_file_location("llm_client", llm_path)
                    # None 守卫: spec / spec.loader 都可能为 None(importlib 官方签名)
                    if spec is None or spec.loader is None:
                        raise RuntimeError(f"无法构造 LLM 客户端 ModuleSpec: {llm_path}")
                    mod = cast(types.ModuleType, importlib.util.module_from_spec(spec))
                    spec.loader.exec_module(mod)
                    self._local_llm_client = mod
                    logger.info("LLM客户端已加载 (DeepSeek优先降级链: DeepSeek → Ollama → GLM → 豆包)")
            except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
                logger.warning(f"LLM客户端加载失败: {e}，将使用纯规则引擎")
        return self._local_llm_client

    def _call_llm_analysis(self, prompt: str, system: str = "") -> Optional[str]:
        """调用本地LLM进行分析

        降级链: DeepSeek (V3/R1, 主 LLM) → Ollama → GLM-5 → 豆包 → 规则引擎
        """
        llm_mod = self._get_llm_client()
        if llm_mod is None:
            logger.warning("LLM不可用，使用规则引擎")
            return None

        try:
            result = llm_mod.chat(
                prompt=prompt,
                system=system or "你是一个专业的量化交易助手，擅长ETF资金流分析和投资决策。",
                temperature=0.3,
                max_tokens=1500,
            )
            if result:
                logger.info(f"LLM分析成功: {len(result)} 字")
                return cast(Optional[str], result)
            else:
                logger.warning("LLM返回为空，降级到规则引擎")
                return None
        except (AttributeError, TypeError, ValueError, OSError) as e:
            logger.error(f"LLM调用失败: {e}")
            return None

    def _parse_etf_flow_data(self, flow_data: Dict) -> Dict[str, Dict]:
        """解析ETF资金流数据为标准化格式"""
        standardized = {}
        for code, data in flow_data.items():
            net_flow = data.get("net_flow_yi", 0)

            # 计算信号强度 (-1 到 1)
            if abs(net_flow) >= SIGNAL_CONFIG["strong_inflow"]:
                strength = 1.0 if net_flow > 0 else -1.0
            elif abs(net_flow) >= SIGNAL_CONFIG["medium_inflow"]:
                strength = 0.6 if net_flow > 0 else -0.6
            elif abs(net_flow) >= SIGNAL_CONFIG["weak_inflow"]:
                strength = 0.3 if net_flow > 0 else -0.3
            else:
                strength = 0.0

            # 计算置信度 (基于数据源质量和流量幅度)
            source = data.get("source", "unknown")
            source_confidence = {
                "wind_mcp": 0.9,

                "eastmoney_push2": 0.8,
                "sina_http": 0.7,
                "price_momentum": 0.5,
            }.get(source, 0.6)

            flow_magnitude = abs(net_flow) / SIGNAL_CONFIG["strong_inflow"]
            confidence = min(1.0, source_confidence * (0.5 + 0.5 * flow_magnitude))

            standardized[code] = {
                "strength": strength,
                "confidence": confidence,
                "source_confidence": source_confidence,
                "net_flow_yi": net_flow,
                "trend": data.get("trend", "中性"),
                "source": source,
                "name": data.get("name", ""),
                "category": data.get("category", ""),
            }

        return standardized

    def _build_llm_prompt(self, flow_data: Dict, realtime_data: Dict, timestamp: str) -> str:
        """构建LLM分析prompt"""
        # 提取关键信号
        strong_signals = []
        for code, data in flow_data.items():
            if abs(data.get("net_flow_yi", 0)) >= SIGNAL_CONFIG["medium_inflow"]:
                strong_signals.append(
                    f"{data.get('name', code)}: {data['trend']} {data.get('net_flow_yi', 0):+.2f}亿 (来源: {data.get('source', '-')})"
                )

        signals_text = "\n".join(strong_signals) if strong_signals else "无显著信号"

        prompt = f"""ETF资金流向分析任务 ({timestamp})

【显著资金流信号】
{signals_text}

【实时行情快照】
"""
        for code, data in realtime_data.items():
            prompt += f"- {data.get('name', code)}: {data.get('price', 0):.3f}元 ({data.get('change_pct', 0):+.2f}%) \n"

        prompt += """
【分析要求】
1. 识别主力资金方向 (加仓/减仓/调仓)
2. 评估信号可信度 (高/中/低)
3. 给出交易建议 (加仓/持有/减仓/观望)
4. 风险提示 (资金流背离/异常波动)

请输出结构化分析结果，不超过500字。"""

        return prompt

    # ------------------------------------------------------------
    # 盘前决策 (09:15-09:25)
    # ------------------------------------------------------------

    def pre_market_decision(self) -> Dict[str, Any]:
        """盘前决策: 基于昨日收盘后资金流信号生成今日预配置

        时间窗口: 09:15-09:25 (集合竞价前)
        数据源: 昨日收盘资金流 + 隔夜新闻
        输出: 今日预配置计划
        """
        logger.info("=" * 60)
        logger.info("【盘前决策】开始生成ETF资金流预配置计划")
        logger.info("=" * 60)

        start_time = time.time()
        timestamp = datetime.now().strftime("%Y-%m-%d 09:15-09:25")

        # Step 1: 获取昨日ETF资金流数据
        logger.info("Step 1: 获取ETF资金流数据...")
        try:
            flow_data = self.tracker.get_all_etf_fund_flows()
            if not flow_data:
                logger.warning("无法获取资金流数据，返回空配置")
                return {
                    "status": "error",
                    "message": "资金流数据获取失败",
                    "timestamp": timestamp,
                    "signals": [],
                    "recommendations": [],
                }
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.error(f"资金流数据获取失败: {e}")
            return {
                "status": "error",
                "message": f"资金流数据获取失败: {e}",
                "timestamp": timestamp,
                "signals": [],
                "recommendations": [],
            }

        # Step 2: 标准化信号
        logger.info("Step 2: 标准化资金流信号...")
        standardized_signals = self._parse_etf_flow_data(flow_data)

        # Step 3: 获取实时行情快照 (预开盘价)
        logger.info("Step 3: 获取实时行情快照...")
        etf_codes = [etf["code"] for etf in NATIONAL_TEAM_ETFS[:13]]  # 前13只核心ETF
        realtime_data = get_realtime_quotes(etf_codes)

        # Step 4: LLM辅助分析 (可选)
        logger.info("Step 4: LLM辅助分析 (可选)...")
        llm_analysis = None
        try:
            prompt = self._build_llm_prompt(flow_data, realtime_data, timestamp)
            llm_analysis = self._call_llm_analysis(prompt)
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.warning(f"LLM分析失败: {e}，继续使用规则引擎")

        # Step 5: 生成交易建议
        logger.info("Step 5: 生成交易建议...")
        recommendations = self._generate_recommendations(standardized_signals, realtime_data)

        # Step 6: 信号融合
        logger.info("Step 6: 信号融合...")
        etf_signals_dict = {
            code: {"strength": sig["strength"], "confidence": sig["confidence"]}
            for code, sig in standardized_signals.items()
        }
        fused_signals = self.fusion_engine.fuse(etf_signals=etf_signals_dict)

        elapsed = time.time() - start_time
        decision_result = {
            "status": "success",
            "phase": "pre_market",
            "timestamp": timestamp,
            "elapsed_seconds": round(elapsed, 2),
            "summary": {
                "total_etfs": len(flow_data),
                "strong_signals": len([s for s in standardized_signals.values() if abs(s["strength"]) >= 0.6]),
                "medium_signals": len([s for s in standardized_signals.values() if 0.3 <= abs(s["strength"]) < 0.6]),
                "total_inflow": sum(d.get("net_flow_yi", 0) for d in flow_data.values()),
            },
            "signals": standardized_signals,
            "fused_signals": [s.to_dict() for s in fused_signals[:10]],
            "recommendations": recommendations,
            "llm_analysis": llm_analysis,
            "realtime_snapshot": realtime_data,
        }

        # 用显式 TypedDict 收窄 summary，根除 [index] 错误
        summary = cast(DecisionSummary, decision_result.get("summary", {}))

        logger.info(
            f"【盘前决策】完成: {summary.get('strong_signals', 0)} 强信号, "
            f"{summary.get('medium_signals', 0)} 中信号, "
            f"耗时 {elapsed:.1f}秒"
        )

        return decision_result

    # ------------------------------------------------------------
    # 盘中决策 (09:30-15:00)
    # ------------------------------------------------------------

    def intraday_decision(self, refresh_interval: int = 300) -> Dict[str, Any]:
        """盘中决策: 实时监控资金流变化 + LLM辅助调仓

        时间窗口: 09:30-15:00 (交易时段)
        数据源: 实时资金流 + 实时行情
        刷新频率: 每5分钟 (refresh_interval秒)
        输出: 实时调仓建议
        """
        logger.info("=" * 60)
        logger.info("【盘中决策】开始实时监控ETF资金流")
        logger.info("=" * 60)

        start_time = time.time()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 检查缓存 (避免重复调用)
        cache_key = "intraday_latest"
        if cache_key in self._decision_cache:
            cached = self._decision_cache[cache_key]
            ts = cached.get("timestamp", 0.0)
            if time.time() - ts < refresh_interval:
                logger.info(f"命中盘中决策缓存 (剩余 {refresh_interval - (time.time() - ts):.0f}秒)")
                return cast(Dict[str, Any], cached.get("result"))

        # Step 1: 获取实时资金流 (东财push2实时数据)
        logger.info("Step 1: 获取实时资金流...")
        try:
            flow_data = self.tracker.get_all_etf_fund_flows()
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.error(f"实时资金流获取失败: {e}")
            flow_data = {}

        # Step 2: 获取实时行情
        logger.info("Step 2: 获取实时行情...")
        etf_codes = [etf["code"] for etf in NATIONAL_TEAM_ETFS[:13]]
        realtime_data = get_realtime_quotes(etf_codes)

        # Step 3: 检测资金流突变 (较盘前变化>50%)
        logger.info("Step 3: 检测资金流突变...")
        sudden_changes = self._detect_sudden_changes(flow_data)

        # Step 4: 生成交易建议
        logger.info("Step 4: 生成交易建议...")
        standardized_signals = self._parse_etf_flow_data(flow_data)
        recommendations = self._generate_recommendations(standardized_signals, realtime_data, sudden_changes)

        # Step 5: LLM辅助决策 (仅在有突变信号时调用)
        llm_analysis = None
        if sudden_changes:
            logger.info("Step 5: 检测到突变信号，调用LLM辅助决策...")
            try:
                prompt = self._build_intraday_llm_prompt(flow_data, realtime_data, sudden_changes, timestamp)
                llm_analysis = self._call_llm_analysis(prompt)
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
                logger.warning(f"LLM分析失败: {e}")

        # Step 6: 信号融合
        etf_signals_dict = {
            code: {"strength": sig["strength"], "confidence": sig["confidence"]}
            for code, sig in standardized_signals.items()
        }
        fused_signals = self.fusion_engine.fuse(etf_signals=etf_signals_dict)

        elapsed = time.time() - start_time
        decision_result = {
            "status": "success",
            "phase": "intraday",
            "timestamp": timestamp,
            "elapsed_seconds": round(elapsed, 2),
            "summary": {
                "total_etfs": len(flow_data),
                "sudden_changes": len(sudden_changes),
                "strong_signals": len([s for s in standardized_signals.values() if abs(s["strength"]) >= 0.6]),
                "total_inflow": sum(d.get("net_flow_yi", 0) for d in flow_data.values()),
            },
            "sudden_changes": sudden_changes,
            "signals": standardized_signals,
            "fused_signals": [s.to_dict() for s in fused_signals[:10]],
            "recommendations": recommendations,
            "llm_analysis": llm_analysis,
            "realtime_snapshot": realtime_data,
        }

        # 更新缓存
        self._decision_cache[cache_key] = {
            "result": decision_result,
            "timestamp": time.time(),
        }

        # 用显式 TypedDict 收窄 summary，根除 [index] 错误
        summary_intra = cast(DecisionSummary, decision_result.get("summary", {}))
        logger.info(
            f"【盘中决策】完成: {summary_intra.get('sudden_changes', 0)} 个突变信号, "
            f"耗时 {elapsed:.1f}秒"
        )

        return decision_result

    def _build_intraday_llm_prompt(
        self, flow_data: Dict, realtime_data: Dict, sudden_changes: List[Dict], timestamp: str
    ) -> str:
        """构建盘中LLM分析prompt (含突变信号)"""
        prompt = f"""ETF盘中突变信号分析 ({timestamp})

【检测到 {len(sudden_changes)} 个资金流突变信号】
"""
        for change in sudden_changes[:5]:
            prompt += f"- {change['name']}: {change['description']} \n"

        prompt += """
【实时行情】
"""
        for code, data in list(realtime_data.items())[:5]:
            prompt += f"- {data.get('name', code)}: {data.get('price', 0):.3f}元 ({data.get('change_pct', 0):+.2f}%) \n"

        prompt += """
【分析要求】
1. 突变信号是否可信 (真突破 vs 假突破)
2. 是否需要立即调仓 (紧急/谨慎/观望)
3. 风险控制建议 (止损位/仓位上限)

请快速响应，输出不超过300字。"""

        return prompt

    # ------------------------------------------------------------
    # 盘后复盘 (15:00-15:30)
    # ------------------------------------------------------------

    def post_market_review(self) -> Dict[str, Any]:
        """盘后复盘: 总结当日资金流走势 + 信号有效性评估

        时间窗口: 15:00-15:30
        数据源: 收盘资金流 + 全日Tick数据
        输出: 复盘报告 + 明日预配置
        """
        logger.info("=" * 60)
        logger.info("【盘后复盘】开始生成ETF资金流复盘报告")
        logger.info("=" * 60)

        start_time = time.time()
        timestamp = datetime.now().strftime("%Y-%m-%d 15:00-15:30")

        # Step 1: 获取收盘资金流
        logger.info("Step 1: 获取收盘资金流...")
        try:
            flow_data = self.tracker.get_all_etf_fund_flows()
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.error(f"收盘资金流获取失败: {e}")
            flow_data = {}

        # Step 2: 获取收盘行情
        logger.info("Step 2: 获取收盘行情...")
        etf_codes = [etf["code"] for etf in NATIONAL_TEAM_ETFS[:13]]
        realtime_data = get_realtime_quotes(etf_codes)

        # Step 3: 评估信号有效性
        logger.info("Step 3: 评估信号有效性...")
        standardized_signals = self._parse_etf_flow_data(flow_data)
        signal_effectiveness = self._evaluate_signal_effectiveness(standardized_signals, realtime_data)

        # Step 4: 生成明日预配置
        logger.info("Step 4: 生成明日预配置...")
        tomorrow_preview = self._generate_tomorrow_preview(standardized_signals, signal_effectiveness)

        elapsed = time.time() - start_time
        review_result = {
            "status": "success",
            "phase": "post_market",
            "timestamp": timestamp,
            "elapsed_seconds": round(elapsed, 2),
            "summary": {
                "total_etfs": len(flow_data),
                "signal_accuracy": signal_effectiveness.get("accuracy_rate", 0),
                "total_inflow": sum(d.get("net_flow_yi", 0) for d in flow_data.values()),
            },
            "signal_effectiveness": signal_effectiveness,
            "signals": standardized_signals,
            "tomorrow_preview": tomorrow_preview,
            "realtime_snapshot": realtime_data,
        }

        logger.info(
            f"【盘后复盘】完成: 信号准确率 {signal_effectiveness.get('accuracy_rate', 0):.1%}, 耗时 {elapsed:.1f}秒"
        )

        return review_result

    # ------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------

    def _generate_recommendations(
        self, signals: Dict, realtime_data: Dict, sudden_changes: Optional[List[Dict]] = None
    ) -> List[Dict]:
        """生成交易建议"""
        recommendations = []
        sudden_changes = sudden_changes or []
        {c.get("code") for c in sudden_changes}

        for code, signal in signals.items():
            if abs(signal["strength"]) < SIGNAL_CONFIG["weak_inflow"]:
                continue

            realtime = realtime_data.get(code, {})
            change_pct = realtime.get("change_pct", 0)

            # 判断信号类型
            if signal["strength"] >= 0.6:
                action = "加仓"
                reason = f"主力净流入 {signal['net_flow_yi']:+.2f}亿"
            elif signal["strength"] >= 0.3:
                action = "关注"
                reason = f"资金小幅流入 {signal['net_flow_yi']:+.2f}亿"
            elif signal["strength"] <= -0.6:
                action = "减仓"
                reason = f"主力净流出 {signal['net_flow_yi']:+.2f}亿"
            else:
                action = "观望"
                reason = f"资金小幅流出 {signal['net_flow_yi']:+.2f}亿"

            # 突变信号升级
            if code in sudden_changes:
                action = "紧急" + action
                reason += " [突变信号]"

            # 价格-资金流背离警告
            if (change_pct > 0 and signal["strength"] < -0.3) or (change_pct < 0 and signal["strength"] > 0.3):
                reason += " ⚠️ 价格-资金流背离"

            recommendations.append(
                {
                    "code": code,
                    "name": signal.get("name", ""),
                    "action": action,
                    "reason": reason,
                    "strength": signal["strength"],
                    "confidence": signal["confidence"],
                    "net_flow_yi": signal["net_flow_yi"],
                    "price_change_pct": change_pct,
                }
            )

        # 按置信度和强度排序
        recommendations.sort(key=lambda x: x["confidence"] * abs(x["strength"]), reverse=True)

        return recommendations

    def _detect_sudden_changes(self, flow_data: Dict, threshold: float = 0.5) -> List[Dict]:
        """检测资金流突变 (较前值变化>threshold)"""
        changes = []
        for code, data in flow_data.items():
            net_flow = data.get("net_flow_yi", 0)
            # 简化版: 检测大额资金流 (绝对值>5亿)
            if abs(net_flow) >= SIGNAL_CONFIG["strong_inflow"]:
                direction = "大幅流入" if net_flow > 0 else "大幅流出"
                changes.append(
                    {
                        "code": code,
                        "name": data.get("name", code),
                        "direction": direction,
                        "magnitude": abs(net_flow),
                        "description": f"{data.get('name', code)} {direction} {abs(net_flow):.2f}亿",
                    }
                )
        return changes

    def _evaluate_signal_effectiveness(self, signals: Dict, realtime_data: Dict) -> Dict:
        """评估信号有效性 (资金流方向 vs 价格变动方向)"""
        correct_predictions = 0
        total_predictions = 0

        for code, signal in signals.items():
            realtime = realtime_data.get(code, {})
            change_pct = realtime.get("change_pct", 0)

            if abs(change_pct) < 0.1:  # 价格变动不明显，跳过
                continue

            total_predictions += 1
            # 资金流方向与价格方向一致 → 正确
            if (signal["strength"] > 0 and change_pct > 0) or (signal["strength"] < 0 and change_pct < 0):
                correct_predictions += 1

        accuracy = correct_predictions / total_predictions if total_predictions > 0 else 0.0

        return {
            "total_predictions": total_predictions,
            "correct_predictions": correct_predictions,
            "accuracy_rate": accuracy,
            "evaluation_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _generate_tomorrow_preview(self, signals: Dict, effectiveness: Dict) -> Dict:
        """生成明日预配置建议"""
        top_inflow = sorted(
            [(c, s) for c, s in signals.items() if s["strength"] > 0],
            key=lambda x: x[1]["strength"],
            reverse=True,
        )[:3]

        top_outflow = sorted(
            [(c, s) for c, s in signals.items() if s["strength"] < 0],
            key=lambda x: x[1]["strength"],
        )[:3]

        return {
            "top_inflow_etfs": [
                {"code": c, "name": s.get("name", ""), "strength": s["strength"]} for c, s in top_inflow
            ],
            "top_outflow_etfs": [
                {"code": c, "name": s.get("name", ""), "strength": s["strength"]} for c, s in top_outflow
            ],
            "suggested_focus": [{"code": c, "action": "加仓"} for c, _ in top_inflow[:2]],
            "risk_warning": [{"code": c, "action": "减仓"} for c, _ in top_outflow[:2]],
        }


# ============================================================
# 定时任务调度器
# ============================================================


class ETFFlowDecisionScheduler:
    """ETF资金流决策定时调度器"""

    # 可选线程属性 — 根除 __init__ 中 = None 的 None 单例推断
    _thread: Optional[threading.Thread]
    _running: bool
    engine: ETFFlowDecisionEngine

    def __init__(self, engine: ETFFlowDecisionEngine):
        self.engine = engine
        self._running = False
        self._thread = None

    def start_intraday_monitoring(self, interval: int = 300):
        """启动盘中实时监控 (后台线程)

        Args:
            interval: 刷新间隔 (秒)，默认5分钟
        """
        if self._running:
            logger.warning("盘中监控已在运行")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True,
        )
        t = self._thread
        if t is not None:
            t.start()
        logger.info(f"盘中监控已启动 (间隔: {interval}秒)")

    def _monitor_loop(self, interval: int):
        """监控循环"""
        while self._running:
            try:
                current_time = datetime.now().strftime("%H:%M")
                if INTRADAY_START <= current_time <= INTRADAY_END:
                    logger.info(f"[{current_time}] 执行盘中决策...")
                    result = self.engine.intraday_decision()
                    if result.get("sudden_changes"):
                        logger.warning(f"检测到 {len(result['sudden_changes'])} 个突变信号!")
                else:
                    time.sleep(60)  # 非交易时段，每分钟检查一次
                    continue

                time.sleep(interval)
            except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
                logger.error(f"盘中监控异常: {e}")
                time.sleep(60)

    def stop(self):
        """停止盘中监控"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("盘中监控已停止")


# ============================================================
# 便捷函数
# ============================================================


def pre_market_decision() -> Dict[str, Any]:
    """便捷函数: 盘前决策"""
    engine = ETFFlowDecisionEngine()
    return engine.pre_market_decision()


def intraday_decision() -> Dict[str, Any]:
    """便捷函数: 盘中决策"""
    engine = ETFFlowDecisionEngine()
    return engine.intraday_decision()


def post_market_review() -> Dict[str, Any]:
    """便捷函数: 盘后复盘"""
    engine = ETFFlowDecisionEngine()
    return engine.post_market_review()


if __name__ == "__main__":
    # 测试入口 / 直接运行入口
    parser = argparse.ArgumentParser(description="ETF资金流向盘前/盘中/盘后决策模块 v1.0")
    parser.add_argument(
        "phase",
        nargs="?",
        default="pre_market",
        choices=["pre_market", "intraday", "post_market"],
        help="决策阶段: pre_market(盘前) / intraday(盘中) / post_market(盘后)",
    )
    parser.add_argument("--output", "-o", help="将完整结果保存为JSON文件路径")
    parser.add_argument("--top", type=int, default=5, help="显示交易建议Top N (默认5)")
    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("ETF资金流向盘前/盘中决策模块 v1.0 - 测试入口")
    logger.info("=" * 80)

    engine = ETFFlowDecisionEngine()

    if args.phase == "pre_market":
        result = engine.pre_market_decision()
    elif args.phase == "intraday":
        result = engine.intraday_decision()
    elif args.phase == "post_market":
        result = engine.post_market_review()
    else:
        logger.error(f"未知阶段: {args.phase}")
        sys.exit(1)

    # 输出结果摘要
    logger.info("\n" + "=" * 80)
    logger.info(f"【{args.phase}】决策结果")
    logger.info("=" * 80)
    logger.info(json.dumps(result.get("summary", {}), ensure_ascii=False, indent=2))

    if result.get("recommendations"):
        logger.info(f"\n【交易建议 Top {args.top}】")
        for rec in result["recommendations"][: args.top]:
            logger.info(f"  {rec['code']} {rec['name']}: {rec['action']} ({rec['confidence']:.2f})")
            logger.info(f"    原因: {rec['reason']}")

    if result.get("llm_analysis"):
        logger.info("\n【LLM分析】")
        logger.info(result["llm_analysis"])

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"\n完整结果已保存到: {out_path}")

    logger.info("\n完成。")
