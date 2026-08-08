"""AI 建议生成 (B3.2 拆分自 PortfolioAnalyzer)

提供以下纯函数:
  - generate_ai_recommendations: 生成AI决策建议 (DeepSeek 优先, 降级到规则引擎)
  - generate_deepseek_recommendations: 调用 DeepSeek 生成结构化交易决策建议

依赖:
  - call_deepseek_fn: 由调用方传入的 LLM 调用函数 (原 generate_daily_report._call_deepseek)
"""

import json
from typing import Callable, Dict, List, Optional


def generate_ai_recommendations(
    pnl_data: Dict,
    hedge_data: Dict,
    net_pnl: float,
    report_date: str,
    deepseek_model: str,
    call_deepseek_fn: Callable[..., Optional[str]],
) -> List[str]:
    """生成AI决策建议 (DeepSeek 优先, 降级到规则引擎)

    Args:
        pnl_data: calculate_pnl 返回的盈亏明细
        hedge_data: analyze_hedge_position 返回的对冲头寸分析
        net_pnl: 组合净盈亏
        report_date: 报告日期 (YYYY-MM-DD)
        deepseek_model: DeepSeek 模型名称
        call_deepseek_fn: LLM 调用函数 (system_prompt, user_prompt, temperature, max_tokens)

    Returns:
        建议列表 (4-6 条)
    """
    # ── 1) 优先调用 DeepSeek 生成智能建议 ──
    deepseek_recs = generate_deepseek_recommendations(
        pnl_data, hedge_data, net_pnl, report_date, deepseek_model, call_deepseek_fn
    )
    if deepseek_recs:
        return deepseek_recs

    # ── 2) 降级: 规则引擎 (兜底) ──
    print("  ⚠️ DeepSeek 不可用, 降级到规则引擎生成建议")
    recommendations = []

    # 基于盈亏情况
    if net_pnl > 0:
        recommendations.append("组合整体盈利，建议维持当前Beta敞口，继续执行建仓计划")
    else:
        recommendations.append("组合出现亏损，建议审视高风险标的，评估是否需要调整仓位")

    # 基于对冲有效性
    effectiveness = hedge_data["summary"].get("hedge_effectiveness", 0)
    if effectiveness > 70:
        recommendations.append("对冲有效性良好，Beta敞口控制在目标区间")
    else:
        recommendations.append("建议增加期货对冲合约数量，提升Beta对冲效率")

    # 基于止损状态
    stop_loss_triggered = pnl_data["details"] and any(
        d["status"] == "STOP_LOSS_TRIGGERED" for d in pnl_data["details"]
    )
    if stop_loss_triggered:
        recommendations.append("存在触发止损标的，建议次日开盘前评估是否执行止损")

    # 基于风格轮动
    tech_performance = [
        d
        for d in pnl_data["details"]
        if d["style"] in ["科技", "高端制造", "成长"] and d["daily_pnl_pct"] is not None
    ]
    if tech_performance and sum(t["daily_pnl_pct"] for t in tech_performance) > 0:
        recommendations.append("科技/成长风格表现优异，建议维持该板块权重配置")

    recommendations.append("建议次日盘中监控VIX和指数波动，动态调整期货对冲仓位")

    return recommendations


def generate_deepseek_recommendations(
    pnl_data: Dict,
    hedge_data: Dict,
    net_pnl: float,
    report_date: str,
    deepseek_model: str,
    call_deepseek_fn: Callable[..., Optional[str]],
) -> Optional[List[str]]:
    """调用 DeepSeek 生成结构化交易决策建议

    生成包含具体操作关键词的建议, 以便 apply_llm_decisions_to_plan.py 识别:
        - "IF空头N手" / "增加期货" / "提升Beta对冲效率" → 期货对冲升级
        - "510050 Put" / "510300 Put" / "Put保护" / "买入Put" → Put 尾部保护
        - "建仓顺序" / "优先建仓" / "调整建仓" → 建仓顺序调整

    Args:
        pnl_data: calculate_pnl 返回的盈亏明细
        hedge_data: analyze_hedge_position 返回的对冲头寸分析
        net_pnl: 组合净盈亏
        report_date: 报告日期 (YYYY-MM-DD)
        deepseek_model: DeepSeek 模型名称
        call_deepseek_fn: LLM 调用函数

    Returns:
        建议列表, DeepSeek 不可用返回 None
    """
    # 准备组合摘要数据供 DeepSeek 分析
    details = pnl_data.get("details", []) or []
    # 取前 10 个标的的盈亏摘要, 避免提示词过长
    top_holdings = []
    for d in sorted(details, key=lambda x: abs(x.get("daily_pnl_pct", 0) or 0), reverse=True)[:5]:
        top_holdings.append({
            "code": d.get("code", ""),
                "name": d.get("name", ""),
                "style": d.get("style", ""),
                "daily_pnl_pct": d.get("daily_pnl_pct"),
                "status": d.get("status", ""),

            }
        )

    hedge_summary = hedge_data.get("summary", {})
    hedge_details = hedge_data.get("details", [])
    portfolio_beta = hedge_data.get("portfolio_beta", 1.3)
    # 取前 5 个对冲工具
    top_hedges = []
    for h in sorted(hedge_details, key=lambda x: abs(x.get("beta_reduced", 0)), reverse=True)[:3]:
        top_hedges.append({
            "code": h.get("code", ""),
                "name": h.get("name", ""),
                "contracts": h.get("contracts", 0),
                "beta_reduced": h.get("beta_reduced", 0),
                "pnl": h.get("pnl", 0),
            }
        )

    portfolio_summary = {
        "report_date": report_date,
        "net_pnl": round(net_pnl, 2),
        "total_positions": len(details),
        "hedge_effectiveness": hedge_summary.get("hedge_effectiveness", 0),
        "portfolio_beta": portfolio_beta,
        "beta_exposure": round(portfolio_beta - sum(h.get("beta_reduced", 0) for h in hedge_details), 3),
        "top_holdings": top_holdings,
        "top_hedges": top_hedges,
        "stop_loss_count": sum(1 for d in details if d.get("status") == "STOP_LOSS_TRIGGERED"),
    }

    system_prompt = (
        "你是 Bridgewater 级别的对冲基金经理, 负责500万A股量化组合的盘后决策。\n"
        "组合结构: 现货400万 + 对冲100万 (IF/IC/IM/IH空头 + 可选Put保护)。\n"
        "硬约束: 单标的≤10%, 单板块≤25%, VaR95≤1.5%, 现金≥5%, 止损-8%, 止盈+20%。\n\n"
        "【重要】不要输出任何分析过程、背景介绍、数据解读或开场白, 只输出建议行本身。\n\n"
        "每条建议必须包含具体操作指令, 使用以下关键词之一以便系统自动识别:\n"
        "  - 期货对冲: 使用 'IF空头N手' 或 '增加期货' 或 '提升Beta对冲效率'\n"
        "  - Put保护: 使用 '510050 Put' 或 '510300 Put' 或 'Put保护' 或 '买入Put'\n"
        "  - 建仓顺序: 使用 '建仓顺序' 或 '优先建仓' 或 '调整建仓'\n"
        "  - 止损调整: 使用 '止损' 或 '移动止损'\n"
        "  - 仓位调整: 使用 '减持' 或 '加仓' 或 '仓位调整'\n"
        "  - 板块调整: 使用 '增加XX板块' 或 '降低XX板块' 或 '板块权重'\n"
        "输出格式: 每行一条建议, 不编号, 不带前缀符号, 直接输出建议文本。\n"
        "建议必须基于数据, 量化, 可执行, 避免空泛表述。\n\n"
        "示例输出 (参考格式):\n"
        "IF空头增加2手至5手，当前Beta敞口0.42超目标0.30，需提升Beta对冲效率\n"
        "中国神华触发止损预警（-7.8%），建议明早若低开超1%立即减持50%\n"
        "优先建仓中际旭创至目标权重5.0%，当前仅3.2%，科技板块资金持续流入\n"
        "增加消费板块权重至15%，降低科技板块至20%，板块轮动信号明确"
    )

    # v2.0: 可读摘要文本替代 JSON dump (节省 token + 提高 LLM 理解)
    pnl_dir = "盈利" if net_pnl > 0 else "亏损"
    holding_lines = "\n".join(
        f"  {h['code']} {h['name']} | 日盈亏:{h['daily_pnl_pct']} | 状态:{h['status']} | 风格:{h['style']}"
        for h in top_holdings
    )
    hedge_lines = "\n".join(
        f"  {h['code']} {h['name']} | 合约:{h['contracts']}手 | 降Beta:{h['beta_reduced']} | 盈亏:{h['pnl']}"
        for h in top_hedges
    ) if top_hedges else "  无活跃对冲"

    user_prompt = (
        f"日期: {report_date} | 组合净{pnl_dir}: {net_pnl:.2f}\n"
        f"组合Beta: {portfolio_beta} → 对冲后敞口: {portfolio_summary['beta_exposure']} | 对冲有效性: {hedge_summary.get('hedge_effectiveness', 0)}%\n"
        f"止损触发: {portfolio_summary['stop_loss_count']}只\n"
        f"---\n"
        f"Top5 持仓:\n{holding_lines}\n"
        f"---\n"
        f"Top3 对冲:\n{hedge_lines}\n"
        f"---\n"
        f"请基于以上数据, 输出 4-6 条结构化交易决策建议, 每行一条:"
    )

    print(f"  🟢 调用 DeepSeek ({deepseek_model}) 生成AI决策建议...")
    result = call_deepseek_fn(system_prompt, user_prompt, temperature=0.2, max_tokens=1200)
    if not result:
        return None

    # 解析 DeepSeek 输出: 按行拆分, 过滤空行和编号
    lines = []
    for line in result.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 去除可能的编号前缀: "1. " / "1) " / "- " / "* "
        for prefix_pattern in [
            "1.",
            "2.",
            "3.",
            "4.",
            "5.",
            "6.",
            "7.",
            "8.",
            "9.",
            "10.",
            "1)",
            "2)",
            "3)",
            "4)",
            "5)",
            "6)",
            "7)",
            "8)",
            "9)",
            "10)",
        ]:
            if line.startswith(prefix_pattern + " "):
                line = line[len(prefix_pattern) + 1 :].strip()
                break
        if line.startswith(("-", "*", "•", "·")):
            line = line[1:].strip()
        # 去除 markdown 加粗
        line = line.replace("**", "").replace("__", "")
        if line and len(line) > 5:  # 过滤过短的行
            lines.append(line)

    # 过滤描述性/分析性行 (非操作建议):
    # DeepSeek 常先输出 "分析输入数据" "日期:" "净盈亏:" 等描述, 再给建议
    # 关键词分两类: 严格匹配 (任何位置出现都过滤) + 行开头匹配 (只在行开头才过滤, 防误杀)
    descriptive_strict = [
        "分析输入数据", "输入数据", "数据分析", "数据解读",
        "日期：", "日期:", "净盈亏：", "净盈亏:",
        "持仓数：", "持仓数:", "对冲有效性：", "对冲有效性:",
        "市场环境", "行情分析", "盘面分析", "盘面回顾",
        "以下是", "基于以上", "综合分析", "综上所述",
    ]
    descriptive_line_start = [
        "组合Beta", "组合 beta", "portfolio beta",
        "组合净值", "组合收益", "组合波动",
        "大盘", "指数", "板块",
    ]
    def _is_descriptive(line: str) -> bool:
        low = line.lower().strip()
        # 严格匹配: 任何位置出现都算描述行
        if any(kw.lower() in low for kw in descriptive_strict):
            return True
        # 行开头匹配: 只在行开头 (或去掉编号前缀后开头) 才算描述行
        for kw in descriptive_line_start:
            if low.startswith(kw.lower()):
                return True
        return False

    filtered = [l for l in lines if not _is_descriptive(l)]
    if not filtered:
        filtered = lines  # 若全部被过滤, 回退到原始结果

    # 操作建议关键词 (apply_llm_decisions_to_plan.py 解析依赖的关键词)
    action_keywords = [
        "IF空头", "增加期货", "提升Beta对冲效率",
        "510050 Put", "510300 Put", "Put保护", "买入Put",
        "建仓顺序", "优先建仓", "调整建仓",
        "止损", "移动止损",
        "减持", "加仓", "仓位调整",
        "板块权重", "增加", "降低",
    ]
    def _has_action_keyword(line: str) -> bool:
        low = line.lower()
        return any(kw.lower() in low for kw in action_keywords)

    # 智能截断: 优先保留含操作关键词的建议, 不足 6 条再补其他
    action_lines = [l for l in filtered if _has_action_keyword(l)]
    non_action_lines = [l for l in filtered if not _has_action_keyword(l)]
    result_lines = (action_lines + non_action_lines)[:6]

    if not result_lines:
        print(f"  ⚠️ DeepSeek 返回内容无法解析为建议列表: {result[:200]}")
        return None

    print(f"  ✅ DeepSeek 生成 {len(lines)} 条, 过滤描述性 {len(lines)-len(filtered)} 条, 保留 {len(result_lines)} 条操作建议 (含关键词 {len(action_lines)} 条)")
    return result_lines
