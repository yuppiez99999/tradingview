"""成交回报统一落盘层 (G2 缺口补齐 — fills 持久化)

此前 `automated_execution_system.OrderRouter.process_execution_queue` 在执行
(`smart_router.execute_route`) 后只把 avg_price 算进内存 return dict 即丢弃,
系统虽已具备撮合能力, 但没有任何统一记录成交回报的位置, 导致:
  - G4 成交驱动 PnL 无从消费真实成交价
  - 任何订单生命周期追溯在成交环节断链

本模块提供进程内单例 `FillsStore`, 把每一笔成交 (live / 模拟 双路径) 追加落盘到
`reports/fills/fills_{date}.jsonl`, 每行为一条独立 JSON 记录, 供 FillsPnLBridge
与 TCA 归因复用。设计为 fail-open: 落盘失败只记日志, 绝不阻断执行链路。
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from collections.abc import Collection
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger(__name__)

# 动态解析项目根目录 (utils/execution/ -> 项目根)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

_FILLS_DIR = _PROJECT_ROOT / "reports" / "fills"


class FillsStore:
    """进程内单例成交回报存储。

    线程安全 (写入加锁), 按交易日分文件。每条记录字段:
      ts            ISO 时间戳
      date          交易日 YYYY-MM-DD
      symbol        标的代码 (纯数字或带后缀)
      side          BUY / SELL
      filled_qty    成交数量 (股)
      avg_price     成交均价
      broker        执行场所 / 模拟池名
      is_live       是否实盘
      strategy      策略/订单来源标签
      source        落盘来源 ('live_route' / 'sim_route')
      meta          额外上下文 (滑点/延迟/venue 数等)
    """

    _instance: FillsStore | None = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._write_lock = threading.Lock()
        self._buffer: dict[str, list[dict[str, Any]]] = {}  # date -> records

    # ------------------------------------------------------------------ #
    # 公共 API
    # ------------------------------------------------------------------ #
    def record_fill(
        self,
        symbol: str,
        side: str,
        filled_qty: float,
        avg_price: float,
        *,
        broker: str = "unknown",
        is_live: bool = False,
        strategy: str = "rebalance",
        source: str = "sim_route",
        date: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """记录一笔成交并落盘。返回记录 dict (便于调用方复用)。

        fail-open: 任何异常只记日志, 不影响执行链路。
        """
        rec_date = date or now_bj().strftime("%Y-%m-%d")
        record = {
            "ts": now_bj().isoformat(timespec="seconds"),
            "date": rec_date,
            "symbol": symbol,
            "side": side,
            "filled_qty": float(filled_qty),
            "avg_price": round(float(avg_price), 4),
            "broker": broker,
            "is_live": bool(is_live),
            "strategy": strategy,
            "source": source,
            "meta": meta or {},
        }
        with self._write_lock:
            try:
                self._append_to_file(rec_date, record)
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
                # 落盘失败时保留在内存缓冲区, 供 load_day 作为兜底合并, 不丢记录。
                logger.warning("[FillsStore] 落盘失败 (内存保留): %s", e)
                self._buffer.setdefault(rec_date, []).append(record)
        return record

    def load_day(
        self,
        date: str | None = None,
        strategies: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        """读取某交易日全部成交 (文件为事实源, 内存仅含落盘失败兜底记录)。

        G4 修复 (2026-08-08): 此前把内存 buffer 与文件合并, 因 record_fill 对同一条记录
        同时写 buffer 和文件, 导致同一进程内读取时每条成交被重复计数 (如 15 笔被读成 30)。
        现改为: 文件是权威落盘源; buffer 只在落盘失败时保留该条作为兜底, 合并后不重复。

        P3.0 门禁 (2026-08-26): 新增 ``strategies`` 可选参数, None=全部 (兼容),
        指定则只返回 ``rec["strategy"] in strategies`` 的记录。
        """
        rec_date = date or now_bj().strftime("%Y-%m-%d")
        # 文件是权威事实源, 先读文件
        records: list[dict[str, Any]] = []
        path = self._file_path(rec_date)
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            records.append(json.loads(line))
            except (
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                OSError,
                RuntimeError,
            ) as e:
                logger.warning("[FillsStore] 读取 %s 失败: %s", path, e)
        # 仅补充落盘失败而保留在内存兜底的记录 (避免与已落盘记录重复)
        for rec in self._buffer.get(rec_date, []):
            if rec not in records:
                records.append(rec)
        # P3.0: 按 strategy 过滤
        if strategies is not None:
            strategies_set = set(strategies)
            records = [r for r in records if r.get("strategy") in strategies_set]
        return records

    def latest_avg_price_by_symbol(
        self,
        date: str | None = None,
        strategies: Collection[str] | None = None,
    ) -> dict[str, float]:
        """返回每个标的当日最新成交均价 (按记录顺序末次覆盖)。"""
        result: dict[str, float] = {}
        for rec in self.load_day(date, strategies=strategies):
            result[rec["symbol"]] = rec["avg_price"]
        return result

    def realized_pnl(
        self,
        date: str | None = None,
        strategies: Collection[str] | None = None,
    ) -> dict[str, float]:
        """估算当日已实现 PnL: SELL 成交价 vs 上一笔 BUY 均价 (简化 FIFO 近似)。

        仅用于日常监控参考, 不作为会计级成本基础。返回 {symbol: realized_pnl}。
        """
        from collections import defaultdict

        buys: dict[str, list[float]] = defaultdict(list)
        realized: dict[str, float] = defaultdict(float)
        for rec in self.load_day(date, strategies=strategies):
            sym = rec["symbol"]
            qty = rec["filled_qty"]
            price = rec["avg_price"]
            if rec["side"] == "BUY":
                buys[sym].append(price)
            elif rec["side"] == "SELL" and buys[sym]:
                # 用最早 BUY 均价配对 (简化)
                avg_buy = sum(buys[sym]) / len(buys[sym])
                realized[sym] += (price - avg_buy) * qty
                buys[sym].pop(0)
        return dict(realized)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _file_path(self, date: str) -> Path:
        return _FILLS_DIR / f"fills_{date}.jsonl"

    def _append_to_file(self, date: str, record: dict[str, Any]) -> None:
        _FILLS_DIR.mkdir(parents=True, exist_ok=True)
        with open(self._file_path(date), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# 模块级便捷单例
_store = FillsStore()


def record_fill(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """模块级便捷函数 — 直接调用 FillsStore 单例。"""
    return _store.record_fill(*args, **kwargs)


def load_day(
    date: str | None = None,
    strategies: Collection[str] | None = None,
) -> list[dict[str, Any]]:
    return _store.load_day(date, strategies=strategies)


if __name__ == "__main__":
    # 自检: 写一条模拟成交并读回
    r = record_fill("600519", "BUY", 100, 1680.5, is_live=False, strategy="self_check")
    print("recorded:", r["symbol"], r["avg_price"])
    day = load_day()
    print("day fills:", len(day))
    print("latest price:", _store.latest_avg_price_by_symbol())
