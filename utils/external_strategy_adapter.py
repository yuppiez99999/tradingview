# -*- coding: utf-8 -*-
"""外部策略适配器 — daily_stock_analysis 策略库集成

核心功能:
    加载 daily_stock_analysis 的 15+ 种 A股策略 YAML 定义,
    通过 LLM 执行策略分析, 输出标准化信号接入 signal_fusion.

策略清单:
    - chan_theory (缠论): 笔/线段/中枢/背驰
    - dragon_head (龙头策略): 板块轮动龙头识别
    - emotion_cycle (情绪周期): 市场情绪阶段判断
    - event_driven (事件驱动): 政策/业绩催化剂
    - bull_trend (牛市趋势): 均线多头排列
    - volume_breakout (放量突破): 量价配合
    - bottom_volume (底部放量): 筑底信号
    - chan_theory 等 15 种

设计原则:
    1. YAML驱动: 策略定义与代码分离, 易于扩展
    2. LLM执行: 策略是提示词模板, 由豆包/GLM-5/DeepSeek执行
    3. 标准输出: 返回 {direction, confidence, score, reasoning}
    4. 优雅降级: LLM不可用时返回中性信号

用法:
    from utils.external_strategy_adapter import ExternalStrategyAdapter

    adapter = ExternalStrategyAdapter()
    signal = adapter.analyze("600519.SH", strategy="chan_theory")
    signals = adapter.analyze_all("600519.SH")  # 所有策略

作者: 28 系统 PM
日期: 2026-08-01
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml

logger = logging.getLogger("external_strategy_adapter")

# ============================================================
# 路径配置
# ============================================================

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STRATEGY_DIR = _PROJECT_ROOT / "config" / "strategies" / "external"

# ============================================================
# LLM 客户端 (复用 ai_report_agent 的加载逻辑)
# ============================================================

_LLM_AVAILABLE = False
_chat_fn = None

_LLM_CLIENT_PATH = _PROJECT_ROOT.parent / "15_每日工作流"
if _LLM_CLIENT_PATH.exists():
    if str(_LLM_CLIENT_PATH) not in sys.path:
        sys.path.insert(0, str(_LLM_CLIENT_PATH))
    try:
        import llm_client
        _chat_fn = llm_client.chat
        _LLM_AVAILABLE = True
        logger.info("ExternalStrategyAdapter: llm_client.py 已加载")
    except Exception as e:
        logger.warning(f"ExternalStrategyAdapter: llm_client 加载失败 ({e})")


# ============================================================
# 适配器主类
# ============================================================


class ExternalStrategyAdapter:
    """外部策略适配器.

    加载 YAML 策略定义, 通过 LLM 执行分析, 返回标准化信号.

    Attributes:
        strategies: 已加载的策略字典 {name: strategy_def}
        llm_available: LLM 客户端是否可用

    Usage:
        >>> adapter = ExternalStrategyAdapter()
        >>> signal = adapter.analyze("600519.SH", strategy="chan_theory")
        >>> print(signal["direction"], signal["confidence"])
    """

    def __init__(self, strategy_dir: Optional[Path] = None) -> None:
        self._strategy_dir = strategy_dir or _STRATEGY_DIR
        self._strategies: Dict[str, Dict] = {}
        self._load_strategies()

    def _load_strategies(self) -> None:
        """加载所有策略 YAML 文件."""
        if not self._strategy_dir.exists():
            logger.warning(f"策略目录不存在: {self._strategy_dir}")
            return

        yaml_files = list(self._strategy_dir.glob("*.yaml"))
        for yaml_file in yaml_files:
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and "name" in data:
                    name = data["name"]
                    self._strategies[name] = data
                    logger.debug(f"加载策略: {name} ({data.get('display_name', '')})")
            except Exception as e:
                logger.warning(f"加载策略失败 {yaml_file.name}: {e}")

        logger.info(f"外部策略加载完成: {len(self._strategies)} 个策略")

    # ============================================================
    # 公开接口
    # ============================================================

    @property
    def available_strategies(self) -> List[str]:
        """可用策略名称列表."""
        return sorted(self._strategies.keys())

    @property
    def llm_available(self) -> bool:
        """LLM 客户端是否可用."""
        return _LLM_AVAILABLE

    def analyze(self, symbol: str, strategy: str = "chan_theory",
                market_data: Optional[Dict] = None) -> Dict[str, Any]:
        """执行单个策略分析.

        Args:
            symbol: 股票代码 (如 "600519.SH")
            strategy: 策略名称 (如 "chan_theory")
            market_data: 可选的市场数据上下文

        Returns:
            标准化信号字典:
            {
                "symbol": "600519.SH",
                "strategy": "chan_theory",
                "direction": "bullish/bearish/neutral",
                "confidence": 0.0-1.0,
                "score": -100 to 100,
                "reasoning": "分析理由",
                "stop_loss": "止损位",
                "target": "目标位",
            }
        """
        if strategy not in self._strategies:
            logger.warning(f"策略不存在: {strategy} (可用: {self.available_strategies})")
            return self._neutral_signal(symbol, strategy, "策略不存在")

        strat_def = self._strategies[strategy]
        instructions = strat_def.get("instructions", "")

        if not _LLM_AVAILABLE or not instructions:
            return self._neutral_signal(symbol, strategy, "LLM不可用或策略无指令")

        # 构造 LLM 提示词
        prompt = self._build_prompt(symbol, strat_def, market_data)

        # 调用 LLM
        try:
            llm_response = _chat_fn(prompt, temperature=0.3, max_tokens=1500)
            return self._parse_llm_response(symbol, strategy, llm_response)
        except Exception as e:
            logger.error(f"LLM 调用失败 ({strategy}/{symbol}): {e}")
            return self._neutral_signal(symbol, strategy, f"LLM异常: {e}")

    def analyze_all(self, symbol: str, market_data: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """执行所有策略分析.

        Args:
            symbol: 股票代码
            market_data: 市场数据上下文

        Returns:
            信号列表 (每个策略一个信号)
        """
        signals = []
        for strategy_name in self._strategies:
            signal = self.analyze(symbol, strategy_name, market_data)
            signals.append(signal)
        logger.info(f"全策略分析完成: {symbol} → {len(signals)} 个信号")
        return signals

    def get_consensus(self, symbol: str, market_data: Optional[Dict] = None) -> Dict[str, Any]:
        """获取所有策略的共识信号.

        Args:
            symbol: 股票代码
            market_data: 市场数据

        Returns:
            共识信号: 多数策略同意的方向 + 平均置信度
        """
        signals = self.analyze_all(symbol, market_data)
        if not signals:
            return self._neutral_signal(symbol, "consensus", "无信号")

        directions = [s["direction"] for s in signals]
        confidences = [s["confidence"] for s in signals if s["confidence"] > 0]
        scores = [s["score"] for s in signals if s["score"] != 0]

        # 多数投票
        bull_count = directions.count("bullish")
        bear_count = directions.count("bearish")

        if bull_count > bear_count:
            consensus_dir = "bullish"
        elif bear_count > bull_count:
            consensus_dir = "bearish"
        else:
            consensus_dir = "neutral"

        avg_conf = sum(confidences) / len(confidences) if confidences else 0
        avg_score = sum(scores) / len(scores) if scores else 0

        return {
            "symbol": symbol,
            "strategy": "consensus",
            "direction": consensus_dir,
            "confidence": round(avg_conf, 3),
            "score": round(avg_score, 1),
            "bull_count": bull_count,
            "bear_count": bear_count,
            "total_strategies": len(signals),
            "reasoning": f"{bull_count}看多/{bear_count}看空/{len(signals)-bull_count-bear_count}中性",
        }

    # ============================================================
    # 内部方法
    # ============================================================

    def _build_prompt(self, symbol: str, strat_def: Dict,
                      market_data: Optional[Dict]) -> str:
        """构造 LLM 提示词."""
        display_name = strat_def.get("display_name", strat_def.get("name", ""))
        instructions = strat_def.get("instructions", "")

        data_context = ""
        if market_data:
            data_context = f"\n## 市场数据\n```json\n{market_data}\n```"

        prompt = f"""你是一位精通{display_name}的A股分析师。

## 任务
分析股票 {symbol} 的{display_name}信号。

## 策略指南
{instructions}
{data_context}

## 输出格式 (严格JSON)
```json
{{
  "direction": "bullish|bearish|neutral",
  "confidence": 0.0-1.0,
  "score": -100到100,
  "reasoning": "一句话分析理由",
  "stop_loss": "止损价位或说明",
  "target": "目标价位或说明"
}}
```

只输出JSON,不要其他文字。"""
        return prompt

    def _parse_llm_response(self, symbol: str, strategy: str,
                            response: str) -> Dict[str, Any]:
        """解析 LLM 响应为标准化信号."""
        import json
        import re

        # 尝试提取 JSON
        json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return {
                    "symbol": symbol,
                    "strategy": strategy,
                    "direction": data.get("direction", "neutral"),
                    "confidence": float(data.get("confidence", 0.5)),
                    "score": float(data.get("score", 0)),
                    "reasoning": data.get("reasoning", ""),
                    "stop_loss": data.get("stop_loss", ""),
                    "target": data.get("target", ""),
                }
            except (json.JSONDecodeError, ValueError):
                pass

        # 解析失败,返回中性信号
        return self._neutral_signal(symbol, strategy, "LLM响应解析失败")

    def _neutral_signal(self, symbol: str, strategy: str,
                        reason: str = "") -> Dict[str, Any]:
        """生成中性信号."""
        return {
            "symbol": symbol,
            "strategy": strategy,
            "direction": "neutral",
            "confidence": 0.0,
            "score": 0,
            "reasoning": reason,
            "stop_loss": "",
            "target": "",
        }

    def get_status(self) -> Dict[str, Any]:
        """获取适配器状态."""
        return {
            "strategy_count": len(self._strategies),
            "strategies": self.available_strategies,
            "llm_available": _LLM_AVAILABLE,
            "strategy_dir": str(self._strategy_dir),
        }


# ============================================================
# 便捷函数
# ============================================================

_default_adapter: Optional[ExternalStrategyAdapter] = None


def get_adapter() -> ExternalStrategyAdapter:
    """获取默认适配器单例."""
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = ExternalStrategyAdapter()
    return _default_adapter


def analyze(symbol: str, strategy: str = "chan_theory") -> Dict[str, Any]:
    """便捷函数: 执行策略分析."""
    return get_adapter().analyze(symbol, strategy)


def analyze_all(symbol: str) -> List[Dict[str, Any]]:
    """便捷函数: 执行所有策略."""
    return get_adapter().analyze_all(symbol)


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="外部策略适配器 (daily_stock_analysis 集成)")
    parser.add_argument("--stock", default="600519.SH", help="股票代码")
    parser.add_argument("--strategy", default="chan_theory", help="策略名称")
    parser.add_argument("--all", action="store_true", help="执行所有策略")
    parser.add_argument("--list", action="store_true", help="列出可用策略")
    parser.add_argument("--status", action="store_true", help="显示适配器状态")
    args = parser.parse_args()

    adapter = ExternalStrategyAdapter()

    if args.status:
        print("外部策略适配器状态:")
        for k, v in adapter.get_status().items():
            print(f"  {k}: {v}")
        sys.exit(0)

    if args.list:
        print(f"可用策略 ({len(adapter.available_strategies)} 个):")
        for name in adapter.available_strategies:
            strat = adapter._strategies.get(name, {})
            print(f"  - {name}: {strat.get('display_name', '')} - {strat.get('description', '')[:50]}")
        sys.exit(0)

    if args.all:
        signals = adapter.analyze_all(args.stock)
        print(f"\n{args.stock} 全策略分析 ({len(signals)} 个):")
        for s in signals:
            print(f"  [{s['strategy']}] {s['direction']} (conf={s['confidence']:.2f}, score={s['score']}) - {s['reasoning'][:60]}")
        consensus = adapter.get_consensus(args.stock)
        print(f"\n共识: {consensus['direction']} (conf={consensus['confidence']:.2f}) - {consensus['reasoning']}")
    else:
        signal = adapter.analyze(args.stock, args.strategy)
        print(f"\n{args.stock} - {args.strategy}:")
        print(f"  方向: {signal['direction']}")
        print(f"  置信度: {signal['confidence']}")
        print(f"  评分: {signal['score']}")
        print(f"  理由: {signal['reasoning']}")
        if signal['stop_loss']:
            print(f"  止损: {signal['stop_loss']}")
        if signal['target']:
            print(f"  目标: {signal['target']}")
