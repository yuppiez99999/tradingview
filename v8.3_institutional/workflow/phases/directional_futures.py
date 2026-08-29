"""Phase 4.9: 方向性期货交易 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py L2003-L2272

搬移内容:
- phase_directional_futures: 主 phase 方法 (CU/AU/T 三品种方向性交易)
- _load_directional_futures_market_data: 加载市场数据 (仅 phase 内调用)
- _load_directional_futures_positions: 加载当前持仓 (仅 phase 内调用)
- _get_futures_prices: 提取最新价格 (仅 phase 内调用)
- _load_directional_futures_risk_state: 加载风控状态 (仅 phase 内调用)
- _save_directional_futures_orders: 保存交易指令 (仅 phase 内调用)

模块级依赖:
- V10_STRATEGY_READY / DirectionalFuturesTrader 通过 get_dw_module() 从 daily_workflow 获取
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Optional

from workflow.context import WorkflowContext, get_dw_module

logger = logging.getLogger("v75.daily_workflow")

# === 从 daily_workflow 模块获取模块级符号 (兼容条件导入) ===
_dw = get_dw_module()
BASE_DIR: Path = (
    getattr(_dw, "BASE_DIR", Path(__file__).resolve().parent.parent)
    if _dw
    else Path(__file__).resolve().parent.parent
)
V10_STRATEGY_READY = getattr(_dw, "V10_STRATEGY_READY", False) if _dw else False

# 类 — 仅当 daily_workflow 模块中已导入时才引入
if _dw is not None and hasattr(_dw, "DirectionalFuturesTrader"):
    DirectionalFuturesTrader = _dw.DirectionalFuturesTrader


def phase_directional_futures(ctx: WorkflowContext) -> dict[str, Any]:
    """方向性期货交易 — CU(沪铜)/AU(黄金)/T(10年国债) 三品种

    v10.0 macro_hedge_account 中的方向性子模块:
        - CU 沪铜:    新能源需求方向, 默认做多
        - AU 黄金:    避险+通胀对冲, 默认做多
        - T  10年国债: 利率方向, 默认做空 (十五五财政发力推升利率)

    信号源:
        - MA20/MA60 趋势 + RSI 超买超卖 + MACD 动量 + 品种默认方向 0.5 票
        - 三重确认: ≥2 票做多, ≤-2 票做空, 否则空仓

    风控:
        - 单笔最大亏损 20% (保证金视角)
        - 日最大亏损 15% (账户视角)
        - 周连续亏损 25% → 暂停 7 天

    Returns:
        方向性期货交易结果
    """
    logger.info("=" * 60)
    logger.info("Phase 4.9: 方向性期货交易 (CU/AU/T)")
    logger.info("=" * 60)

    result: dict[str, Any] = {
        "status": "PASS",
        "action": "skip",
        "signals": [],
        "orders": [],
        "risk_status": "normal",
        "total_margin_used": 0.0,
        "total_notional": 0.0,
        "pause_until": None,
    }

    if not V10_STRATEGY_READY:
        result["status"] = "SKIP"
        result["reason"] = "v10.0 方向性期货模块未加载"
        logger.warning("[DirectionalFutures] v10.0 模块未加载, 跳过")
        ctx.state["phases"]["directional_futures"] = result
        return result

    try:
        # 1. 加载市场数据 (CU/AU/T 的 OHLCV)
        market_data = _load_directional_futures_market_data()

        # 2. 加载当前持仓
        current_positions = _load_directional_futures_positions()

        # 3. 获取当前价格
        prices = _get_futures_prices(market_data)

        # 4. 加载风控状态
        daily_pnl_pct, weekly_loss_pct, last_pause_date = (
            _load_directional_futures_risk_state()
        )

        # 5. 调用 DirectionalFuturesTrader.run()
        trader = DirectionalFuturesTrader()
        df_result = trader.run(
            market_data=market_data,
            current_positions=current_positions,
            prices=prices,
            trade_date=date.today(),
            daily_pnl_pct=daily_pnl_pct,
            weekly_consecutive_loss_pct=weekly_loss_pct,
            last_loss_pause_date=last_pause_date,
        )

        # 6. 输出摘要
        summary = trader.summary(df_result)
        logger.info("\n" + summary)

        # 7. 保存指令到文件
        orders_saved = _save_directional_futures_orders(df_result.orders)

        # 8. 更新结果
        result.update(
            {
                "status": "PASS",
                "action": df_result.action,
                "signals": [
                    asdict(s) if hasattr(s, "__dataclass_fields__") else dict(s)
                    for s in df_result.signals
                ],
                "orders": [
                    asdict(o) if hasattr(o, "__dataclass_fields__") else dict(o)
                    for o in df_result.orders
                ],
                "risk_status": df_result.risk_status,
                "total_margin_used": df_result.total_margin_used,
                "total_notional": df_result.total_notional,
                "pause_until": (
                    df_result.pause_until.isoformat() if df_result.pause_until else None
                ),
                "orders_file": orders_saved,
            }
        )

        # 9. 风控告警
        if df_result.risk_status == "warning":
            logger.warning(f"[DirectionalFutures] 风控告警: 日亏损 {daily_pnl_pct:.2%}")
        elif df_result.risk_status == "paused":
            logger.error(f"[DirectionalFutures] 已暂停交易至 {df_result.pause_until}")

    except Exception as e:  # fail-safe: 方向性期货交易失败不阻断主流程
        logger.error(f"[DirectionalFutures] 方向性期货交易失败: {e}", exc_info=True)
        result["status"] = "ERROR"
        result["reason"] = str(e)

    # === 写入 state ===
    ctx.state["phases"]["directional_futures"] = result
    logger.info("-" * 60)
    logger.info(
        "Phase 4.9 完成: 动作=%s, 风控=%s, 保证金=¥%.0f, 名义=¥%.0f",
        result.get("action", ""),
        result.get("risk_status", ""),
        result.get("total_margin_used", 0),
        result.get("total_notional", 0),
    )
    logger.info("=" * 60)
    return result


def _load_directional_futures_market_data() -> dict[str, dict[str, Any]]:
    """加载方向性期货市场数据 (CU/AU/T 的 OHLCV)

    数据源优先级:
        1. config/futures_market_data.json (本地缓存)
        2. Wind MCP / iFinD MCP (实时获取, 待实现)
        3. 模拟数据 (60 日 OHLCV, 用于模块自测)

    Returns:
        {symbol: {"closes": [...], "volumes": [...], "opens": [...], "highs": [...], "lows": [...]}}
    """
    market_data: dict[str, dict[str, Any]] = {}

    # 1. 尝试从本地缓存加载
    try:
        cache_path = BASE_DIR.parent / "config" / "futures_market_data.json"
        if cache_path.exists():
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            for symbol in ("CU", "AU", "T"):
                if symbol in data:
                    market_data[symbol] = data[symbol]
            if len(market_data) == 3:
                logger.info("[DirectionalFutures] 从缓存加载 CU/AU/T 行情数据")
                return market_data
    except Exception as e:  # fail-safe: 缓存加载失败用模拟数据
        logger.debug(f"[DirectionalFutures] 缓存加载失败: {e}")

    # 2. 实时数据源 (TODO: Wind MCP / iFinD MCP 集成)
    # 当前版本: 使用模拟数据 (60 日) 触发模块逻辑
    logger.warning("[DirectionalFutures] 实时期货行情未集成, 使用模拟数据 (60 日)")
    random.seed(42)  # 可复现

    base_prices = {"CU": 75000.0, "AU": 550.0, "T": 102.5}
    for symbol, base in base_prices.items():
        closes = []
        volumes = []
        price = base
        for _i in range(60):
            # 模拟价格波动 (±2%)
            change = random.uniform(-0.02, 0.02)
            price = price * (1 + change)
            closes.append(round(price, 4))
            volumes.append(random.randint(10000, 100000))
        market_data[symbol] = {
            "closes": closes,
            "volumes": volumes,
            "opens": [c * (1 + random.uniform(-0.01, 0.01)) for c in closes],
            "highs": [c * (1 + random.uniform(0, 0.015)) for c in closes],
            "lows": [c * (1 - random.uniform(0, 0.015)) for c in closes],
        }

    return market_data


def _load_directional_futures_positions() -> dict[str, dict]:
    """加载当前方向性期货持仓

    Returns:
        {symbol: {"direction": "long"/"short"/"flat", "contracts": int, "entry_price": float}}
    """
    positions: dict[str, dict] = {}
    try:
        pos_path = BASE_DIR.parent / "config" / "directional_futures_positions.json"
        if pos_path.exists():
            with open(pos_path, encoding="utf-8") as f:
                data = json.load(f)
            for symbol in ("CU", "AU", "T"):
                if symbol in data:
                    positions[symbol] = data[symbol]
    except Exception as e:  # fail-safe: 持仓加载失败用默认空仓
        logger.debug(f"[DirectionalFutures] 持仓加载失败: {e}")

    # 默认空仓
    for symbol in ("CU", "AU", "T"):
        if symbol not in positions:
            positions[symbol] = {
                "direction": "flat",
                "contracts": 0,
                "entry_price": 0.0,
            }

    return positions


def _get_futures_prices(market_data: dict[str, dict[str, Any]]) -> dict[str, float]:
    """从市场数据中提取最新价格

    Args:
        market_data: {symbol: {"closes": [...]}}

    Returns:
        {symbol: 最新收盘价}
    """
    prices = {}
    for symbol in ("CU", "AU", "T"):
        closes = market_data.get(symbol, {}).get("closes", [])
        if closes:
            prices[symbol] = float(closes[-1])
        else:
            # 兜底默认值
            defaults = {"CU": 75000.0, "AU": 550.0, "T": 102.5}
            prices[symbol] = defaults[symbol]
    return prices


def _load_directional_futures_risk_state() -> tuple[float, float, Optional[date]]:
    """加载方向性期货风控状态

    Returns:
        (当日盈亏百分比, 周连续亏损百分比, 上次暂停日期)
    """
    try:
        risk_path = BASE_DIR.parent / "config" / "directional_futures_risk.json"
        if risk_path.exists():
            with open(risk_path, encoding="utf-8") as f:
                data = json.load(f)
            daily_pnl = float(data.get("daily_pnl_pct", 0.0))
            weekly_loss = float(data.get("weekly_consecutive_loss_pct", 0.0))
            pause_str = data.get("last_loss_pause_date")
            pause_date = date.fromisoformat(pause_str) if pause_str else None
            return (daily_pnl, weekly_loss, pause_date)
    except Exception as e:  # fail-safe: 风控状态加载失败用默认值
        logger.debug(f"[DirectionalFutures] 风控状态加载失败: {e}")
    return (0.0, 0.0, None)


def _save_directional_futures_orders(orders: list[Any]) -> Optional[str]:
    """保存方向性期货交易指令到文件

    Args:
        orders: 指令列表 (FuturesOrder 对象或字典)

    Returns:
        保存的文件路径, 失败返回 None
    """
    if not orders:
        return None

    try:
        orders_dir = BASE_DIR.parent / "trade_instructions"
        orders_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        file_path = orders_dir / f"directional_futures_orders_{today}.json"

        orders_data = []
        for o in orders:
            if hasattr(o, "__dataclass_fields__"):
                orders_data.append(asdict(o))
            elif isinstance(o, dict):
                orders_data.append(o)
            else:
                orders_data.append(str(o))

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(orders_data, f, ensure_ascii=False, indent=2, default=str)

        logger.info(f"[DirectionalFutures] 指令已保存: {file_path}")
        return str(file_path)
    except Exception as e:  # fail-safe: 指令保存失败返回 None
        logger.error(f"[DirectionalFutures] 指令保存失败: {e}")
        return None
