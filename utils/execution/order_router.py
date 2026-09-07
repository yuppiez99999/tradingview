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
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import uuid
from collections import deque
from datetime import datetime
from typing import TypedDict

logger = logging.getLogger(__name__)


# === 从本文件抽取的独立组件 ===
from utils.execution.execution_components import (  # noqa: E402
    SpecialDayEntry,  # noqa: F401 — re-export for backward compat
)


# W6.3.3 类型安全: 为 special_days / execution_pools 的字面量字典添加 TypedDict,
# 消除 mypy [index] 错误 (裸 Dict[str, Any] 无法推断嵌套 value 类型)。
class ExecutionPoolEntry(TypedDict):
    """订单路由执行池配置条目。"""

    broker: str
    priority: str
    max_concurrent: int
    min_balance: int


# T3.6 迁移修正: __file__ 从根目录变为 utils/execution/, 需回退两级到项目根目录
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# G2 补齐: 成交回报统一落盘层 (fail-safe, 导入失败不影响执行链路)
try:
    from utils.execution.fills_store import FillsStore

    _FILLS_STORE_AVAILABLE = True
except (ImportError, AttributeError):
    FillsStore = None
    _FILLS_STORE_AVAILABLE = False


class OrderRouter:
    """
    订单路由器 — 生产级: 对接 SmartOrderRouter 进行实盘执行
    """

    def __init__(
        self,
        smart_router: object = None,
        broker: object = None,
        kill_switch: object = None,
    ) -> None:
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
            "normal": {
                "broker": "broker_a",
                "priority": "normal",
                "max_concurrent": 10,
                "min_balance": 100000,
            },
            "priority": {
                "broker": "broker_b",
                "priority": "high",
                "max_concurrent": 5,
                "min_balance": 500000,
            },
            "emergency": {
                "broker": "broker_c",
                "priority": "critical",
                "max_concurrent": 3,
                "min_balance": 1000000,
            },
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
                    return {
                        "success": False,
                        "error": "无可用执行池",
                        "suggested_action": "等待",
                    }

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

        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
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
            active_count = sum(
                1
                for order in self.active_orders.values()
                if order.get("target_pool") == pool_name
            )

        if active_count >= pool["max_concurrent"]:
            return False

        # 余额限制检查 — R1-20260907 审查: 此处原为"简化处理直接 return True"占位 stub。
        # 执行池 min_balance 属路由软约束 (池选择), 不再在此判定真实账户余额;
        # 真实资金校验已上移至 _execute_order 实盘分支的 _enforce_live_cash (决策路径 fail-closed)。
        # paper-only: 本占位在真实资金上线前不得作为余额依据 (见 R1 审查修复记录)。
        return True

    def _find_available_pool(self) -> ExecutionPoolEntry | None:
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
            active_count = sum(
                1
                for order in self.active_orders.values()
                if order.get("target_pool") == pool_name
            )
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

        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
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

    def _execute_order(self, order: dict) -> dict:  # noqa: C901
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
                # R1-20260907 审查: 实盘下单前真实资金前置校验 (决策路径 fail-closed,
                # 取代 _check_pool_availability 处的余额占位 stub)。
                cash_ok, cash_err = self._enforce_live_cash(
                    symbol=symbol, side=side, qty=qty, limit_price=limit_price
                )
                if not cash_ok:
                    return {
                        "success": False,
                        "error": cash_err,
                        "cash_blocked": True,
                    }
                # P0-2 修复: 实盘执行前必须检查 KillSwitch 熔断状态
                # 原代码直接调用 smart_router, 即使 KillSwitch 已触发 L2/L3 熔断仍会下单
                if self._kill_switch is not None:
                    try:
                        ks_status = self._kill_switch.check_margin_status()
                        ks_level = (
                            ks_status.get("level", 0)
                            if isinstance(ks_status, dict)
                            else 0
                        )
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
                    except (
                        ValueError,
                        KeyError,
                        TypeError,
                        AttributeError,
                        OSError,
                        RuntimeError,
                    ) as ks_err:
                        # KillSwitch 检查异常时 fail-closed: 拒绝下单
                        logger.error(
                            "[OrderRouter] KillSwitch 检查异常, fail-closed 拒绝下单: %s",
                            ks_err,
                        )
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

                avg_price = (
                    sum(f.avg_price * f.filled_qty for f in fills) / total_filled
                )

                # 滑点 = avg_price vs arrival_price
                # P1 修复: arrival_price 可能为 0 (盘口缺失 mid/ask1/bid1), 加强保护
                arrival_price = (
                    order_book.get("mid")
                    or (order_book.get("ask1", 0) + order_book.get("bid1", 0)) / 2
                )
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

            # ---- 回测/模拟路径 (保留兼容) ----
            # 修复 BUG-E4: 严格校验限价, 防止 0 价格成交
            if (
                limit_price is None
                or not isinstance(limit_price, (int, float))
                or limit_price <= 0
            ):
                # 市价单: 尝试从持仓文件获取参考价
                ref_price = self._get_reference_price(symbol)
                if ref_price is None or ref_price <= 0:
                    return {
                        "success": False,
                        "error": f"无法获取 {symbol} 参考价格, 拒绝生成 0 价格成交",
                    }
                limit_price = ref_price

            # P1 修复: order['target_pool'] 用 .get() 保护, 缺失时用 'normal' 兜底
            pool_name = order.get("target_pool", "normal")
            pool_cfg = self.execution_pools.get(pool_name)
            if pool_cfg is None:
                logger.warning(
                    "[OrderRouter] 未知的 target_pool=%s, 用 'normal' 兜底",
                    pool_name,
                )
                pool_cfg = self.execution_pools.get("normal")
            broker_name = (
                pool_cfg.get("broker", "simulated_broker")
                if pool_cfg
                else "simulated_broker"
            )

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

        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            return {"success": False, "error": str(e)}

    def _enforce_live_cash(
        self, symbol: str, side: str, qty: float, limit_price: float | None
    ) -> tuple[bool, str]:
        """实盘下单前真实资金校验 (R1-20260907 审查: 取代 _check_pool_availability 余额占位 stub).

        决策路径 fail-closed:
        - broker 提供可用资金 (get_available_funds 优先, get_account_info.available 兜底) 时,
          买入名义金额 > 可用资金 → 拒绝下单;
        - 资金查询抛异常 (无法确认余额) → 拒绝下单;
        观测路径 fail-open:
        - broker 无资金接口/字段不可解析 → 显式告警后放行, 由券商端拒单兜底 (不静默)。

        注: 市价单 (limit_price 无效) 无法估算名义金额, 不在此拦截 (交由行情价后校验)。
        """
        if side != "BUY" or qty <= 0:
            return True, ""
        try:
            notional = qty * float(limit_price or 0)
        except (TypeError, ValueError):
            notional = 0.0
        if notional <= 0:
            return True, ""
        available: float | None = None
        try:
            funds_fn = getattr(self.broker, "get_available_funds", None)
            if callable(funds_fn):
                raw = funds_fn()
                if isinstance(raw, (int, float)):
                    available = float(raw)
            if available is None:
                acct_fn = getattr(self.broker, "get_account_info", None)
                if callable(acct_fn):
                    acct = acct_fn()
                    if isinstance(acct, dict):
                        raw = acct.get("available")
                        if isinstance(raw, (int, float)):
                            available = float(raw)
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as exc:
            return False, f"账户资金查询异常, 拒绝实盘下单 {symbol}: {exc}"
        if available is None:
            logger.warning(
                "[OrderRouter] broker(%s) 无可用资金接口/字段, 实盘资金前置校验跳过 "
                "(依赖券商端拒单兜底, 不静默)",
                type(self.broker).__name__,
            )
            return True, ""
        if available < 0:
            return False, f"账户可用资金异常 ({available}), 拒绝实盘下单 {symbol}"
        if notional > available:
            return False, (
                f"可用资金不足: 需约 ¥{notional:,.2f} > 可用 ¥{available:,.2f}, "
                f"拒绝实盘买入 {symbol}"
            )
        return True, ""

    def _get_reference_price(self, symbol: str) -> float | None:
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
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
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
