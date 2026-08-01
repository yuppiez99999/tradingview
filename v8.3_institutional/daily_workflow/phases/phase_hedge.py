#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase implementation: phase_hedge

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the phase_hedge phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def phase_hedge(workflow) -> Dict[str, Any]:
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

    # 从 phase_market 读取 VIX (不再硬编码)
    market_phase = workflow.state.get("phases", {}).get("market", {})
    vix_level = float(market_phase.get("vix", 18.5))
    circuit_level = market_phase.get("circuit_level", "NORMAL")

    # === EDB 期货数据接入 (AI 算力核心品种) ===
    edb_futures = workflow._get_edb_futures_data()
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
    scanner_summary = workflow._get_futures_scanner_summary()
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
    prices = dict(workflow.config.MOCK_PRICES)
    if workflow.market_data_provider is not None:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        _fetched = 0
        _skipped = 0
        def _fetch_one(code):
            try:
                return code, workflow.market_data_provider.get_market_data(code)
            except Exception:
                return code, None
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(_fetch_one, code): code for code in list(prices.keys())}
                for fut in as_completed(futures, timeout=30):
                    try:
                        code, quote = fut.result(timeout=5)
                        if not quote or not quote.get("index_price"):
                            _skipped += 1
                            continue
                        # === DataGate 数据质量门控 (P0-8: 坏数据不交易) ===
                        # 尽量从行情快照提取质量元数据; 缺失时 DataGate 默认放行, 不破坏现有行为
                        snapshot = {
                            "price": quote.get("index_price"),
                            "quality_score": quote.get("quality_score"),
                            "timestamp": quote.get("timestamp") or quote.get("update_time") or quote.get("data_time"),
                            "source": quote.get("source") or quote.get("provider"),
                        }
                        try:
                            gate = workflow.data_gate.check_and_gate(code, snapshot)
                        except Exception as _gate_exc:  # 门控异常不得阻断行情获取
                            logger.warning("[DataGate] %s 门控异常, 降级放行: %s", code, _gate_exc)
                            gate = None
                        if gate is not None and not gate.allowed:
                            logger.warning(
                                "[DataGate] %s 数据门控拦截(不更新价格): %s | score=%.1f",
                                code, gate.reasons, gate.quality_score,
                            )
                            _skipped += 1
                            continue
                        prices[code] = float(quote["index_price"])
                        _fetched += 1
                    except Exception:
                        _skipped += 1
        except Exception as exc:
            logger.warning("获取实时价格失败 (超时/异常)，回退 MOCK_PRICES: %s", exc)
        logger.info("实时价格获取: %d 成功, %d 回退 MOCK", _fetched, _skipped)

    # 当前持仓：优先读取 config/positions.json 真实持仓，失败则回退 MOCK_PRICES 等权假设
    # v8.4 修复: 原 bug 把 positions dict 当 list 遍历 + key 格式不匹配 (510050.SH vs sh510050)
    #           导致永远回退 MOCK_PRICES 等权, 真实持仓从未被使用
    _positions_json = BASE_DIR.parent / "config" / "positions.json"
    _codes = list(workflow.config.MOCK_PRICES.keys())
    _real_positions = {}  # {mock_key: shares} 真实持仓
    if _positions_json.exists():
        try:
            with open(_positions_json, "r", encoding="utf-8") as _f:
                _pos_data = json.load(_f)
            _pos_dict = _pos_data.get("positions", {})
            # positions.json key 格式 "510050.SH" → MOCK_PRICES key 格式 "sh510050"
            _items = _pos_dict.items() if isinstance(_pos_dict, dict) else []
            for _pos_key, _pos_info in _items:
                if not isinstance(_pos_info, dict):
                    continue
                if '.' in _pos_key:
                    _code_part, _suffix = _pos_key.split('.')
                    _mock_key = f"{_suffix.lower()}{_code_part}"
                else:
                    _mock_key = _pos_key.lower()
                if _mock_key in workflow.config.MOCK_PRICES:
                    _shares = int(_pos_info.get("shares", 0) or 0)
                    if _shares > 0:
                        _real_positions[_mock_key] = _shares
            if _real_positions:
                _codes = list(_real_positions.keys())
                logger.info("phase_hedge 加载真实持仓: %d 只 (真实 shares)", len(_codes))
            else:
                logger.info("phase_hedge 真实持仓 shares 全为 0, 回退 MOCK_PRICES 等权")
        except Exception as _exc:
            logger.warning("读取 config/positions.json 失败，回退 MOCK_PRICES: %s", _exc)

    _n = len(_codes)
    if _n > 0:
        _target_value = float(workflow.capital)
        _value_per_asset = _target_value / _n
        positions = {}
        for _code in _codes:
            _price = prices.get(_code, 0)
            # v8.4: 优先使用真实 shares, 缺失时用等权假设
            _real_shares = _real_positions.get(_code)
            if _real_shares and _real_shares > 0:
                _shares = _real_shares
            elif _price and _price > 0:
                _shares = int(_value_per_asset / _price / 100) * 100
            else:
                _shares = 100
            # 2026-07-09 新增: 对 18 标的全覆盖日志 (含 6 个新标的)
            if _code in ("sz300274", "sh603019", "sh600089", "sh688017", "sh600219", "sh600019"):
                logger.info(f"[18标的覆盖] {_code} 价格={_price} 股数={_shares} "
                            f"金额={_shares*_price:.0f} 真实={_real_shares is not None}")
            positions[_code] = max(_shares, 100)
        if _real_positions:
            _total_real = sum(positions.get(c, 0) * prices.get(c, 0) for c in _codes)
            logger.info("phase_hedge 真实持仓: %d 只, 总市值约 %.0f 元", _n, _total_real)
        else:
            logger.info("phase_hedge 等权假设持仓: %d 只, 单只约 %.0f 元", _n, _value_per_asset)
    else:
        positions = {code: 0 for code in workflow.config.MOCK_PRICES}

    # 历史收益率：优先真实 OHLCV，失败回退模拟收益率
    n_days = 60
    try:
        import numpy as np
        import pandas as pd
        price_frames = []
        def _fetch_hist(code):
            try:
                if workflow.market_data_provider is not None:
                    return code, workflow.market_data_provider.get_historical_data(code, period="3m")
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
            if workflow.market_data_provider is not None:
                try:
                    mkt = workflow.market_data_provider.get_historical_data(idx_code, period="3m")
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
    # v8.4: 传入 hwm_drawdown 和 bs_loss, 让 TailRiskHedger 4 状态机能正确判定 regime
    #       原 bug: 未传这两个参数, coordinate 默认 0.0, regime 永远判定为 NORMAL
    try:
        hc = HedgeCoordinator()
        _hwm_dd = float(workflow.state.get("phases", {}).get("risk", {})
                        .get("drawdown_status", {}).get("hwm_drawdown", 0.0) or 0.0)
        _bs_loss = float(workflow.state.get("phases", {}).get("market", {})
                         .get("portfolio_drop", 0.0) or 0.0)
        coordinated = hc.coordinate(
            positions=positions,
            prices=prices,
            returns=returns,
            market_returns=market_returns,
            vix=vix_level,
            portfolio_value=workflow.capital,
            hwm_drawdown=_hwm_dd,
            bs_loss=_bs_loss,
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

        style_beta = workflow._style_beta_proxy(positions, prices)
        coordinated["portfolio_beta"] = style_beta
        beta_order = workflow._compute_beta_hedge_order(style_beta, workflow.capital, degraded=True)
        coordinated.setdefault("summary", {})["beta_hedge"] = beta_order.get("action")
        if beta_order.get("action") not in ("NO_HEDGE", "SKIP", "ERROR"):
            coordinated.setdefault("orders", [])
            coordinated["orders"].append({**beta_order, "hedge_type": "BETA"})
        logger.info(f"风格 Beta 代理: {style_beta:.3f}, action={beta_order.get('action')}, reason={beta_order.get('reason', '')}")

        # 回退后重算汇总指标，避免总对冲比例/成本仍为 0
        try:
            _orders = coordinated.get("orders", [])
            _pv = float(workflow.capital)
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

    # v8.4: sim_mode 走 SimExecutionEngine, 不再误走 CTP/THS 实盘路径
    # 原 bug: sim_mode=True 时进入 not dry_run 分支, 尝试连接 CTP/THS 失败后降级 MockBroker
    if hedge_orders and workflow.sim_mode and workflow.sim_engine is not None:
        logger.info(f"[对冲执行-sim] {len(hedge_orders)} 笔对冲指令通过 SimExecutionEngine 执行")
        try:
            executed_orders = workflow._execute_sim_hedge_orders(hedge_orders)
        except Exception as e:
            logger.error(f"[对冲执行-sim] 异常: {e}", exc_info=True)
            executed_orders.append({
                "type": "EXECUTION_ERROR",
                "status": "FAILED",
                "error": str(e),
                "reason": "sim_mode 对冲执行异常",
            })

    elif hedge_orders and not workflow.dry_run:
        # 尝试加载真实券商网关（按优先级：CTP > 同花顺 > Mock）
        # v8.6.9 P3-3 重构: CTP→同花顺连接逻辑抽离为 _connect_live_broker(), 三处复用
        broker = None
        broker, _broker_source = workflow._connect_live_broker()
        if broker is not None:
            pass

        # 优先级3: 降级到 MockBroker（仅用于测试/开发环境）
        # v8.6.8 P1-LIVE-08: 降级时明确标记, 避免误认为实盘
        if broker is None:
            logger.warning(
                "⚠️ [对冲执行] 实盘模式但未检测到真实券商网关, 降级 MockBroker\n"
                "   生产环境请配置: CTP_FRONT_ADDR / THS_ACCOUNT\n"
                "   当前日期: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") +
                "\n   ⚠️ 注意: 对冲订单将仅记录到 MockBroker, 不会真实执行!"
            )
            broker = MockBroker(price_dict={
                str(k): v for k, v in workflow.config.MOCK_PRICES.items()
            })

        # 对冲指令执行（所有 broker 类型通用；修复: 补全缺失的 try 匹配 L2305 except）
        try:

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

                # 期权买入 (Vol 对冲)
                elif action in ("BUY_PUT_SPREAD", "BUY_BARE_PUT", "BUY_EMERGENCY_PUT"):
                    budget = float(order.get("budget", 0))
                    if budget > 0:
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
                                strike=order.get("strike", 0.0),
                            )
                            fill = broker.wait_fill(oid)
                            executed_orders.append({
                                "type": hedge_type,
                                "action": action,
                                "instrument": opt_symbol,
                                "side": "BUY",
                                "option_type": "PUT",
                                "strike": order.get("strike", 0.0),
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
                        est_gold_price = workflow.config.MOCK_PRICES.get(gold_symbol, 5.85)
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
    elif hedge_orders and workflow.dry_run:
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
        workflow.state["phases"]["market"]["build_allowed"] = False

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

    workflow.state["phases"]["hedge"] = {"status": "PASS", **hedge_status}
    workflow.state["hedge_status"] = hedge_status

    # === 持久化对冲执行记录 ===
    try:
        trade_date = getattr(self, "trade_date", None) or datetime.now().strftime("%Y-%m-%d")
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

    # === v8.4: 对冲完整性门禁 (Beta+Delta 双约束) ===
    # sim_mode: 不达标仅告警 (继续运行积累数据)
    # live_mode: 不达标 fail-closed (终止后续 phase)
    try:
        from gate_manager import HedgeCompletenessGate
        gate = HedgeCompletenessGate()
        sim_engine_ref = workflow.sim_engine if (workflow.sim_mode and workflow.sim_engine) else None
        result = gate.evaluate(
            portfolio_beta_before=float(coordinated.get("portfolio_beta", 0) or 0),
            portfolio_value=float(getattr(self, "capital", 5_000_000)),
            hedge_orders_executed=executed_orders,
            sim_engine=sim_engine_ref,
        )
        hedge_status["hedge_completeness_gate"] = result.to_dict()
        logger.info(f"[Gate-HEDGE] passed={result.passed}, "
                    f"beta_after={result.metrics.get('portfolio_beta_after', 0):.3f}, "
                    f"net_delta={result.metrics.get('net_delta', 0):.4f}, "
                    f"blockers={result.blockers}")
        if not result.passed:
            if workflow.live_mode:
                logger.critical(f"[Gate-HEDGE] live fail-closed: {result.blockers}")
                workflow.state["fail_closed"] = True
                workflow.state["fail_closed_reason"] = f"HedgeCompletenessGate: {result.blockers}"
            else:
                logger.warning(f"[Gate-HEDGE] sim 告警不阻断, 继续积累数据: {result.blockers}")
    except ImportError:
        logger.warning("gate_manager 未安装, 对冲完整性门禁跳过")
    except Exception as e:
        logger.error(f"对冲完整性门禁异常 (不阻断): {e}", exc_info=True)

    return hedge_status

    # --------------------------------------------------------
    # v8.4: sim_mode 对冲执行 (通过 SimExecutionEngine)
    # --------------------------------------------------------

