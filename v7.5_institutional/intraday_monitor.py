# -*- coding: utf-8 -*-
"""
盘中监控与动态调整模块 — v2.0
=================================

目标:
  - 读取当前持仓、AI 确认结果、动态风控结果；
  - 接入实时行情：Wind MCP -> iFinD MCP -> 新浪实时价；
  - 输出盘中监控摘要、风险事件和可执行的动态调整建议；
  - v2.0: 支持连续监控、定时轮询、异常恢复与价格序列缓存。

输入:
  - config/positions.json
  - trade_instructions/ai_approved_YYYY-MM-DD.json
  - reports/dynamic_risk_YYYY-MM-DD.json
输出:
  - reports/intraday_monitor_YYYY-MM-DD.json
  - reports/intraday_monitor_series_YYYY-MM-DD.json
"""

from __future__ import annotations

import json
import os
import sys
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_BASE = Path(__file__).resolve().parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

try:
    from utils.logger import get_logger
    logger = get_logger("intraday_monitor")
except Exception:
    logger = logging.getLogger("intraday_monitor")


class RealtimePriceCache:
    """连续监控时的实时价格缓存

    保存每次采集的快照，避免覆盖上一轮数据。
    """

    def __init__(self) -> None:
        self.series: Dict[str, List[Dict[str, Any]]] = {}
        self.latest: Dict[str, Dict[str, Any]] = {}

    def merge(self, prices: Dict[str, Dict[str, Any]]) -> None:
        now = datetime.now().isoformat()
        for code, payload in prices.items():
            entry = {"ts": now, **payload}
            self.latest[code] = payload
            self.series.setdefault(code, []).append(entry)

    def latest_prices(self) -> Dict[str, Dict[str, Any]]:
        return dict(self.latest)


class IntradayMonitor:
    """盘中监控器"""

    def __init__(self, trade_date: Optional[str] = None):
        self.trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        self.date_compact = self.trade_date.replace("-", "")
        self.timestamp = datetime.now().isoformat()

        self.project_root = _BASE.parent
        self.positions_path = self.project_root / "config" / "positions.json"
        self.approved_path = self.project_root.parent / "trade_instructions" / f"ai_approved_{self.date_compact}.json"
        self.dynamic_risk_path = _BASE / "reports" / f"dynamic_risk_{self.date_compact}.json"
        self.out_path = _BASE / "reports" / f"intraday_monitor_{self.date_compact}.json"
        self.series_path = _BASE / "reports" / f"intraday_monitor_series_{self.date_compact}.json"

        self.positions: Dict[str, Any] = {}
        self.approved: Dict[str, Any] = {}
        self.dynamic_risk: Dict[str, Any] = {}
        self.realtime_prices: Dict[str, Dict[str, Any]] = {}
        self.price_cache = RealtimePriceCache()
        self.source_priority = ["wind_mcp", "ifind_mcp", "sina_realtime"]
        self.watch_mode = False
        self.watch_interval_seconds = 60
        self.watch_max_rounds = 0
        self.watch_round = 0
        self.watch_errors: List[Dict[str, Any]] = []

    def load_positions(self) -> bool:
        if not self.positions_path.exists():
            logger.warning("缺少 positions.json")
            return False
        try:
            self.positions = json.loads(self.positions_path.read_text(encoding="utf-8"))
            return True
        except Exception as e:
            logger.warning(f"读取 positions.json 失败: {e}")
            return False

    def load_approved(self) -> bool:
        if not self.approved_path.exists():
            logger.warning("缺少 ai_approved 文件")
            return False
        try:
            self.approved = json.loads(self.approved_path.read_text(encoding="utf-8"))
            return True
        except Exception as e:
            logger.warning(f"读取 ai_approved 文件失败: {e}")
            return False

    def load_dynamic_risk(self) -> bool:
        if not self.dynamic_risk_path.exists():
            logger.warning("缺少 dynamic_risk 文件")
            return False
        try:
            self.dynamic_risk = json.loads(self.dynamic_risk_path.read_text(encoding="utf-8"))
            return True
        except Exception as e:
            logger.warning(f"读取 dynamic_risk 文件失败: {e}")
            return False

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

    def _fetch_sina_realtime(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        if not codes:
            return {}
        sina_codes = [self._to_sina_code(c) for c in codes]
        url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
        try:
            import requests
            resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=15)
            if resp.status_code != 200:
                return {}
            text = resp.text
        except Exception:
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        for orig_code, sina_code in zip(codes, sina_codes):
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

    def _fetch_wind_realtime(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        try:
            from wind_mcp_fetcher import wind_get_quote
        except Exception:
            return {}
        result: Dict[str, Dict[str, Any]] = {}
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
            except Exception:
                continue
        return result

    def _fetch_ifind_realtime(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        try:
            from utils.ifind_client import IFindClient
        except Exception:
            return {}
        token = os.environ.get("IFIND_TOKEN", "")
        if not token:
            return {}
        client = IFindClient(auth_token=token, max_concurrency=2)
        result: Dict[str, Dict[str, Any]] = {}
        stock_codes = [c for c in codes if not c.startswith(('51', '58', '15'))]
        fund_codes = [c for c in codes if c.startswith(('51', '58', '15'))]
        try:
            if stock_codes:
                r = client.call("stock", "get_stock_price_indicators", {"windcode": stock_codes, "indexes": "最新成交价,前收盘价,今日开盘价,今日最高价,今日最低价,涨跌幅"})
                if r.get("ok"):
                    data = (((r.get("data") or {}).get("result") or {}).get("content") or [])
                    if data:
                        inner = data[0].get("text") or ""
                        try:
                            payload = json.loads(inner)
                            rows = ((payload.get("data") or payload).get("rows") or [])
                            cols = [c.get("name") for c in (((payload.get("data") or payload).get("columns") or []))]
                            for row in rows:
                                if isinstance(row, list) and cols:
                                    rec = dict(zip(cols, row))
                                else:
                                    rec = row if isinstance(row, dict) else {}
                                code = rec.get("windcode") or rec.get("代码") or ""
                                price = rec.get("最新成交价") or rec.get("最新价")
                                prev = rec.get("前收盘价")
                                if code and price and prev:
                                    result[code] = {
                                        "close": float(price),
                                        "prev_close": float(prev),
                                        "open": float(rec.get("今日开盘价") or 0) or None,
                                        "high": float(rec.get("今日最高价") or 0) or None,
                                        "low": float(rec.get("今日最低价") or 0) or None,
                                        "change_pct": float(rec.get("涨跌幅") or 0) or None,
                                        "source": "ifind_mcp",
                                        "name": rec.get("名称") or "",
                                    }
                        except Exception:
                            pass
            if fund_codes:
                r = client.call("fund", "get_fund_price_indicators", {"windcode": fund_codes, "indexes": "最新成交价,前收盘价,今日开盘价,今日最高价,今日最低价,涨跌幅"})
                if r.get("ok"):
                    data = (((r.get("data") or {}).get("result") or {}).get("content") or [])
                    if data:
                        inner = data[0].get("text") or ""
                        try:
                            payload = json.loads(inner)
                            rows = ((payload.get("data") or payload).get("rows") or [])
                            cols = [c.get("name") for c in (((payload.get("data") or payload).get("columns") or []))]
                            for row in rows:
                                if isinstance(row, list) and cols:
                                    rec = dict(zip(cols, row))
                                else:
                                    rec = row if isinstance(row, dict) else {}
                                code = rec.get("windcode") or rec.get("代码") or ""
                                price = rec.get("最新成交价") or rec.get("最新价")
                                prev = rec.get("前收盘价")
                                if code and price and prev:
                                    result[code] = {
                                        "close": float(price),
                                        "prev_close": float(prev),
                                        "open": float(rec.get("今日开盘价") or 0) or None,
                                        "high": float(rec.get("今日最高价") or 0) or None,
                                        "low": float(rec.get("今日最低价") or 0) or None,
                                        "change_pct": float(rec.get("涨跌幅") or 0) or None,
                                        "source": "ifind_mcp",
                                        "name": rec.get("名称") or "",
                                    }
                        except Exception:
                            pass
        except Exception:
            pass
        return result

    def _collect_realtime_prices(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
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

    def fetch_realtime_prices(self) -> Dict[str, Dict[str, Any]]:
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

    def analyze(self, round_index: Optional[int] = None) -> Dict[str, Any]:
        positions = self.positions.get("positions", {}) or {}
        approved_orders = self.approved.get("approved_instructions", []) or []
        adjusted_limits = self.dynamic_risk.get("adjusted_limits", {}) if isinstance(self.dynamic_risk, dict) else {}

        self.fetch_realtime_prices()

        pending_codes = []
        pending_amount = 0.0
        for order in approved_orders:
            code = order.get("code") or order.get("full_code") or ""
            if code:
                pending_codes.append(self._normalize_code(code))
            pending_amount += float(order.get("amount", 0) or 0)

        risk_events: List[Dict[str, Any]] = []
        for code, pos in positions.items():
            pos_amount = float(pos.get("amount", 0) or 0)
            if pos_amount <= 0:
                continue
            concentration = 0.0
            try:
                concentration = float(pos.get("target_weight", 0) or 0)
            except Exception:
                concentration = 0.0
            max_concentration = float(adjusted_limits.get("max_position_concentration", 0.30) or 0.30)
            if concentration > max_concentration:
                risk_events.append({
                    "code": code,
                    "name": pos.get("name"),
                    "type": "concentration",
                    "detail": f"权重 {concentration:.2%} 超过上限 {max_concentration:.2%}",
                })

            rt = self.realtime_prices.get(self._normalize_code(code))
            if rt:
                change_pct = float(rt.get("change_pct", 0) or 0)
                if change_pct <= -5:
                    risk_events.append({
                        "code": code,
                        "name": pos.get("name"),
                        "type": "intraday_drop",
                        "detail": f"实时跌幅 {change_pct:.2f}%，触发盘中预警",
                    })

        adjustments: List[Dict[str, Any]] = []
        if pending_amount > float(adjusted_limits.get("max_total_amount", 200000) or 200000):
            adjustments.append({
                "type": "reduce_total_amount",
                "detail": f"待执行总额 {pending_amount:,.0f} 超过动态上限，建议分批或缩额",
            })
        if risk_events:
            adjustments.append({
                "type": "trim_concentration",
                "detail": f"发现 {len(risk_events)} 个风险事件，建议减持或对冲",
            })

        monitor_ts = datetime.now().isoformat()
        return {
            "trade_date": self.trade_date,
            "generated_at": self.timestamp,
            "monitor_ts": monitor_ts,
            "round_index": round_index,
            "module": "intraday_monitor",
            "status": "PASS",
            "summary": {
                "position_count": len(positions),
                "pending_order_count": len(approved_orders),
                "pending_amount": pending_amount,
                "realtime_price_count": len(self.realtime_prices),
                "risk_event_count": len(risk_events),
                "dynamic_limits": adjusted_limits,
            },
            "risk_events": risk_events,
            "adjustments": adjustments,
            "realtime_prices": self.realtime_prices,
            "realtime_price_series": {k: v[-50:] for k, v in self.price_cache.series.items()},
            "notes": [
                "实时行情优先级: Wind MCP -> iFinD MCP -> 新浪实时价",
                "若实时价获取失败，仍可基于持仓快照生成监控建议",
                "watch 模式会持续轮询，并将价格序列写入 intraday_monitor_series_*.json",
            ],
        }

    def save(self, payload: Dict[str, Any]) -> Path:
        self.out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"已写入盘中监控: {self.out_path}")
        return self.out_path

    def save_series(self) -> Optional[Path]:
        if not self.price_cache.series:
            return None
        payload = {
            "trade_date": self.trade_date,
            "generated_at": datetime.now().isoformat(),
            "module": "intraday_monitor_series",
            "count": len(self.price_cache.series),
            "series": self.price_cache.series,
            "watch_errors": self.watch_errors,
        }
        self.series_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"已写入连续价格序列: {self.series_path}")
        return self.series_path

    def run(self) -> Dict[str, Any]:
        self.load_positions()
        self.load_approved()
        self.load_dynamic_risk()
        result = self.analyze()
        self.save(result)
        self.save_series()
        return {"status": result.get("status"), "path": str(self.out_path), "summary": result.get("summary")}

    def run_watch(self) -> Dict[str, Any]:
        """连续监控：按 interval 轮询多轮，记录价格序列"""

        self.load_positions()
        self.load_approved()
        self.load_dynamic_risk()
        self.watch_mode = True
        self.watch_round = 0
        last_result: Dict[str, Any] = {}

        while True:
            self.watch_round += 1
            try:
                result = self.analyze(round_index=self.watch_round)
                self.save(result)
                self.save_series()
                last_result = {
                    "status": result.get("status"),
                    "path": str(self.out_path),
                    "summary": result.get("summary"),
                    "round": self.watch_round,
                }
                logger.info(
                    "[watch] round=%s prices=%s risk_events=%s",
                    self.watch_round,
                    result.get("summary", {}).get("realtime_price_count", 0),
                    result.get("summary", {}).get("risk_event_count", 0),
                )
            except Exception as exc:
                self.watch_errors.append({
                    "round": self.watch_round,
                    "ts": datetime.now().isoformat(),
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

        return last_result or {"status": "PASS"}


def run_intraday_monitor(trade_date: Optional[str] = None, watch: bool = False, interval_seconds: int = 60, max_rounds: int = 0) -> Dict[str, Any]:
    monitor = IntradayMonitor(trade_date=trade_date)
    monitor.watch_interval_seconds = max(5, int(interval_seconds))
    monitor.watch_max_rounds = int(max_rounds) if int(max_rounds) > 0 else 0
    if watch:
        return monitor.run_watch()
    return monitor.run()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="盘中监控与动态调整")
    parser.add_argument("--date", default=None, help="交易日期 YYYY-MM-DD")
    parser.add_argument("--watch", action="store_true", help="连续监控模式")
    parser.add_argument("--interval", type=int, default=60, help="连续监控轮询间隔秒数")
    parser.add_argument("--max-rounds", type=int, default=0, help="连续监控最大轮次，0 表示无限")
    args = parser.parse_args()
    result = run_intraday_monitor(
        trade_date=args.date,
        watch=args.watch,
        interval_seconds=args.interval,
        max_rounds=args.max_rounds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
