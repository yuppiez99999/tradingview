"""Phase 3: 风险预算计算 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py L1051-L1382

搬移内容:
- phase_risk: 主 phase 方法
- _infer_style_from_code: 基于代码前缀推断持仓风格 (被 phase_hedge 跨 phase 调用)
- _style_beta_proxy: 风格 Beta 代理估算 (被 phase_hedge 跨 phase 调用)
- _get_if_realtime: IF 期货实时价获取 (被 phase_hedge 跨 phase 调用)

跨 phase 调用处理:
daily_workflow.py 保留转发方法, 其他 phase 通过 self._xxx 调用时透明转发到本模块。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import requests

try:
    import certifi
    _SSL_VERIFY = certifi.where()
except ImportError:
    _SSL_VERIFY = True

from workflow.context import WorkflowContext, get_dw_module

logger = logging.getLogger("v75.daily_workflow")

# === 从 daily_workflow 模块获取模块级符号 ===
_dw = get_dw_module()
BASE_DIR: Path = getattr(_dw, "BASE_DIR", Path(__file__).resolve().parent.parent) if _dw else Path(__file__).resolve().parent.parent


def phase_risk(ctx: WorkflowContext) -> dict[str, Any]:
    """风险预算计算 — 2026 年交易计划组合级别

    基于 `2026年交易计划.md` 的资金配置:
    - 总资金 500 万 = 股票/ETF 300 万 (60%) + 对冲 200 万 (40%)
    - 4 阶段建仓: P1 35% / P2 30% / P3 20% / P4 15%
    - 三层风控: 黄色 6% / 橙色 8% / 红色 12%
    - VaR 预算: 95% < 5%, 99% < 8%
    """
    # 懒初始化（支持单独运行该 phase）
    if not hasattr(ctx, "rm"):
        try:
            from risk.risk_manager import RiskManager
            ctx.rm = RiskManager(total_capital=ctx.capital)
        except Exception as e:  # fail-safe: 风控初始化降级
            logger.warning(f"RiskManager 初始化失败，使用模拟模式: {e}")
            ctx.rm = None
    logger.info("=" * 60)
    logger.info("Phase 3: 风险预算计算 (组合级别 — 500万 4阶段)")
    logger.info("=" * 60)

    # 当前组合净值 (建仓前为现金状态)
    current_equity = ctx.capital
    dd_status = ctx.rm.update_drawdown(current_equity)

    # === 从交易计划读取当日资金配置 ===
    plan_phase = ctx.trade_plan.get("phase", {})
    plan_exec = ctx.trade_plan.get("execution_plan", {})
    day_capital = float(plan_phase.get("day_capital", 0))
    morning_total = float(plan_exec.get("morning_total", 0))
    afternoon_total = float(plan_exec.get("afternoon_total", 0))
    grand_total = float(plan_exec.get("grand_total", 0))

    # === 资金配置明细 ===
    stock_capital = ctx.config.STOCK_CAPITAL
    hedge_capital = ctx.config.HEDGE_CAPITAL

    # === 风险预算计算 (简化版, 实盘应从 RiskManager 获取) ===
    # 单日建仓资金占股票组合的比例
    build_ratio = day_capital / stock_capital if stock_capital > 0 else 0

    # 个股止损 (取中风险 -12% 作为组合止损参考)
    portfolio_stop = ctx.config.STOP_LOSS_RULES["科技股"]

    # VaR 预算 (使用配置上限)
    var_95_limit = 0.05
    var_99_limit = 0.08

    logger.info(f"组合净值: {current_equity:,.0f}")
    logger.info(f"回撤状态: {dd_status} (模式: {ctx.rm.mode})")
    logger.info(f"股票组合资金: {stock_capital:,.0f} (60%)")
    logger.info(f"期权对冲资金: {hedge_capital:,.0f} (40%)")
    logger.info(f"当日建仓资金: {day_capital:,.0f} (占股票组合 {build_ratio:.2%})")
    logger.info(f"  上午批次: {morning_total:,.0f}")
    logger.info(f"  下午批次: {afternoon_total:,.0f}")
    logger.info(f"  合计: {grand_total:,.0f}")
    logger.info(f"组合止损: {portfolio_stop:.2%}, VaR95<{var_95_limit:.0%}, VaR99<{var_99_limit:.0%}")

    risk_status = {
        "equity": current_equity,
        "drawdown_status": dd_status,
        "mode": ctx.rm.mode,
        "position_factor": ctx.rm.position_size_factor,
        "stock_capital": stock_capital,
        "hedge_capital": hedge_capital,
        "day_capital": day_capital,
        "morning_total": morning_total,
        "afternoon_total": afternoon_total,
        "grand_total": grand_total,
        "build_ratio": build_ratio,
        "portfolio_stop": portfolio_stop,
        "var_95_limit": var_95_limit,
        "var_99_limit": var_99_limit,
        "yellow_warning": ctx.config.YELLOW_WARNING,
        "orange_warning": ctx.config.ORANGE_WARNING,
        "red_warning": ctx.config.RED_WARNING,
        "full_stop": ctx.config.FULL_STOP,
    }
    ctx.state["phases"]["risk"] = {"status": "PASS", **risk_status}
    ctx.state["risk_status"] = risk_status

    # === 顶级风险管理: 压力测试情景库 ===
    if ctx.stress_test_engine is not None:
        try:
            positions_list = (ctx._get_portfolio_positions_for_stress_test()
                              if hasattr(ctx, "_get_portfolio_positions_for_stress_test") else [])
            if positions_list:
                portfolio_value = sum(float(p.get("amount", 0)) for p in positions_list)
                if portfolio_value > 0:
                    stress_results = ctx.stress_test_engine.run_all_scenarios(
                        positions=positions_list,
                        total_portfolio_value=portfolio_value,
                    )
                    stress_summary = ctx.stress_test_engine.summarize(stress_results)
                    ctx.state["phases"]["risk_stress_test"] = {
                        "n_scenarios": stress_summary.get("n_scenarios", 0),
                        "worst_scenario": stress_summary.get("worst_scenario", ""),
                        "worst_return": stress_summary.get("worst_return", 0.0),
                        "worst_pnl": stress_summary.get("worst_pnl", 0.0),
                        "best_return": stress_summary.get("best_return", 0.0),
                        "avg_return": stress_summary.get("avg_return", 0.0),
                        "n_breaches": stress_summary.get("n_breaches", 0),
                        "breach_scenarios": stress_summary.get("breach_scenarios", []),
                        "avg_var_change": stress_summary.get("avg_var_change", 0.0),
                    }
                    worst = ctx.stress_test_engine.get_worst_scenario(stress_results)
                    if worst:
                        logger.info(
                            "[StressTest] %d 场景: 最严重='%s' return=%.2f%% pnl=¥%.0f, breaches=%d",
                            stress_summary.get("n_scenarios", 0),
                            worst.scenario_name,
                            worst.portfolio_return * 100,
                            worst.portfolio_pnl,
                            stress_summary.get("n_breaches", 0),
                        )
                        if worst.is_breach:
                            logger.warning(
                                "[StressTest] ⚠️ 风险突破: %s 收益 %.2f%% 低于阈值 %.0f%%",
                                worst.scenario_name,
                                worst.portfolio_return * 100,
                                ctx.stress_test_engine.risk_threshold * 100,
                            )
        except Exception as exc:  # fail-safe: 压力测试失败不阻断主流程
            logger.error("[StressTest] 压力测试失败: %s", exc, exc_info=True)

    # === 顶级风险管理: 风险预算约束优化 (自动 TE 再平衡建议) ===
    if ctx.risk_budget_opt is not None and ctx.barra_decomposer is not None:
        try:
            barra_state = ctx.state.get("phases", {}).get("report_barra")
            if barra_state and barra_state.get("active_risk", 0) > 0.05:
                logger.warning(
                    "[RiskBudget] Barra 显示 TE=%.2f%% 超过 5%% 预算, 生成再平衡建议",
                    barra_state["active_risk"] * 100,
                )
                ctx.state["phases"]["risk_budget_rebalance_needed"] = True
                ctx.state["phases"]["risk_budget_target_te"] = 0.05
                ctx.state["phases"]["risk_budget_current_te"] = barra_state["active_risk"]
        except Exception as exc:  # fail-safe: 风险预算建议失败不阻断主流程
            logger.error("[RiskBudget] 再平衡建议生成失败: %s", exc, exc_info=True)

    return risk_status


# === 私有子方法 (被 phase_hedge 跨 phase 调用, daily_workflow.py 保留转发) ===

def _infer_style_from_code(code: str) -> str:
    """基于代码前缀粗略推断持仓风格（建仓计划缺失时的回退）"""
    c = re.sub(r'^(?:sh|sz|bj|SH|SZ|BJ)', '', str(code)).lower()
    if c.startswith("588"):
        return "高端制造"
    if c.startswith("515"):
        return "红利"
    if c.startswith("518"):
        return "避险"
    if c.startswith("688") or c.startswith("300"):
        return "科技"
    if c.startswith("002"):
        return "制造"
    if c == "600276":
        return "医药"
    if c == "600900":
        return "防御"
    if c == "600036":
        return "银行"
    if c == "000425":
        return "制造"
    if c == "601088":
        return "顺周期"
    # 2026-07-09 新增 6 标的
    if c == "300274":
        return "新能源"
    if c == "603019":
        return "科技"
    if c == "600089":
        return "制造"
    if c == "688017":
        return "制造"
    if c == "600219":
        return "资源"
    if c == "600019":
        return "资源"
    return "科技"


def _style_beta_proxy(positions: dict[str, float],
                      prices: dict[str, float]) -> float:
    """风格 Beta 代理：当真实历史收益率失效时，基于持仓风格权重估算组合 Beta

    Args:
        positions: 持仓 {symbol: quantity}
        prices: 当前价格

    Returns:
        估算的组合 Beta
    """
    # 风格 Beta 映射（来源：AGENTS.md 配置风格）
    # 2026-07-09 新增 6 标的: "新能源" + "资源" 风格 Beta 已校准
    style_beta_map = {
        "宽基": 0.95,
        "高端制造": 1.15,
        "科技": 1.20,
        "制造": 1.05,
        "新能源": 1.15,   # 阳光电源: 光伏储能, Beta 略高于制造
        "医药": 0.85,
        "银行": 0.75,
        "防御": 0.60,
        "顺周期": 1.10,
        "避险": -0.10,
        "红利": 0.70,
        "成长": 1.25,
        "资源": 1.10,    # 南山铝业/宝钢股份: 周期性大宗, Beta 与顺周期相当
    }

    # 优先从 500万建仓计划读取 style 和 weight，回退到 v7.6 主计划
    build_plan_path = BASE_DIR.parent / "500万建仓计划_20260706.json"
    style_weights: dict[str, float] = {}
    if build_plan_path.exists():
        try:
            with open(build_plan_path, encoding="utf-8") as _f:
                _plan = json.load(_f)
            _target = _plan.get("target_portfolio", _plan.get("stock_etf_account", {}).get("positions", {}))
            for _code, _info in _target.items():
                if _code in positions and _code in prices:
                    _style = _info.get("style")
                    _weight = float(_info.get("weight", 0.0))
                    if _style and _weight > 0:
                        style_weights[_style] = style_weights.get(_style, 0.0) + _weight
        except Exception as _exc:  # fail-safe: 建仓计划读取失败回退
            logger.warning("读取 500万建仓计划失败，回退代码前缀推断: %s", _exc)

    # 若仍未获取到风格权重，尝试 v7.6 主计划
    if not style_weights:
        master_plan_path = BASE_DIR / "trade_plans" / "auto_trade_plan_500w_2026-2030.json"
        if master_plan_path.exists():
            try:
                with open(master_plan_path, encoding="utf-8") as _f:
                    _plan = json.load(_f)
                _target = _plan.get("stock_etf_account", {}).get("positions", {})
                for _info in _target.values():
                    _code = _info.get("code")
                    if _code and _code in positions and _code in prices:
                        _style = _info.get("style")
                        _weight = float(_info.get("target_weight", 0.0))
                        if _style and _weight > 0:
                            style_weights[_style] = style_weights.get(_style, 0.0) + _weight
            except Exception as _exc:  # fail-safe: 主计划读取失败回退
                logger.warning("读取 v7.6 主计划失败，回退代码前缀推断: %s", _exc)

    # 若仍无法获取风格权重，基于代码前缀推断
    if not style_weights:
        logger.warning("风格权重为空，基于代码前缀推断风格权重")
        for _code in positions:
            if _code in prices:
                _style = _infer_style_from_code(_code)
                style_weights[_style] = style_weights.get(_style, 0.0) + 1.0

    # 若仍无法获取风格权重，使用市场中性默认值
    if not style_weights:
        logger.warning("风格权重仍为空，使用市场中性 Beta=1.00")
        return 1.00

    beta_port = 0.0
    total_weight = sum(style_weights.values())
    if total_weight <= 0:
        return 1.00
    for style, weight in style_weights.items():
        beta_port += (weight / total_weight) * style_beta_map.get(style, 1.0)
    return float(beta_port)


def _get_if_realtime() -> dict:
    """获取 IF 期货实时价（新浪/腾讯 HTTP 回退）"""
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://stock.finance.sina.com.cn/",
    }

    def _first_positive(parts, indices):
        for idx in indices:
            try:
                v = float(parts[idx])
                if v > 0:
                    return v
            except (ValueError, TypeError, IndexError):
                continue
        return None

    sina_candidates = ["IF0", "IF2506", "IF"]
    for sym in sina_candidates:
        try:
            url = f"https://hq.sinajs.cn/list=nf_{sym}"
            resp = session.get(url, headers=headers, timeout=10, verify=_SSL_VERIFY)
            text = resp.text
            m = re.search(r'var hq_str_nf_' + re.escape(sym) + r'="(.+)"', text)
            if not m:
                continue
            parts = m.group(1).split(",")
            if len(parts) < 15:
                continue
            latest = _first_positive(parts, [8, 7, 3, 2])
            if latest is None:
                continue
            return {
                "symbol": sym,
                "source": "sina_http",
                "price": latest,
            }
        except Exception:  # fail-safe: 单源失败尝试下一源
            continue

    try:
        url = "https://qt.gtimg.cn/q=IF"
        resp = session.get(url, headers=headers, timeout=10, verify=_SSL_VERIFY)
        text = resp.text
        m = re.search(r'v_(.+)="(.+)"', text)
        if m:
            parts = m.group(2).split("~")
            if len(parts) >= 5:
                latest = _first_positive(parts, [3, 5])
                if latest:
                    return {
                        "symbol": "IF",
                        "source": "tencent_http",
                        "price": latest,
                    }
    except Exception:  # fail-safe: 腾讯源失败返回空
        pass

    return {}
