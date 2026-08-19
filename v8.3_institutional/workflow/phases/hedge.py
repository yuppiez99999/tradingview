"""Phase 4: 三联对冲评估 + 自动执行 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py L1147-L1621 (phase_hedge) + L896-L955 (_get_edb_futures_data /
_get_futures_scanner_summary) + L1077-L1145 (_compute_beta_hedge_order)

搬移内容:
- phase_hedge: 主 phase 方法 (475 行)
- _get_edb_futures_data: EDB 期货/商品数据获取 (带当日缓存)
- _get_futures_scanner_summary: 期货期权扫描器汇总
- _compute_beta_hedge_order: 基于 BetaHedger 的降级对冲指令计算
- _execute_sim_hedge_orders: **新增** — 模拟盘对冲订单路由 (按 test_phase_hedge_sim_branch.py 规约实现)

跨 phase 调用处理:
- _style_beta_proxy / _get_if_realtime 已拆至 workflow/phases/risk.py, 本模块通过 import 复用
- daily_workflow.py 保留 _get_edb_futures_data / _get_futures_scanner_summary / _compute_beta_hedge_order
  的转发方法 (其他 phase 若调用保持透明)
"""
from __future__ import annotations

import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from workflow.context import WorkflowContext, get_dw_module

logger = logging.getLogger("v75.daily_workflow")

# === 从 daily_workflow 模块获取模块级符号 ===
_dw = get_dw_module()
BASE_DIR: Path = getattr(_dw, "BASE_DIR", Path(__file__).resolve().parent.parent) if _dw else Path(__file__).resolve().parent.parent


# ============================================================
# 私有辅助方法 (从 daily_workflow.py 搬移, 零行为变更)
# ============================================================


def _get_edb_futures_data(ctx: WorkflowContext, names: Optional[list[str]] = None) -> dict[str, dict[str, Any]]:
    """获取 EDB 期货/商品数据 (带当日缓存)

    同一天内多次调用只请求一次 API，后续从缓存读取。

    Args:
        names: 品种名称列表，默认读取 AI 算力核心 6 品种

    Returns:
        {name: edb_result} 字典
    """
    EDB_READY = bool(getattr(_dw, "EDB_READY", False)) if _dw else False
    if not EDB_READY:
        return {}

    today = ctx.trade_date
    # 缓存存储在 wf 实例上 (跨 phase 共享, 与拆分前一致)
    wf = ctx._wf
    if getattr(wf, "_edb_cache_date", "") == today and getattr(wf, "_edb_cache", None):
        return wf._edb_cache

    if names is None:
        names = ["锡", "铜", "铝", "银", "碳酸锂", "多晶硅"]

    try:
        EDBFuturesData = getattr(_dw, "EDBFuturesData", None) if _dw else None
        if EDBFuturesData is None:
            return {}
        results = EDBFuturesData.fetch_all()
        wf._edb_cache = {name: results.get(name, {}) for name in names if name in results}
        wf._edb_cache_date = today
        logger.info("EDB 期货数据获取完成 (已缓存): %d 个品种", len(wf._edb_cache))
        return wf._edb_cache
    except Exception:
        logger.error("EDB 期货数据获取失败", exc_info=True)
        return {}


def _get_futures_scanner_summary() -> dict[str, dict[str, Any]]:
    """运行期货期权扫描器，并转换为与 edb_summary 兼容的结构

    Returns:
        {name: {latest, latest_date, ret20, score, opportunity, reasons}} 字典
    """
    SCANNER_READY = bool(getattr(_dw, "SCANNER_READY", False)) if _dw else False
    if not SCANNER_READY:
        return {}
    try:
        FuturesOptionsScanner = getattr(_dw, "FuturesOptionsScanner", None) if _dw else None
        if FuturesOptionsScanner is None:
            return {}
        scanner = FuturesOptionsScanner()
        result = scanner.run()
        summary: dict[str, dict[str, Any]] = {}
        for item in result.get("all", []):
            name = item.get("name")
            if not name:
                continue
            summary[name] = {
                "latest": item.get("latest"),
                "latest_date": item.get("latest_date"),
                "ret20": item.get("ret20"),
                "score": item.get("score"),
                "opportunity": item.get("opportunity"),
                "reasons": item.get("reasons"),
                "scanner_category": item.get("category"),
            }
        return summary
    except Exception:
        logger.error("期货期权扫描器执行失败", exc_info=True)
        return {}


def _compute_beta_hedge_order(portfolio_beta: float, portfolio_value: float, degraded: bool = False) -> dict[str, object]:
    """基于 BetaHedger 计算对冲指令（降级路径）

    Args:
        portfolio_beta: 组合 Beta
        portfolio_value: 组合市值
        degraded: 是否为降级模式（降低触发阈值 + 兜底保护）
    """
    try:
        from hedging.beta_hedger import BetaHedger

        # 复用 risk.py 的 _get_if_realtime (跨 phase 调用)
        from workflow.phases.risk import _get_if_realtime
        futures_config = {
            "IF": {"multiplier": 300, "beta": 1.0, "price": 3800.0},
            "IC": {"multiplier": 200, "beta": 1.2, "price": 5500.0},
            "IM": {"multiplier": 200, "beta": 1.1, "price": 5800.0},
        }
        if_realtime = _get_if_realtime()
        if if_realtime and if_realtime.get("price"):
            futures_config["IF"]["price"] = float(if_realtime["price"])
            logger.info(f"注入 IF 实时价: {if_realtime['price']} (来源: {if_realtime.get('source', 'unknown')})")

        beta_trigger = 0.35 if degraded else 0.7
        # 2026-07-09 方案C: beta_target 0.3→0.25, 提升对冲比率至 75% (目标: 回撤<15%)
        beta_target = 0.25
        beta_hedger = BetaHedger(futures_config=futures_config, beta_trigger=beta_trigger, beta_target=beta_target)
        order = beta_hedger.compute_hedge(portfolio_beta, portfolio_value)

        # 降级兜底：如果 BetaHedger 仍返回 NO_HEDGE，强制生成最小对冲指令
        if degraded and order.get("action") == "NO_HEDGE":
            fut = futures_config.get("IF", futures_config["IF"])
            live_price = float(fut.get("price", 3800.0))
            notional_per_contract = float(fut.get("multiplier", 300)) * live_price
            n_contracts = max(1, int(round((portfolio_beta - beta_target) * portfolio_value / notional_per_contract)))
            if n_contracts <= 0:
                n_contracts = 1
            commission_rate = 0.000023
            slippage_rate = 0.0001
            margin_rate = 0.12
            estimated_cost = n_contracts * notional_per_contract * (commission_rate + slippage_rate)
            estimated_margin = n_contracts * notional_per_contract * margin_rate
            order = {
                "action": "SHORT_FUTURES",
                "instrument": "IF",
                "contracts": n_contracts,
                "direction": "SELL",
                "multiplier": int(fut.get("multiplier", 300)),
                "futures_price": live_price,
                "futures_beta": float(fut.get("beta", 1.0)),
                "notional": float(n_contracts * notional_per_contract),
                "estimated_cost": float(estimated_cost),
                "cost_ratio": float(estimated_cost / portfolio_value) if portfolio_value > 0 else 0.0,
                "current_beta": float(portfolio_beta),
                "target_beta": float(beta_target),
                "beta_reduced": float(portfolio_beta - beta_target),
                "reason": "降级兜底强制对冲",
                "cost_breakdown": {
                    "commission_rate": commission_rate,
                    "slippage_rate": slippage_rate,
                    "margin_rate": margin_rate,
                    "estimated_commission": float(n_contracts * notional_per_contract * commission_rate),
                    "estimated_slippage": float(n_contracts * notional_per_contract * slippage_rate),
                    "estimated_margin": float(estimated_margin),
                    "cost_note": "仅含佣金+滑点估算，不含保证金利息/冲击成本",
                },
            }
            logger.warning(f"降级兜底: Beta {portfolio_beta:.3f} 仍触发 NO_HEDGE，强制 {n_contracts} 手 IF 空头")
        return order
    except Exception as exc:
        logger.error("风格 Beta 代理计算对冲指令失败: %s", exc)
        return {"action": "ERROR", "reason": str(exc)}


# ============================================================
# _execute_sim_hedge_orders: 模拟盘对冲订单路由 (新增, 符合 test_phase_hedge_sim_branch.py 规约)
# ============================================================


def _execute_sim_hedge_orders(sim_engine: Any, mock_prices: dict[str, float], orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """模拟盘模式: 将对冲订单按 action 路由到 sim_engine 的对应 broker 接口。

    路由规则 (与 test_phase_hedge_sim_branch.py 规约对齐):
        - SHORT_FUTURES         → sim_engine.execute_futures_orders
        - PUT_SPREAD / BUY_PUT  → sim_engine.execute_options_orders (按 budget_allocation 拆分)
        - SAFE_HAVEN_ALLOC      → sim_engine.execute_stock_orders
        - DOWNGRADE_TO_PUT_SPREAD → 仅记录跳过 (status=DOWNGRADED)
        - 未知 action            → SKIP_UNKNOWN_ACTION
        - SHORT_FUTURES contracts=0 / 期货价格缺失 → SKIP_NO_PRICE_OR_QTY
        - 执行异常               → FAILED + error 字段

    Args:
        sim_engine: SimExecutionEngine 实例 (提供 execute_futures/options/stock_orders)
        mock_prices: 价格字典 (用于 SAFE_HAVEN_ALLOC 估算 gold_qty)
        orders: 对冲订单列表 (来自 HedgeCoordinator.coordinate())

    Returns:
        成交记录列表, 每条含 type/action/instrument/side/contracts/price/status/fill_record
    """
    if not orders:
        return []

    executed: list[dict[str, Any]] = []

    for order in orders:
        hedge_type = order.get("hedge_type", "UNKNOWN")
        action = order.get("action", "")

        # --- 降级: 仅记录跳过 ---
        if action == "DOWNGRADE_TO_PUT_SPREAD":
            executed.append({
                "type": hedge_type,
                "action": action,
                "reason": order.get("reason", "成本超限降级"),
                "status": "DOWNGRADED",
            })
            continue

        # --- 未知 action: 跳过 ---
        if action not in ("SHORT_FUTURES", "PUT_SPREAD", "BUY_PUT_SPREAD",
                          "BUY_BARE_PUT", "BUY_EMERGENCY_PUT", "SAFE_HAVEN_ALLOC"):
            executed.append({
                "type": hedge_type,
                "action": action,
                "status": "SKIP_UNKNOWN_ACTION",
            })
            continue

        # --- SHORT_FUTURES: 路由到期货 broker ---
        if action == "SHORT_FUTURES":
            fut_code = order.get("instrument", "IF")
            contracts = int(order.get("contracts", 0) or 0)
            fut_price = float(order.get("futures_price", 0) or 0)
            if contracts <= 0 or fut_price <= 0:
                executed.append({
                    "type": hedge_type,
                    "action": action,
                    "instrument": fut_code,
                    "status": "SKIP_NO_PRICE_OR_QTY",
                })
                continue
            sim_order = {
                "symbol": fut_code,
                "qty": contracts,
                "side": "SELL_SHORT",
                "price": fut_price,
                "order_type": "LIMIT",
            }
            try:
                fills = sim_engine.execute_futures_orders([sim_order])
                fill = fills[0] if fills else {}
                executed.append({
                    "type": hedge_type,
                    "action": action,
                    "instrument": fut_code,
                    "side": "SELL_SHORT",
                    "contracts": contracts,
                    "price": float(fill.get("price", fut_price)),
                    "notional": order.get("notional", 0),
                    "cost": order.get("estimated_cost", 0),
                    "status": "FILLED",
                    "fill_record": fill,
                })
            except Exception as exc:
                logger.error("SHORT_FUTURES 模拟执行失败: %s", exc)
                executed.append({
                    "type": hedge_type,
                    "action": action,
                    "instrument": fut_code,
                    "side": "SELL_SHORT",
                    "contracts": contracts,
                    "price": fut_price,
                    "status": "FAILED",
                    "error": str(exc),
                })
            continue

        # --- PUT_SPREAD / BUY_PUT*: 路由到期权 broker (按 budget_allocation 拆分) ---
        if action in ("PUT_SPREAD", "BUY_PUT_SPREAD", "BUY_BARE_PUT", "BUY_EMERGENCY_PUT"):
            budget_allocation = order.get("budget_allocation") or {}
            # 若无 budget_allocation, 用单笔订单兜底
            if not budget_allocation:
                budget = float(order.get("budget", 0) or 0)
                opt_symbol = order.get("instrument", "50ETF_OPTIONS")
                opt_price = budget / 10000.0 if budget > 0 else 0.0
                sim_orders = [{
                    "symbol": opt_symbol,
                    "qty": 1,
                    "side": "BUY",
                    "price": opt_price,
                    "order_type": "LIMIT",
                    "option_type": "PUT",
                }]
            else:
                sim_orders = []
                for symbol, amount in budget_allocation.items():
                    amt = float(amount or 0)
                    sim_orders.append({
                        "symbol": symbol,
                        "qty": 1,
                        "side": "BUY",
                        "price": amt / 10000.0 if amt > 0 else 0.0,
                        "order_type": "LIMIT",
                        "option_type": "PUT",
                    })
            try:
                fills = sim_engine.execute_options_orders(sim_orders)
                for fill in fills:
                    executed.append({
                        "type": hedge_type,
                        "action": action,
                        "instrument": fill.get("symbol", order.get("instrument", "OPTIONS")),
                        "side": "BUY",
                        "option_type": "PUT",
                        "budget": order.get("budget", 0),
                        "price": float(fill.get("price", 0)),
                        "status": "FILLED",
                        "fill_record": fill,
                    })
            except Exception as exc:
                logger.error("%s 模拟执行失败: %s", action, exc)
                executed.append({
                    "type": hedge_type,
                    "action": action,
                    "instrument": order.get("instrument", "OPTIONS"),
                    "side": "BUY",
                    "status": "FAILED",
                    "error": str(exc),
                })
            continue

        # --- SAFE_HAVEN_ALLOC: 路由到股票 broker ---
        if action == "SAFE_HAVEN_ALLOC":
            gold_value = float(order.get("gold_value", 0) or 0)
            gold_etf = order.get("gold_etf", "518880")
            est_price = float(mock_prices.get(gold_etf, 5.85))
            gold_qty = int(gold_value / est_price / 100) * 100 if est_price > 0 else 0
            if gold_qty <= 0:
                executed.append({
                    "type": hedge_type,
                    "action": action,
                    "instrument": gold_etf,
                    "status": "SKIP_NO_PRICE_OR_QTY",
                })
                continue
            sim_order = {
                "symbol": gold_etf,
                "qty": gold_qty,
                "side": "BUY",
                "price": est_price,
                "order_type": "LIMIT",
            }
            try:
                fills = sim_engine.execute_stock_orders([sim_order])
                fill = fills[0] if fills else {}
                executed.append({
                    "type": hedge_type,
                    "action": action,
                    "instrument": gold_etf,
                    "side": "BUY",
                    "contracts": gold_qty,
                    "price": float(fill.get("price", est_price)),
                    "gold_value": gold_value,
                    "status": "FILLED",
                    "fill_record": fill,
                })
            except Exception as exc:
                logger.error("SAFE_HAVEN_ALLOC 模拟执行失败: %s", exc)
                executed.append({
                    "type": hedge_type,
                    "action": action,
                    "instrument": gold_etf,
                    "side": "BUY",
                    "status": "FAILED",
                    "error": str(exc),
                })
            continue

    return executed


# ============================================================
# phase_hedge: 主 phase 方法 (从 daily_workflow.py L1147-L1621 原样搬移)
# ============================================================


def phase_hedge(ctx: WorkflowContext) -> dict[str, Any]:
    """三联对冲评估 + 自动执行（与 7.4 AutoHedgeExecutor 行为对齐）

    修复点:
        1. 协调器 coordinate() 传正确签名 (positions/prices/returns/market_returns/vix)
        2. 对冲指令自动送 MockBroker 执行
        3. VIX 从 phase_market 读取，不再硬编码
        4. EDB 期货数据接入，补充 AI 算力商品价格信号
    """
    logger.info("=" * 60)
    logger.info("Phase 4: 三联对冲评估 + 自动执行")
    logger.info("=" * 60)

    HedgeCoordinator = getattr(_dw, "HedgeCoordinator", None) if _dw else None
    MockBroker = getattr(_dw, "MockBroker", None) if _dw else None

    # 从 phase_market 读取 VIX (不再硬编码)
    market_phase = ctx.state.get("phases", {}).get("market", {})
    vix_level = float(market_phase.get("vix", 18.5))
    circuit_level = market_phase.get("circuit_level", "NORMAL")

    # === EDB 期货数据接入 (AI 算力核心品种) ===
    edb_futures = _get_edb_futures_data(ctx)
    edb_summary = {}
    if edb_futures:
        for name, data in edb_futures.items():
            latest = data.get("latest")
            latest_date = data.get("latest_date")
            rows = data.get("series", [])
            ret20 = None
            if len(rows) >= 21:
                ret20 = (rows[-1][1] - rows[-21][1]) / rows[-21][1]
            edb_summary[name] = {
                "latest": latest,
                "latest_date": latest_date,
                "ret20": ret20,
                "query": data.get("query"),
            }
            logger.info("EDB 期货摘要: %s", json.dumps(edb_summary, ensure_ascii=False, default=str))

    # === 期货期权扫描器补充 (EDB + AKShare 回退) ===
    scanner_summary = _get_futures_scanner_summary()
    if scanner_summary:
        merged = dict(edb_summary)
        for name, data in scanner_summary.items():
            if name in merged:
                merged[name].update({k: v for k, v in data.items() if v is not None})
            else:
                merged[name] = data
        edb_summary = merged
        logger.info("期货扫描器已合并: %d 个品种", len(edb_summary))

    # 真实行情回退：优先 MarketDataProvider，失败回退 MOCK_PRICES
    # 使用线程池+超时防止单个 Wind MCP 调用卡死整个 phase
    prices = dict(ctx.config.MOCK_PRICES)
    market_data_provider = getattr(ctx, "market_data_provider", None)
    if market_data_provider is not None:
        _fetched = 0
        _skipped = 0
        def _fetch_one(code):
            try:
                return code, market_data_provider.get_market_data(code)
            except Exception:
                return code, None
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(_fetch_one, code): code for code in list(prices.keys())}
                for fut in as_completed(futures, timeout=30):
                    try:
                        code, quote = fut.result(timeout=5)
                        if quote and quote.get("index_price"):
                            prices[code] = float(quote["index_price"])
                            _fetched += 1
                        else:
                            _skipped += 1
                    except Exception:
                        _skipped += 1
        except Exception as exc:
            logger.warning("获取实时价格失败 (超时/异常)，回退 MOCK_PRICES: %s", exc)
        logger.info("实时价格获取: %d 成功, %d 回退 MOCK", _fetched, _skipped)

    # 当前持仓：优先读取 config/positions.json，失败则回退 MOCK_PRICES 等权假设
    _positions_json = BASE_DIR.parent / "config" / "positions.json"
    _codes = list(ctx.config.MOCK_PRICES.keys())
    if _positions_json.exists():
        try:
            with open(_positions_json, encoding="utf-8") as _f:
                _pos_data = json.load(_f)
            # positions.json key 格式 "588080.SH" → MOCK_PRICES key 格式 "sh588080"
            def _to_mock_key(code: str) -> str:
                if "." in code:
                    sym, suffix = code.split(".", 1)
                    return f"{suffix.lower()}{sym}"
                return code
            _codes = [_to_mock_key(c) for c in _pos_data.get("positions", [])
                      if _to_mock_key(c) in ctx.config.MOCK_PRICES]
            if _codes:
                logger.info("phase_hedge 加载真实持仓代码: %d 只", len(_codes))
        except Exception as _exc:
            logger.warning("读取 config/positions.json 失败，回退 MOCK_PRICES: %s", _exc)

    _n = len(_codes)
    if _n > 0:
        _target_value = float(ctx.capital)
        _value_per_asset = _target_value / _n
        positions = {}
        for _code in _codes:
            _price = prices.get(_code, 0)
            if _price and _price > 0:
                _shares = int(_value_per_asset / _price / 100) * 100
                # 2026-07-09 新增: 对 18 标的全覆盖日志 (含 6 个新标的)
                if _code in ("sz300274", "sh603019", "sh600089", "sh688017", "sh600219", "sh600019"):
                    logger.info(f"[18标的覆盖] {_code} 价格={_price} 股数={_shares} 金额={_shares*_price:.0f}")
                positions[_code] = max(_shares, 100)
            else:
                positions[_code] = 100
        logger.info("phase_hedge 等权假设持仓: %d 只, 单只约 %.0f 元", _n, _value_per_asset)
    else:
        positions = {code: 0 for code in ctx.config.MOCK_PRICES}

    # 历史收益率：优先真实 OHLCV，失败回退模拟收益率
    n_days = 60
    try:
        import numpy as np
        import pandas as pd
        price_frames = []
        def _fetch_hist(code):
            try:
                if market_data_provider is not None:
                    return code, market_data_provider.get_historical_data(code, period="3m")
            except Exception:
                pass
            return code, None
        with ThreadPoolExecutor(max_workers=4) as pool:
            hist_futures = {pool.submit(_fetch_hist, code): code for code in prices}
            for fut in as_completed(hist_futures, timeout=60):
                try:
                    code, hist = fut.result(timeout=10)
                except Exception:
                    continue
                if hist is None or (hasattr(hist, "empty") and hist.empty):
                    continue
                if "close" in hist.columns:
                    rets = hist["close"].astype(float).pct_change().dropna()
                    if len(rets) >= n_days:
                        rets = rets.tail(n_days)
                    price_frames.append(rets.rename(code))
        if price_frames:
            returns = pd.concat(price_frames, axis=1).fillna(0.0)
            if returns.shape[0] < n_days:
                returns = returns.reindex(range(n_days)).fillna(0.0)
            returns = returns.tail(n_days).reset_index(drop=True)
        else:
            raise RuntimeError("no_price_frames")
        market_series = None
        for idx_code in ["sh000001", "sz399001", "sz399006", "sh000016"]:
            mkt = None
            if market_data_provider is not None:
                try:
                    mkt = market_data_provider.get_historical_data(idx_code, period="3m")
                except Exception:
                    mkt = None
            if mkt is not None and "close" in mkt.columns and not mkt.empty:
                market_series = mkt["close"].astype(float).pct_change().dropna()
                if len(market_series) >= n_days:
                    market_series = market_series.tail(n_days)
                break
        if market_series is None:
            raise RuntimeError("no_market_series")
        market_returns = market_series.reset_index(drop=True)
        logger.info("对冲评估已使用真实价格与历史收益率")
    except Exception as exc:
        logger.warning("真实收益率获取失败，回退模拟数据: %s", exc)
        import numpy as np
        import pandas as pd
        np.random.seed(42)
        # 用更稳的默认序列：轻微正漂移 + 低波动，减少极端模拟收益
        drift = 0.0003
        vol = 0.012
        market_drift = 0.0002
        market_vol = 0.010
        returns = pd.DataFrame({
            code: np.random.normal(drift, vol, n_days) for code in prices
        })
        market_returns = pd.Series(np.random.normal(market_drift, market_vol, n_days))

    # === 三联对冲协调器 (正确签名调用) ===
    try:
        if HedgeCoordinator is None:
            raise ImportError("HedgeCoordinator 模块未加载")
        hc = HedgeCoordinator()
        coordinated = hc.coordinate(
            positions=positions,
            prices=prices,
            returns=returns,
            market_returns=market_returns,
            vix=vix_level,
            portfolio_value=ctx.capital,
        )
        logger.info(f"对冲协调: action={coordinated.get('action')}, "
                    f"总对冲比例={coordinated.get('total_hedge_pct', 0):.2%}, "
                    f"组合Beta={coordinated.get('portfolio_beta', 0):.3f}")
    except Exception as e:
        logger.error(f"对冲协调器执行失败: {e}")
        coordinated = {
            "action": "ERROR", "reason": str(e),
            "orders": [], "total_hedge_pct": 0,
            "summary": {"beta_hedger": "ERROR", "vol_hedger": "ERROR", "corr_hedger": "ERROR"},
        }

    # === 降级：风格 Beta 代理 (真实数据失效时，或主协调器判定 NO_HEDGE 但需验证价格链路) ===
    portfolio_beta = coordinated.get("portfolio_beta", 0.0)
    coordinated_action = coordinated.get("action", "")
    beta_invalid = False
    if portfolio_beta is None:
        beta_invalid = True
    elif isinstance(portfolio_beta, float):
        beta_invalid = math.isnan(portfolio_beta) or math.isinf(portfolio_beta)
    elif not isinstance(portfolio_beta, (int, float)):
        beta_invalid = True

    if beta_invalid or portfolio_beta <= 0.0 or coordinated_action in ("NO_HEDGE", "SKIP", "ERROR"):
        if beta_invalid:
            logger.warning("组合 Beta 异常 (%s)，回退到风格 Beta 代理", portfolio_beta)
        elif portfolio_beta <= 0.0:
            logger.warning("组合 Beta 计算为 0，回退到风格 Beta 代理")
        elif coordinated_action == "NO_HEDGE":
            logger.info("主协调器判定 NO_HEDGE，使用风格 Beta 代理验证价格链路")
        else:
            logger.warning("主协调器返回 %s，回退到风格 Beta 代理", coordinated_action)

        # 复用 risk.py 的 _style_beta_proxy (跨 phase 调用)
        from workflow.phases.risk import _style_beta_proxy
        style_beta = _style_beta_proxy(positions, prices)
        coordinated["portfolio_beta"] = style_beta
        beta_order = _compute_beta_hedge_order(style_beta, ctx.capital, degraded=True)
        coordinated.setdefault("summary", {})["beta_hedge"] = beta_order.get("action")
        if beta_order.get("action") not in ("NO_HEDGE", "SKIP", "ERROR"):
            coordinated.setdefault("orders", [])
            coordinated["orders"].append({**beta_order, "hedge_type": "BETA"})
        logger.info(f"风格 Beta 代理: {style_beta:.3f}, action={beta_order.get('action')}, reason={beta_order.get('reason', '')}")

        # 回退后重算汇总指标，避免总对冲比例/成本仍为 0
        try:
            _orders = coordinated.get("orders", [])
            _pv = float(ctx.capital)
            _total_hedge_value = 0.0
            _total_cost = 0.0
            for _o in _orders:
                _total_hedge_value += float(_o.get("notional", 0.0) or 0.0)
                _total_cost += float(_o.get("estimated_cost", 0.0) or _o.get("cost", 0.0) or 0.0)
            coordinated["total_hedge_pct"] = _total_hedge_value / _pv if _pv > 0 else 0.0
            coordinated["total_cost"] = _total_cost
        except Exception as _exc:
            logger.warning("回退后重算对冲汇总失败: %s", _exc)

    # === 自动执行对冲指令 (与 7.4 AutoHedgeExecutor 对齐) ===
    hedge_orders = coordinated.get("orders", [])
    executed_orders = []

    if hedge_orders and not ctx.dry_run:
        try:
            if MockBroker is None:
                raise ImportError("MockBroker 模块未加载")
            broker = MockBroker(price_dict={
                str(k): v for k, v in ctx.config.MOCK_PRICES.items()
            })

            for order in hedge_orders:
                hedge_type = order.get("hedge_type", "UNKNOWN")
                action = order.get("action", "")

                # 期货空头下单 (Beta 对冲)
                if action == "SHORT_FUTURES":
                    fut_code = order.get("instrument", "IF")
                    contracts = int(order.get("contracts", 0))
                    fut_price = float(order.get("futures_price", 0))
                    if contracts > 0 and fut_price > 0:
                        logger.info(f"[对冲执行] {hedge_type}: {fut_code} 空头 {contracts} 手 @ {fut_price}")
                        try:
                            oid = broker.place(
                                symbol=fut_code,
                                qty=contracts,
                                side="SELL_SHORT",
                                order_type="LIMIT",
                                price=fut_price,
                            )
                            fill = broker.wait_fill(oid)
                            executed_orders.append({
                                "type": hedge_type,
                                "action": action,
                                "instrument": fut_code,
                                "side": "SELL_SHORT",
                                "contracts": contracts,
                                "price": fill["price"] if fill else fut_price,
                                "notional": order.get("notional", 0),
                                "cost": order.get("estimated_cost", 0),
                                "status": fill["order_type"] if fill else "FILLED",
                                "reason": "Beta 对冲自动执行",
                                "order_id": oid,
                                "cost_breakdown": order.get("cost_breakdown"),
                            })
                        except Exception as exc:
                            logger.error(f"Beta 对冲执行失败: {exc}")
                            executed_orders.append({
                                "type": hedge_type,
                                "action": action,
                                "instrument": fut_code,
                                "side": "SELL_SHORT",
                                "contracts": contracts,
                                "price": fut_price,
                                "notional": order.get("notional", 0),
                                "cost": order.get("estimated_cost", 0),
                                "status": "FAILED",
                                "reason": f"Beta 对冲执行失败: {exc}",
                                "cost_breakdown": order.get("cost_breakdown"),
                            })

                # 期权买入 (Vol 对冲 + Tail 对冲)
                elif action in ("BUY_PUT_SPREAD", "BUY_BARE_PUT", "BUY_EMERGENCY_PUT",
                                "PUT_SPREAD", "BARE_PUT", "EMERGENCY_PUT"):
                    budget = float(order.get("budget", 0))
                    budget_allocation = order.get("budget_allocation") or {}
                    otm_ladder = order.get("otm_ladder") or []

                    # --- Tail 对冲: 按 budget_allocation 多标的分配 ---
                    if budget_allocation:
                        regime_lbl = order.get("regime", "normal")
                        logger.info(f"[对冲执行] {hedge_type}: {action} 预算 {budget:.0f} "
                                    f"regime={regime_lbl} 标的={list(budget_allocation.keys())}")
                        for symbol, amount in budget_allocation.items():
                            amt = float(amount or 0)
                            if amt <= 0:
                                continue
                            otm_pct = float(otm_ladder[0].get("otm_pct", 0.10)) if otm_ladder else 0.10
                            # 按各标的现货价计算 strike (修复: 原逻辑用首个 spot 统一计算所有标的 strike)
                            spot_sym = 0.0
                            for pk, pv in prices.items():
                                if symbol in pk and pv > 0:
                                    spot_sym = float(pv)
                                    break
                            if spot_sym <= 0:
                                spot_sym = float(otm_ladder[0].get("strike", 0.0)) / (1 - otm_pct) if otm_ladder else 0.0
                            strike = round(spot_sym * (1 - otm_pct), 4)
                            opt_price = amt / 10000.0
                            try:
                                oid = broker.place(
                                    symbol=f"{symbol}P",
                                    qty=1,
                                    side="BUY",
                                    order_type="LIMIT",
                                    price=opt_price,
                                    option_type="PUT",
                                    strike=strike,
                                )
                                fill = broker.wait_fill(oid)
                                executed_orders.append({
                                    "type": hedge_type,
                                    "action": action,
                                    "instrument": f"{symbol} Put",
                                    "side": "BUY",
                                    "option_type": "PUT",
                                    "strike": strike,
                                    "otm_pct": otm_pct,
                                    "budget": amt,
                                    "protection_ratio": order.get("protection_ratio", 0),
                                    "price": fill["price"] if fill else opt_price,
                                    "status": "FILLED",
                                    "reason": f"Tail 对冲自动执行 (regime={regime_lbl}, OTM={otm_pct:.0%})",
                                    "order_id": oid,
                                })
                            except Exception as exc:
                                logger.error(f"Tail 对冲执行失败 {symbol}: {exc}")
                                executed_orders.append({
                                    "type": hedge_type,
                                    "action": action,
                                    "instrument": f"{symbol} Put",
                                    "side": "BUY",
                                    "option_type": "PUT",
                                    "strike": strike,
                                    "budget": amt,
                                    "status": "FAILED",
                                    "reason": f"Tail 对冲执行失败: {exc}",
                                })

                    # --- Vol 对冲: 单标的 ---
                    elif budget > 0:
                        logger.info(f"[对冲执行] {hedge_type}: {action} 预算 {budget:.0f}")
                        try:
                            opt_symbol = order.get("instrument", "50ETF_OPTIONS")
                            opt_price = budget / 10000.0 if budget > 0 else 0.0
                            oid = broker.place(
                                symbol=opt_symbol,
                                qty=1,
                                side="BUY",
                                order_type="LIMIT",
                                price=opt_price,
                                option_type="PUT",
                                strike=0.0,
                            )
                            fill = broker.wait_fill(oid)
                            executed_orders.append({
                                "type": hedge_type,
                                "action": action,
                                "instrument": opt_symbol,
                                "side": "BUY",
                                "option_type": "PUT",
                                "strike": 0.0,
                                "budget": budget,
                                "delta_target": order.get("delta_target", -0.2),
                                "coverage": order.get("actual_coverage", 0),
                                "price": fill["price"] if fill else opt_price,
                                "status": "FILLED",
                                "reason": f"Vol 对冲自动执行 (VIX={vix_level})",
                                "order_id": oid,
                            })
                        except Exception as exc:
                            logger.error(f"Vol 对冲执行失败: {exc}")
                            executed_orders.append({
                                "type": hedge_type,
                                "action": action,
                                "instrument": order.get("instrument", "50ETF_OPTIONS"),
                                "side": "BUY",
                                "option_type": "PUT",
                                "strike": 0.0,
                                "budget": budget,
                                "delta_target": order.get("delta_target", -0.2),
                                "coverage": order.get("actual_coverage", 0),
                                "status": "FAILED",
                                "reason": f"Vol 对冲执行失败: {exc}",
                            })

                # 避险资产配置 (Correlation 对冲)
                elif action == "SAFE_HAVEN_ALLOC":
                    gold_value = float(order.get("gold_value", 0))
                    repo_value = float(order.get("repo_value", 0))
                    logger.info(f"[对冲执行] {hedge_type}: 黄金ETF {gold_value:.0f} + 逆回购 {repo_value:.0f}")
                    try:
                        gold_symbol = order.get("gold_etf", "518880")
                        est_gold_price = ctx.config.MOCK_PRICES.get(gold_symbol, 5.85)
                        gold_qty = int(gold_value / est_gold_price / 100) * 100
                        if gold_qty > 0:
                            oid = broker.place(
                                symbol=gold_symbol,
                                qty=gold_qty,
                                side="BUY",
                                order_type="LIMIT",
                                price=est_gold_price,
                            )
                            fill = broker.wait_fill(oid)
                            executed_orders.append({
                                "type": hedge_type,
                                "action": action,
                                "gold_etf": gold_symbol,
                                "gold_qty": gold_qty,
                                "gold_value": gold_value,
                                "gold_price": fill["price"] if fill else est_gold_price,
                                "repo_symbol": order.get("repo_symbol", "GC001"),
                                "repo_value": repo_value,
                                "status": "FILLED",
                                "reason": f"Correlation 对冲自动执行 (ρ̄={order.get('avg_corr', 0):.3f})",
                                "order_id": oid,
                            })
                    except Exception as exc:
                        logger.error(f"Correlation 对冲执行失败: {exc}")
                        executed_orders.append({
                            "type": hedge_type,
                            "action": action,
                            "gold_etf": order.get("gold_etf", "518880"),
                            "gold_value": gold_value,
                            "repo_symbol": order.get("repo_symbol", "GC001"),
                            "repo_value": repo_value,
                            "status": "FAILED",
                            "reason": f"Correlation 对冲执行失败: {exc}",
                        })

                # 降级为 Put Spread
                elif action == "DOWNGRADE_TO_PUT_SPREAD":
                    logger.info(f"[对冲执行] {hedge_type}: 降级至 Put Spread (成本超限)")
                    executed_orders.append({
                        "type": hedge_type,
                        "action": action,
                        "reason": order.get("reason", "成本超限降级"),
                        "status": "DOWNGRADED",
                    })

            logger.info(f"对冲执行完成: {len(executed_orders)} 笔指令已成交")

        except Exception as e:
            logger.error(f"对冲执行通道异常: {e}")
            executed_orders.append({
                "type": "EXECUTION_ERROR",
                "status": "FAILED",
                "error": str(e),
            })
    elif hedge_orders and ctx.dry_run:
        logger.info(f"DRY-RUN 模式: {len(hedge_orders)} 笔对冲指令未执行")
        for order in hedge_orders:
            executed_orders.append({**order, "status": "DRY_RUN"})

    # === 熔断级别强制加对冲 (LEVEL_3+) ===
    if circuit_level in ("LEVEL_3", "LEVEL_4"):
        logger.warning(f"[{circuit_level}] 熔断触发, 强制加对冲")
        force_action = {
            "type": "CIRCUIT_BREAKER_HEDGE",
            "circuit_level": circuit_level,
            "force_reduce_pct": 0.50 if circuit_level == "LEVEL_3" else 1.0,
            "status": "TRIGGERED",
            "reason": f"熔断 {circuit_level} 强制对冲",
        }
        executed_orders.append(force_action)
        ctx.state["phases"]["market"]["build_allowed"] = False

    hedge_status = {
        "vix": vix_level,
        "circuit_level": circuit_level,
        "portfolio_beta": coordinated.get("portfolio_beta", 0),
        "actions": coordinated.get("summary", {}),
        "orders": executed_orders,
        "coordinated": coordinated,
        "total_hedge_pct": coordinated.get("total_hedge_pct", 0),
        "total_cost": coordinated.get("total_cost", 0),
        "hedge_enabled": len(executed_orders) > 0,
        "edb_futures": edb_summary,
    }

    logger.info(f"对冲汇总: 启用={hedge_status['hedge_enabled']}, "
                f"指令数={len(executed_orders)}, "
                f"总对冲比例={hedge_status['total_hedge_pct']:.2%}")

    ctx.state["phases"]["hedge"] = {"status": "PASS", **hedge_status}
    ctx.state["hedge_status"] = hedge_status

    # === 持久化对冲执行记录 ===
    try:
        trade_date = getattr(ctx, "trade_date", None) or datetime.now().strftime("%Y-%m-%d")
        reports_dir = BASE_DIR / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        hedge_fill_path = reports_dir / f"hedge_execution_fill_{trade_date}.json"
        fill_payload = {
            "trade_date": trade_date,
            "generated_at": datetime.now().isoformat(),
            "portfolio_beta": coordinated.get("portfolio_beta", 0),
            "total_hedge_pct": coordinated.get("total_hedge_pct", 0),
            "total_cost": coordinated.get("total_cost", 0),
            "hedge_enabled": len(executed_orders) > 0,
            "orders": executed_orders,
        }
        with open(hedge_fill_path, "w", encoding="utf-8") as f:
            json.dump(fill_payload, f, ensure_ascii=False, indent=2)
        logger.info(f"对冲成交记录已落盘: {hedge_fill_path}")
    except Exception as exc:
        logger.error(f"对冲成交记录落盘失败: {exc}")

    return hedge_status
