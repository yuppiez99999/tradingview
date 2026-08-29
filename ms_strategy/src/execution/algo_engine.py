"""
v7.5 AlgoEngine — 执行算法引擎：TWAP / VWAP / POV 策略调度
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL §3.2-3.4
"""
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


class AlgoType(StrEnum):
    """执行算法类型枚举"""
    TWAP = "TWAP"
    VWAP = "VWAP"
    POV = "POV"
    ICEBERG = "ICEBERG"


@dataclass
class TradingSession:
    """交易时段定义"""
    name: str
    start: time
    end: time
    algo: str  # 'TWAP' | 'VWAP' | 'POV'


@dataclass
class AlgoConfig:
    """算法配置"""
    iceberg_pct_of_depth: float = 0.10
    max_attempts: int = 5
    throttle_seconds: int = 2
    per_trade_break: float = 0.005
    daily_break: float = 0.010
    global_slow_threshold: float = 0.003

    # Algo 特定参数
    twap_slice_minutes: int = 1
    vwap_window_minutes: int = 30
    pov_participation_rate: float = 0.10


@dataclass
class SliceOrder:
    """拆单后的子订单"""
    quantity: int
    suggested_time: Optional[datetime] = None
    price: Optional[float] = None
    algo: str = "TWAP"
    limit_price: Optional[float] = None


class AlgoEngine:
    """v7.5 算法执行引擎：按交易时段匹配 TWAP/VWAP/POV"""

    # P1-1: 期货夜盘时段 (21:00-02:30 次日)
    FUTURES_NIGHT_SESSIONS: list[TradingSession] = [
        TradingSession('FUT_NIGHT', time(21, 0), time(23, 59), 'TWAP'),
        TradingSession('FUT_NIGHT2', time(0, 0), time(2, 30), 'TWAP'),
    ]

    def __init__(self, sor=None, config_path: Optional[str] = None):
        """
        Args:
            sor: SmartOrderRouter 实例 (可选，可后续通过 set_sor 注入)
            config_path: execution.yaml 路径
        """
        self.sor = sor
        self.config_path = config_path

        # 默认时段
        self.sessions: list[TradingSession] = [
            TradingSession('OPEN',  time(9, 30), time(9, 45),  'TWAP'),
            TradingSession('MORN',  time(9, 45), time(11, 30), 'VWAP'),
            TradingSession('NOON',  time(13, 0), time(14, 30), 'VWAP'),
            TradingSession('CLOSE', time(14, 30), time(15, 0), 'TWAP'),
        ]
        self.algo_cfg = AlgoConfig()

        if config_path:
            self._load_config(config_path)

    def _load_config(self, path: str) -> None:
        try:
            with open(path, encoding='utf-8') as f:
                cfg = yaml.safe_load(f)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning(f"加载执行配置失败: {e}，使用默认配置")
            return

        # 解析时段
        if 'trading_windows' in cfg:
            self.sessions = []
            for tw in cfg['trading_windows']:
                start_h, start_m = map(int, tw['start'].split(':'))
                end_h, end_m = map(int, tw['end'].split(':'))
                self.sessions.append(TradingSession(
                    name=tw.get('session', 'UNKNOWN'),
                    start=time(start_h, start_m),
                    end=time(end_h, end_m),
                    algo=tw.get('algo', 'TWAP')
                ))

        # 解析 SOR 参数
        sor_cfg = cfg.get('sor', {})
        self.algo_cfg = AlgoConfig(
            iceberg_pct_of_depth=sor_cfg.get('iceberg_pct_of_depth', 0.10),
            max_attempts=sor_cfg.get('max_attempts', 5),
            throttle_seconds=sor_cfg.get('throttle_seconds', 2),
            per_trade_break=cfg.get('slippage', {}).get('per_trade_break', 0.005),
            daily_break=cfg.get('slippage', {}).get('daily_break', 0.010),
            global_slow_threshold=cfg.get('slippage', {}).get('global_slow_threshold', 0.003),
        )

    # ---------- 时段判断 ----------
    def get_current_session(self, now: Optional[time] = None) -> Optional[TradingSession]:
        """返回当前交易时段"""
        if now is None:
            now = datetime.now().time()
        for session in self.sessions:
            if session.start <= now <= session.end:
                return session
        return None

    def is_trading_hours(self) -> bool:
        return self.get_current_session() is not None

    def is_futures_trading_hours(self) -> bool:
        """P1-1: 判断是否在期货交易时段 (含夜盘)"""
        now = datetime.now().time()
        # 先检查日盘
        for session in self.sessions:
            if session.start <= now <= session.end:
                return True
        # 再检查夜盘
        for session in self.FUTURES_NIGHT_SESSIONS:
            if session.start <= now <= session.end:
                return True
        return False

    def get_asset_sessions(self, asset_type: str = "STOCK") -> list[TradingSession]:
        """P1-1: 根据品种类型返回适用的交易时段

        Args:
            asset_type: "STOCK" / "FUTURE" / "OPTION"

        Returns:
            交易时段列表
        """
        if asset_type in ("FUTURE", "FUTURES"):
            # 期货含夜盘
            return self.sessions + self.FUTURES_NIGHT_SESSIONS
        # 股票/ETF/期权 仅日盘
        return self.sessions

    def set_sor(self, sor) -> None:
        """后续注入 SmartOrderRouter"""
        self.sor = sor

    def _split_iceberg(self, total_qty: int, depth: Optional[dict],
                       now: datetime) -> list[SliceOrder]:
        """ICEBERG 拆单：每片取盘口深度的 10%"""
        if depth:
            bid_vol = depth.get("bid1_vol", 0)
            ask_vol = depth.get("ask1_vol", 0)
            ref_vol = max(bid_vol, ask_vol, 1)
            slice_size = max(1, int(ref_vol * 0.10))
        else:
            slice_size = max(1, total_qty // 10)

        slices: list[SliceOrder] = []
        remaining = total_qty
        offset = 0
        while remaining > 0:
            q = min(slice_size, remaining)
            mid_price = None
            if depth and "bid1" in depth and "ask1" in depth:
                mid_price = (depth["bid1"] + depth["ask1"]) / 2.0
            slices.append(SliceOrder(
                quantity=q,
                suggested_time=now + timedelta(minutes=offset),
                price=mid_price,
            ))
            remaining -= q
            offset += 1
        return slices

    def _split_twap(self, total_qty: int, window_minutes: int,
                    now: datetime) -> list[SliceOrder]:
        """TWAP 拆单：均匀分配到时间窗口"""
        n_slices = max(1, window_minutes // 5)
        slice_size = max(1, total_qty // n_slices)
        slices: list[SliceOrder] = []
        remaining = total_qty
        for i in range(n_slices):
            q = min(slice_size, remaining)
            if q <= 0:
                break
            slices.append(SliceOrder(
                quantity=q,
                suggested_time=now + timedelta(minutes=i * 5),
            ))
            remaining -= q
        # 余量并入最后一片
        if remaining > 0 and slices:
            last = slices[-1]
            slices[-1] = SliceOrder(
                quantity=last.quantity + remaining,
                suggested_time=last.suggested_time,
                price=last.price,
            )
        return slices

    def _split_vwap(self, total_qty: int,
                    volume_profile: Optional[list[float]],
                    now: datetime) -> Optional[list[SliceOrder]]:
        """VWAP 拆单：按成交量分布分配。无有效 profile 时返回 None 以触发回退"""
        if not volume_profile or len(volume_profile) == 0:
            return None
        total_vol = sum(volume_profile)
        if total_vol <= 0:
            return None

        slices: list[SliceOrder] = []
        for i, v in enumerate(volume_profile):
            q = int(total_qty * v / total_vol)
            if q > 0:
                slices.append(SliceOrder(
                    quantity=q,
                    suggested_time=now + timedelta(minutes=i * 5),
                ))
        # 余量
        allocated = sum(s.quantity for s in slices)
        if allocated < total_qty and slices:
            last = slices[-1]
            slices[-1] = SliceOrder(
                quantity=last.quantity + (total_qty - allocated),
                suggested_time=last.suggested_time,
                price=last.price,
            )
        return slices

    def _split_pov(self, total_qty: int, participation_rate: float,
                   now: datetime) -> list[SliceOrder]:
        """POV 拆单：按参与率拆分"""
        slice_size = max(1, int(total_qty * participation_rate))
        slices: list[SliceOrder] = []
        remaining = total_qty
        offset = 0
        while remaining > 0:
            q = min(slice_size, remaining)
            slices.append(SliceOrder(
                quantity=q,
                suggested_time=now + timedelta(minutes=offset),
            ))
            remaining -= q
            offset += 1
        return slices

    # ---------- 拆单算法 ----------
    def split(self,
              total_qty: int,
              side: str,
              algo: AlgoType = AlgoType.TWAP,
              depth: Optional[dict] = None,
              window_minutes: int = 30,
              volume_profile: Optional[list[float]] = None,
              participation_rate: float = 0.1) -> list[SliceOrder]:
        """
        拆单算法 — 将大单拆分为多个小单

        Args:
            total_qty: 总数量
            side: 'BUY' / 'SELL'
            algo: AlgoType.TWAP / VWAP / POV / ICEBERG
            depth: 盘口深度 {'bid1_vol':, 'ask1_vol':, 'bid1':, 'ask1':}
            window_minutes: 执行窗口分钟数
            volume_profile: VWAP 用的成交量分布
            participation_rate: POV 参与率

        Returns:
            SliceOrder 列表 (含 quantity, 建议时间, 价格等)
        """
        total_qty = int(total_qty)
        if total_qty <= 0:
            return []

        now = datetime.utcnow()

        if algo == AlgoType.ICEBERG:
            return self._split_iceberg(total_qty, depth, now)

        if algo == AlgoType.TWAP:
            return self._split_twap(total_qty, window_minutes, now)

        if algo == AlgoType.VWAP:
            result = self._split_vwap(total_qty, volume_profile, now)
            if result is not None:
                return result
            # 无 profile 回退 TWAP
            return self.split(total_qty, side, AlgoType.TWAP, depth,
                              window_minutes)

        if algo == AlgoType.POV:
            return self._split_pov(total_qty, participation_rate, now)

        # 默认: 单片
        return [SliceOrder(quantity=total_qty, suggested_time=now)]

    # ---------- 算法执行入口 ----------
    def execute_order(self, symbol: str, target_qty: int, side: str,
                      decision_price: float,
                      algo: Optional[str] = None,
                      price_limit: Optional[float] = None) -> list[dict]:
        """
        按当前时段自动选择算法执行

        Args:
            symbol: 标的代码
            target_qty: 目标数量
            side: 'BUY' / 'SELL'
            decision_price: 决策价格（用于滑点计算）
            algo: 显式指定算法（可选），否则按当前时段自动选择
            price_limit: 限价（可选）

        Returns:
            fills 列表
        """
        # EX-11: 未注入 SOR 时禁止解引用崩溃, fail-open 记日志返回空 (观测路径)
        if self.sor is None:
            logger.warning("[AlgoEngine] sor 未注入, 无法执行 %s %s %d, 跳过", symbol, side, target_qty)
            return []

        # 确定算法
        if algo is None:
            session = self.get_current_session()
            if session is None:
                # 非交易时段：市价执行
                logger.info(f"非交易时段，使用 TWAP 执行 {symbol}")
                return self.sor.execute_twap(symbol, target_qty, side, decision_price)
            algo = session.algo

        logger.info(f"[AlgoEngine] {symbol} {side} {target_qty} 使用 {algo}")

        if algo.upper() == 'TWAP':
            return self.sor.execute_twap(
                symbol, target_qty, side, decision_price,
                window_minutes=self.algo_cfg.twap_slice_minutes * 5
            )
        if algo.upper() == 'VWAP':
            return self.sor.execute_vwap(
                symbol, target_qty, side, decision_price,
                window_minutes=self.algo_cfg.vwap_window_minutes
            )
        if algo.upper() == 'POV':
            return self.sor.execute_pov(
                symbol, target_qty, side, decision_price,
                participation_rate=self.algo_cfg.pov_participation_rate
            )
        logger.warning(f"未知算法 {algo}，回退到标准执行")
        return self.sor.execute(symbol, target_qty, side, decision_price)

    # ---------- 批量执行 ----------
    def execute_batch(self, orders: list[dict]) -> list[dict]:
        """
        批量执行订单

        Args:
            orders: [{'symbol': ..., 'qty': ..., 'side': ..., 'decision_price': ..., 'algo': ...}, ...]

        Returns:
            所有成交
        """
        all_fills = []
        for order in orders:
            fills = self.execute_order(
                symbol=order['symbol'],
                target_qty=order['qty'],
                side=order.get('side', 'BUY'),
                decision_price=order.get('decision_price', 0),
                algo=order.get('algo'),
                price_limit=order.get('price_limit'),
            )
            all_fills.extend(fills)
        return all_fills

    # ---------- 日末重置 ----------
    def end_of_day(self) -> None:
        """日末清理：重置滑点状态"""
        self.sor.reset_daily_slip()
        logger.info("[AlgoEngine] 日末重置完成")
