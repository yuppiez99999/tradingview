"""
盘中实时监控与动态调整 (Intraday Real-time Monitor & Dynamic Adjustment) — v1.1

核心能力:
1. 实时持仓监控 — 盈亏/回撤/风格偏离/对冲有效性
2. 动态信号调整 — 根据盘中走势动态调整建仓节奏
3. 自动风控触发 — 三级熔断/加速减持/暂停交易
4. 对冲动态再平衡 — Beta 偏离超阈值时自动调仓
5. 事件驱动响应 — 涨跌停/异动/新闻事件快速反应
6. 连续监控与价格序列缓存 — run_watch 模式 + RealtimePriceCache

监控频率:
- 快监控 (5秒): 关键持仓盈亏、止损止盈、熔断触发
- 中监控 (1分钟): 风格偏离、对冲有效性、仓位控制
- 慢监控 (5分钟): 板块轮动、宏观信号、资金流验证

动态调整策略:
- 顺趋势: 突破确认 → 加速建仓 (仓位系数 1.0 → 1.2)
- 逆趋势: 跌破关键位 → 减速建仓 (仓位系数 1.0 → 0.5)
- 极端: 触发熔断 → 暂停新开仓 + 触发止损
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger("v7.5.intraday_monitor")

_BASE = Path(__file__).resolve().parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))


# ============================================================
# 价格缓存与连续监控支持
# ============================================================

class RealtimePriceCache:
    """连续监控时的实时价格缓存

    保存每次采集的快照，避免覆盖上一轮数据。
    """

    def __init__(self) -> None:
        self.series: dict[str, list[dict[str, Any]]] = {}
        self.latest: dict[str, dict[str, Any]] = {}

    def merge(self, prices: dict[str, dict[str, Any]]) -> None:
        now = now_bj().isoformat()
        for code, payload in prices.items():
            entry = {"ts": now, **payload}
            self.latest[code] = payload
            self.series.setdefault(code, []).append(entry)

    def latest_prices(self) -> dict[str, dict[str, Any]]:
        return dict(self.latest)


# ============================================================
# 枚举与数据结构
# ============================================================

class MonitorLevel(Enum):
    FAST = "fast"         # 5s 级
    MEDIUM = "medium"     # 1min 级
    SLOW = "slow"         # 5min 级


class AdjustmentAction(Enum):
    ACCELERATE = "accelerate"   # 加速建仓
    DECELERATE = "decelerate"   # 减速建仓
    PAUSE = "pause"             # 暂停新开仓
    RESUME = "resume"           # 恢复建仓
    REDUCE = "reduce"           # 减仓
    HEDGE_UP = "hedge_up"       # 增加对冲
    HEDGE_DOWN = "hedge_down"   # 减少对冲


@dataclass
class PositionSnapshot:
    """持仓快照"""
    symbol: str
    name: str = ""
    quantity: int = 0
    avg_cost: float = 0.0
    current_price: float = 0.0
    market_value: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    style: str = ""             # 风格标签


@dataclass
class MonitorAlert:
    """监控告警"""
    alert_id: str
    level: str                  # "info" / "warning" / "critical"
    category: str               # "pnl" / "risk" / "hedge" / "style" / "event"
    message: str
    timestamp: str = ""
    symbol: str = ""
    current_value: float = 0.0
    threshold: float = 0.0
    action: AdjustmentAction | None = None


@dataclass
class DynamicAdjustment:
    """动态调整决策"""
    action: AdjustmentAction
    reason: str
    magnitude: float = 1.0      # 调整幅度 (如仓位系数)
    triggered_by: str = ""
    triggered_at: str = ""
    expires_at: str = ""        # 调整过期时间


# ============================================================
# 实时监控引擎
# ============================================================

class IntradayMonitor:
    """盘中实时监控引擎

    三层监控架构:
    - FAST (5s): 止损止盈、熔断检测、关键位突破
    - MEDIUM (60s): 风格偏离、对冲有效性、仓位偏差
    - SLOW (300s): 板块轮动信号、宏观验证、资金流确认

    使用方式:
        monitor = IntradayMonitor()
        monitor.set_portfolio(portfolio_data)
        monitor.register_callback(on_adjustment)

        while trading:
            monitor.update_prices(price_dict)
            alerts = monitor.check()
            if alerts:
                handle_alerts(alerts)
            time.sleep(5)
    """

    # 默认阈值配置
    DEFAULT_THRESHOLDS = {
        # 快监控
        "single_stop_loss_pct": -0.08,        # 单票止损 -8%
        "single_take_profit_pct": 0.15,       # 单票止盈 +15%
        "portfolio_drawdown_fast": -0.03,     # 组合快熔断 -3%
        "limit_up_down": 0.095,               # 涨跌停阈值
        # 中监控
        "style_deviation_pct": 0.10,          # 风格偏离 ±10%
        "hedge_beta_deviation": 0.15,         # Beta 对冲偏离 ±15%
        "position_deviation_pct": 0.05,       # 仓位偏差 ±5%
        "portfolio_drawdown_medium": -0.05,   # 组合中熔断 -5%
        # 慢监控
        "sector_rotation_signal": 0.03,       # 板块轮动信号阈值
        "portfolio_drawdown_slow": -0.08,     # 组合慢熔断 -8%
        "max_single_sector_pct": 0.40,        # 单板块上限 40%
    }

    def __init__(
        self,
        thresholds: dict | None = None,
        initial_position_coef: float = 1.0,
    ):
        self.thresholds = {**self.DEFAULT_THRESHOLDS, **(thresholds or {})}

        # 持仓与价格
        self.positions: dict[str, PositionSnapshot] = {}
        self.portfolio_value: float = 0.0
        self.cost_basis: float = 0.0

        # 动态状态
        self.position_coef: float = initial_position_coef
        self.hedge_ratio: float = 0.0
        self.portfolio_beta: float = 1.0
        self.trading_paused: bool = False
        self.pause_reason: str = ""

        # 历史数据 (用于计算回撤/趋势)
        self._value_history: deque = deque(maxlen=240)  # 2小时 × 30次/小时
        self._peak_value: float = 0.0
        self._drawdown: float = 0.0

        # 回调函数
        self._callbacks: list[Callable] = []

        # 告警历史
        self.alerts: list[MonitorAlert] = []
        self.adjustments: list[DynamicAdjustment] = []

        # 时间戳控制
        self._last_fast_check: float = 0
        self._last_medium_check: float = 0
        self._last_slow_check: float = 0
        self._alert_counter: int = 0

        # 连续监控状态
        self.realtime_prices: dict[str, dict[str, Any]] = {}
        self.price_cache = RealtimePriceCache()
        self.watch_mode = False
        self.watch_interval_seconds = 60
        self.watch_max_rounds = 0
        self.watch_round = 0
        self.watch_errors: list[dict[str, Any]] = []
        self.source_priority = ["wind_mcp", "ifind_mcp", "sina_realtime"]

        logger.info("盘中监控引擎初始化完成, 仓位系数=%.2f", initial_position_coef)

    # ----------------------------------------------------------
    # 数据更新接口
    # ----------------------------------------------------------

    def set_portfolio(self, positions: list[dict], total_cost: float) -> None:
        """设置初始持仓

        Args:
            positions: [{"symbol": "600519.SH", "name": "贵州茅台",
                         "quantity": 100, "avg_cost": 1800.0,
                         "style": "高端制造"}, ...]
            total_cost: 总成本基准
        """
        self.cost_basis = total_cost
        self.positions = {}
        for p in positions:
            snap = PositionSnapshot(
                symbol=p["symbol"],
                name=p.get("name", p["symbol"]),
                quantity=int(p.get("quantity", 0)),
                avg_cost=float(p.get("avg_cost", 0)),
                style=p.get("style", ""),
            )
            self.positions[snap.symbol] = snap

        logger.info("设置持仓: %d 只标的, 总成本 ¥%s",
                    len(self.positions), f"{total_cost:,.0f}")

    # ----------------------------------------------------------
    # 实时价格采集
    # ----------------------------------------------------------

    def _normalize_code(self, code: str) -> str:
        code = str(code).strip()
        if "." in code:
            return code
        if code.startswith(('6', '5', '9')):
            return f"{code}.SH"
        if code.startswith(('0', '2', '3', '159', '16')):
            return f"{code}.SZ"
        if code.startswith('8'):
            return f"{code}.BJ"
        return f"{code}.SH"

    def _to_sina_code(self, code: str) -> str:
        code = self._normalize_code(code)
        num, suffix = code.split('.', 1)
        if suffix in ('SH', 'SS'):
            return f'sh{num}'
        if suffix == 'SZ':
            return f'sz{num}'
        return f'sz{num}'

    def _fetch_sina_realtime(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        if not codes:
            return {}
        sina_codes = [self._to_sina_code(c) for c in codes]
        url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
        try:
            import requests

            from utils.safe_url import validate_url

            url = validate_url(url)
            resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=15)
            if resp.status_code != 200:
                return {}
            text = resp.text
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return {}

        result: dict[str, dict[str, Any]] = {}
        for orig_code, sina_code in zip(codes, sina_codes, strict=True):
            prefix = f'hq_str_{sina_code}="'
            idx = text.find(prefix)
            if idx < 0:
                continue
            start = idx + len(prefix)
            end = text.find('"', start)
            if end < 0:
                continue
            content = text[start:end]
            if not content:
                continue
            fields = content.split(',')
            if len(fields) < 6:
                continue
            try:
                name = fields[0]
                open_price = float(fields[1])
                prev_close = float(fields[2])
                current = float(fields[3])
                high = float(fields[4])
                low = float(fields[5])
            except (ValueError, IndexError):
                continue
            if current <= 0 or prev_close <= 0:
                continue
            result[orig_code] = {
                "close": current,
                "prev_close": prev_close,
                "open": open_price,
                "high": high,
                "low": low,
                "change_pct": round((current - prev_close) / prev_close * 100, 2),
                "source": "sina_realtime",
                "name": name,
            }
        return result

    def _fetch_wind_realtime(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        try:
            from wind_mcp_fetcher import wind_get_quote
        except ImportError:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return {}
        result: dict[str, dict[str, Any]] = {}
        for code in codes:
            try:
                quote = wind_get_quote(code, is_fund=code.startswith(('51', '58', '15')))
                if quote and quote.get("price"):
                    result[code] = {
                        "close": quote.get("price"),
                        "prev_close": quote.get("prev_close"),
                        "open": quote.get("open"),
                        "high": quote.get("high"),
                        "low": quote.get("low"),
                        "change_pct": quote.get("change"),
                        "source": "wind_mcp",
                        "name": "",
                    }
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                continue
        return result

    def _build_quote_from_ifind_row(self, row: Any,
                                    cols: list[str]) -> tuple[str, dict[str, Any]] | None:
        """从 iFinD 行数据构建报价字典，无效行返回 None"""
        if isinstance(row, list) and cols:
            rec = dict(zip(cols, row))  # noqa: B905 - row 长度由外部数据源决定, 截断/补缺是预期
        else:
            rec = row if isinstance(row, dict) else {}
        code = rec.get("windcode") or rec.get("代码") or ""
        price = rec.get("最新成交价") or rec.get("最新价")
        prev = rec.get("前收盘价")
        if code and price and prev:
            return code, {
                "close": float(price),
                "prev_close": float(prev),
                "open": float(rec.get("今日开盘价") or 0) or None,
                "high": float(rec.get("今日最高价") or 0) or None,
                "low": float(rec.get("今日最低价") or 0) or None,
                "change_pct": float(rec.get("涨跌幅") or 0) or None,
                "source": "ifind_mcp",
                "name": rec.get("名称") or "",
            }
        return None

    def _fetch_quotes_from_ifind(self, client: Any, category: str, method: str,
                                 codes: list[str], indexes: str) -> dict[str, dict[str, Any]]:
        """从 iFinD 获取指定类别的报价数据并解析"""
        result: dict[str, dict[str, Any]] = {}
        if not codes:
            return result
        r = client.call(category, method, {"windcode": codes, "indexes": indexes})
        if not r.get("ok"):
            return result
        data = (((r.get("data") or {}).get("result") or {}).get("content") or [])
        if not data:
            return result
        inner = data[0].get("text") or ""
        try:
            payload = json.loads(inner)
            rows = ((payload.get("data") or payload).get("rows") or [])
            cols = [c.get("name") for c in ((payload.get("data") or payload).get("columns") or [])]
            for row in rows:
                quote = self._build_quote_from_ifind_row(row, cols)
                if quote:
                    code, quote_data = quote
                    result[code] = quote_data
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        return result

    def _fetch_ifind_realtime(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        try:
            from utils.ifind_client import IFindClient
        except ImportError:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return {}
        token = os.environ.get("IFIND_TOKEN", "")
        if not token:
            return {}
        # 安全修复: IFindClient 构造函数从环境变量自动读取 Token
        client = IFindClient(max_concurrency=2)
        result: dict[str, dict[str, Any]] = {}
        stock_codes = [c for c in codes if not c.startswith(('51', '58', '15'))]
        fund_codes = [c for c in codes if c.startswith(('51', '58', '15'))]
        indexes = "最新成交价,前收盘价,今日开盘价,今日最高价,今日最低价,涨跌幅"
        try:
            result.update(self._fetch_quotes_from_ifind(
                client, "stock", "get_stock_price_indicators", stock_codes, indexes))
            result.update(self._fetch_quotes_from_ifind(
                client, "fund", "get_fund_price_indicators", fund_codes, indexes))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        return result

    def _collect_realtime_prices(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        prices = self._fetch_wind_realtime(codes)
        source = "wind_mcp"
        if not prices:
            prices = self._fetch_ifind_realtime(codes)
            source = "ifind_mcp"
        if not prices:
            prices = self._fetch_sina_realtime(codes)
            source = "sina_realtime"
        if prices:
            for payload in prices.values():
                payload.setdefault("source", source)
        return prices

    def fetch_realtime_prices(self) -> dict[str, dict[str, Any]]:
        codes = []
        for code in (self.positions.get("positions", {}) or {}).keys():
            if not code:
                continue
            normalized = self._normalize_code(code)
            if normalized not in codes:
                codes.append(normalized)

        if not codes:
            return {}

        prices = self._collect_realtime_prices(codes)
        self.realtime_prices = prices
        self.price_cache.merge(prices)
        return prices

    def update_prices(self, prices: dict[str, float]) -> None:
        """更新实时价格

        Args:
            prices: {"600519.SH": 1850.0, "510300.SH": 4.20, ...}
        """
        total_value = 0.0
        for symbol, price in prices.items():
            if symbol not in self.positions:
                continue
            pos = self.positions[symbol]
            pos.current_price = price
            pos.market_value = pos.quantity * price
            if pos.avg_cost > 0:
                pos.pnl = (price - pos.avg_cost) * pos.quantity
                pos.pnl_pct = (price - pos.avg_cost) / pos.avg_cost
            total_value += pos.market_value

        self.portfolio_value = total_value

        # 更新历史与回撤
        self._value_history.append((time.time(), total_value))
        if total_value > self._peak_value:
            self._peak_value = total_value
        if self._peak_value > 0:
            self._drawdown = (total_value - self._peak_value) / self._peak_value

    def update_hedge_info(self, hedge_ratio: float, portfolio_beta: float) -> None:
        """更新对冲状态"""
        self.hedge_ratio = hedge_ratio
        self.portfolio_beta = portfolio_beta

    # ----------------------------------------------------------
    # 回调注册
    # ----------------------------------------------------------

    def register_callback(self, callback: Callable) -> None:
        """注册调整回调

        callback(adjustment: DynamicAdjustment, alert: MonitorAlert)
        """
        self._callbacks.append(callback)

    def _fire_callbacks(self, adjustment: DynamicAdjustment,
                        alert: MonitorAlert) -> None:
        for cb in self._callbacks:
            try:
                cb(adjustment, alert)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.error("回调执行失败: %s", e)

    # ----------------------------------------------------------
    # 主检查循环
    # ----------------------------------------------------------

    def check(self) -> list[MonitorAlert]:
        """执行一次检查 (建议每 1-5 秒调用一次)

        Returns:
            本次触发的告警列表
        """
        now = time.time()
        all_alerts: list[MonitorAlert] = []

        # 快监控 (5s)
        if now - self._last_fast_check >= 5:
            alerts = self._check_fast()
            all_alerts.extend(alerts)
            self._last_fast_check = now

        # 中监控 (60s)
        if now - self._last_medium_check >= 60:
            alerts = self._check_medium()
            all_alerts.extend(alerts)
            self._last_medium_check = now

        # 慢监控 (300s)
        if now - self._last_slow_check >= 300:
            alerts = self._check_slow()
            all_alerts.extend(alerts)
            self._last_slow_check = now

        # 处理告警, 生成调整决策
        for alert in all_alerts:
            self.alerts.append(alert)
            adjustment = self._alert_to_adjustment(alert)
            if adjustment:
                self.adjustments.append(adjustment)
                self._fire_callbacks(adjustment, alert)
                self._apply_adjustment(adjustment)

        return all_alerts

    # ----------------------------------------------------------
    # 快监控 (5s)
    # ----------------------------------------------------------

    def _check_fast(self) -> list[MonitorAlert]:
        """快监控: 止损止盈 + 熔断 + 涨跌停"""
        alerts: list[MonitorAlert] = []

        if self.portfolio_value == 0:
            return alerts

        # 1. 单票止损止盈
        for symbol, pos in self.positions.items():
            if pos.avg_cost <= 0 or pos.quantity <= 0:
                continue

            # 止损
            if pos.pnl_pct <= self.thresholds["single_stop_loss_pct"]:
                alerts.append(self._make_alert(
                    "critical", "pnl",
                    f"{pos.name}({symbol}) 触发止损: {pos.pnl_pct:.1%}",
                    symbol=symbol, current_value=pos.pnl_pct,
                    threshold=self.thresholds["single_stop_loss_pct"],
                    action=AdjustmentAction.REDUCE,
                ))

            # 止盈
            if pos.pnl_pct >= self.thresholds["single_take_profit_pct"]:
                alerts.append(self._make_alert(
                    "warning", "pnl",
                    f"{pos.name}({symbol}) 触发止盈: {pos.pnl_pct:.1%}",
                    symbol=symbol, current_value=pos.pnl_pct,
                    threshold=self.thresholds["single_take_profit_pct"],
                    action=AdjustmentAction.REDUCE,
                ))

        # 2. 组合快熔断
        if self._drawdown <= self.thresholds["portfolio_drawdown_fast"]:
            alerts.append(self._make_alert(
                "critical", "risk",
                f"组合回撤 {self._drawdown:.1%}, 触发快熔断 "
                f"(阈值 {self.thresholds['portfolio_drawdown_fast']:.0%})",
                current_value=self._drawdown,
                threshold=self.thresholds["portfolio_drawdown_fast"],
                action=AdjustmentAction.PAUSE,
            ))

        return alerts

    # ----------------------------------------------------------
    # 中监控 (60s)
    # ----------------------------------------------------------

    def _check_medium(self) -> list[MonitorAlert]:
        """中监控: 风格偏离 + 对冲有效性 + 仓位偏差"""
        alerts: list[MonitorAlert] = []

        if self.portfolio_value == 0:
            return alerts

        # 1. 风格偏离检测
        style_deviation = self._calc_style_deviation()
        for style, dev in style_deviation.items():
            if abs(dev) > self.thresholds["style_deviation_pct"]:
                direction = "超配" if dev > 0 else "低配"
                alerts.append(self._make_alert(
                    "warning", "style",
                    f"{style} {direction} {abs(dev):.1%}",
                    current_value=dev,
                    threshold=self.thresholds["style_deviation_pct"],
                ))

        # 2. 对冲 Beta 偏离
        target_beta = 0.3  # 目标 Beta
        beta_dev = abs(self.portfolio_beta - target_beta)
        if beta_dev > self.thresholds["hedge_beta_deviation"]:
            action = (AdjustmentAction.HEDGE_UP
                      if self.portfolio_beta > target_beta
                      else AdjustmentAction.HEDGE_DOWN)
            alerts.append(self._make_alert(
                "warning", "hedge",
                f"组合 Beta {self.portfolio_beta:.2f} 偏离目标 {target_beta:.2f} "
                f"(偏差 {beta_dev:.2f})",
                current_value=self.portfolio_beta,
                threshold=target_beta,
                action=action,
            ))

        # 3. 组合中熔断
        if self._drawdown <= self.thresholds["portfolio_drawdown_medium"]:
            alerts.append(self._make_alert(
                "critical", "risk",
                f"组合回撤 {self._drawdown:.1%}, 触发中熔断",
                current_value=self._drawdown,
                threshold=self.thresholds["portfolio_drawdown_medium"],
                action=AdjustmentAction.PAUSE,
            ))

        return alerts

    # ----------------------------------------------------------
    # 慢监控 (5min)
    # ----------------------------------------------------------

    def _check_slow(self) -> list[MonitorAlert]:
        """慢监控: 板块集中度 + 慢熔断 + 趋势确认"""
        alerts: list[MonitorAlert] = []

        if self.portfolio_value == 0:
            return alerts

        # 1. 板块集中度
        style_values = self._calc_style_values()
        for style, value in style_values.items():
            pct = value / self.portfolio_value if self.portfolio_value > 0 else 0
            if pct > self.thresholds["max_single_sector_pct"]:
                alerts.append(self._make_alert(
                    "warning", "style",
                    f"{style} 集中度 {pct:.1%}, 超过上限 "
                    f"{self.thresholds['max_single_sector_pct']:.0%}",
                    current_value=pct,
                    threshold=self.thresholds["max_single_sector_pct"],
                ))

        # 2. 组合慢熔断
        if self._drawdown <= self.thresholds["portfolio_drawdown_slow"]:
            alerts.append(self._make_alert(
                "critical", "risk",
                f"组合回撤 {self._drawdown:.1%}, 触发慢熔断 (全面减仓)",
                current_value=self._drawdown,
                threshold=self.thresholds["portfolio_drawdown_slow"],
                action=AdjustmentAction.REDUCE,
            ))

        # 3. 趋势判断 (用于动态调整仓位系数)
        trend = self._detect_trend()
        if trend == "strong_up":
            alerts.append(self._make_alert(
                "info", "event",
                "检测到强势上涨趋势, 考虑加速建仓",
                action=AdjustmentAction.ACCELERATE,
            ))
        elif trend == "strong_down":
            alerts.append(self._make_alert(
                "warning", "event",
                "检测到强势下跌趋势, 减速建仓",
                action=AdjustmentAction.DECELERATE,
            ))

        return alerts

    # ----------------------------------------------------------
    # 辅助计算
    # ----------------------------------------------------------

    def _calc_style_values(self) -> dict[str, float]:
        """计算各风格市值"""
        style_values: dict[str, float] = {}
        for pos in self.positions.values():
            style = pos.style or "unknown"
            style_values[style] = style_values.get(style, 0) + pos.market_value
        return style_values

    def _calc_style_deviation(self) -> dict[str, float]:
        """计算风格偏离 (简单实现, 实际需配置目标风格)"""
        # TODO: 从配置读取目标风格权重
        style_values = self._calc_style_values()
        total = sum(style_values.values()) or 1
        deviation = {}
        for style, value in style_values.items():
            current_pct = value / total
            # 默认目标: 平均分配 (实际应从配置读取)
            target_pct = 1.0 / max(len(style_values), 1)
            deviation[style] = current_pct - target_pct
        return deviation

    def _detect_trend(self) -> str:
        """简单趋势检测 (基于历史净值)

        Returns:
            "strong_up" / "up" / "neutral" / "down" / "strong_down"
        """
        if len(self._value_history) < 10:
            return "neutral"

        values = [v for _, v in self._value_history]
        if len(values) < 2:
            return "neutral"

        first = values[0]
        last = values[-1]
        if first == 0:
            return "neutral"

        change = (last - first) / first
        if change > 0.02:
            return "strong_up"
        if change > 0.005:
            return "up"
        if change < -0.02:
            return "strong_down"
        if change < -0.005:
            return "down"
        return "neutral"

    def _make_alert(
        self,
        level: str,
        category: str,
        message: str,
        symbol: str = "",
        current_value: float = 0.0,
        threshold: float = 0.0,
        action: AdjustmentAction | None = None,
    ) -> MonitorAlert:
        """创建告警"""
        self._alert_counter += 1
        return MonitorAlert(
            alert_id=f"ALT_{self._alert_counter:06d}",
            level=level,
            category=category,
            message=message,
            timestamp=now_bj().isoformat(),
            symbol=symbol,
            current_value=current_value,
            threshold=threshold,
            action=action,
        )

    def _alert_to_adjustment(self, alert: MonitorAlert) -> DynamicAdjustment | None:
        """根据告警生成调整决策"""
        if alert.action is None:
            return None

        now = now_bj()
        # 调整默认持续 30 分钟
        expires = now.timestamp() + 1800

        return DynamicAdjustment(
            action=alert.action,
            reason=alert.message,
            magnitude=self._calc_adjustment_magnitude(alert),
            triggered_by=alert.alert_id,
            triggered_at=now.isoformat(),
            expires_at=datetime.fromtimestamp(expires).isoformat(),
        )

    def _calc_adjustment_magnitude(self, alert: MonitorAlert) -> float:
        """计算调整幅度"""
        if alert.level == "critical":
            return 0.5 if alert.action == AdjustmentAction.REDUCE else 0.0
        if alert.level == "warning":
            return 0.8
        return 1.0

    def _apply_adjustment(self, adj: DynamicAdjustment) -> None:
        """应用调整决策"""
        if adj.action == AdjustmentAction.PAUSE:
            self.trading_paused = True
            self.pause_reason = adj.reason
            self.position_coef = 0.0
            logger.warning("交易暂停: %s", adj.reason)

        elif adj.action == AdjustmentAction.RESUME:
            self.trading_paused = False
            self.pause_reason = ""
            self.position_coef = 1.0
            logger.info("交易恢复")

        elif adj.action == AdjustmentAction.ACCELERATE:
            if not self.trading_paused:
                self.position_coef = min(1.5, self.position_coef * 1.2)
                logger.info("加速建仓: 仓位系数 → %.2f", self.position_coef)

        elif adj.action == AdjustmentAction.DECELERATE:
            if not self.trading_paused:
                self.position_coef = max(0.3, self.position_coef * 0.8)
                logger.info("减速建仓: 仓位系数 → %.2f", self.position_coef)

        elif adj.action == AdjustmentAction.REDUCE:
            self.position_coef = max(0.0, self.position_coef * adj.magnitude)
            logger.warning("减仓信号: 仓位系数 → %.2f (原因: %s)",
                           self.position_coef, adj.reason)

    # ----------------------------------------------------------
    # 状态查询
    # ----------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """获取当前监控状态"""
        return {
            "portfolio_value": round(self.portfolio_value, 2),
            "cost_basis": round(self.cost_basis, 2),
            "total_pnl": round(self.portfolio_value - self.cost_basis, 2),
            "total_pnl_pct": (
                round((self.portfolio_value - self.cost_basis) / self.cost_basis, 4)
                if self.cost_basis > 0 else 0
            ),
            "drawdown": round(self._drawdown, 4),
            "peak_value": round(self._peak_value, 2),
            "position_coef": round(self.position_coef, 2),
            "hedge_ratio": round(self.hedge_ratio, 4),
            "portfolio_beta": round(self.portfolio_beta, 4),
            "trading_paused": self.trading_paused,
            "pause_reason": self.pause_reason,
            "alert_count_today": len(self.alerts),
            "adjustment_count_today": len(self.adjustments),
            "positions_count": len(self.positions),
        }

    def get_position_pnl(self, symbol: str) -> dict | None:
        """获取单票盈亏"""
        pos = self.positions.get(symbol)
        if not pos:
            return None
        return {
            "symbol": pos.symbol,
            "name": pos.name,
            "quantity": pos.quantity,
            "avg_cost": pos.avg_cost,
            "current_price": pos.current_price,
            "market_value": pos.market_value,
            "pnl": pos.pnl,
            "pnl_pct": pos.pnl_pct,
            "style": pos.style,
        }

    def get_all_position_pnl(self) -> list[dict]:
        """获取所有持仓盈亏"""
        return [self.get_position_pnl(s) for s in self.positions]


    # ----------------------------------------------------------
    # 连续监控与文件输出
    # ----------------------------------------------------------

    def run(self) -> dict[str, Any]:
        now = now_bj()
        trade_date = now.strftime("%Y-%m-%d")
        date_compact = trade_date.replace("-", "")
        reports_dir = _BASE / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        out_path = reports_dir / f"intraday_monitor_{date_compact}.json"
        reports_dir / f"intraday_monitor_series_{date_compact}.json"

        self.fetch_realtime_prices()
        alerts = self.check()
        status = self.get_status()
        payload = {
            "trade_date": trade_date,
            "generated_at": now.isoformat(),
            "module": "intraday_monitor",
            "status": "PASS",
            "watch_mode": self.watch_mode,
            "summary": {
                "portfolio_value": status.get("portfolio_value"),
                "total_pnl_pct": status.get("total_pnl_pct"),
                "drawdown": status.get("drawdown"),
                "position_coef": status.get("position_coef"),
                "realtime_price_count": len(self.realtime_prices),
                "alert_count": len(alerts),
                "adjustment_count": len(self.adjustments),
                "trading_paused": status.get("trading_paused"),
                "pause_reason": status.get("pause_reason"),
            },
            "alerts": [
                {
                    "alert_id": a.alert_id,
                    "level": a.level,
                    "category": a.category,
                    "message": a.message,
                    "timestamp": a.timestamp,
                    "symbol": a.symbol,
                    "current_value": a.current_value,
                    "threshold": a.threshold,
                    "action": a.action.value if a.action else None,
                }
                for a in self.alerts
            ],
            "adjustments": [
                {
                    "action": adj.action.value,
                    "reason": adj.reason,
                    "magnitude": adj.magnitude,
                    "triggered_by": adj.triggered_by,
                    "triggered_at": adj.triggered_at,
                    "expires_at": adj.expires_at,
                }
                for adj in self.adjustments
            ],
            "realtime_prices": self.realtime_prices,
            "realtime_price_series": {k: v[-50:] for k, v in self.price_cache.series.items()},
            "notes": [
                "实时行情优先级: Wind MCP -> iFinD MCP -> 新浪实时价",
                "watch 模式会持续轮询，并将价格序列写入 intraday_monitor_series_*.json",
            ],
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("已写入盘中监控: %s", out_path)
        return {"status": "PASS", "path": str(out_path), "summary": payload["summary"]}

    def run_watch(self) -> dict[str, Any]:
        """连续监控：按 interval 轮询多轮，记录价格序列"""
        now = now_bj()
        trade_date = now.strftime("%Y-%m-%d")
        date_compact = trade_date.replace("-", "")
        reports_dir = _BASE / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        series_path = reports_dir / f"intraday_monitor_series_{date_compact}.json"

        self.watch_mode = True
        self.watch_round = 0
        last_result: dict[str, Any] = {}

        while True:
            self.watch_round += 1
            try:
                self.fetch_realtime_prices()
                alerts = self.check()
                status = self.get_status()
                payload = {
                    "trade_date": trade_date,
                    "generated_at": now.isoformat(),
                    "module": "intraday_monitor",
                    "status": "PASS",
                    "watch_mode": self.watch_mode,
                    "round_index": self.watch_round,
                    "summary": {
                        "portfolio_value": status.get("portfolio_value"),
                        "total_pnl_pct": status.get("total_pnl_pct"),
                        "drawdown": status.get("drawdown"),
                        "position_coef": status.get("position_coef"),
                        "realtime_price_count": len(self.realtime_prices),
                        "alert_count": len(alerts),
                        "adjustment_count": len(self.adjustments),
                        "trading_paused": status.get("trading_paused"),
                        "pause_reason": status.get("pause_reason"),
                    },
                    "alerts": [
                        {
                            "alert_id": a.alert_id,
                            "level": a.level,
                            "category": a.category,
                            "message": a.message,
                            "timestamp": a.timestamp,
                            "symbol": a.symbol,
                            "current_value": a.current_value,
                            "threshold": a.threshold,
                            "action": a.action.value if a.action else None,
                        }
                        for a in self.alerts
                    ],
                    "adjustments": [
                        {
                            "action": adj.action.value,
                            "reason": adj.reason,
                            "magnitude": adj.magnitude,
                            "triggered_by": adj.triggered_by,
                            "triggered_at": adj.triggered_at,
                            "expires_at": adj.expires_at,
                        }
                        for adj in self.adjustments
                    ],
                    "realtime_prices": self.realtime_prices,
                    "realtime_price_series": {k: v[-50:] for k, v in self.price_cache.series.items()},
                    "notes": [
                        "实时行情优先级: Wind MCP -> iFinD MCP -> 新浪实时价",
                        "watch 模式会持续轮询，并将价格序列写入 intraday_monitor_series_*.json",
                    ],
                }
                out_path = reports_dir / f"intraday_monitor_{date_compact}.json"
                out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                last_result = {
                    "status": "PASS",
                    "path": str(out_path),
                    "summary": payload["summary"],
                    "round": self.watch_round,
                }
                logger.info(
                    "[watch] round=%s prices=%s alerts=%s adjustments=%s",
                    self.watch_round,
                    len(self.realtime_prices),
                    len(alerts),
                    len(self.adjustments),
                )
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                self.watch_errors.append({
                    "round": self.watch_round,
                    "ts": now_bj().isoformat(),
                    "error": str(exc),
                })
                logger.error("[watch] round=%s 异常: %s", self.watch_round, exc, exc_info=True)

            if self.watch_max_rounds and self.watch_round >= self.watch_max_rounds:
                break
            if self.watch_interval_seconds <= 0:
                break
            try:
                time.sleep(self.watch_interval_seconds)
            except KeyboardInterrupt:
                break

        series_payload = {
            "trade_date": trade_date,
            "generated_at": now_bj().isoformat(),
            "module": "intraday_monitor_series",
            "count": len(self.price_cache.series),
            "series": self.price_cache.series,
            "watch_errors": self.watch_errors,
        }
        series_path.write_text(json.dumps(series_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("已写入连续价格序列: %s", series_path)
        return last_result or {"status": "PASS"}


__all__ = [
    "AdjustmentAction",
    "DynamicAdjustment",
    "IntradayMonitor",
    "MonitorAlert",
    "MonitorLevel",
    "PositionSnapshot",
    "RealtimePriceCache",
]
