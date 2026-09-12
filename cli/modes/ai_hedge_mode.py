"""AI Hedge Fund — 19位大师级AI分析师联合决策模式"""

import os

from core.context import (
    _AI_HEDGE_IMPORTED,
    ML_PREDICTOR_AVAILABLE,
    get_ai_coordinator,
    get_archive_dir,
    load_portfolio_config,
    logger,
)
from utils.datetime_utils import now_bj


def run_ai_hedge_mode(args):
    """AI Hedge Fund — 19位大师级AI分析师联合决策模式

    v5.9 优化：
    - 先跑ML信号扫描筛选高置信度标的，避免对所有标的无差别做AI深度分析
    - 集成AI协调器记录决策到统一数据库
    - 懒加载优化，首次导入后缓存
    """
    global _AI_HEDGE_IMPORTED, _AI_HEDGE_MODULE
    if not _AI_HEDGE_IMPORTED:
        try:
            from quant_modules.ai_hedge_fund.orchestrator import (
                get_available_analysts,
                print_trading_output,
                run_ai_hedge_fund,
            )

            _AI_HEDGE_MODULE = {
                "run": run_ai_hedge_fund,
                "print": print_trading_output,
                "analysts": get_available_analysts,
            }
            _AI_HEDGE_IMPORTED = True
        except ImportError as e:
            print(f"❌ AI Hedge Fund 模块不可用: {e}")
            print(
                "   请安装依赖: pip install langgraph langchain langchain-openai python-dotenv"
            )
            return

    run_ai_hedge_fund = _AI_HEDGE_MODULE["run"]
    print_trading_output = _AI_HEDGE_MODULE["print"]
    get_available_analysts = _AI_HEDGE_MODULE["analysts"]

    print("=" * 70)
    print("  🤖 AI Hedge Fund — 多分析师联合决策系统")
    print("  19位大师级AI分析师 + 风控 + 组合管理")
    print("=" * 70)

    tickers = []
    if hasattr(args, "ticker") and args.ticker:
        tickers = args.ticker
    else:
        try:
            cfg = load_portfolio_config()
            for asset in cfg.get("assets", []):
                code = asset.get("code", "")
                if (
                    code
                    and code != "CASH"
                    and not code.startswith(("51", "58", "159", "56"))
                ):
                    tickers.append(code)
        except Exception:
            pass
        if not tickers:
            tickers = ["600036", "000001", "300750", "600519", "688981"]

    ml_high_confidence = set()
    if len(tickers) > 5 and not getattr(args, "skip_ml_filter", False):
        print(
            f"\n  🧠 ML预筛选: 先扫描 {len(tickers)} 只标的ML信号，只对高置信度标的做AI深度分析..."
        )
        try:
            if ML_PREDICTOR_AVAILABLE:
                from utils.ml_predictor import MLModelPredictor

                _ml = MLModelPredictor()
                ml_results = _ml.predict_batch(tickers[:15])
                for r in ml_results:
                    code = r.get("code", "")
                    prob = r.get("probability", 0.5)
                    if abs(prob - 0.5) > 0.15:
                        ml_high_confidence.add(code)
                print(
                    f"  ✅ ML预筛选完成: {len(ml_high_confidence)}/{len(tickers)} 只高置信度标的"
                )
                if ml_high_confidence:
                    tickers = list(ml_high_confidence)
                else:
                    print("  ⚠️ 无高置信度标的，取置信度最高的前5只")
                    sorted(
                        r.get("probability", 0.5)
                        for r in ml_results
                        if "probability" in r
                    )
        except Exception as e:
            print(f"  ⚠️ ML预筛选跳过: {e}")

    print(f"\n  分析标的 ({len(tickers)}只): {', '.join(tickers)}")

    if hasattr(args, "analysts") and args.analysts:
        selected = args.analysts
    else:
        selected = None

    available = get_available_analysts()
    print(f"  可用分析师: {len(available)} 位")
    if selected:
        print(f"  已选择: {', '.join(selected)}")

    try:
        coordinator = get_ai_coordinator()
        can_run, msg = coordinator.can_proceed(estimated_tokens=len(tickers) * 8000)
        if not can_run:
            print(f"\n  ⚠️ AI协调器: {msg}")
            print("   已超出每日Token预算，AI Hedge Fund分析中止")
            print("   提示: 可设置环境变量 AI_TOKEN_BUDGET 调整预算上限")
            return
        if msg:
            print(f"\n  ⚡ {msg}")
    except Exception:
        coordinator = None

    print("\n  正在启动 AI Hedge Fund 工作流...")
    print("  (需要 OPENAI_API_KEY / DEEPSEEK_API_KEY 等 LLM API Key)")
    print("-" * 70)

    result = run_ai_hedge_fund(
        tickers=tickers,
        start_date=getattr(args, "start_date", None),
        end_date=getattr(args, "end_date", None),
        selected_analysts=selected,
        show_reasoning=getattr(args, "show_reasoning", False),
        model_name=getattr(args, "model", None),
        model_provider=getattr(args, "provider", None),
    )

    print_trading_output(result)

    if coordinator:
        decisions = result.get("decisions", {})
        for ticker, decision in decisions.items():
            action = decision.get("action", "HOLD")
            reasoning = decision.get("reasoning", "")
            confidence = decision.get("confidence", 0.5)
            try:
                coordinator.record_decision(
                    source="ai_hedge",
                    ticker=ticker,
                    action=action,
                    confidence=confidence,
                    reasoning=str(reasoning)[:2000],
                    model_used=getattr(args, "model", "deepseek"),
                    task_type="deep_research",
                )
            except Exception:
                pass

    try:
        import json

        report = {
            "timestamp": now_bj().isoformat(),
            "mode": "ai_hedge",
            "tickers": tickers,
            "decisions": result.get("decisions", {}),
            "analyst_signals": result.get("analyst_signals", {}),
            "ml_pre_filtered": bool(ml_high_confidence),
        }
        report_dir = get_archive_dir(
            base_dir=os.path.dirname(os.path.abspath(__file__))
        )
        report_path = os.path.join(
            report_dir, f'AI_Hedge_Fund_{now_bj().strftime("%Y%m%d_%H%M%S")}.json'
        )
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n  📄 报告已保存: {report_path}")
    except Exception as e:
        logger.warning(f"报告保存失败: {e}")
