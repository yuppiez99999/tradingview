"""
v7.5 AlgoEngine — 执行算法引擎：TWAP / VWAP / POV 策略调度
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL §3.2-3.4
"""
import logging
from pathlib import Path
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Optional, List, Dict
from dataclasses import dataclass

import yaml  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class AlgoType(str, Enum):
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

    def __init__(self, sor=None, config_path: Optional[str] = None):
        """
        Args:
            sor: SmartOrderRouter 实例 (可选，可后续通过 set_sor 注入)
            config_path: execution.yaml 路径 (P1-Q8: 不传则尝试 ConfigManager)
        """
        self.sor = sor
        self.config_path = config_path

        # 默认时段
        self.sessions: List[TradingSession] = [
            TradingSession('OPEN',  time(9, 30), time(9, 45), 'TWAP'),
            TradingSession('MORN',  time(9, 45), time(11, 30), 'VWAP'),
            TradingSession('NOON',  time(13, 0), time(14, 30), 'VWAP'),
            TradingSession('CLOSE', time(14, 30), time(15, 0), 'TWAP'),
        ]
        self.algo_cfg = AlgoConfig()

        if config_path:
            self._load_config(config_path)
        else:
            # P1-Q8: 未传 config_path, 尝试从 ConfigManager 加载 execution.yaml
            self._load_from_config_manager()

    def _load_from_config_manager(self) -> None:
        """通过 ConfigManager 加载 execution.yaml (P1-Q8, 失败静默)"""
        try:
            _project_root = Path(__file__).resolve().parent.parent.parent.parent
            import sys as _sys
            if str(_project_root) not in _sys.path:
                _sys.path.insert(0, str(_project_root))
            from utils.config_manager import get_execution_config
            cfg = get_execution_config()
            if cfg:
                self._apply_config_dict(cfg)
        except Exception as e:
            logger.debug(f"ConfigManager 加载 execution 配置失败, 使用默认: {e}")

    def _load_config(self, path: str) -> None:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"加载执行配置失败: {e}，使用默认配置")
            return

        self._apply_config_dict(cfg)

    def _apply_config_dict(self, cfg: dict) -> None:
        """从配置字典应用参数 (P1-Q8 拆分: 复用 ConfigManager 路径与显式路径)"""
        if not isinstance(cfg, dict):
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

    def set_sor(self, sor) -> None:
        """后续注入 SmartOrderRouter"""
        self.sor = sor

    # ---------- 拆单算法 ----------
    def split(self,
              total_qty: int,
              side: str,
              algo: AlgoType = AlgoType.TWAP,
              depth: Optional[Dict] = None,
              window_minutes: int = 30,
              volume_profile: Optional[List[float]] = None,
              participation_rate: float = 0.1) -> List[SliceOrder]:
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

        slices: List[SliceOrder] = []
        now = datetime.utcnow()

        if algo == AlgoType.ICEBERG:
            # Iceberg: 每片取盘口深度的 10%
            if depth:
                bid_vol = depth.get("bid1_vol", 0)
                ask_vol = depth.get("ask1_vol", 0)
                ref_vol = max(bid_vol, ask_vol, 1)
                slice_size = max(1, int(ref_vol * 0.10))
            else:
                slice_size = max(1, total_qty // 10)

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

        if algo == AlgoType.TWAP:
            n_slices = max(1, window_minutes // 5)
            slice_size = max(1, total_qty // n_slices)
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

        if algo == AlgoType.VWAP:
            if volume_profile and len(volume_profile) > 0:
                total_vol = sum(volume_profile)
                if total_vol > 0:
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
            # 无 profile 回退 TWAP
            return self.split(total_qty, side, AlgoType.TWAP, depth,
                              window_minutes)

        if algo == AlgoType.POV:
            # POV: 按 participation_rate 拆分
            slice_size = max(1, int(total_qty * participation_rate))
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

        # 默认: 单片
        return [SliceOrder(quantity=total_qty, suggested_time=now)]

    # ---------- 算法执行入口 ----------
    def execute_order(self, symbol: str, target_qty: int, side: str,
                      decision_price: float,
                      algo: Optional[str] = None,
                      price_limit: Optional[float] = None) -> List[Dict]:
        """
        按当前时段自动选择算法执行

        Args:
            symbol: 标的代码
            target_qty: 目标数量
            side: 'BUY' / 'SELL'
            decision_price: 决策价格（用于滑点计算）
            algo: 显式指定算法（可选），否则按当前时段自动选择
            price_limit: 限价（可选, 当前版本未启用, 预留接口)

        Returns:
            fills 列表
        """
        _ = price_limit  # 预留接口, 当前由 SOR 内部处理限价

        # 确定算法
        if algo is None:
            session = self.get_current_session()
            if session is None:
                # 非交易时段：市价执行
                logger.info(f"非交易时段，使用 TWAP 执行 {symbol}")
                return self.sor.execute_twap(symbol, target_qty, side, decision_price)
            algo = session.algo

        logger.info(f"[AlgoEngine] {symbol} {side} {target_qty} 使用 {algo}")

        algo_upper = algo.upper()
        if algo_upper == 'TWAP':
            return self.sor.execute_twap(
                symbol, target_qty, side, decision_price,
                window_minutes=self.algo_cfg.twap_slice_minutes * 5
            )
        if algo_upper == 'VWAP':
            return self.sor.execute_vwap(
                symbol, target_qty, side, decision_price,
                window_minutes=self.algo_cfg.vwap_window_minutes
            )
        if algo_upper == 'POV':
            return self.sor.execute_pov(
                symbol, target_qty, side, decision_price,
                participation_rate=self.algo_cfg.pov_participation_rate
            )
        # 未知算法回退
        logger.warning(f"未知算法 {algo}，回退到标准执行")
        return self.sor.execute(symbol, target_qty, side, decision_price)

    # ---------- 批量执行 ----------
    def execute_batch(self, orders: List[Dict]) -> List[Dict]:
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
