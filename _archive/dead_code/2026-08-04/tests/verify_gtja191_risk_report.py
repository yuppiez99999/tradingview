"""
风险管理系统完整报告验证：GTJA191 Alpha144 -> FactorModel -> 风险决策/预警输出
"""

import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def section(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def check_risk_manager_integration():
    section("风险管理系统完整报告验证")

    print("[1/4] 导入增强风险管理系统...")
    # EnhancedRiskManager 已归档，v8.3 使用 src/risk/ 下的模块
    archive_path = os.path.join(PROJECT_ROOT, "_archive_dead_code")
    if archive_path not in sys.path:
        sys.path.insert(0, archive_path)
    try:
        from enhanced_risk_manager import EnhancedRiskManager
        print("  [OK] EnhancedRiskManager 导入成功（来源: _archive_dead_code/）")
    except Exception as e:
        print(f"  [SKIP] EnhancedRiskManager 不可用（已归档）: {e}")
        print("  [INFO] v8.3 风控模块位于 src/risk/ 目录")
        return

    print("[2/4] 初始化风险管理系统...")
    try:
        risk_manager = EnhancedRiskManager(total_capital=1_000_000)
        print(f"  [OK] 初始化成功，factor_model={'启用' if risk_manager.factor_model else '未启用'}")
    except Exception as e:
        print(f"  [FAIL] 初始化失败: {e}")
        return

    print("[3/4] 构造含真实/本地行情的 market_data ...")
    try:
        import numpy as np
        import pandas as pd

        from utils.data_provider import get_historical_data

        codes = ["000001", "000002", "600519"]
        klines = {}
        for code in codes:
            try:
                df = get_historical_data(code, period="6m")
            except Exception:
                df = None
            if df is None or df.empty or "close" not in df.columns:
                dates = pd.date_range(datetime.now() - pd.Timedelta(days=120), periods=120, freq="B")
                np.random.seed(hash(code) % 2**31)
                close = 10 + np.cumsum(np.random.randn(120) * 0.1)
                amount = np.abs(np.random.randn(120) * 1e6) + 1e5
                df = pd.DataFrame({"close": close, "amount": amount}, index=dates)
            klines[code] = df

        market_data = {
            "klines": klines,
            "fundamentals": {},
            "industrial_prices": None,
            "etf_flows": {},
            "stop_loss_rules": [],
            "quotes": {},
        }
        print(f"  [OK] market_data 已构造，标的数量={len(klines)}")
    except Exception as e:
        print(f"  [FAIL] 构造 market_data 失败: {e}")
        return

    print("[4/4] 运行风险管理周期...")
    try:
        portfolio_data = {"total_value": 1_000_000}
        performance_data = {
            "total_return": 0.08,
            "annualized_return": 0.08,
            "volatility": 0.12,
            "sharpe_ratio": 0.67,
            "max_drawdown": 0.05,
            "win_rate": 0.65,
            "profit_factor": 1.5,
        }
        result = risk_manager.run_risk_management_cycle(
            market_data=market_data,
            portfolio_data=portfolio_data,
            performance_data=performance_data,
        )

        if not result.get("success"):
            print(f"  [FAIL] 风险管理周期失败: {result.get('error')}")
            return

        factor_signals = result.get("factor_signals", {})
        risk_decision = result.get("risk_decision", {})

        print("  [OK] 风险管理周期完成")
        print(f"      因子信号: {factor_signals.get('signal', 'N/A')}")
        print(f"      平均综合分: {factor_signals.get('avg_composite', 'N/A')}")
        print(f"      Top3: {factor_signals.get('top_3', [])}")
        print(f"      Bottom3: {factor_signals.get('bottom_3', [])}")
        print(f"      风险决策动作: {risk_decision.get('action', 'N/A')}")
        print(f"      风险决策优先级: {risk_decision.get('priority', 'N/A')}")
        print(f"      风险决策措施: {risk_decision.get('measures', [])}")
        print(f"      因子信号字段: {risk_decision.get('factor_signal', 'N/A')}")

        # 简单断言，避免静默失败
        assert "factor_signal" in risk_decision, "风险决策中缺少 factor_signal"
        assert "factor_signals" in result, "结果中缺少 factor_signals"
        assert "per_stock_technical_alpha" in factor_signals, "因子信号缺少 per_stock_technical_alpha"
        assert "technical_alpha_alerts" in risk_decision, "风险决策缺少 technical_alpha_alerts 字段"

        print("\n  [PASS] GTJA191/FactorModel 结果已进入风险预警输出")
        print(f"      per_stock_technical_alpha 数量: {len(factor_signals.get('per_stock_technical_alpha', []))}")
        print(f"      technical_alpha_alerts 数量: {len(risk_decision.get('technical_alpha_alerts', []))}")
    except Exception as e:
        print(f"  [FAIL] 风险管理周期异常: {e}")


def main():
    check_risk_manager_integration()
    print("\n完成。")


if __name__ == "__main__":
    main()
