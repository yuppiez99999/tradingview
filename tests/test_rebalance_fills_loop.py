"""G2/G4 执行闭环回归测试.

验证:
    - G2: 再平衡撮合后 positions.json 持仓真实回写 (apply_fills_to_positions)
    - G2: 批量路由无死锁 (N > max_concurrent 时不零执行)
    - G2: 切片缺 size 字段被"无效数量"拒绝
    - G4: FillsStore 无双重计数 (load_day 文件为事实源)
    - G4: PnL 桥接用成交均价覆盖 close (有成交走成交)
    - G4: TCA ingest_fills_from_store 从 FillsStore 读回报归因

遵循 AAA 模式 + 不可变性 + fail-open 铁律.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.execution.fills_pnl_bridge import augment_market_prices  # noqa: E402
from utils.execution.fills_store import FillsStore  # noqa: E402
from utils.tca_post_trade_attribution import PostTradeAttribution  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def test_date() -> str:
    """用唯一日期隔离, 避免污染真实 fills 文件."""
    return "2099-12-31-test"


@pytest.fixture
def clean_store(test_date: str):
    """清空测试日期的 fills 文件, 测试后清理."""
    store = FillsStore()
    path = store._file_path(test_date)
    if path.exists():
        path.unlink()
    yield store
    if path.exists():
        path.unlink()


@pytest.fixture
def positions_backup():
    """备份 positions.json, 测试后还原 (G2 回写测试用)."""
    pos_file = _PROJECT_ROOT / "config" / "positions.json"
    backup = None
    if pos_file.exists():
        backup = pos_file.read_text(encoding="utf-8")
    yield pos_file
    if backup is not None:
        pos_file.write_text(backup, encoding="utf-8")


# --------------------------------------------------------------------------- #
# G4: FillsStore 无双重计数
# --------------------------------------------------------------------------- #
def test_fills_store_no_duplicate(clean_store, test_date):
    """15 笔成交落盘后 load_day 应读回 15, 而非 30 (buffer+文件重复合并)."""
    for i in range(15):
        clean_store.record_fill(
            symbol=f"600519.{i}",
            side="BUY" if i % 2 == 0 else "SELL",
            filled_qty=100,
            avg_price=1680.0 + i,
            date=test_date,
            strategy="test",
        )
    loaded = clean_store.load_day(test_date)
    assert len(loaded) == 15, f"期望 15 笔, 实际 {len(loaded)} (双重计数?)"


# --------------------------------------------------------------------------- #
# G4: PnL 桥接 — 成交价覆盖 close
# --------------------------------------------------------------------------- #
def test_augment_prices_fill_overrides_close(clean_store, test_date):
    """有成交的标的, close 被成交均价覆盖, 且标记 close_source='fill'."""
    clean_store.record_fill(
        symbol="600519",
        side="BUY",
        filled_qty=100,
        avg_price=1650.0,
        date=test_date,
        strategy="test",
    )
    mp = {"600519": {"close": 1700.0, "prev_close": 1680.0}}
    augmented = augment_market_prices(mp, date=test_date)
    assert augmented["600519"]["close"] == 1650.0
    assert augmented["600519"]["close_source"] == "fill"
    # 原 dict 未被修改 (不可变性)
    assert mp["600519"]["close"] == 1700.0


def test_augment_prices_no_fill_falls_back(clean_store, test_date):
    """无成交时原样返回行情价, close_source 不存在."""
    mp = {"600519": {"close": 1700.0, "prev_close": 1680.0}}
    augmented = augment_market_prices(mp, date=test_date)
    assert augmented["600519"]["close"] == 1700.0
    assert "close_source" not in augmented["600519"]


# --------------------------------------------------------------------------- #
# G4: TCA 从 FillsStore 归因
# --------------------------------------------------------------------------- #
def test_tca_ingest_from_store(clean_store, test_date):
    """ingest_fills_from_store 应读回当日成交并归因, 返回笔数 > 0."""
    clean_store.record_fill(
        symbol="600519",
        side="BUY",
        filled_qty=100,
        avg_price=1680.0,
        date=test_date,
        strategy="test",
        meta={"order_id": "test-001"},
    )
    attr = PostTradeAttribution(save_to_file=False)
    ingested = attr.ingest_fills_from_store(date=test_date)
    assert ingested == 1, f"期望归因 1 笔, 实际 {ingested}"


def test_tca_ingest_empty_returns_zero(clean_store, test_date):
    """无成交时 ingest 返回 0, 不报错."""
    attr = PostTradeAttribution(save_to_file=False)
    ingested = attr.ingest_fills_from_store(date=test_date)
    assert ingested == 0


# --------------------------------------------------------------------------- #
# G2: 批量路由无死锁 (N > max_concurrent 时不零执行)
# --------------------------------------------------------------------------- #
def test_rebalance_batch_no_deadlock():
    """路由 15 笔 ( > max_concurrent=10), 不应整队零执行.

    验证 _can_execute_order 只统计 in-flight(executing) 而非排队 pending.
    """
    from utils.execution.automated_execution_system import OrderRouter

    router = OrderRouter.__new__(OrderRouter)  # 绕过 __init__ 双签保护
    router.active_orders = {}
    router.execution_pools = {
        "normal": {
            "broker": "b",
            "priority": "normal",
            "max_concurrent": 10,
            "min_balance": 0,
        },
    }
    router._orders_lock = __import__("threading").Lock()

    # 模拟 15 笔 pending 订单进入队列, 但无一处于 executing
    for i in range(15):
        router.active_orders[f"ord-{i}"] = {
            "target_pool": "normal",
            "status": "pending",  # 关键: 排队中而非执行中
        }

    # 每笔订单处理前标记 executing, 检查能否继续执行
    executable = 0
    for i in range(15):
        router.active_orders[f"ord-{i}"]["status"] = "executing"
        # _can_execute_order 接收 order 字典 (从中提取 target_pool)
        if router._can_execute_order(router.active_orders[f"ord-{i}"]):
            executable += 1
        # 模拟处理后恢复 pending (实际场景由执行线程管理)
        router.active_orders[f"ord-{i}"]["status"] = "pending"

    assert executable == 15, f"期望 15 笔全部可执行, 实际 {executable} (死锁?)"


# --------------------------------------------------------------------------- #
# G2: 切片缺 size 字段被拒绝
# --------------------------------------------------------------------------- #
def test_rebalance_slice_missing_size_rejected():
    """_execute_order 读取 slice_info['size'] 为 0/缺失时应被'无效数量'拒绝."""
    from utils.execution.automated_execution_system import OrderRouter

    router = OrderRouter.__new__(OrderRouter)
    order = {
        "order_id": "test-slice",
        "symbol": "600519",
        "side": "BUY",
        "slice_info": {"instrument": "600519", "direction": "buy"},  # 缺 size
        "execution_plan": {},
        "target_pool": "normal",
        "status": "pending",
    }
    result = router._execute_order(order)
    assert result.get("success") is False
    assert "size" in result.get("error", "").lower() or "数量" in result.get(
        "error", ""
    )


# --------------------------------------------------------------------------- #
# G2: 持仓回写 positions.json
# --------------------------------------------------------------------------- #
def test_apply_fills_to_positions(positions_backup, clean_store, test_date):
    """撮合成交后, positions.json 对应标持仓数量应变化.

    用真实 positions.json, 测试后 fixture 自动还原.
    """
    import rebalance_order_executor as rbe

    # 读取一个真实存在的标的 key
    pos_data = json.loads(positions_backup.read_text(encoding="utf-8"))
    positions = pos_data.get("positions", {})
    assert positions, "positions.json 无持仓数据, 测试无法继续"
    first_sym = next(iter(positions))
    sym_num = first_sym.split(".")[0]

    # 构造一笔该标的的 BUY 成交
    fills = [
        {
            "symbol": sym_num,
            "side": "BUY",
            "filled_qty": 100,
            "avg_price": 10.0,
            "ts": datetime.now().isoformat(),
            "broker": "test",
            "is_live": False,
            "strategy": "rebalance",
            "source": "sim_route",
            "meta": {},
        }
    ]

    old_shares = float(positions[first_sym].get("shares", 0))
    updated = rbe.apply_fills_to_positions(fills, test_date)
    assert updated >= 1, "期望至少更新 1 个标的持仓"

    # 验证回写结果
    new_data = json.loads(positions_backup.read_text(encoding="utf-8"))
    new_positions = new_data.get("positions", {})
    assert first_sym in new_positions
    new_shares = float(new_positions[first_sym].get("shares", 0))
    assert (
        new_shares == old_shares + 100
    ), f"期望 {old_shares}+100={old_shares+100}, 实际 {new_shares}"
