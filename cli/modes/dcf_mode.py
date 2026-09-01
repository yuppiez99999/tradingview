"""
DCF 估值模式 — v5.10 新增
基于 financial-services DCF 方法论，对接现有数据层与报告归档
"""

from __future__ import annotations

from typing import Any


def run_dcf_mode(args) -> str | None:
    """
    执行 DCF 估值分析，生成专业 Excel 模型。

    支持参数:
      --ticker / -t         股票代码，如 600519
      --name                公司名称，默认取代码
      --output / -o         输出 Excel 文件路径
      --no-ai               跳过 AI 增强（保留以兼容主入口参数）
    """
    ticker = getattr(args, "ticker", None) or getattr(args, "kronos_code", None)
    if not ticker:
        print(
            '❌ 请提供 --ticker 参数，例如: python "量化策略系统 v5.10.py" --dcf --ticker 600519'
        )
        return None

    company_name = getattr(args, "name", None) or ticker
    output_path = getattr(args, "output", None)

    print("\n📊 DCF 估值分析")
    print("=" * 70)
    print(f"标的: {ticker} ({company_name})")

    try:
        from utils.dcf_model import run_dcf_analysis
    except ImportError as e:
        print(f"❌ DCF 模块加载失败: {e}")
        return None

    # 尝试从现有数据层获取最新价格与 Shares Outstanding（若可用）
    market_data: dict[str, Any] = {}
    try:
        from quant_modules.data_layer import DataConnectorManager

        connector_manager = DataConnectorManager()
        quote = connector_manager.get_quote(ticker)
        if quote:
            market_data["stock_price"] = float(quote.get("price", 0) or 0)
            market_data["shares_outstanding"] = float(quote.get("shares", 0) or 0)
            market_data["net_debt"] = float(quote.get("net_debt", 0) or 0)
    except Exception:
        pass

    # 若数据不足，使用兜底值
    if not market_data.get("stock_price"):
        market_data.setdefault("stock_price", 0.0)
    if not market_data.get("shares_outstanding"):
        market_data.setdefault("shares_outstanding", 0.0)
    if not market_data.get("net_debt"):
        market_data.setdefault("net_debt", 0.0)

    result_path = run_dcf_analysis(
        ticker=ticker,
        company_name=company_name,
        market_data=market_data,
        output_path=output_path,
    )

    print(f"✅ DCF 模型已生成: {result_path}")
    return result_path
