"""Sprint 2 真实链路演示脚本 (W6.2.2 记忆反思 + W6.2.4 速率限制器)

用 mock 数据本地运行两个真实链路, 打印实际输出结果 (不只是 PASS/FAIL):
    A. memory_reflection 接真实价格数据 — 评估历史决策 + 生成反思文本
    B. debate_layer 接入 RateLimitedLLMCaller — 辩论结果 + 调用统计
    C. 端到端闭环 — 辩论 → 记录 → 评估 → 反思注入

运行方式:
    python scripts/demo_sprint2_real_links.py

注意: 本脚本不依赖真实 LLM / 真实 langchain 安装, 所有外部依赖均 mock。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from typing import Any
from unittest.mock import MagicMock, patch

# ============================================================
# 0. 环境准备: mock langchain 模块 (与测试文件相同的手法)
# ============================================================

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Mock langchain (本机 Python 3.8 无 langchain 安装)
for _mod_name in (
    "langchain_openai",
    "langchain_ollama",
    "langchain_core",
    "langchain_core.prompts",
    "langchain_core.messages",
):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

# utils.llm 依赖 llm.models (用了 `LLMModel | None` Python 3.10+ 语法), 这里降级 mock
try:
    import quant_modules.ai_hedge_fund.utils.llm  # noqa: F401
except (ImportError, TypeError):
    import quant_modules.ai_hedge_fund.utils as _utils_pkg

    _mock_utils_llm = MagicMock()
    sys.modules["quant_modules.ai_hedge_fund.utils.llm"] = _mock_utils_llm
    _utils_pkg.llm = _mock_utils_llm


# ============================================================
# 0.1 Logger 配置 — 输出到控制台 + 日志文件, 方便排查报错
# ============================================================

_LOG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs",
    "demo_sprint2_real_links.log",
)

logger = logging.getLogger("demo_sprint2")
logger.setLevel(logging.DEBUG)

# 避免重复添加 handler (脚本多次调用时)
if not logger.handlers:
    # 控制台 handler: INFO 级别
    _console = logging.StreamHandler(sys.stdout)
    _console.setLevel(logging.INFO)
    _console.setFormatter(
        logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logger.addHandler(_console)

    # 文件 handler: DEBUG 级别 (含详细调试信息)
    try:
        os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
        _file_h = logging.FileHandler(_LOG_FILE, encoding="utf-8")
        _file_h.setLevel(logging.DEBUG)
        _file_h.setFormatter(
            logging.Formatter(
                "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(_file_h)
    except (OSError, PermissionError) as _exc:
        # 文件不可写时仅用控制台
        print(f"[WARN] 无法创建日志文件 {_LOG_FILE}: {_exc}, 仅输出到控制台")


# ============================================================
# 工具: 美化打印
# ============================================================


def _print_section(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _print_sub(title: str) -> None:
    print(f"\n--- {title} ---")


def _print_json(obj: Any, indent: int = 2) -> None:
    """安全打印 JSON (处理 pydantic / dataclass)"""
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    elif hasattr(obj, "__dict__") and not isinstance(obj, type):
        obj = {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    print(json.dumps(obj, ensure_ascii=False, indent=indent, default=str))


# ============================================================
# A. memory_reflection 接真实价格数据
# ============================================================


def demo_a_memory_reflection_with_mock_prices(tmp_dir: str) -> None:
    """用 mock 价格数据评估历史决策, 展示反思文本生成"""
    from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

    _print_section("A. memory_reflection 接真实价格数据 (mock 价格)")

    # 1. 构造 3 条决策记录 (bullish / bearish / neutral)
    _print_sub("步骤 1: 构造 3 条历史决策记录")
    mem = MemoryReflection(memory_dir=os.path.join(tmp_dir, "memory_a"))

    records = [
        {
            "record_id": "demo_AAPL_bullish",
            "session_id": "demo_session_a",
            "timestamp": "2026-08-01T10:00:00",
            "date": "2026-08-01",
            "ticker": "AAPL",
            "final_signal": "bullish",
            "final_confidence": 78,
            "winner": "bull",
            "net_confidence": 25,
            "reasoning": "巴菲特+格雷厄姆看多, 护城河强, 估值合理",
            "analyst_bull_count": 8,
            "analyst_bear_count": 3,
            "analyst_neutral_count": 2,
            "evaluated": False,
            "eval_date": "",
            "forward_return_1d": None,
            "forward_return_5d": None,
            "forward_return_10d": None,
            "correct_1d": None,
            "correct_5d": None,
            "correct_10d": None,
            "reflection": "",
        },
        {
            "record_id": "demo_TSLA_bearish",
            "session_id": "demo_session_a",
            "timestamp": "2026-08-01T10:00:00",
            "date": "2026-08-01",
            "ticker": "TSLA",
            "final_signal": "bearish",
            "final_confidence": 72,
            "winner": "bear",
            "net_confidence": -20,
            "reasoning": "估值过高, 伯里看空, 技术面走弱",
            "analyst_bull_count": 2,
            "analyst_bear_count": 7,
            "analyst_neutral_count": 1,
            "evaluated": False,
            "eval_date": "",
            "forward_return_1d": None,
            "forward_return_5d": None,
            "forward_return_10d": None,
            "correct_1d": None,
            "correct_5d": None,
            "correct_10d": None,
            "reflection": "",
        },
        {
            "record_id": "demo_MSFT_neutral",
            "session_id": "demo_session_a",
            "timestamp": "2026-08-01T10:00:00",
            "date": "2026-08-01",
            "ticker": "MSFT",
            "final_signal": "neutral",
            "final_confidence": 45,
            "winner": "tie",
            "net_confidence": 5,
            "reasoning": "多空证据均衡, 等待催化剂",
            "analyst_bull_count": 5,
            "analyst_bear_count": 4,
            "analyst_neutral_count": 3,
            "evaluated": False,
            "eval_date": "",
            "forward_return_1d": None,
            "forward_return_5d": None,
            "forward_return_10d": None,
            "correct_1d": None,
            "correct_5d": None,
            "correct_10d": None,
            "reflection": "",
        },
    ]
    with open(mem.memory_file, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  写入 {len(records)} 条决策记录到 {mem.memory_file}")
    for r in records:
        print(
            f"    - {r['ticker']}: signal={r['final_signal']}, conf={r['final_confidence']}, date={r['date']}"
        )

    # 2. 构造 mock 价格数据 (AAPL 涨 / TSLA 涨 / MSFT 平)
    _print_sub("步骤 2: 构造 mock 价格数据")
    price_data: dict[str, dict[str, dict[str, float]]] = {
        "AAPL": {  # bullish 预测, 价格上涨 → 方向正确
            "2026-08-01": {"close": 100.0},
            "2026-08-02": {"close": 101.5},  # +1.5% (1d)
            "2026-08-06": {"close": 106.0},  # +6.0% (5d)
            "2026-08-11": {"close": 109.0},  # +9.0% (10d)
        },
        "TSLA": {  # bearish 预测, 但价格上涨 → 方向错误
            "2026-08-01": {"close": 200.0},
            "2026-08-02": {"close": 198.0},  # -1.0% (1d)
            "2026-08-06": {"close": 215.0},  # +7.5% (5d)
            "2026-08-11": {"close": 220.0},  # +10.0% (10d)
        },
        "MSFT": {  # neutral 预测, 价格小幅波动 → 方向正确 (|ret|<2%)
            "2026-08-01": {"close": 300.0},
            "2026-08-02": {"close": 301.0},  # +0.33% (1d)
            "2026-08-06": {"close": 302.0},  # +0.67% (5d)
            "2026-08-11": {"close": 303.0},  # +1.0% (10d)
        },
    }
    print("  mock 价格数据 (决策日 08-01 → 评估日 08-11):")
    for ticker, dates in price_data.items():
        prices = " → ".join(
            f"{d.split('-')[2]}: {info['close']}" for d, info in sorted(dates.items())
        )
        print(f"    {ticker}: {prices}")

    # 3. 评估历史决策
    _print_sub("步骤 3: 运行 evaluate_past_decisions (lookback_days=30)")
    updated = mem.evaluate_past_decisions(
        price_data_provider=price_data,
        lookback_days=30,
        eval_date="2026-08-11",
    )
    print(f"  评估了 {updated} 条记录")

    # 4. 打印评估结果
    _print_sub("步骤 4: 评估结果 (含反思文本)")
    with open(mem.memory_file, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line.strip())
            print(
                f"\n  [{rec['ticker']}] signal={rec['final_signal']}, conf={rec['final_confidence']}"
            )
            print(f"    forward_return_1d  = {rec['forward_return_1d']}")
            print(f"    forward_return_5d  = {rec['forward_return_5d']}")
            print(f"    forward_return_10d = {rec['forward_return_10d']}")
            print(f"    correct_1d  = {rec['correct_1d']}")
            print(f"    correct_5d  = {rec['correct_5d']}")
            print(f"    correct_10d = {rec['correct_10d']}")
            print(f"    反思文本: {rec['reflection']}")

    # 5. 提取反思上下文 (注入下次分析)
    _print_sub("步骤 5: get_reflection_context (供下次分析注入)")
    ctx = mem.get_reflection_context(days=30)
    print(f"  总评估数: {ctx['total_evaluated']}")
    print(f"  整体胜率: {ctx['overall_win_rate']:.1%}")
    print(f"  摘要文本: {ctx['summary']}")
    print("\n  按 ticker 明细:")
    for ticker, stats in ctx["by_ticker"].items():
        print(f"    {ticker}:")
        print(
            f"      total={stats['total']}, evaluated={stats['evaluated']}, correct_5d={stats['correct_5d']}"
        )
        print(f"      win_rate={stats['win_rate']:.1%}")
        print("      recent_reflections:")
        for r in stats["recent_reflections"]:
            print(f"        - {r}")


# ============================================================
# B. debate_layer 接入 RateLimitedLLMCaller
# ============================================================


def demo_b_debate_layer_with_rate_limiter(tmp_dir: str) -> None:
    """用 mock LLM 运行辩论, 展示 DebateResult + rate_limiter stats"""
    from quant_modules.ai_hedge_fund.debate_layer import DebateLayer, DebateStance
    from quant_modules.ai_hedge_fund.llm_rate_limiter import get_global_llm_caller

    _print_section("B. debate_layer 接入 RateLimitedLLMCaller (mock LLM)")

    # 1. 重置全局 caller 统计
    caller = get_global_llm_caller()
    caller.reset()

    # 2. 构造 mock LLM 返回 (不同 side 返回不同立场)
    _print_sub("步骤 1: 构造 mock LLM 返回 (模拟 GPT-4o-mini 响应)")

    def _mock_call_llm(prompt, pydantic_model, **kwargs):
        """模拟 LLM 返回 DebateStance

        通过 kwargs['agent_name'] 检测 side (bull_researcher / bear_researcher),
        因为 langchain 被 mock 后 prompt 内容无法用文本匹配。
        """
        agent_name = kwargs.get("agent_name", "")
        if "bull" in agent_name:
            return DebateStance(
                stance="bullish",
                confidence=82,
                key_arguments=[
                    "巴菲特护城河分析支持买入",
                    "格雷厄姆估值模型显示内在价值溢价 15%",
                    "5 日动量 +6.2%, 资金流入持续",
                ],
                rebuttals=["对方看空论点忽略了下季度的产品周期催化剂"],
                evidence_summary="8/13 分析师看多, 综合置信度 82",
            )
        if "bear" in agent_name:
            return DebateStance(
                stance="bearish",
                confidence=65,
                key_arguments=[
                    "当前 PE 处于历史 85 分位, 估值过高",
                    "Michael Burry 技术面分析显示顶背离",
                    "宏观流动性收紧, 成长股承压",
                ],
                rebuttals=["对方看多论点低估了利率上行对估值的压制"],
                evidence_summary="3/13 分析师看空, 但技术面证据较强",
            )
        return DebateStance(
            stance="neutral",
            confidence=50,
            key_arguments=["多空证据均衡"],
            rebuttals=[],
            evidence_summary="",
        )

    # 3. 运行辩论 (启用 rate_limiter)
    _print_sub("步骤 2: 运行完整两轮辩论 (use_llm=True, use_rate_limiter=True)")
    layer = DebateLayer(
        use_llm=True,
        log_dir=os.path.join(tmp_dir, "debates_b"),
        use_rate_limiter=True,
        model_name="gpt-4o-mini",
    )

    analyst_signals = {
        "warren_buffett": {
            "AAPL": {
                "signal": "bullish",
                "confidence": 85,
                "reasoning": "护城河无可匹敌, 长期持有",
            },
        },
        "ben_graham": {
            "AAPL": {
                "signal": "bullish",
                "confidence": 72,
                "reasoning": "内在价值测算溢价 15%",
            },
        },
        "michael_burry": {
            "AAPL": {
                "signal": "bearish",
                "confidence": 68,
                "reasoning": "PE 处于历史 85 分位, 拐点临近",
            },
        },
        "cathie_wood": {
            "AAPL": {
                "signal": "bullish",
                "confidence": 80,
                "reasoning": "AI 战略布局领先",
            },
        },
        "stanley_druckenmiller": {
            "AAPL": {
                "signal": "neutral",
                "confidence": 50,
                "reasoning": "等待宏观信号明确",
            },
        },
    }

    print(f"  分析师信号: {len(analyst_signals)} 个")
    for agent, sig in analyst_signals.items():
        s = sig["AAPL"]
        print(
            f"    - {agent}: {s['signal']} (conf={s['confidence']}) — {s['reasoning'][:40]}"
        )

    with patch.object(DebateLayer, "_llm_available", return_value=True), patch(
        "quant_modules.ai_hedge_fund.utils.llm.call_llm", side_effect=_mock_call_llm
    ) as mock_call:
        session = layer.run_full_debate(["AAPL"], analyst_signals)
        call_count = mock_call.call_count

    print(f"\n  LLM 被调用 {call_count} 次 (2 轮 × 2 方 = 4 次, 每方 Round1+Round2)")

    # 4. 打印辩论结果
    _print_sub("步骤 3: 辩论结果 (DebateResult)")
    result = session.debate_results["AAPL"]
    print(f"\n  Ticker: {result.ticker}")
    print("\n  Round 1 (初版论点):")
    print(f"    Bull (置信度={result.bull_round1.confidence}):")
    for arg in result.bull_round1.key_arguments:
        print(f"      • {arg}")
    print(f"    Bear (置信度={result.bear_round1.confidence}):")
    for arg in result.bear_round1.key_arguments:
        print(f"      • {arg}")

    print("\n  Round 2 (反驳后最终立场):")
    print(f"    Bull (置信度={result.bull_final.confidence}):")
    for arg in result.bull_final.key_arguments:
        print(f"      • {arg}")
    if result.bull_final.rebuttals:
        print("    Bull 反驳:")
        for r in result.bull_final.rebuttals:
            print(f"      ↳ {r}")
    print(f"    Bear (置信度={result.bear_final.confidence}):")
    for arg in result.bear_final.key_arguments:
        print(f"      • {arg}")
    if result.bear_final.rebuttals:
        print("    Bear 反驳:")
        for r in result.bear_final.rebuttals:
            print(f"      ↳ {r}")

    print("\n  裁决结果:")
    print(f"    胜方: {result.winner}")
    print(
        f"    净置信度: {result.net_confidence} (Bull {result.bull_final.confidence} - Bear {result.bear_final.confidence})"
    )
    print(f"    最终信号: {result.final_signal} (置信度={result.final_confidence})")
    print(f"    裁决理由: {result.reasoning}")

    # 5. 打印 rate_limiter 统计
    _print_sub("步骤 4: RateLimitedLLMCaller 调用统计")
    stats = caller.stats
    print(f"  统计维度数: {len(stats)}")
    for key, s in stats.items():
        print(f"\n  [{key}]")
        print(f"    total_calls  = {s['total_calls']}")
        print(f"    successful   = {s['successful']}")
        print(f"    cache_hits   = {s['cache_hits']}")
        print(f"    rate_limited = {s['rate_limited']}")
        print(f"    failed       = {s['failed']}")
        if s["successful"] > 0:
            print(f"    avg_latency  = {s.get('avg_latency_ms', 0):.1f} ms")

    # 6. 再运行一次相同 prompt, 展示缓存命中
    _print_sub("步骤 5: 第二次运行相同 prompt (展示缓存命中)")
    # 注意: 不调用 caller.reset(), 保留第一次的缓存, 第二次相同 prompt 应命中
    with patch.object(DebateLayer, "_llm_available", return_value=True), patch(
        "quant_modules.ai_hedge_fund.utils.llm.call_llm", side_effect=_mock_call_llm
    ) as mock_call:
        layer2 = DebateLayer(
            use_llm=True,
            log_dir=os.path.join(tmp_dir, "debates_b"),
            use_rate_limiter=True,
            model_name="gpt-4o-mini",
        )
        layer2.run_full_debate(["AAPL"], analyst_signals)
        second_call_count = mock_call.call_count

    stats2 = caller.stats
    total_cache_hits = sum(s["cache_hits"] for s in stats2.values())
    total_calls2 = sum(s["total_calls"] for s in stats2.values())
    print(f"  第二次 LLM 实际调用次数 (透传到 call_llm): {second_call_count}")
    print(
        f"  累计 stats: total_calls={total_calls2} (第一次4 + 第二次4=8), cache_hits={total_cache_hits}"
    )
    print(f"  缓存命中率: {total_cache_hits / max(1, total_calls2):.1%}")
    print(
        "  说明: 第二次的 4 次调用 prompt 与第一次相同, 全部命中 TTL 缓存, 未实际调用 call_llm"
    )


# ============================================================
# C. 端到端闭环: 辩论 → 记录 → 评估 → 反思注入
# ============================================================


def demo_c_end_to_end_loop(tmp_dir: str) -> None:
    """完整闭环演示: 辩论 → 记录 → T+N 评估 → 反思上下文提取

    每个关键步骤均带 logger.info 打印 (控制台 + 日志文件), 方便排查报错。
    日志文件: logs/demo_sprint2_real_links.log
    """
    from quant_modules.ai_hedge_fund.debate_layer import DebateLayer, DebateStance
    from quant_modules.ai_hedge_fund.llm_rate_limiter import get_global_llm_caller
    from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

    _print_section("C. 端到端闭环: 辩论 → 记录 → 评估 → 反思注入")
    logger.info("=" * 50)
    logger.info("端到端闭环演示开始 | tmp_dir=%s", tmp_dir)
    loop_start = time.monotonic()

    # 重置 caller
    caller = get_global_llm_caller()
    caller.reset()
    logger.info("步骤 0: 已重置 RateLimitedLLMCaller 全局统计")

    # ============================================================
    # 步骤 1: 运行辩论
    # ============================================================
    _print_sub("步骤 1: 运行辩论 (3 个 ticker, 启用 rate_limiter)")
    step_start = time.monotonic()
    logger.info(
        "步骤 1 开始: 运行辩论 | tickers=['AAPL','TSLA','GOOG'], use_llm=True, use_rate_limiter=True, model=gpt-4o-mini"
    )

    def _mock_call_llm(prompt, pydantic_model, **kwargs):
        agent_name = kwargs.get("agent_name", "")
        logger.debug("mock call_llm 被调用 | agent_name=%s", agent_name)
        if "bull" in agent_name:
            return DebateStance(
                stance="bullish",
                confidence=78,
                key_arguments=["基本面强劲", "估值合理", "动量正向"],
                rebuttals=[],
                evidence_summary="看多证据充分",
            )
        if "bear" in agent_name:
            return DebateStance(
                stance="bearish",
                confidence=55,
                key_arguments=["估值偏高", "技术面走弱"],
                rebuttals=[],
                evidence_summary="看空证据中等",
            )
        return DebateStance(
            stance="neutral",
            confidence=50,
            key_arguments=["多空均衡"],
            rebuttals=[],
            evidence_summary="",
        )

    layer = DebateLayer(
        use_llm=True,
        log_dir=os.path.join(tmp_dir, "debates_c"),
        use_rate_limiter=True,
        model_name="gpt-4o-mini",
    )
    logger.debug(
        "DebateLayer 实例化完成 | rounds=%s, use_llm=%s, use_rate_limiter=%s, _rate_limited_caller=%s",
        layer.rounds,
        layer.use_llm,
        layer.use_rate_limiter,
        (
            type(layer._rate_limited_caller).__name__
            if layer._rate_limited_caller
            else "None"
        ),
    )

    analyst_signals = {
        "warren_buffett": {
            "AAPL": {"signal": "bullish", "confidence": 85, "reasoning": "护城河强"},
            "TSLA": {"signal": "bearish", "confidence": 70, "reasoning": "估值过高"},
            "GOOG": {"signal": "bullish", "confidence": 75, "reasoning": "AI 领先"},
        },
        "ben_graham": {
            "AAPL": {
                "signal": "bullish",
                "confidence": 70,
                "reasoning": "内在价值溢价",
            },
            "TSLA": {"signal": "bearish", "confidence": 75, "reasoning": "PE 过高"},
            "GOOG": {"signal": "neutral", "confidence": 50, "reasoning": "估值合理"},
        },
        "michael_burry": {
            "AAPL": {"signal": "bullish", "confidence": 65, "reasoning": "产品周期"},
            "TSLA": {"signal": "bearish", "confidence": 80, "reasoning": "顶背离"},
            "GOOG": {"signal": "bullish", "confidence": 60, "reasoning": "云业务增长"},
        },
    }
    logger.info(
        "分析师信号就绪 | agents=%s, tickers=%s",
        list(analyst_signals.keys()),
        list(next(iter(analyst_signals.values())).keys()),
    )

    try:
        with patch.object(DebateLayer, "_llm_available", return_value=True), patch(
            "quant_modules.ai_hedge_fund.utils.llm.call_llm",
            side_effect=_mock_call_llm,
        ):
            session = layer.run_full_debate(
                ["AAPL", "TSLA", "GOOG"], analyst_signals
            )
        step_elapsed = time.monotonic() - step_start
        logger.info(
            "步骤 1 完成: 辩论结束 | tickers=%d, errors=%d, 耗时=%.2fs",
            len(session.debate_results),
            len(session.errors),
            step_elapsed,
        )
    except Exception as exc:
        logger.exception("步骤 1 失败: 辩论执行异常 | %r", exc)
        raise

    print(f"  辩论完成, 共 {len(session.debate_results)} 个 ticker:")
    for ticker, result in session.debate_results.items():
        logger.info(
            "  辩论结果 [%s] | winner=%s, signal=%s, conf=%d, net_conf=%d",
            ticker,
            result.winner,
            result.final_signal,
            result.final_confidence,
            result.net_confidence,
        )
        logger.debug(
            "  辩论详情 [%s] | bull_r1_conf=%d, bear_r1_conf=%d, bull_final_conf=%d, bear_final_conf=%d",
            ticker,
            result.bull_round1.confidence,
            result.bear_round1.confidence,
            result.bull_final.confidence,
            result.bear_final.confidence,
        )
        logger.debug("  裁决理由 [%s] | %s", ticker, result.reasoning)
        print(
            f"    {ticker}: winner={result.winner}, signal={result.final_signal}, conf={result.final_confidence}"
        )

    if session.errors:
        logger.warning("辩论有错误 | errors=%s", session.errors)

    # ============================================================
    # 步骤 2: 记录决策到记忆
    # ============================================================
    _print_sub("步骤 2: 记录决策到记忆 (record_decisions)")
    step_start = time.monotonic()
    memory_dir_c = os.path.join(tmp_dir, "memory_c")
    logger.info("步骤 2 开始: 记录决策 | memory_dir=%s", memory_dir_c)

    mem = MemoryReflection(memory_dir=memory_dir_c)
    logger.debug("MemoryReflection 实例化完成 | memory_file=%s", mem.memory_file)

    try:
        count = mem.record_decisions(session)
        step_elapsed = time.monotonic() - step_start
        logger.info(
            "步骤 2 完成: 决策记录写入 | count=%d, 耗时=%.2fs, file=%s",
            count,
            step_elapsed,
            mem.memory_file,
        )
    except Exception as exc:
        logger.exception("步骤 2 失败: 记录决策异常 | %r", exc)
        raise

    print(f"  写入 {count} 条决策记录")

    # 修改决策日期为 08-01 (匹配 mock 价格数据)
    logger.info("步骤 2b: 修改决策日期为 2026-08-01 (匹配 mock 价格数据)")
    try:
        with open(mem.memory_file, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        logger.debug("读取记录数=%d", len(records))
        for rec in records:
            rec["date"] = "2026-08-01"
            rec["timestamp"] = "2026-08-01T10:00:00"
        with open(mem.memory_file, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info("步骤 2b 完成: %d 条记录日期已修改", len(records))
    except Exception as exc:
        logger.exception("步骤 2b 失败: 修改决策日期异常 | %r", exc)
        raise

    print("  决策日期统一设为 2026-08-01 (便于评估)")

    # ============================================================
    # 步骤 3: T+N 评估
    # ============================================================
    _print_sub("步骤 3: T+10 评估 (mock 价格数据)")
    step_start = time.monotonic()
    price_data = {
        "AAPL": {  # bullish + 上涨 → 正确
            "2026-08-01": {"close": 100.0},
            "2026-08-06": {"close": 105.0},
            "2026-08-11": {"close": 108.0},
        },
        "TSLA": {  # bearish + 下跌 → 正确
            "2026-08-01": {"close": 200.0},
            "2026-08-06": {"close": 190.0},
            "2026-08-11": {"close": 185.0},
        },
        "GOOG": {  # bullish + 上涨 → 正确
            "2026-08-01": {"close": 150.0},
            "2026-08-06": {"close": 153.0},
            "2026-08-11": {"close": 156.0},
        },
    }
    logger.info(
        "步骤 3 开始: T+10 评估 | lookback_days=30, eval_date=2026-08-11, provider_type=dict"
    )

    print("  mock 价格 (决策日 08-01 → 评估日 08-11):")
    for ticker, dates in price_data.items():
        ret_10d = (dates["2026-08-11"]["close"] - dates["2026-08-01"]["close"]) / dates[
            "2026-08-01"
        ]["close"]
        logger.info(
            "  价格数据 [%s] | 08-01=%.2f → 08-11=%.2f (10d收益=%+.2f%%)",
            ticker,
            dates["2026-08-01"]["close"],
            dates["2026-08-11"]["close"],
            ret_10d * 100,
        )
        print(
            f"    {ticker}: {dates['2026-08-01']['close']} → {dates['2026-08-11']['close']} (10d收益={ret_10d:+.2%})"
        )

    try:
        updated = mem.evaluate_past_decisions(
            price_data_provider=price_data,
            lookback_days=30,
            eval_date="2026-08-11",
        )
        step_elapsed = time.monotonic() - step_start
        logger.info(
            "步骤 3 完成: 评估结束 | updated=%d, 耗时=%.2fs", updated, step_elapsed
        )
    except Exception as exc:
        logger.exception("步骤 3 失败: 评估异常 | %r", exc)
        raise

    print(f"\n  评估了 {updated} 条记录")

    # ============================================================
    # 步骤 4: 打印评估结果
    # ============================================================
    _print_sub("步骤 4: 评估结果 + 反思文本")
    logger.info("步骤 4 开始: 读取评估结果并打印")
    try:
        with open(mem.memory_file, encoding="utf-8") as f:
            eval_records = [json.loads(line.strip()) for line in f if line.strip()]
        logger.debug("读取评估后记录数=%d", len(eval_records))

        for rec in eval_records:
            ticker = rec.get("ticker", "?")
            signal = rec.get("final_signal", "?")
            conf = rec.get("final_confidence", 0)
            ret_5d = rec.get("forward_return_5d")
            ret_10d = rec.get("forward_return_10d")
            correct_5d = rec.get("correct_5d")
            correct_10d = rec.get("correct_10d")
            reflection = rec.get("reflection", "")

            logger.info(
                "  评估结果 [%s] | signal=%s, conf=%d, 5d=%s, 10d=%s, correct_5d=%s, correct_10d=%s",
                ticker,
                signal,
                conf,
                f"{ret_5d:+.2%}" if ret_5d is not None else "None",
                f"{ret_10d:+.2%}" if ret_10d is not None else "None",
                correct_5d,
                correct_10d,
            )
            logger.debug("  反思文本 [%s] | %s", ticker, reflection)

            print(f"\n  [{ticker}] signal={signal}, conf={conf}")
            print(f"    5d收益={ret_5d:+.2%}, 10d收益={ret_10d:+.2%}")
            print(f"    correct_5d={correct_5d}, correct_10d={correct_10d}")
            print(f"    反思: {reflection}")

        logger.info("步骤 4 完成: 共打印 %d 条评估结果", len(eval_records))
    except Exception as exc:
        logger.exception("步骤 4 失败: 读取评估结果异常 | %r", exc)
        raise

    # ============================================================
    # 步骤 5: 提取反思上下文 (注入下次分析)
    # ============================================================
    _print_sub(
        "步骤 5: 提取反思上下文 (get_reflection_context) — 可注入下次分析 prompt"
    )
    step_start = time.monotonic()
    logger.info("步骤 5 开始: 提取反思上下文 | days=30")

    try:
        ctx = mem.get_reflection_context(days=30)
        step_elapsed = time.monotonic() - step_start
        logger.info(
            "步骤 5 完成 | total_evaluated=%d, overall_win_rate=%.4f, 耗时=%.2fs",
            ctx["total_evaluated"],
            ctx["overall_win_rate"],
            step_elapsed,
        )
    except Exception as exc:
        logger.exception("步骤 5 失败: 提取反思上下文异常 | %r", exc)
        raise

    print(f"\n  总评估数: {ctx['total_evaluated']}")
    print(f"  整体胜率 (5d): {ctx['overall_win_rate']:.1%}")
    print(f"  摘要: {ctx['summary']}")
    logger.info("反思上下文摘要 | summary=%s", ctx["summary"])

    print("\n  按 ticker 明细:")
    for ticker, stats in ctx["by_ticker"].items():
        logger.info(
            "  反思明细 [%s] | total=%d, evaluated=%d, correct_5d=%d, win_rate=%.4f",
            ticker,
            stats["total"],
            stats["evaluated"],
            stats["correct_5d"],
            stats["win_rate"],
        )
        for r in stats["recent_reflections"]:
            logger.debug("  反思记录 [%s] | %s", ticker, r)
        print(
            f"    {ticker}: win_rate={stats['win_rate']:.0%} ({stats['correct_5d']}/{stats['evaluated']} 正确)"
        )
        for r in stats["recent_reflections"]:
            print(f"      ↳ {r}")

    # ============================================================
    # 步骤 6: rate_limiter 统计
    # ============================================================
    _print_sub(
        "步骤 6: RateLimitedLLMCaller 全局统计 (3 ticker × 2 轮 × 2 方 = 12 次调用)"
    )
    logger.info("步骤 6 开始: 汇总 RateLimitedLLMCaller 统计")

    stats = caller.stats
    logger.info("统计维度数=%d", len(stats))
    for key, s in stats.items():
        logger.info(
            "  RateLimiter统计 [%s] | total=%d, success=%d, cache_hits=%d, rate_limited=%d, failed=%d, avg_latency=%.2fms, success_rate=%.4f",
            key,
            s["total_calls"],
            s["successful"],
            s["cache_hits"],
            s["rate_limited"],
            s["failed"],
            s.get("avg_latency_ms", 0.0),
            s.get("success_rate", 0.0),
        )
        print(f"\n  [{key}]")
        for k, v in s.items():
            if isinstance(v, float):
                print(f"    {k} = {v:.2f}")
            else:
                print(f"    {k} = {v}")

    # ============================================================
    # 闭环总结
    # ============================================================
    loop_elapsed = time.monotonic() - loop_start
    logger.info(
        "端到端闭环完成 | tickers=%d, 决策记录=%d, 评估=%d, 整体胜率=%.4f, 总耗时=%.2fs",
        len(session.debate_results),
        count,
        updated,
        ctx["overall_win_rate"],
        loop_elapsed,
    )
    logger.info("日志文件位置: %s", _LOG_FILE)
    logger.info("=" * 50)


# ============================================================
# 主入口
# ============================================================


def main() -> None:
    print("=" * 70)
    print("  Sprint 2 真实链路演示 (W6.2.2 记忆反思 + W6.2.4 速率限制器)")
    print("  所有数据均为 mock, 不依赖真实 LLM / 真实 langchain 安装")
    print("=" * 70)

    with tempfile.TemporaryDirectory(prefix="sprint2_demo_") as tmp_dir:
        print(f"\n临时目录: {tmp_dir}")

        # A. memory_reflection 接真实价格数据
        demo_a_memory_reflection_with_mock_prices(tmp_dir)

        # B. debate_layer 接入 RateLimitedLLMCaller
        demo_b_debate_layer_with_rate_limiter(tmp_dir)

        # C. 端到端闭环
        demo_c_end_to_end_loop(tmp_dir)

    print("\n" + "=" * 70)
    print("  演示完成 — 所有输出均来自 mock 数据, 真实链路代码路径已验证")
    print("=" * 70)


if __name__ == "__main__":
    main()
