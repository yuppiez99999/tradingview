"""决策记忆反思模块 (Wave 6 W6.2.3, TradingAgents 风格)

借鉴 TradingAgents 的决策记忆机制: 记录历史决策与实际收益对比,
生成反思注入下次分析, 形成"决策 → 执行 → 反馈 → 改进"闭环。

核心能力:
    1. 记录每次辩论/决策的完整上下文 (ticker / signal / confidence / debate verdict)
    2. 在 T+N 日后回溯实际收益, 对比预测方向是否正确
    3. 按 ticker / agent / signal_type 聚合历史胜率
    4. 生成结构化反思文本, 注入下次分析的 prompt 或 metadata

存储: reports/ai_hedge_fund/memory/reflections.jsonl (按行追加, 便于增量写入)
查询: 按 ticker / date_range / signal 方向过滤

与现有系统关系:
    - 辩论层 (debate_layer.py) 输出 DebateSession → 记忆层记录决策
    - T+N 日后由外部触发器 (daily_workflow 或 cron) 调用 evaluate_past_decisions
    - 下次 run_ai_hedge_fund 时通过 get_reflection_context 提取近期反思注入 state
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("ai_hedge_fund.memory")

# 存储目录
_MEMORY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "reports", "ai_hedge_fund", "memory",
)
_MEMORY_FILE = "reflections.jsonl"


# ============================================================
# 数据模型
# ============================================================


@dataclass
class DecisionRecord:
    """单次决策记录 (写入 JSONL 的一行)"""
    record_id: str               # 唯一 ID (session_id + ticker)
    session_id: str              # 辩论会话 ID
    timestamp: str               # 决策时间 ISO
    date: str                    # 决策日期 YYYY-MM-DD
    ticker: str
    # 决策内容
    final_signal: str            # bullish / bearish / neutral
    final_confidence: int        # 0-100
    winner: str                  # bull / bear / tie
    net_confidence: int          # bull_conf - bear_conf
    reasoning: str = ""          # 裁决理由
    # 分析师信号快照 (聚合统计)
    analyst_bull_count: int = 0
    analyst_bear_count: int = 0
    analyst_neutral_count: int = 0
    # 评估字段 (T+N 日后填充)
    evaluated: bool = False
    eval_date: str = ""          # 评估日期
    forward_return_1d: Optional[float] = None
    forward_return_5d: Optional[float] = None
    forward_return_10d: Optional[float] = None
    # 预测正确性 (方向匹配)
    correct_1d: Optional[bool] = None
    correct_5d: Optional[bool] = None
    correct_10d: Optional[bool] = None
    # 反思文本
    reflection: str = ""


# ============================================================
# 记忆反思管理器
# ============================================================


class MemoryReflection:
    """决策记忆反思管理器 (Wave 6 W6.2.3)

    用法:
        # 1. 记录决策 (辩论结束后)
        mem = MemoryReflection()
        mem.record_decisions(session)

        # 2. T+N 日后评估
        mem.evaluate_past_decisions(price_data_provider, lookback_days=10)

        # 3. 下次分析前提取反思
        reflections = mem.get_reflection_context(tickers=['600036'], days=30)
    """

    def __init__(self, memory_dir: str | None = None, memory_file: str | None = None):
        self.memory_dir = memory_dir or _MEMORY_DIR
        self.memory_file = memory_file or os.path.join(self.memory_dir, _MEMORY_FILE)
        os.makedirs(self.memory_dir, exist_ok=True)

    # ------------------------------------------------------------
    # 1. 记录决策
    # ------------------------------------------------------------

    def record_decisions(self, session: Any) -> int:
        """从 DebateSession 提取决策记录写入 JSONL

        Args:
            session: DebateSession 对象 (debate_layer.py)

        Returns:
            写入的记录数
        """
        # 兼容 DebateSession dataclass 或 dict
        if hasattr(session, "to_dict"):
            session_dict = session.to_dict()
        elif isinstance(session, dict):
            session_dict = session
        else:
            logger.warning("record_decisions: 无法识别的 session 类型")
            return 0

        session_id = session_dict.get("session_id", datetime.now().strftime("%Y%m%d_%H%M%S"))
        timestamp = session_dict.get("timestamp", datetime.now().isoformat())
        date_str = timestamp[:10] if timestamp else datetime.now().strftime("%Y-%m-%d")
        debate_results = session_dict.get("debate_results", {})
        analyst_snapshot = session_dict.get("analyst_signals_snapshot", {})

        count = 0
        records: List[str] = []

        for ticker, result in debate_results.items():
            # 聚合该 ticker 的分析师信号统计
            bull_count = 0
            bear_count = 0
            neutral_count = 0
            for agent_id, sigs in analyst_snapshot.items():
                if not isinstance(sigs, dict):
                    continue
                sig = sigs.get(ticker, {})
                if isinstance(sig, dict):
                    s = sig.get("signal", "neutral")
                    if s == "bullish":
                        bull_count += 1
                    elif s == "bearish":
                        bear_count += 1
                    else:
                        neutral_count += 1

            record = DecisionRecord(
                record_id=f"{session_id}_{ticker}",
                session_id=session_id,
                timestamp=timestamp,
                date=date_str,
                ticker=ticker,
                final_signal=result.get("final_signal", "neutral"),
                final_confidence=int(result.get("final_confidence", 0)),
                winner=result.get("winner", "tie"),
                net_confidence=int(result.get("net_confidence", 0)),
                reasoning=(result.get("reasoning") or "")[:300],
                analyst_bull_count=bull_count,
                analyst_bear_count=bear_count,
                analyst_neutral_count=neutral_count,
            )
            records.append(json.dumps(asdict(record), ensure_ascii=False))
            count += 1

        # 追加写入 JSONL
        if records:
            with open(self.memory_file, "a", encoding="utf-8") as f:
                for line in records:
                    f.write(line + "\n")
            logger.info("记录 %d 条决策到 %s", count, self.memory_file)

        return count

    # ------------------------------------------------------------
    # 2. 评估历史决策 (T+N 日回溯)
    # ------------------------------------------------------------

    def evaluate_past_decisions(
        self,
        price_data_provider: Any = None,
        lookback_days: int = 10,
        eval_date: str | None = None,
    ) -> int:
        """回溯评估未评估的决策记录

        Args:
            price_data_provider: 可调用对象, 接受 (ticker, date) 返回 {close: float, ...}
                                 如果为 None 则仅标记 evaluated=True 但不填收益
            lookback_days: 最多回溯多少天前的决策
            eval_date: 评估基准日期 (None=今天)

        Returns:
            评估的记录数
        """
        if not os.path.exists(self.memory_file):
            return 0

        eval_date_str = eval_date or datetime.now().strftime("%Y-%m-%d")
        cutoff_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        # 读取所有记录
        records: List[Dict[str, Any]] = []
        with open(self.memory_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        updated = 0
        for rec in records:
            if rec.get("evaluated"):
                continue
            if rec.get("date", "") < cutoff_date:
                continue  # 超出回溯窗口

            ticker = rec.get("ticker", "")
            decision_date = rec.get("date", "")

            # 计算各窗口 forward return
            if price_data_provider is not None:
                try:
                    base_price = self._get_close_price(price_data_provider, ticker, decision_date)
                    if base_price and base_price > 0:
                        for window in (1, 5, 10):
                            ret_field = f"forward_return_{window}d"
                            corr_field = f"correct_{window}d"
                            target_date = (
                                datetime.strptime(decision_date, "%Y-%m-%d") + timedelta(days=window)
                            ).strftime("%Y-%m-%d")
                            future_price = self._get_close_price(price_data_provider, ticker, target_date)
                            if future_price and future_price > 0:
                                ret = (future_price - base_price) / base_price
                                rec[ret_field] = round(ret, 6)
                                # 方向正确性: bullish → ret>0 正确, bearish → ret<0 正确
                                signal = rec.get("final_signal", "neutral")
                                if signal == "bullish":
                                    rec[corr_field] = ret > 0
                                elif signal == "bearish":
                                    rec[corr_field] = ret < 0
                                else:
                                    rec[corr_field] = abs(ret) < 0.02
                except Exception as exc:
                    logger.warning("评估 %s/%s 失败: %r", ticker, decision_date, exc)

            rec["evaluated"] = True
            rec["eval_date"] = eval_date_str

            # 生成反思文本
            rec["reflection"] = self._generate_reflection_text(rec)
            updated += 1

        # 重写文件
        if updated > 0:
            with open(self.memory_file, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            logger.info("评估了 %d 条历史决策", updated)

        return updated

    # ------------------------------------------------------------
    # 3. 提取反思上下文 (注入下次分析)
    # ------------------------------------------------------------

    def get_reflection_context(
        self,
        tickers: List[str] | None = None,
        days: int = 30,
    ) -> Dict[str, Any]:
        """提取近期反思上下文, 供下次分析的 prompt 或 metadata 使用

        Args:
            tickers: 过滤指定股票 (None=全部)
            days: 最近多少天的记录

        Returns:
            {
                "summary": "近期胜率/反思摘要文本",
                "by_ticker": {ticker: {"win_rate": float, "recent_reflections": [str]}},
                "overall_win_rate": float,
                "total_evaluated": int,
            }
        """
        if not os.path.exists(self.memory_file):
            return {"summary": "", "by_ticker": {}, "overall_win_rate": 0.0, "total_evaluated": 0}

        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        records: List[Dict[str, Any]] = []
        with open(self.memory_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("date", "") >= cutoff:
                        if tickers is None or rec.get("ticker") in tickers:
                            records.append(rec)
                except json.JSONDecodeError:
                    continue

        if not records:
            return {"summary": "", "by_ticker": {}, "overall_win_rate": 0.0, "total_evaluated": 0}

        # 按 ticker 聚合
        by_ticker: Dict[str, Dict[str, Any]] = {}
        total_correct = 0
        total_evaluated = 0

        for rec in records:
            t = rec.get("ticker", "")
            if t not in by_ticker:
                by_ticker[t] = {
                    "total": 0,
                    "evaluated": 0,
                    "correct_5d": 0,
                    "recent_reflections": [],
                }
            by_ticker[t]["total"] += 1
            if rec.get("evaluated"):
                by_ticker[t]["evaluated"] += 1
                total_evaluated += 1
                # 用 5 日正确性作为主指标 (兼顾时效和稳定性)
                correct = rec.get("correct_5d")
                if correct is True:
                    by_ticker[t]["correct_5d"] += 1
                    total_correct += 1
                reflection = rec.get("reflection", "")
                if reflection:
                    by_ticker[t]["recent_reflections"].append(reflection)

        # 计算胜率
        for t, stats in by_ticker.items():
            eval_count = max(1, stats["evaluated"])
            stats["win_rate"] = round(stats["correct_5d"] / eval_count, 4)
            stats["recent_reflections"] = stats["recent_reflections"][-3:]  # 最近 3 条

        overall_win_rate = round(total_correct / max(1, total_evaluated), 4)

        # 生成摘要文本
        summary_parts = []
        for t, stats in by_ticker.items():
            if stats["evaluated"] > 0:
                summary_parts.append(
                    f"{t}: {stats['correct_5d']}/{stats['evaluated']} 正确 (胜率 {stats['win_rate']:.0%})"
                )
        summary = "; ".join(summary_parts) if summary_parts else "暂无评估数据"

        return {
            "summary": summary,
            "by_ticker": by_ticker,
            "overall_win_rate": overall_win_rate,
            "total_evaluated": total_evaluated,
        }

    # ------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------

    @staticmethod
    def _get_close_price(provider: Any, ticker: str, date_str: str) -> Optional[float]:
        """从 price_data_provider 获取收盘价

        provider 可以是:
            - callable(ticker, date) -> dict with 'close'
            - dict[ticker][date] -> dict with 'close'
        """
        try:
            if callable(provider):
                data = provider(ticker, date_str)
            elif isinstance(provider, dict):
                data = provider.get(ticker, {}).get(date_str, {})
            else:
                return None
            if isinstance(data, dict):
                close = data.get("close") or data.get("close_price")
                return float(close) if close is not None else None
        except Exception:
            return None
        return None

    @staticmethod
    def _generate_reflection_text(rec: Dict[str, Any]) -> str:
        """根据评估结果生成反思文本"""
        ticker = rec.get("ticker", "")
        signal = rec.get("final_signal", "neutral")
        conf = rec.get("final_confidence", 0)
        ret_5d = rec.get("forward_return_5d")
        correct_5d = rec.get("correct_5d")

        parts = [f"{ticker} 预测={signal}(conf={conf})"]

        if ret_5d is not None:
            parts.append(f"5日收益={ret_5d:+.2%}")
        if correct_5d is not None:
            parts.append("方向正确" if correct_5d else "方向错误")

        # 反思建议
        if correct_5d is False:
            if signal == "bullish":
                parts.append("反思: 看多判断失误, 可能低估了空头证据, 下次应提高 Bear 论点权重")
            elif signal == "bearish":
                parts.append("反思: 看空判断失误, 可能忽略了多头信号, 下次应提高 Bull 论点权重")
            else:
                parts.append("反思: 中性判断方向偏差, 建议增强信号强度区分")
        elif correct_5d is True:
            parts.append("反思: 方向正确, 当前辩论机制有效")

        return " | ".join(parts)


# ============================================================
# 价格数据适配器 (对接 MarketDataProvider / reports/shadow)
# ============================================================


def make_market_price_provider(
    provider: Any = None,
    period: str = "1y",
    field: str = "close",
) -> Callable[[str, str], Optional[Dict[str, float]]]:
    """构造 price_data_provider callable, 对接 MarketDataProvider

    把 MarketDataProvider.get_historical_data(symbol, period) 返回的 DataFrame
    包装成 MemoryReflection.evaluate_past_decisions 需要的格式:
        callable(ticker, date_str) -> {"close": float}

    Args:
        provider: MarketDataProvider 实例 (None 则自动创建)
        period: 历史数据周期 ("1y" / "6m" / "1m" 等)
        field: 收盘价字段名 (默认 "close")

    Returns:
        callable(ticker, date_str) -> {"close": float} 或 None

    用法:
        provider_fn = make_market_price_provider()
        mem = MemoryReflection()
        mem.evaluate_past_decisions(price_data_provider=provider_fn, lookback_days=10)
    """
    if provider is None:
        try:
            from utils.data_provider import MarketDataProvider
            provider = MarketDataProvider()
        except Exception as exc:
            logger.warning("无法初始化 MarketDataProvider: %r", exc)
            def _empty_provider(ticker: str, date_str: str) -> None:
                return None
            return _empty_provider

    # 内部缓存: {symbol: {date_str: close_price}} 避免重复拉取
    _price_cache: Dict[str, Dict[str, float]] = {}

    def _provider_fn(ticker: str, date_str: str) -> Optional[Dict[str, float]]:
        """从 MarketDataProvider 获取指定 ticker 在 date_str 的收盘价"""
        # 检查缓存
        if ticker in _price_cache:
            close = _price_cache[ticker].get(date_str)
            if close is not None:
                return {"close": close}

        # 拉取历史数据
        try:
            df = provider.get_historical_data(ticker, period=period)
            if df is None or df.empty:
                return None

            # 缓存所有日期的收盘价
            if ticker not in _price_cache:
                _price_cache[ticker] = {}
            for idx, row in df.iterrows():
                # 提取日期字符串 (兼容 datetime/date/str index)
                if hasattr(idx, "strftime"):
                    d = idx.strftime("%Y-%m-%d")
                else:
                    d = str(idx)[:10]
                try:
                    price = float(row[field])
                    _price_cache[ticker][d] = price
                except (ValueError, TypeError, KeyError):
                    continue

            # 返回目标日期
            close = _price_cache[ticker].get(date_str)
            if close is not None:
                return {"close": close}

            # 精确匹配失败: 取最近的交易日 (不超过 ±3 天)
            target_dt = datetime.strptime(date_str, "%Y-%m-%d")
            best_date = None
            best_diff = 999
            for d_str, _ in _price_cache[ticker].items():
                try:
                    d_dt = datetime.strptime(d_str, "%Y-%m-%d")
                    diff = abs((d_dt - target_dt).days)
                    if diff < best_diff and diff <= 3:
                        best_diff = diff
                        best_date = d_str
                except ValueError:
                    continue

            if best_date is not None:
                return {"close": _price_cache[ticker][best_date]}

        except Exception as exc:
            logger.debug("获取 %s/%s 价格失败: %r", ticker, date_str, exc)

        return None

    return _provider_fn


def make_shadow_returns_provider(
    jsonl_path: Optional[str] = None,
) -> Callable[[str, str], Optional[Dict[str, float]]]:
    """构造基于 reports/shadow/daily_returns.jsonl 的组合收益 provider

    注意: daily_returns.jsonl 存储的是组合层面日度收益 (非个股),
    此 provider 仅用于评估组合级别决策方向正确性。

    Args:
        jsonl_path: daily_returns.jsonl 路径 (None 则自动定位)

    Returns:
        callable(ticker, date_str) -> {"close": float}
        其中 close 是组合累计净值 (从 daily_return 反推)
    """
    if jsonl_path is None:
        # memory_reflection.py 在 quant_modules/ai_hedge_fund/ 下, 上溯 3 层到项目根
        base = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        jsonl_path = os.path.join(base, "reports", "shadow", "daily_returns.jsonl")

    # 加载并构建累计净值序列
    daily_returns: Dict[str, float] = {}
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    d = rec.get("date", "")
                    r = rec.get("daily_return")
                    if d and r is not None:
                        daily_returns[d] = float(r)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        logger.warning("shadow returns 文件不存在: %s", jsonl_path)
        def _empty(ticker: str, date_str: str) -> None:
            return None
        return _empty

    # 构建累计净值 (从最早日期开始, 初始净值=1.0)
    sorted_dates = sorted(daily_returns.keys())
    nav_by_date: Dict[str, float] = {}
    nav = 1.0
    for d in sorted_dates:
        nav *= (1.0 + daily_returns[d])
        nav_by_date[d] = nav

    def _provider_fn(ticker: str, date_str: str) -> Optional[Dict[str, float]]:
        """返回组合在 date_str 的累计净值 (作为 'close')"""
        # 精确匹配
        if date_str in nav_by_date:
            return {"close": nav_by_date[date_str]}
        # 最近交易日 (±3 天)
        try:
            target_dt = datetime.strptime(date_str, "%Y-%m-%d")
            best_date = None
            best_diff = 999
            for d_str in nav_by_date:
                d_dt = datetime.strptime(d_str, "%Y-%m-%d")
                diff = abs((d_dt - target_dt).days)
                if diff < best_diff and diff <= 3:
                    best_diff = diff
                    best_date = d_str
            if best_date:
                return {"close": nav_by_date[best_date]}
        except ValueError:
            pass
        return None

    return _provider_fn
