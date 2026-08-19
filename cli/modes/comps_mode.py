"""
Comps 可比公司分析模式 — v5.10 新增
基于 financial-services 可比分析方法论
"""

from __future__ import annotations

from typing import Any, Optional


def run_comps_mode(args) -> Optional[str]:
    """
    执行可比公司分析，生成 Excel。

    当前支持两种数据来源:
      1. 通过 --sector 指定行业，自动加载示例可比公司数据（演示用）
      2. 未来可对接现有数据层自动拉取行业 peers
    """
    sector = getattr(args, "sector", None) or "Technology"
    output_path = getattr(args, "output", None)

    print("\n📈 可比公司分析")
    print("=" * 70)
    print(f"行业: {sector}")

    try:
        from utils.comps_analyzer import run_comps_analysis
    except ImportError as e:
        print(f"❌ Comps 模块加载失败: {e}")
        return None

    # 最小示例数据（演示用，实际应从数据层/配置文件获取）
    companies: list[dict[str, Any]] = [
        {
            "name": "Company A",
            "ticker": "A",
            "revenue": 12000,
            "revenue_growth": 0.12,
            "gross_margin": 0.68,
            "ebitda": 4800,
            "ebitda_margin": 0.40,
            "market_cap": 60000,
            "net_debt": 6000,
            "net_income": 2400,
            "ev": 66000,
        },
        {
            "name": "Company B",
            "ticker": "B",
            "revenue": 18000,
            "revenue_growth": 0.10,
            "gross_margin": 0.62,
            "ebitda": 7200,
            "ebitda_margin": 0.40,
            "market_cap": 90000,
            "net_debt": 9000,
            "net_income": 3600,
            "ev": 99000,
        },
        {
            "name": "Company C",
            "ticker": "C",
            "revenue": 9000,
            "revenue_growth": 0.15,
            "gross_margin": 0.72,
            "ebitda": 3600,
            "ebitda_margin": 0.40,
            "market_cap": 45000,
            "net_debt": 4500,
            "net_income": 1800,
            "ev": 49500,
        },
    ]

    try:
        result_path = run_comps_analysis(
            sector=sector,
            companies=companies,
            output_path=output_path,
        )
        print(f"✅ 可比分析已生成: {result_path}")
        return result_path
    except Exception as e:
        print(f"❌ 可比分析失败: {e}")
        return None
