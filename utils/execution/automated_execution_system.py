"""
自动化执行系统 - 世界级对冲基金的自动化交易执行架构

系统特点：
- 7:00AM自动执行：精确的时间控制，确保按时执行
- 智能订单路由：基于市场状况和交易成本的最优订单路由
- 滑点控制：多层级滑点控制，确保执行质量
- 分层执行策略：基于市场状态的分层执行策略
- 实时执行监控：执行过程的实时监控和异常处理
- 自动恢复机制：执行失败后的自动重试和恢复

核心功能：
1. 定时执行控制：精确的定时执行和日历管理
2. 市场状态评估：基于市场状况的执行策略选择
3. 订单生成和路由：智能订单生成和路由
4. 执行质量控制：多层级执行质量控制
5. 异常处理：全面的异常处理和恢复机制
6. 性能监控：执行性能监控和分析
"""

import json
import logging
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from datetime import time as datetime_time
from typing import Any, Optional, TypedDict, cast

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# === 从本文件抽取的独立组件 ===
from utils.execution.execution_components import (  # noqa: E402
    ExecutionStrategy,
    MarketStateEvaluator,
    SpecialDayEntry,  # noqa: F401 — re-export for backward compat
    TradingCalendar,
)


# W6.3.3 类型安全: 为 special_days / execution_pools 的字面量字典添加 TypedDict,
# 消除 mypy [index] 错误 (裸 Dict[str, Any] 无法推断嵌套 value 类型)。
class ExecutionPoolEntry(TypedDict):
    """订单路由执行池配置条目。"""

    broker: str
    priority: str
    max_concurrent: int
    min_balance: int

# schedule 模块为可选依赖 (本文件实际未使用其 API, 仅保留 import 以兼容旧代码)
try:
    import schedule  # noqa: F401
except ImportError:
    schedule = None
import os
import sys

# T3.6 迁移修正: __file__ 从根目录变为 utils/execution/, 需回退两级到项目根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 确保 v8.3_institutional 对冲模块可导入 (路径基于项目根目录)
_V7_5_SRC = os.path.join(_PROJECT_ROOT, "v8.3_institutional", "src")
if _V7_5_SRC not in sys.path:
    sys.path.insert(0, _V7_5_SRC)
# 同时将项目根目录加入 sys.path (便于 import utils.* 等绝对路径)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# G2 补齐: 成交回报统一落盘层 (fail-safe, 导入失败不影响执行链路)
try:
    from utils.execution.fills_store import FillsStore

    _FILLS_STORE_AVAILABLE = True
except (ImportError, AttributeError):
    FillsStore = None
    _FILLS_STORE_AVAILABLE = False

try:
    from ms_strategy.src.hedging.hedge_coordinator import HedgeCoordinator

    _HEDGE_AVAILABLE = True
except (ImportError, AttributeError):
    HedgeCoordinator = None
    _HEDGE_AVAILABLE = False

# G1 QMT 真实下单接线 (2026-08-09): 统一 broker 装配点
# fail-open 降级: 导入/装配失败 → get_broker() 内部降级 SimulatedBroker, 不阻断主链路
try:
    from utils.execution.broker_factory import get_broker

    _GET_BROKER_AVAILABLE = True
except (ImportError, AttributeError):
    get_broker = None
    _GET_BROKER_AVAILABLE = False

try:
    from utils.data_provider import MarketDataProvider, get_market_data  # noqa: F401
    from utils.data_types import safe_float
    from utils.logger import get_logger
    from utils.order_execution import cancel_order, execute_order  # noqa: F401
    from utils.risk_metrics import calculate_es, calculate_var  # noqa: F401

    logger = get_logger("automated_execution_system")
except ImportError:
    # W6.3.3: 移除冗余 `import logging` (已在 L33 导入), 消除 [union-attr];
    #         fallback safe_float 签名须与 utils.data_types.safe_float 完全一致,
    #         否则 mypy [misc] "conditional function variants must have identical signatures"。
    logger = logging.getLogger("automated_execution_system")

    def safe_float(val: object, default: Optional[float] = None) -> Optional[float]:
        return val if val is not None else default

try:
    from wind_mcp_fetcher import wind_get_quote

    _WIND_MCP_AVAILABLE = True
except (ImportError, ModuleNotFoundError, ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
    wind_get_quote = None
    _WIND_MCP_AVAILABLE = False


def _to_wind_code(symbol: str) -> tuple[str, bool]:
    s = str(symbol).strip()
    for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    if s.startswith(("51", "58")):
        return f"{s}.SH", True
    if s.startswith(("15", "16")):
        return f"{s}.SZ", True
    if s.startswith(("00", "30")):
        return f"{s}.SZ", False
    if s.startswith("6"):
        return f"{s}.SH", False
    if s.startswith(("4", "8")):
        return f"{s}.BJ", False
    return f"{s}.SH", False

class OrderRouter:
    """
    订单路由器 — 生产级: 对接 SmartOrderRouter 进行实盘执行
    """

    def __init__(self, smart_router: object = None, broker: object = None, kill_switch: object = None) -> None:
        # ---------- 实盘执行组件 (传入则为实盘; None 则 fallback 模拟) ----------
        self.smart_router = smart_router
        self.broker = broker
        # P0-1 修复: 实盘模式需要双重确认 — 参数非 None + 环境变量 TRADING_ENV=production
        # 原代码仅凭参数非 None 即判定实盘, 开发/测试环境误传参数会直接进入实盘路径
        _params_present = smart_router is not None and broker is not None
        _env_confirmed = os.environ.get("TRADING_ENV", "").lower() == "production"
        self._use_live = _params_present and _env_confirmed
        if _params_present and not _env_confirmed:
            logger.warning(
                "[OrderRouter] smart_router/broker 已传入但 TRADING_ENV != production, "
                "强制降级为模拟模式 (P0-1 双签保护)"
            )
        # P0-2 修复: 注入 KillSwitch 实例, 实盘执行前检查熔断状态
        self._kill_switch = kill_switch

        # 执行池配置
        # W6.3.3: 标注为 Dict[str, ExecutionPoolEntry], 消除 pool["max_concurrent"] 的 [index] ignore。
        self.execution_pools: dict[str, ExecutionPoolEntry] = {
            "normal": {"broker": "broker_a", "priority": "normal", "max_concurrent": 10, "min_balance": 100000},
            "priority": {"broker": "broker_b", "priority": "high", "max_concurrent": 5, "min_balance": 500000},
            "emergency": {"broker": "broker_c", "priority": "critical", "max_concurrent": 3, "min_balance": 1000000},
        }

        # 当前活跃订单
        self.active_orders = {}

        # 执行队列
        self.execution_queue: deque = deque(maxlen=50)

        # 执行统计
        self.execution_stats = {
            "total_orders": 0,
            "successful_orders": 0,
            "failed_orders": 0,
            "average_time": 0.0,
            "average_slippage": 0.0,
        }

        # 修复 BUG-E3: 线程安全锁, 保护多线程共享数据结构
        import threading

        self._orders_lock = threading.Lock()  # 保护 active_orders
        self._queue_lock = threading.Lock()  # 保护 execution_queue
        self._stats_lock = threading.Lock()  # 保护 execution_stats

        mode = "实盘" if self._use_live else "回测/模拟"
        logger.info(f"订单路由器初始化完成 (模式={mode})")

    def route_order(self, execution_plan: dict, market_state: str) -> dict:
        """
        路由订单到执行池

        Args:
            execution_plan: 执行计划
            market_state: 市场状态

        Returns:
            路由结果
        """
        try:
            # 根据市场状态选择执行池
            if market_state in ["crisis", "stress"]:
                pool_name = "emergency"
            elif market_state == "illiquid":
                pool_name = "priority"
            else:
                pool_name = "normal"

            pool = self.execution_pools[pool_name]

            # 检查执行池可用性
            if not self._check_pool_availability(pool):
                # 如果当前池不可用，尝试其他池
                available_pool = self._find_available_pool()
                if available_pool:
                    pool = available_pool
                    pool_name = list(self.execution_pools.keys())[
                        list(self.execution_pools.values()).index(available_pool)
                    ]
                else:
                    return {"success": False, "error": "无可用执行池", "suggested_action": "等待"}

            # 为每个切片生成订单
            # P0 修复: 从 execution_plan / slice_info 提取 symbol/side 注入 order
            # 原代码 order 字典缺少 symbol/side 字段, 导致 _execute_order 中
            # order.get('symbol', '') 永远返回空, order.get('side', 'BUY') 永远返回 'BUY'
            # 实盘下单时 symbol 为空 → smart_router.route() 失败; SELL 订单被当成 BUY
            routed_orders = []
            plan_symbol = execution_plan.get("instrument", "") or ""
            plan_direction = execution_plan.get("total_direction", "buy") or "buy"
            for slice_info in execution_plan["slices"]:
                order_id = self._generate_order_id()
                # 优先用 slice_info 的 instrument/direction, 回退到 execution_plan
                symbol = slice_info.get("instrument", "") or plan_symbol
                side = slice_info.get("direction", "") or plan_direction
                if not symbol:
                    logger.error(
                        "[OrderRouter] 切片缺少 symbol, 跳过此切片 (slice_id=%s)",
                        slice_info.get("slice_id", "unknown"),
                    )
                    continue
                if not side:
                    logger.error(
                        "[OrderRouter] 切片缺少 side, 跳过此切片 (symbol=%s)",
                        symbol,
                    )
                    continue

                order = {
                    "order_id": order_id,
                    "slice_info": slice_info,
                    "execution_plan": execution_plan,
                    "target_pool": pool_name,
                    "priority": pool["priority"],
                    "created_at": datetime.now().isoformat(),
                    "status": "pending",
                    "retry_count": 0,
                    "symbol": symbol,  # P0 修复: 显式注入 symbol
                    "side": side.upper(),  # P0 修复: 显式注入 side (大写)
                }

                routed_orders.append(order)

            # 更新活跃订单 (P1 修复: 加锁保护多线程写入)
            with self._orders_lock:
                for order in routed_orders:
                    # N-3 修复 (2026-08-09): 防御性不变量 — 订单ID唯一性不应被违反。
                    # M-7 (2026-08-09): 用显式 raise 而非 assert, 避免 -O 优化模式下被剥离。
                    if order["order_id"] in self.active_orders:
                        raise RuntimeError(
                            f"订单ID碰撞: {order['order_id']} 已存在于活跃订单 (应全局唯一)"
                        )
                    self.active_orders[order["order_id"]] = order

            # 加入执行队列 (P1 修复: 加锁保护多线程写入)
            with self._queue_lock:
                for order in routed_orders:
                    self.execution_queue.append(order)

            logger.info(f"订单路由完成: {len(routed_orders)}个订单到{pool_name}池")

            return {
                "success": True,
                "routed_orders": routed_orders,
                "target_pool": pool_name,
                "estimated_wait_time": self._estimate_wait_time(pool_name),
            }

        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"订单路由失败: {e}")
            return {"success": False, "error": str(e)}

    def _check_pool_availability(self, pool: ExecutionPoolEntry) -> bool:
        """检查执行池可用性"""
        # 修复 BUG-E2: 通过对象身份查找 pool_name, 而非用 index (字符串==整数永远False)
        pool_name = None
        for name, p in self.execution_pools.items():
            if p is pool:
                pool_name = name
                break

        if pool_name is None:
            logger.warning("[OrderRouter] 未找到 pool 对应的名称, 判定为不可用")
            return False

        # 检查并发限制: 用 pool_name 字符串匹配 (P1 修复: 加锁保护读取)
        with self._orders_lock:
            active_count = sum(1 for order in self.active_orders.values() if order.get("target_pool") == pool_name)

        if active_count >= pool["max_concurrent"]:
            return False

        # 检查余额限制（简化处理）
        # 实际应该查询真实的账户余额
        return True

    def _find_available_pool(self) -> Optional[ExecutionPoolEntry]:
        """查找可用的执行池"""
        for pool in self.execution_pools.values():
            if self._check_pool_availability(pool):
                return pool
        return None

    def _generate_order_id(self) -> str:
        """生成订单ID

        N-3 修复 (2026-08-09): 原实现用 datetime 秒级 + np.random.randint(1000,9999),
        同秒生成多个订单时存在碰撞风险。改为 uuid4() 全局唯一 (122-bit 随机熵,
        碰撞概率可忽略), 并在 __init__ 中加断言防御 (活跃订单不允许 ID 重复)。
        """
        return f"ORD_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:12]}"

    def _estimate_wait_time(self, pool_name: str) -> float:
        """估算等待时间"""
        pool = self.execution_pools[pool_name]

        # 基础等待时间
        base_wait = 10.0

        # 加上当前活跃订单的影响 (P1 修复: 加锁保护读取)
        with self._orders_lock:
            active_count = sum(1 for order in self.active_orders.values() if order.get("target_pool") == pool_name)
        # P2-5 修复: 原公式 active_count * max_concurrent * 5.0 反直觉 (并发越大等待越久)
        # 正确公式: 等待时间与并发数成反比, 并发越大吞吐越高等待越短
        # W6.3.3: execution_pools 已标注为 Dict[str, ExecutionPoolEntry],
        # pool 类型为 ExecutionPoolEntry, pool["max_concurrent"] 为 int, 无需 [index] ignore。
        queue_wait = active_count * 5.0 / max(pool["max_concurrent"], 1)

        return base_wait + queue_wait

    def process_execution_queue(self) -> None:
        """处理执行队列 (P1 修复: 加锁保护队列与订单状态变更)"""
        try:
            while True:
                # P1 修复: 用 _queue_lock 保护队列读取, 避免 route_order 并发 append 导致的竞态
                with self._queue_lock:
                    if not self.execution_queue:
                        break
                    order = self.execution_queue[0]

                # 检查是否可以执行 (内部已加 _orders_lock)
                if not self._can_execute_order(order):
                    break

                # G2 修复 (2026-08-08): 标记为 in-flight, 供 _can_execute_order 计数并发在途单。
                # 此前 active_count 统计所有 pending 队列单, 导致批量路由 (N > max_concurrent) 时
                # 首单即被判定为"池已满"而 break, 整队零执行 (再平衡只生成不撮合断链)。
                with self._orders_lock:
                    order["status"] = "executing"

                # 执行订单 (不持锁, _execute_order 可能耗时较长)
                execution_result = self._execute_order(order)

                # P1 修复: 用 _orders_lock 保护订单状态变更, 避免 get_router_summary 读到中间状态
                with self._orders_lock:
                    if execution_result.get("success"):
                        order["status"] = "completed"
                        order["completed_at"] = datetime.now().isoformat()
                        order["execution_result"] = execution_result
                        # G2 补齐: 成交回报统一落盘 (fail-open, 不阻断执行链路)
                        self._record_fill_for_order(order, execution_result)
                    else:
                        order["status"] = "failed"
                        order["error"] = execution_result.get("error", "未知错误")
                        order["retry_count"] = order.get("retry_count", 0) + 1
                        # 重试逻辑
                        if order["retry_count"] < 3:
                            order["status"] = "pending"
                        else:
                            order["status"] = "abandoned"

                # 从队列中移除 (deque popleft 线程安全, 但加锁语义清晰)
                with self._queue_lock:
                    if self.execution_queue and self.execution_queue[0] is order:
                        self.execution_queue.popleft()

                # 更新统计 (内部已加 _stats_lock)
                self._update_execution_stats(execution_result)

                logger.info(f"订单处理完成: {order['order_id']} - {order['status']}")

        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"执行队列处理失败: {e}")

    def _can_execute_order(self, order: dict) -> bool:
        """检查是否可以执行订单"""
        # 检查执行池可用性
        pool_name = order.get("target_pool", "normal")
        pool = self.execution_pools.get(pool_name)

        if not pool:
            return False

        # 检查并发限制 (P1 修复: 加锁保护读取)
        # G2 修复 (2026-08-08): 只统计"in-flight"订单 (status=executing), 不再统计排队中的
        # pending 单。原逻辑把整个队列的 pending 订单都计为 active, 当批量路由订单数
        # > max_concurrent 时, 首单即判定"池已满"而 break, 导致队列死锁零执行。
        # 现在每笔订单在处理前标记 executing, 顺序处理时 in-flight 恒 ≤ 1, 队列可正常排空,
        # 同时保留 max_concurrent 对并发执行场景 (多线程) 的真实限制语义。
        with self._orders_lock:
            active_count = sum(
                1
                for o in self.active_orders.values()
                if o.get("target_pool") == pool_name and o.get("status") == "executing"
            )

        if active_count >= pool["max_concurrent"]:
            return False

        return True

    def _execute_order(self, order: dict) -> dict:
        """执行单个订单 — 生产级: SmartOrderRouter 路由 + Iceberg + 滑点熔断"""
        try:
            slice_info = order.get("slice_info") or {}
            # P0 修复: route_order 已注入 symbol/side, 这里用 .get() 保护并校验非空
            symbol = str(order.get("symbol", "") or "").strip()
            side = str(order.get("side", "BUY") or "BUY").strip().upper()
            qty = slice_info.get("size", 0) or 0
            limit_price = slice_info.get("price")

            # P0 修复: 实盘/模拟路径都校验 symbol 非空 (原代码 symbol='' 会静默通过)
            if not symbol:
                return {
                    "success": False,
                    "error": "order.symbol 为空, 拒绝执行 (P0 修复: 防止实盘空 symbol 下单)",
                }
            # P0 修复: 校验 side 合法
            if side not in ("BUY", "SELL"):
                return {
                    "success": False,
                    "error": f"非法 side={side}, 必须为 BUY/SELL",
                }
            # P0 修复: 校验 qty 正数
            try:
                qty = float(qty)
            except (TypeError, ValueError) as qty_err:
                return {
                    "success": False,
                    "error": f"qty 无法转换为数值: {qty} ({qty_err})",
                }
            if qty <= 0:
                return {
                    "success": False,
                    "error": f"无效的数量 {qty}, 拒绝执行",
                }

            if self._use_live:
                # ---- 实盘路径: SmartOrderRouter ----
                # P0-2 修复: 实盘执行前必须检查 KillSwitch 熔断状态
                # 原代码直接调用 smart_router, 即使 KillSwitch 已触发 L2/L3 熔断仍会下单
                if self._kill_switch is not None:
                    try:
                        ks_status = self._kill_switch.check_margin_status()
                        ks_level = ks_status.get("level", 0) if isinstance(ks_status, dict) else 0
                        if ks_level >= 2:
                            logger.error(
                                "[OrderRouter] KillSwitch 熔断中 (level=%d), 拒绝实盘下单 %s",
                                ks_level,
                                symbol,
                            )
                            return {
                                "success": False,
                                "error": f"KillSwitch 熔断中 (level={ks_level}), 禁止实盘下单",
                                "kill_switch_blocked": True,
                            }
                    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as ks_err:
                        # KillSwitch 检查异常时 fail-closed: 拒绝下单
                        logger.error("[OrderRouter] KillSwitch 检查异常, fail-closed 拒绝下单: %s", ks_err)
                        return {
                            "success": False,
                            "error": f"KillSwitch 检查异常: {ks_err}",
                            "kill_switch_error": True,
                        }

                order_book = self.broker.get_order_book(symbol, levels=5)
                if order_book is None:
                    raise ValueError(f"无法获取 {symbol} 盘口深度, 取消执行")

                routing = self.smart_router.route(
                    symbol=symbol,
                    side=side,
                    total_shares=qty,
                    order_books={symbol: order_book},
                    max_venues=2,
                    strategy="LIQUIDITY_FIRST",
                )

                fills = self.smart_router.execute_route(
                    routing=routing,
                    symbol=symbol,
                    side=side,
                    target_qty=qty,
                    broker=self.broker,
                )

                if not fills:
                    return {"success": False, "error": "所有场所均执行失败"}

                # P1-10 修复: 除零保护 — fills 非空但 filled_qty 全为 0 时分母为 0
                total_filled = sum(f.filled_qty for f in fills)
                if total_filled == 0:
                    return {"success": False, "error": "所有场所成交量为 0"}

                avg_price = sum(f.avg_price * f.filled_qty for f in fills) / total_filled

                # 滑点 = avg_price vs arrival_price
                # P1 修复: arrival_price 可能为 0 (盘口缺失 mid/ask1/bid1), 加强保护
                arrival_price = order_book.get("mid") or (order_book.get("ask1", 0) + order_book.get("bid1", 0)) / 2
                if not arrival_price or arrival_price <= 0:
                    # arrival_price 为 0 时无法计算滑点, 用 avg_price 作为 fallback
                    logger.warning(
                        "[OrderRouter] %s arrival_price=%.4f 非法, 滑点置 0 (avg_price=%.4f)",
                        symbol,
                        arrival_price,
                        avg_price,
                    )
                    slippage = 0.0
                else:
                    slippage = avg_price / arrival_price - 1
                if side == "SELL":
                    slippage = -slippage

                return {
                    "success": True,
                    "execution_time": getattr(routing, "latency_ms", 0) or 0,
                    "slippage": round(slippage, 6),
                    "filled_size": total_filled,
                    "average_price": round(avg_price, 4),
                    "broker": "smart_router",
                    "venue_count": len(fills),
                    "is_live": True,
                }

            else:
                # ---- 回测/模拟路径 (保留兼容) ----
                # 修复 BUG-E4: 严格校验限价, 防止 0 价格成交
                if limit_price is None or not isinstance(limit_price, (int, float)) or limit_price <= 0:
                    # 市价单: 尝试从持仓文件获取参考价
                    ref_price = self._get_reference_price(symbol)
                    if ref_price is None or ref_price <= 0:
                        return {"success": False, "error": f"无法获取 {symbol} 参考价格, 拒绝生成 0 价格成交"}
                    limit_price = ref_price

                # P1 修复: order['target_pool'] 用 .get() 保护, 缺失时用 'normal' 兜底
                pool_name = order.get("target_pool", "normal")
                pool_cfg = self.execution_pools.get(pool_name)
                if pool_cfg is None:
                    logger.warning(
                        "[OrderRouter] 未知的 target_pool=%s, 用 'normal' 兜底",
                        pool_name,
                    )
                    pool_cfg = self.execution_pools.get("normal", {})
                broker_name = pool_cfg.get("broker", "simulated_broker") if pool_cfg else "simulated_broker"

                execution_time = 0.01  # 回测中执行延迟可忽略
                # 按 A-share 最低滑点 (2bp 大盘 / 5bp 中小盘)
                # P0 修复: symbol 现已保证非空, 前缀判断可正确工作
                slippage_bps = 2 if symbol.startswith(("60", "00", "30")) else 5
                slippage = slippage_bps / 10000.0
                # side 已在入口校验为 BUY/SELL 之一, 无需再处理未知方向
                if side == "BUY":
                    fill_price = limit_price * (1 + slippage)
                else:  # SELL
                    fill_price = limit_price * (1 - slippage)

                return {
                    "success": True,
                    "execution_time": execution_time,
                    "slippage": slippage,
                    "filled_size": qty,
                    "average_price": round(fill_price, 4),
                    "broker": broker_name,
                    "is_live": False,
                }

        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            return {"success": False, "error": str(e)}

    def _get_reference_price(self, symbol: str) -> Optional[float]:
        """获取参考价格 (用于市价单回测时 fallback)

        从持仓文件或行情接口获取标的参考价格,
        避免 limit_price 为 None 时生成 0 价格成交。
        """
        if not symbol:
            return None
        try:
            import os

            positions_path = os.path.join(
                _PROJECT_ROOT, "config", "positions.json"
            )  # P1-11: 原路径 utils/execution/config/ 不存在
            if os.path.exists(positions_path):
                with open(positions_path, encoding="utf-8") as f:
                    data = json.load(f)
                for key, pos in data.get("positions", {}).items():
                    if symbol in key:
                        price = pos.get("est_price", pos.get("last_price", 0))
                        if price and float(price) > 0:
                            return float(price)
            return None
        except (json.JSONDecodeError, OSError, ValueError, TypeError):  # noqa: BLE001
            return None

    def _record_fill_for_order(self, order: dict, execution_result: dict) -> None:
        """G2 补齐: 将成功执行的成交回报统一落盘到 FillsStore。

        fail-open: 任何异常只记日志, 绝不阻断执行链路。
        """
        if not _FILLS_STORE_AVAILABLE:
            return
        try:
            symbol = str(order.get("symbol", "") or "").strip()
            side = str(order.get("side", "BUY") or "BUY").strip().upper()
            filled_qty = execution_result.get("filled_size", 0) or 0
            avg_price = execution_result.get("average_price", 0) or 0
            if not symbol or filled_qty <= 0 or avg_price <= 0:
                return
            store = FillsStore()
            store.record_fill(
                symbol=symbol,
                side=side,
                filled_qty=filled_qty,
                avg_price=avg_price,
                broker=execution_result.get("broker", "unknown"),
                is_live=execution_result.get("is_live", False),
                strategy=order.get("strategy", "rebalance"),
                source="live_route" if execution_result.get("is_live") else "sim_route",
                meta={
                    "slippage": execution_result.get("slippage"),
                    "venue_count": execution_result.get("venue_count"),
                    "execution_time": execution_result.get("execution_time"),
                    "order_id": order.get("order_id"),
                },
            )
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning("[OrderRouter] 成交落盘失败 (已忽略): %s", e)

    def _update_execution_stats(self, execution_result: dict) -> None:
        """更新执行统计 (P1 修复: 加锁保护多线程写入)"""
        # P1 修复: 用 .get() 保护字段访问, 避免 KeyError
        success = execution_result.get("success", False)
        exec_time = execution_result.get("execution_time", 0) or 0
        slippage = execution_result.get("slippage", 0) or 0

        with self._stats_lock:
            self.execution_stats["total_orders"] += 1
            total = self.execution_stats["total_orders"]

            if success:
                self.execution_stats["successful_orders"] += 1
                # 增量平均: new_avg = (old_avg * (n-1) + new_value) / n
                self.execution_stats["average_time"] = (
                    self.execution_stats["average_time"] * (total - 1) + exec_time
                ) / total
                self.execution_stats["average_slippage"] = (
                    self.execution_stats["average_slippage"] * (total - 1) + slippage
                ) / total
            else:
                self.execution_stats["failed_orders"] += 1

    def get_router_summary(self) -> dict:
        """获取路由器总结 (P1 修复: 加锁保护读取, 避免读到中间状态)"""
        # 活跃订单统计 (P1 修复: 加锁保护快照)
        with self._orders_lock:
            active_orders = list(self.active_orders.values())
        with self._queue_lock:
            queue_length = len(self.execution_queue)
        with self._stats_lock:
            stats_snapshot = dict(self.execution_stats)

        # 按状态统计
        status_stats: dict[str, int] = {}
        for order in active_orders:
            status = order.get("status", "unknown")
            status_stats[status] = status_stats.get(status, 0) + 1

        # 按池统计
        pool_stats: dict[str, int] = {}
        for order in active_orders:
            pool = order.get("target_pool", "unknown")
            pool_stats[pool] = pool_stats.get(pool, 0) + 1

        return {
            "total_active_orders": len(active_orders),
            "queue_length": queue_length,
            "status_distribution": status_stats,
            "pool_distribution": pool_stats,
            "execution_stats": stats_snapshot,
            "current_time": datetime.now().isoformat(),
        }


class AutomatedExecutionSystem:
    """
    自动化执行系统 - 主控制器
    """

    def __init__(self, total_capital: float = 1000000) -> None:
        self.total_capital = total_capital

        # 初始化组件
        self.trading_calendar = TradingCalendar()
        self.market_evaluator = MarketStateEvaluator()
        self.execution_strategy = ExecutionStrategy()
        # G1 接入: 注入 broker (默认 SimulatedBroker; 仅 enabled+dry_run=false+TRADING_ENV=production 才实盘)
        _broker = None
        if _GET_BROKER_AVAILABLE:
            try:
                _broker = get_broker()
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
                logger.warning("[G1] broker 装配失败, OrderRouter 走空 broker 降级模拟: %s", exc)
        self.order_router = OrderRouter(broker=_broker)

        # 系统状态
        self.system_enabled = False
        self.is_running = False
        # W6.3.3: 显式标注为 Optional[threading.Thread], 消除 start_system() 中
        # 三处 [union-attr] / 裸 ignore (原 = None 让 mypy 推断为 None 单例类型)。
        self.execution_thread: Optional[threading.Thread] = None

        # 对冲模块
        self.hedge_enabled = False
        # W6.3.3: 标注为 Optional[HedgeCoordinator], 消除 _run_hedge_decision 中
        # .coordinate() 的裸 ignore (原 = None 让 mypy 无法收窄实例属性)。
        self.hedge_coordinator: Optional["HedgeCoordinator"] = None
        self.last_hedge_plan: Optional[dict] = None
        if _HEDGE_AVAILABLE:
            try:
                self.hedge_coordinator = HedgeCoordinator()
                self.hedge_enabled = True
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
                logger.warning("对冲模块初始化失败: %s", exc)

        # 执行状态
        self.current_market_state = "normal"
        self.current_execution_plan = None
        self.current_routed_orders: list[Any] = []

        # 系统历史
        self.system_history: deque = deque(maxlen=100)

        # 配置参数
        self.config = {
            "auto_start": True,
            "execution_window_check": True,
            "risk_pre_check": True,
            "max_retry_attempts": 3,
            "emergency_stop": True,
            "performance_monitoring": True,
        }

        logger.info(f"自动化执行系统初始化完成，总资本: {total_capital:,.0f}元")

    def start_system(self) -> None:
        """启动系统"""
        if not self.system_enabled:
            self.system_enabled = True
            self.is_running = True

            # 启动执行线程
            # W6.3.3: execution_thread 已标注为 Optional[threading.Thread],
            # 赋值后 mypy 正确识别为 Thread, 三处 ignore 全部消除。
            self.execution_thread = threading.Thread(target=self._execution_loop)
            self.execution_thread.daemon = True
            self.execution_thread.start()

            # 启动性能监控
            if self.config["performance_monitoring"]:
                self._start_performance_monitoring()

            logger.info("自动化执行系统启动")

    def enable_hedge(self, enabled: bool = True) -> bool:
        """开启或关闭对冲模块"""
        if not _HEDGE_AVAILABLE or self.hedge_coordinator is None:
            logger.warning("对冲模块不可用，无法开启")
            return False
        self.hedge_enabled = bool(enabled)
        logger.info("对冲模块已%s", "开启" if self.hedge_enabled else "关闭")
        return self.hedge_enabled

    def disable_hedge(self) -> bool:
        """关闭对冲模块"""
        return self.enable_hedge(False)

    def stop_system(self) -> None:
        """停止系统"""
        self.is_running = False
        self.system_enabled = False

        if self.execution_thread:
            self.execution_thread.join()

        logger.info("自动化执行系统停止")

    def _execution_loop(self) -> None:
        """执行循环"""
        while self.is_running:
            try:
                next_execution = self.trading_calendar.get_next_execution_time()
                if not next_execution:
                    time.sleep(60)
                    continue

                current_time = datetime.now()
                if current_time < next_execution:
                    sleep_time = (next_execution - current_time).total_seconds()
                    time.sleep(min(sleep_time, 60))
                    continue

                matched_execution = self._match_current_execution(current_time)
                if not matched_execution:
                    time.sleep(60)
                    continue

                logger.info(f"进入执行窗口: {matched_execution}")
                self._execute_daily_trading(matched_execution)
                time.sleep(60)

            except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
                logger.error(f"执行循环错误: {e}")
                time.sleep(60)

    def _match_current_execution(self, current_time: datetime) -> Optional[str]:
        """根据当前时间匹配应触发的执行项"""
        execution_map = {
            "daily_execution": (datetime_time(6, 30), datetime_time(8, 0)),
            "morning_review": (datetime_time(9, 30), datetime_time(10, 30)),
            "afternoon_adjustment": (datetime_time(13, 30), datetime_time(14, 30)),
        }

        now_time = current_time.time()
        for name, (start, end) in execution_map.items():
            is_allowed, _message = self.trading_calendar.is_within_execution_window(name)
            if is_allowed:
                return name
            if start <= now_time <= end:
                return name
        return None

    def _execute_daily_trading(self, execution_name: str = "daily_execution") -> Optional[dict]:
        """执行每日交易"""
        try:
            logger.info(f"开始每日交易执行: {execution_name}")
            logger.debug(f"执行参数: total_capital={self.total_capital}, market_state={self.current_market_state}")

            # 0. 每日自动更新历史收益率数据（供对冲引擎使用真实Beta/相关性）
            try:
                self._update_historical_returns()
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as update_exc:
                logger.warning("历史收益率自动更新失败: %s", update_exc)

            # 0.5 更新持仓实时价格
            try:
                self._update_position_prices()
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as update_exc:
                logger.warning("持仓价格更新失败: %s", update_exc)

            # 1. 市场状态评估
            market_data = self._get_market_data()
            logger.debug(f"市场数据: { {k: v for k, v in market_data.items() if k != 'correlation_matrix'} }")
            market_state = self.market_evaluator.evaluate_market_state(market_data)

            if isinstance(market_state, dict):
                self.current_market_state = market_state["market_state"]
                market_state_data = market_state
            else:
                self.current_market_state = market_state
                market_state_data = {"market_state": market_state}

            logger.info(f"市场状态评估完成: {self.current_market_state}")

            # 2. 检查风险
            if self.config["risk_pre_check"]:
                if not self._risk_pre_check(market_state_data):
                    logger.warning("风险预检查失败，取消今日交易")
                    return

            # 3. 对冲决策（可选）
            # W6.3.3: 显式标注 hedge_plan: Optional[Dict], 与 last_hedge_plan 类型一致,
            # 消除 [assignment] ignore (原 hedge_plan = None 让 mypy 推断为 None 单例)。
            hedge_plan: Optional[dict] = None
            if self.hedge_enabled and self.hedge_coordinator is not None:
                hedge_plan = self._run_hedge_decision(market_data, market_state_data)
                hedge_plan = self._apply_hedge_triggers(market_data, hedge_plan)
                self.last_hedge_plan = hedge_plan

            # 4-8. 生成交易计划并路由
            # P1-1 修复: 原代码硬编码 SPY 假订单 (trade_info={instrument:"SPY",trade_size:100000}),
            # route_order 消费的永远是这个假 SPY 单, 与真实再平衡/对冲订单完全脱钩。
            # 现改为由再平衡订单生成真实 execution_plan 并路由 (单一执行点), 删除假 SPY。
            # 初始占位, 由步骤 11 的再平衡路由结果填充。
            execution_plan = None
            routing_result = {"success": False, "routed_orders": [], "error": "rebalance_not_generated"}

            # 9. 记录执行结果 (rebalance_plan 在步骤 11 填充)
            # W6.3.3: 显式标注 Dict[str, Any], 消除 [var-annotated]
            # (execution_plan=None + routing_result 混合类型让 mypy 无法推断)。
            execution_result: dict[str, Any] = {
                "market_state": self.current_market_state,
                "execution_plan": execution_plan,
                "routed_orders": [],
                "routing_result": routing_result,
                "execution_time": datetime.now().isoformat(),
                "execution_name": execution_name,
                "hedge_plan": hedge_plan,
                "rebalance_plan": None,
            }

            system_record = {
                "timestamp": datetime.now().isoformat(),
                "event": execution_name,
                "execution_result": execution_result,
                "market_state_data": market_state_data,
            }

            self.system_history.append(system_record)

            # 10. 生成对冲执行单
            try:
                self._generate_hedge_execution_orders(hedge_plan)
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
                # B4 修复 (2026-08-08): 静默吞咽 -> 计数+告警+状态标记.
                # 见 docs/CODE_REVIEW_COMPREHENSIVE_20260808.md B4 + docs/CODE_REVIEW_GAP_AUDIT D-7.
                # 原: 仅 logger.warning, "对冲实际没生效"与"普通告警"在日志里长得一样.
                # 现: 告警独立通道 (send_alert) + 失败计数 + execution_result 显式标记.
                logger.error("[HEDGE_FAIL] 对冲执行单生成失败: %s", exc, exc_info=True)
                execution_result.setdefault("hedge_failure", {
                    "error": str(exc),
                    "timestamp": datetime.now().isoformat(),
                })
                self._consecutive_hedge_failures = getattr(self, "_consecutive_hedge_failures", 0) + 1
                if self._consecutive_hedge_failures >= 3:
                    try:
                        from utils.notify import send_alert
                        send_alert(
                            title="[CRITICAL] 连续对冲失败",
                            content=f"连续 {self._consecutive_hedge_failures} 次对冲执行失败, 最近: {exc}",
                            level="critical",
                        )
                    except Exception:
                        logger.warning("告警 send_alert 调用失败 (fail-open 不阻断)")

            # 11. 生成再平衡执行单并路由 (执行断链修复: P0-3 已接入 order_router)
            try:
                rebalance_report = self._generate_rebalance_orders()
                execution_result["rebalance_plan"] = rebalance_report
                # 同步当前执行计划/路由结果 (供状态快照与结果记录使用)
                if rebalance_report is not None:
                    routing = rebalance_report.get("routing") or {}
                    if routing.get("success"):
                        self.current_execution_plan = routing.get("execution_plan")
                        self.current_routed_orders = routing.get("routed_orders", [])
                        execution_result["execution_plan"] = routing.get("execution_plan")
                        execution_result["routed_orders"] = self.current_routed_orders
                        execution_result["routing_result"] = routing
                    else:
                        # B4: 路由失败也要记录, 不能只在 success 分支记.
                        execution_result.setdefault("rebalance_failure", {
                            "routing_result": routing,
                            "timestamp": datetime.now().isoformat(),
                        })
            except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as exc:
                # B4 修复: 同上, 静默吞咽 -> 计数+告警.
                logger.error("[REBALANCE_FAIL] 再平衡订单生成失败: %s", exc, exc_info=True)
                execution_result.setdefault("rebalance_failure", {
                    "error": str(exc),
                    "timestamp": datetime.now().isoformat(),
                })
                self._consecutive_rebalance_failures = getattr(self, "_consecutive_rebalance_failures", 0) + 1
                if self._consecutive_rebalance_failures >= 3:
                    try:
                        from utils.notify import send_alert
                        send_alert(
                            title="[CRITICAL] 连续再平衡失败",
                            content=f"连续 {self._consecutive_rebalance_failures} 次再平衡失败, 最近: {exc}",
                            level="critical",
                        )
                    except Exception:
                        logger.warning("告警 send_alert 调用失败 (fail-open 不阻断)")

            logger.info(f"每日交易执行完成: {execution_name}")

        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"每日交易执行失败: {e}", exc_info=True)
            failure_record = {
                "timestamp": datetime.now().isoformat(),
                "event": "execution_failure",
                "error": str(e),
                "market_state": self.current_market_state,
                "execution_name": execution_name,
            }
            self.system_history.append(failure_record)

    def _run_hedge_decision(self, market_data: dict, market_state_data: dict) -> Optional[dict]:
        """运行对冲决策"""
        try:
            # 1. 读取真实持仓与价格
            positions_path = os.path.join(
                _PROJECT_ROOT, "config", "positions.json"
            )  # P1-11: 原路径 utils/execution/config/ 不存在
            positions = {}
            prices = {}
            style_map = {}
            if os.path.exists(positions_path):
                with open(positions_path, encoding="utf-8") as f:
                    pos_data = json.load(f).get("positions", {})
                for _key, item in pos_data.items():
                    code = item.get("code")
                    qty = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
                    price = item.get("est_price", 0.0)
                    if code and qty:
                        positions[code] = float(qty)
                        prices[code] = float(price)
                        style_map[code] = item.get("style", "其他")

            # 2. 尝试加载真实历史收益率；缺失时使用风格 Beta 估算
            returns = pd.DataFrame()
            market_returns = pd.Series(dtype=float)
            returns_path = os.path.join(os.path.dirname(__file__), "config", "returns_history.json")
            market_path = os.path.join(os.path.dirname(__file__), "config", "market_returns.json")
            if os.path.exists(returns_path) and os.path.exists(market_path):
                try:
                    returns = pd.read_json(returns_path, orient="split")
                    market_returns = pd.read_json(market_path, orient="split", typ="series")
                    returns.columns = returns.columns.astype(str)
                    logger.info("已加载历史收益率数据: %s 条, %s 个标的", len(market_returns), returns.shape[1])
                except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
                    logger.warning("加载历史收益率失败: %s", e)

            if returns.empty or market_returns is None or len(market_returns) == 0:
                style_beta_proxy = {
                    "宽基": 0.95,
                    "高端制造": 1.15,
                    "科技": 1.20,
                    "制造": 1.05,
                    "新能源": 1.10,
                    "医药": 0.85,
                    "化工": 1.00,
                    "银行": 0.75,
                    "防御": 0.60,
                    "顺周期": 1.10,
                    "避险": -0.10,
                    "红利": 0.70,
                    "成长": 1.25,
                }
                day_capital = float(
                    sum(positions.get(s, 0) * prices.get(s, 0) for s in positions) or self.total_capital
                )
                portfolio_beta_est = 0.0
                for code, qty in positions.items():
                    amt = qty * prices.get(code, 0.0)
                    style = style_map.get(code, "其他")
                    beta = style_beta_proxy.get(style, 1.0)
                    portfolio_beta_est += (amt / day_capital) * beta if day_capital > 0 else 0.0
            else:
                portfolio_beta_est = 0.0

            # 3. 构造市场输入
            vix = float(market_data.get("vix_future_price", 20.0) or 20.0)
            portfolio_value = float(self.total_capital)
            hwm_drawdown = 0.03
            bs_loss = 0.0

            # 4. 调用真实对冲引擎
            # W6.3.3: mypy 无法跨方法收窄实例属性 self.hedge_coordinator (调用者已做
            # is not None 检查但 mypy 不信任实例属性在方法调用间不变)。引入局部变量
            # coordinator 并显式 None 守卫, 使 mypy 在 .coordinate() 处收窄为 HedgeCoordinator。
            coordinator = self.hedge_coordinator
            if coordinator is None:
                logger.warning("对冲协调器未初始化, 跳过对冲决策")
                return None
            plan = cast(
                dict,
                coordinator.coordinate(
                    positions=positions,
                    prices=prices,
                    returns=returns,
                    market_returns=market_returns,
                    vix=vix,
                    portfolio_value=portfolio_value,
                    hwm_drawdown=hwm_drawdown,
                    bs_loss=bs_loss,
                ),
            )

            # 5. 若仍缺少真实数据且 Beta 过高，给出结构化 fallback
            if plan.get("action") == "NO_HEDGE" and portfolio_beta_est > 0.7:
                plan = dict(plan)
                plan["action"] = "PREPARE_HEDGE"
                plan["portfolio_beta"] = float(portfolio_beta_est)
                plan["prepared_reason"] = f"估算组合Beta {portfolio_beta_est:.2f} > 0.7，建议准备 Beta 对冲/避险配置"

            logger.info(
                "对冲决策完成: action=%s, total_hedge_pct=%.2f%%, cost_pct=%.2f%%",
                plan.get("action"),
                float(plan.get("total_hedge_pct", 0.0) or 0.0) * 100,
                float(plan.get("total_cost_pct", 0.0) or 0.0) * 100,
            )
            return plan
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"对冲决策失败: {e}")
            return None

    def _update_position_prices(self) -> None:
        """更新持仓实时价格 - 优先 Wind MCP"""
        try:
            positions_path = os.path.join(
                _PROJECT_ROOT, "config", "positions.json"
            )  # P1-11: 原路径 utils/execution/config/ 不存在
            if not os.path.exists(positions_path):
                return

            with open(positions_path, encoding="utf-8") as f:
                data = json.load(f)
            positions = data.get("positions", {})

            update_count = 0
            fail_count = 0

            for item in positions.values():
                code = item.get("code")
                # old_price 已移除 (F841)
                shares = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)

                if not code or not shares:
                    continue

                real_time_price = None
                price_source = None

                if _WIND_MCP_AVAILABLE and wind_get_quote is not None:
                    try:
                        wind_code, is_fund = _to_wind_code(code)
                        quote = wind_get_quote(wind_code, is_fund=is_fund)
                        if quote and quote.get("price") is not None:
                            real_time_price = float(quote["price"])
                            if real_time_price > 0:
                                price_source = "wind_mcp"
                    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
                        logger.debug("Wind MCP 获取价格失败 %s: %s", code, e)

                if real_time_price is None or real_time_price <= 0:
                    fail_count += 1
                    continue

                item["est_price"] = real_time_price
                item["last_update"] = datetime.now().isoformat()
                item["price_source"] = price_source or "unknown"
                update_count += 1

            if update_count > 0:
                with open(positions_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info("持仓价格更新完成: 成功 %s, 失败 %s", update_count, fail_count)
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning("持仓价格更新失败: %s", e)

    def _update_historical_returns(self) -> None:
        """更新历史收益率数据并写入 config/"""
        try:
            positions_path = os.path.join(
                _PROJECT_ROOT, "config", "positions.json"
            )  # P1-11: 原路径 utils/execution/config/ 不存在
            if not os.path.exists(positions_path):
                return

            with open(positions_path, encoding="utf-8") as f:
                positions_data = json.load(f).get("positions", {})

            symbols = [item.get("code") for item in positions_data.values() if item.get("code")]
            if not symbols:
                return

            provider = MarketDataProvider()
            period = "1y"
            returns_data = {}
            for symbol in symbols:
                try:
                    df = provider.get_historical_data(symbol, period)
                    if df is not None and not df.empty and "close" in df.columns:
                        df["return"] = df["close"].pct_change()
                        returns_data[symbol] = df["return"].dropna()
                except (ValueError, TypeError, KeyError, AttributeError, OSError):
                    continue

            if not returns_data:
                return

            returns_df = pd.DataFrame(returns_data)
            returns_path = os.path.join(os.path.dirname(__file__), "config", "returns_history.json")
            returns_df.to_json(returns_path, orient="split", date_format="iso")

            market_symbol = "510300"
            market_df = provider.get_historical_data(market_symbol, period)
            if market_df is not None and not market_df.empty and "close" in market_df.columns:
                market_returns = market_df["close"].pct_change().dropna()
                market_path = os.path.join(os.path.dirname(__file__), "config", "market_returns.json")
                market_returns.to_json(market_path, orient="split", date_format="iso")

            logger.info("历史收益率自动更新完成: %s 个标的", len(returns_df.columns))
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning("历史收益率自动更新异常: %s", e)

    def _apply_hedge_triggers(self, market_data: dict, hedge_plan: Optional[dict]) -> Optional[dict]:
        """基于 VIX / 回撤 / 市场状态做强制触发覆盖"""
        if not hedge_plan:
            return hedge_plan

        plan = dict(hedge_plan)
        vix = float(market_data.get("vix_future_price", 20.0) or 20.0)
        market_state = str(self.current_market_state)
        drawdown = float(getattr(self, "current_drawdown", 0.0) or 0.0)

        forced_reason = None
        if vix >= 30.0:
            forced_reason = f"VIX={vix:.1f} >= 30.0"
        elif market_state in {"elevated", "crisis", "stress"}:
            forced_reason = f"market_state={market_state}"
        elif drawdown >= 0.03:
            forced_reason = f"drawdown={drawdown * 100:.1f}% >= 3%"

        if forced_reason and plan.get("action") == "NO_HEDGE":
            plan["action"] = "FORCED_HEDGE"
            plan["forced_reason"] = forced_reason
            logger.warning("对冲被自动触发: %s", forced_reason)

        return plan

    def _generate_hedge_execution_orders(self, hedge_plan: Optional[dict]) -> Optional[dict]:
        """根据对冲决策生成可执行订单文件"""
        try:
            positions_path = os.path.join(
                _PROJECT_ROOT, "config", "positions.json"
            )  # P1-11: 原路径 utils/execution/config/ 不存在
            positions = {}
            prices = {}
            if os.path.exists(positions_path):
                with open(positions_path, encoding="utf-8") as f:
                    pos_data = json.load(f).get("positions", {})
                for item in pos_data.values():
                    code = item.get("code")
                    qty = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
                    price = item.get("est_price", 0.0)
                    if code and qty:
                        positions[code] = float(qty)
                        prices[code] = float(price)

            plan = hedge_plan or {}
            plan.setdefault("portfolio_beta", 0.0)
            plan.setdefault("total_hedge_pct", 0.0)
            plan.setdefault("action", "NO_HEDGE")

            try:
                # C2 修复: build_orders 需要 5 个参数 (plan, positions, prices, hedge_positions, positions_data)
                # 通过 load_positions() 统一加载, 避免调用参数缺失导致 TypeError 被静默吞掉后生成空单。
                #
                # 导入路径加固 (原: sys.path.insert(0, os.path.dirname(__file__))):
                #   hedge_execution_orders.py 位于项目根目录, 不在 utils/execution/ 下。
                #   旧实现把 utils/execution/ 插到 sys.path 最前, 依赖根目录已在 sys.path(_PROJECT_ROOT)
                #   兜底回退才能找到, 属脆弱依赖且每次调用污染 sys.path。
                #   现改为用 importlib 从 _PROJECT_ROOT 显式加载, 不依赖 sys.path 顺序, 目录调整不失效。
                import importlib.util  # noqa: F401

                # W6.3.3: 不预声明 Optional[ModuleType] — 在 if/else 两分支各自赋值,
                # mypy 从两分支的共同类型推断为 ModuleType, 无需 None 收窄。
                # 消除原 [assignment] ignore + 新引入的 [arg-type] / [union-attr] 错误。
                _mod_path = os.path.join(_PROJECT_ROOT, "hedge_execution_orders.py")
                if os.path.exists(_mod_path):
                    _spec = importlib.util.spec_from_file_location("_hedge_execution_orders_c2", _mod_path)
                    # spec_from_file_location 返回 Optional[ModuleSpec], 需显式 None 守卫。
                    if _spec is None or _spec.loader is None:
                        raise ImportError(f"无法创建 importlib spec for {_mod_path}")
                    _hedge_mod = importlib.util.module_from_spec(_spec)
                    _spec.loader.exec_module(_hedge_mod)
                else:
                    # 显式回退到模块导入 (若根目录在 sys.path)
                    import hedge_execution_orders as _hedge_mod

                build_orders = _hedge_mod.build_orders
                load_positions = _hedge_mod.load_positions

                positions, prices, hedge_positions, positions_data = load_positions()
                orders = build_orders(plan, positions, prices, hedge_positions, positions_data)
            except (ImportError, TypeError, KeyError, ValueError, OSError, AttributeError) as err:
                # 交易/风控路径不允许静默降级为空单, 必须显式失败并上抛, 避免组合裸露在下行风险中。
                logger.error("生成对冲执行单失败(不允许静默空单): %s", err)
                raise

            report_dir = os.path.join(os.path.dirname(__file__), "reports")
            os.makedirs(report_dir, exist_ok=True)
            out_path = os.path.join(report_dir, f"hedge_execution_orders_{datetime.now().strftime('%Y%m%d')}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(orders, f, ensure_ascii=False, indent=2)

            logger.info("对冲执行单已生成: %s", out_path)

            # P1-3 修复: 对冲订单双路径统一。
            # build_orders() 返回的 orders 使用 action 字段 (BUY_PROTECTION/SELL_SHORT/BUY),
            # 而 hedge_order_executor._collect_pending_orders 期望 direction 字段
            # (BUY_PUT/SELL_CALL_COVERED) 且必须 status=="PENDING" 才会收集。
            # 此前 _generate_hedge_execution_orders 仅写盘 hedge_execution_orders_*.json,
            # 无下游消费者 (执行断链)。现在把转换后的订单回写进 trade_plan 的
            # hedge_execution.active_orders 嵌套字典, 由 hedge_order_executor 统一撮合。
            self._writeback_hedge_orders_to_trade_plan(orders)
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning("生成对冲执行单失败: %s", e)

    def _writeback_hedge_orders_to_trade_plan(self, orders_result: dict) -> None:
        """将 build_orders 产出的对冲订单回写进 trade_plan, 供 hedge_order_executor 撮合。

        build_orders() 返回 {date, action, portfolio_beta, hedge_pct, orders:[...]}。
        orders 中期权订单使用 action 字段 (BUY_PROTECTION/SELL_SHORT/BUY),
        而 hedge_order_executor._collect_pending_orders 期望:
          - direction = "BUY_PUT" / "SELL_CALL_COVERED"
          - status = "PENDING"
          - type = "OPTIONS"
        本方法做字段转换并写入 trade_plan["hedge_execution"]["active_orders"] 嵌套字典
        (put_protection / covered_call 子列表), 与 _collect_pending_orders 契约对齐。
        """
        if not isinstance(orders_result, dict):
            return
        raw_orders = orders_result.get("orders") or []
        if not raw_orders:
            logger.info("对冲订单回写: 无订单, 跳过 trade_plan 回写")
            return

        # --- 字段转换: action → direction, 补 status/type/order_id ---
        put_orders: list = []
        call_orders: list = []
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        for idx, o in enumerate(raw_orders):
            if not isinstance(o, dict):
                continue
            otype = str(o.get("type", "")).upper()
            action = str(o.get("action", "")).upper()
            instrument = str(o.get("instrument", "")).lower()

            # 仅处理期权订单 (跳过期货/避险资产)
            is_option = (
                "OPTION" in otype
                or "put" in instrument
                or "call" in instrument
                or "BUY_PROTECTION" in action
            )
            if not is_option:
                continue

            # action → direction 映射
            if "BUY_PROTECTION" in action or ("BUY" in action and "put" in instrument):
                direction = "BUY_PUT"
                target_list = put_orders
            elif "SELL" in action and ("call" in instrument or "COVERED" in action):
                direction = "SELL_CALL_COVERED"
                target_list = call_orders
            elif "put" in instrument:
                direction = "BUY_PUT"
                target_list = put_orders
            elif "call" in instrument:
                direction = "SELL_CALL_COVERED"
                target_list = call_orders
            else:
                continue

            # 从 instrument 提取标的代码 (如 "510050 Put" → "510050")
            import re as _re
            m = _re.match(r"\s*(\d{6})", instrument)
            underlying_code = m.group(1) if m else ""
            exchange_suffix = ".SH" if underlying_code and underlying_code[0] in "56" else ".SZ"
            underlying_full = f"{underlying_code}{exchange_suffix}" if underlying_code else ""

            converted = {
                "order_id": f"HEDGE_{direction}_{underlying_code}_{ts}_{idx}",
                "type": "OPTIONS",
                "instrument": o.get("instrument", ""),
                "exchange": o.get("exchange", ""),
                "direction": direction,
                "underlying": underlying_full,
                "underlying_code": underlying_full,
                "contracts": o.get("contracts", 0),
                "strike_rule": f"OTM_{int(float(o.get('otm_pct', 0.05)) * 100)}pct" if o.get("otm_pct") else "OTM_5pct",
                "est_strike_price": float(o.get("strike", 0.0)),
                "otm_pct": o.get("otm_pct", 0.05),
                "premium_budget": o.get("premium_budget", 0.0),
                "status": "PENDING",
                "rationale": o.get("reason", ""),
                "framework": o.get("framework", []),
                "priority": o.get("priority", "primary"),
            }
            target_list.append(converted)

        if not put_orders and not call_orders:
            logger.info("对冲订单回写: 无期权订单 (仅期货/避险), 跳过")
            return

        # --- 定位 trade_plan 文件 (与 hedge_order_executor L328-335 搜索逻辑一致) ---
        date_compact = datetime.now().strftime("%Y%m%d")
        v83_trade_plans = os.path.join(_PROJECT_ROOT, "v8.3_institutional", "trade_plans")
        plan_candidates = [
            os.path.join(v83_trade_plans, f"trade_plan_{date_compact}.json"),
            os.path.join(_PROJECT_ROOT, f"trade_plan_{date_compact}.json"),
        ]
        plan_path = next((p for p in plan_candidates if os.path.exists(p)), None)

        if not plan_path:
            logger.warning(
                "对冲订单回写: 未找到 trade_plan_%s.json, 期权订单无法回写撮合。"
                "请先运行 EOD 管道生成 trade_plan, 或手动运行 hedge_order_executor。",
                date_compact,
            )
            return

        try:
            with open(plan_path, encoding="utf-8") as f:
                plan = json.load(f)
            if not isinstance(plan, dict):
                logger.warning("对冲订单回写: trade_plan 格式异常 (非 dict), 跳过")
                return

            # 写入 hedge_execution.active_orders 嵌套字典
            he = plan.setdefault("hedge_execution", {})
            if not isinstance(he, dict):
                he = {}
                plan["hedge_execution"] = he

            active_orders = he.get("active_orders")
            if not isinstance(active_orders, dict):
                active_orders = {
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "status": "PENDING_EXECUTION",
                    "put_protection": [],
                    "covered_call": [],
                }
                he["active_orders"] = active_orders

            # 追加到已有列表 (不覆盖已有 PENDING 订单)
            existing_put = active_orders.get("put_protection") or []
            existing_call = active_orders.get("covered_call") or []
            active_orders["put_protection"] = existing_put + put_orders
            active_orders["covered_call"] = existing_call + call_orders
            active_orders["date"] = datetime.now().strftime("%Y-%m-%d")
            active_orders["status"] = "PENDING_EXECUTION"
            active_orders["generated_at"] = datetime.now().isoformat()

            # 同步写入 hedge_execution.options_orders / covered_call_orders (兼容来源 1/2)
            he["options_orders"] = (he.get("options_orders") or []) + put_orders
            he["covered_call_orders"] = (he.get("covered_call_orders") or []) + call_orders

            with open(plan_path, "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)

            logger.info(
                "对冲订单回写 trade_plan 成功: %s (PUT=%d, CALL=%d)",
                plan_path,
                len(put_orders),
                len(call_orders),
            )
        except (OSError, json.JSONDecodeError, TypeError) as e:
            logger.error("对冲订单回写 trade_plan 失败: %s", e)

    def _get_market_data(self) -> dict:
        """获取市场数据 - 优先 Wind MCP"""
        try:
            # 优先从 Wind MCP 获取沪深300实时价格
            index_price = None
            if _WIND_MCP_AVAILABLE and wind_get_quote is not None:
                try:
                    quote = wind_get_quote("510300.SH", is_fund=True)
                    if quote and quote.get("price") is not None:
                        index_price = float(quote["price"])
                except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
                    logger.debug("Wind MCP 获取市场指数失败: %s", e)

            # 回退到历史数据
            if index_price is None or index_price <= 0:
                try:
                    base_dir = os.path.dirname(__file__)
                    market_path = os.path.join(base_dir, "config", "market_returns.json")
                    if os.path.exists(market_path):
                        market_returns = pd.read_json(market_path, orient="split", typ="series")
                        if not market_returns.empty:
                            index_price = safe_float(float(market_returns.iloc[-1]) * 1000 + 3000)
                except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
                    logger.warning("读取 market_returns.json 失败, 跳过指数价格推断", exc_info=True)

            # 最终兜底 — 生产环境拒绝静默使用3000假指数，抛出异常强制上游处理
            if index_price is None or index_price <= 0:
                raise ValueError(
                    "市场指数价格无法获取 (Wind MCP / 历史数据均不可用)，"
                    "拒绝使用硬编码 3000 假数据。请检查数据源连接或 config/market_returns.json。"
                )

            # 尝试从历史收益率计算真实指标
            try:
                base_dir = os.path.dirname(__file__)
                returns_path = os.path.join(base_dir, "config", "returns_history.json")
                market_path = os.path.join(base_dir, "config", "market_returns.json")

                if os.path.exists(returns_path) and os.path.exists(market_path):
                    returns = pd.read_json(returns_path, orient="split")
                    market_returns = pd.read_json(market_path, orient="split", typ="series")
                    returns.columns = returns.columns.astype(str)

                    if not market_returns.empty and not returns.empty:
                        # 计算市场波动率
                        vol = safe_float(market_returns.std() * (252**0.5))

                        # 计算 VaR
                        var_95 = safe_float(market_returns.quantile(0.05))
                        var_99 = safe_float(market_returns.quantile(0.01))

                        # 计算相关性矩阵
                        corr_matrix = returns.corr().fillna(0.0).values.tolist()
                        if len(corr_matrix) == 0:
                            corr_matrix = np.eye(3).tolist()

                        # 计算 beta（组合相对市场）
                        betas = []
                        for col in returns.columns:
                            try:
                                series = returns[col].dropna()
                                if len(series) < 5:
                                    continue
                                cov = series.cov(market_returns)
                                var_m = market_returns.var()
                                if var_m > 0 and not np.isnan(cov):
                                    betas.append(float(cov / var_m))
                            except (ValueError, TypeError, KeyError, AttributeError, OSError):
                                logger.warning(f"计算 {col} beta 失败, 跳过", exc_info=True)
                                continue

                        # 组合 beta 取有效值的平均
                        if betas:
                            beta = safe_float(sum(betas) / len(betas))
                        else:
                            beta = safe_float(1.0)

                        # 跟踪误差
                        tracking_error = safe_float(market_returns.std() * (252**0.5) * 0.5)

                        return {
                            "index_price": safe_float(index_price),
                            "volatility": safe_float(vol),
                            "var_95": safe_float(var_95),
                            "var_99": safe_float(var_99),
                            "liquidity": safe_float(1.0),
                            "sentiment_score": safe_float(0.0),
                            "correlation_matrix": corr_matrix,
                            "beta": safe_float(beta),
                            "tracking_error": safe_float(tracking_error),
                            "market_correlation": safe_float(0.7),
                            "volatility_skew": safe_float(0.0),
                            "volatility_term": safe_float(0.0),
                            "vix_future_price": safe_float(20.0),
                            "kurtosis": safe_float(3.0),
                            "skewness": safe_float(0.0),
                            "extreme_events": safe_float(0, default=0),
                        }
            except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
                logger.debug("历史收益率市场数据计算失败: %s", e)

            # 历史收益率计算失败 — 上游数据不可用，不允许静默返回假数据
            # (此处 raise 在外层 try 中, e 不在作用域, 异常链由外层 except 的 `from e` 保留)
            raise RuntimeError(
                "市场数据计算失败: 历史收益率文件存在但计算异常。"
                "拒绝返回硬编码假数据 (volatility=0.15, VaR=0.02 等)。"
                "请检查 config/returns_history.json 和 config/market_returns.json 文件完整性。"
            )
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning("获取市场数据失败: %s", e)
            raise RuntimeError(
                f"市场数据完全不可用 (index_price={index_price})，拒绝返回全量硬编码假数据。原始错误: {e}"
            ) from e

    def _risk_pre_check(self, market_state_data: dict) -> bool:
        """执行风险预检查"""
        try:
            market_state = market_state_data["market_state"]
            if market_state in ["crisis", "stress"]:
                logger.warning(f"市场状态异常: {market_state}")
                return False

            var_95 = market_state_data.get("individual_scores", {}).get("var", 0)
            if var_95 > 0.8:
                logger.warning(f"VaR风险过高: {var_95}")
                return False

            liquidity = market_state_data.get("individual_scores", {}).get("liquidity", 0)
            if liquidity > 0.8:
                logger.warning(f"流动性风险过高: {liquidity}")
                return False

            logger.debug("风险预检查通过")
            return True

        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"风险预检查失败: {e}")
            return False

    def _start_performance_monitoring(self) -> None:
        """启动性能监控"""
        monitoring_thread = threading.Thread(target=self._performance_monitoring_loop)
        monitoring_thread.daemon = True
        monitoring_thread.start()

    def _performance_monitoring_loop(self) -> None:
        """性能监控循环"""
        while self.is_running:
            try:
                summary = self.get_system_summary()
                perf = summary["performance_metrics"]
                total_orders = self.order_router.execution_stats["total_orders"]

                if total_orders > 0:
                    if perf["execution_success_rate"] < 0.8:
                        logger.warning("执行成功率过低，系统性能下降")

                    if perf["average_execution_time"] > 30:
                        logger.warning("执行时间过长，系统性能下降")

                    if perf["average_slippage"] > 0.01:
                        logger.warning("滑点过大，系统性能下降")
                else:
                    logger.debug("性能监控：暂无订单执行记录，跳过阈值告警")

                time.sleep(300)
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
                logger.error(f"性能监控错误: {e}")
                time.sleep(300)

    def get_system_summary(self) -> dict:
        """获取系统总结"""
        market_evaluator_summary = self.market_evaluator.get_market_state_summary()

        hedge_summary = {
            "available": _HEDGE_AVAILABLE,
            "enabled": self.hedge_enabled,
            "coordinator_loaded": self.hedge_coordinator is not None,
            "last_action": self.last_hedge_plan.get("action") if isinstance(self.last_hedge_plan, dict) else None,
            "last_total_hedge_pct": self.last_hedge_plan.get("total_hedge_pct")
            if isinstance(self.last_hedge_plan, dict)
            else None,
            "last_total_cost_pct": self.last_hedge_plan.get("total_cost_pct")
            if isinstance(self.last_hedge_plan, dict)
            else None,
        }

        return {
            "system_status": "running" if self.is_running else "stopped",
            "current_market_state": self.current_market_state,
            "current_plan": self.current_execution_plan,
            "routed_orders_count": len(self.current_routed_orders),
            "hedge": hedge_summary,
            # 各组件状态
            "trading_calendar": self.trading_calendar.get_execution_summary(),
            "market_state_evaluator": market_evaluator_summary,
            "execution_strategy": self.execution_strategy.get_execution_summary(),
            "order_router": self.order_router.get_router_summary(),
            # 系统统计
            "system_history_count": len(self.system_history),
            "last_execution": self.system_history[-1]["timestamp"] if self.system_history else None,
            # 性能指标
            "performance_metrics": {
                "execution_success_rate": self.order_router.execution_stats["successful_orders"]
                / max(self.order_router.execution_stats["total_orders"], 1),
                "average_execution_time": self.order_router.execution_stats["average_time"],
                "average_slippage": self.order_router.execution_stats["average_slippage"],
            },
        }

    def get_execution_schedule(self, days_ahead: int = 7) -> list[dict]:
        """获取执行计划"""
        return self.trading_calendar.get_execution_schedule(days_ahead)

    def _generate_rebalance_orders(self) -> Optional[dict]:
        """生成再平衡执行订单"""
        try:
            # T3.6 迁移修正: 使用绝对路径导入, 不再 sys.path.insert
            from utils.execution.rebalance_execution_orders import (
                TARGET_ALLOCATION,
                build_report,
                calc_current_allocation,
                generate_rebalance_orders,
                load_positions,
            )

            positions, prices, styles = load_positions()
            style_allocation = calc_current_allocation(positions, prices, styles)
            orders = generate_rebalance_orders(style_allocation, TARGET_ALLOCATION, positions, prices)
            report = build_report(style_allocation, TARGET_ALLOCATION, orders)

            # T3.6 迁移修正: 输出到项目根目录的 reports/, 而非 __file__ 所在目录
            report_dir = os.path.join(_PROJECT_ROOT, "reports")
            os.makedirs(report_dir, exist_ok=True)
            out_path = os.path.join(report_dir, f"rebalance_execution_orders_{datetime.now().strftime('%Y%m%d')}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)

            valid_count = report["summary"]["valid_orders"]
            total_count = report["summary"]["total_orders"]
            logger.info(f"再平衡订单生成完成: {valid_count}/{total_count} 有效订单")

            # P0-3 修复: 再平衡订单此前只写盘不执行 (执行断链)。
            # 将有效订单构建成 execution_plan 接入 OrderRouter, 真正进入撮合/执行队列。
            valid_orders = [o for o in orders if o.get("validation", {}).get("valid")]
            if valid_orders:
                slices = []
                for o in valid_orders:
                    slices.append(
                        {
                            "slice_id": f"rebal_{o['code']}_{o['action']}_{datetime.now().strftime('%H%M%S')}",
                            "instrument": o["code"],
                            "direction": "buy" if o["action"] == "BUY" else "sell",
                            "shares": o.get("shares", 0),
                            # G2 修复 (2026-08-08): 补齐 _execute_order 契约字段。
                            # _execute_order 读 slice_info["size"] 作为成交数量, 读 slice_info["price"]
                            # 作为限价; 此前仅填 shares/est_price, 导致 qty=0 被"无效数量"拒绝,
                            # 再平衡订单永远无法撮合成交 (只生成不执行断链)。
                            "size": o.get("shares", 0),
                            "price": o.get("est_price", 0.0),
                            "est_price": o.get("est_price", 0.0),
                            "est_amount": o.get("est_amount", 0.0),
                            "style": o.get("style", ""),
                        }
                    )
                rebalance_plan = {
                    "trade_id": f"REBAL_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    "instrument": slices[0]["instrument"],
                    "total_direction": slices[0]["direction"],
                    "num_slices": len(slices),
                    "slices": slices,
                }
                routing_result = self.order_router.route_order(rebalance_plan, self.current_market_state)
                # P1-1: 附带 execution_plan, 供 _execute_daily_trading 步骤 11 同步 current_execution_plan
                routing_result["execution_plan"] = rebalance_plan
                if routing_result.get("success"):
                    self.order_router.process_execution_queue()
                    logger.info(
                        f"再平衡订单已接入执行: {len(routing_result.get('routed_orders', []))}个订单进入执行队列"
                    )
                else:
                    logger.error(f"再平衡订单路由失败: {routing_result.get('error')}")
                report["routing"] = routing_result
            else:
                logger.info("再平衡订单无有效订单, 无需路由")

            return report
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"生成再平衡订单失败: {e}")
            return None


# 主程序
if __name__ == "__main__":
    logger.info("自动化执行系统启动")
    logger.info("=" * 50)

    # 创建自动化执行系统
    execution_system = AutomatedExecutionSystem(total_capital=1000000)

    # 尝试开启对冲模块
    if execution_system.enable_hedge(True):
        logger.info("对冲模块: 已开启")
    else:
        logger.info("对冲模块: 不可用或开启失败")

    # 启动系统
    execution_system.start_system()

    # 等待一段时间观察执行
    time.sleep(10)

    # 获取系统总结
    summary = execution_system.get_system_summary()

    logger.info("\n系统状态")
    logger.info("=" * 50)
    logger.info(f"系统状态: {summary['system_status']}")
    logger.info(f"当前市场状态: {summary['current_market_state']}")
    logger.info(f"当前执行计划: {'有' if summary['current_plan'] else '无'}")
    logger.info(f"路由订单数: {summary['routed_orders_count']}")

    logger.info("\n组件状态")
    logger.info("=" * 50)
    logger.info(f"交易日历: 总执行数={summary['trading_calendar'].get('total_executions', 0)}")
    logger.info(f"市场评估: 当前状态={summary['market_state_evaluator'].get('current_state', 'unknown')}")
    logger.info(f"执行策略: 成功率={summary['execution_strategy'].get('success_rate', 0):.2%}")
    logger.info(f"订单路由: 活跃订单={summary['order_router'].get('total_active_orders', 0)}")

    logger.info("\n性能指标")
    logger.info("=" * 50)
    perf = summary["performance_metrics"]
    logger.info(f"执行成功率: {perf['execution_success_rate']:.2%}")
    logger.info(f"平均执行时间: {perf['average_execution_time']:.2f}秒")
    logger.info(f"平均滑点: {perf['average_slippage']:.4%}")

    # 获取未来执行计划
    future_schedule = execution_system.get_execution_schedule(3)
    logger.info("\n未来3天执行计划")
    logger.info("=" * 50)
    for day in future_schedule:
        logger.info(f"{day['date']}: {len(day['executions'])}个执行")

    logger.info("\n系统运行中...按Ctrl+C停止")

    try:
        # 保持运行
        while True:
            time.sleep(30)
            # 更新状态
            current_summary = execution_system.get_system_summary()
            logger.info(
                f"\r当前时间: {datetime.now().strftime('%H:%M:%S')} | "
                f"系统状态: {current_summary['system_status']} | "
                f"市场状态: {current_summary['current_market_state']}",
                end="",
            )
    except KeyboardInterrupt:
        logger.info("\n正在停止系统...")
        execution_system.stop_system()
        logger.info("系统已停止")
