# -*- coding: utf-8 -*-
"""
系统集成层 v1.0 — 步骤5

将 P0 修复接入自动化执行系统主流程:

  P0-1 IC-based SignalFusion   — 信号生成阶段注入真实 rank_IC 动态权重
  P0-2 ModelDriftDetector       — 每日运行后做漂移检测 + 自动重训练触发
  P0-3 CostAwareBacktest        — 周度成本感知回测验证
  步骤4 StopLossMonitor         — 盘中 (morning_review) 自动止损止盈触发

设计原则:
- 继承 AutomatedExecutionSystem, 仅覆盖 _execute_daily_trading 注入钩子
- 主流程异常不影响系统集成 (fail-safe, 每个钩子独立 try/except)
- 模拟模式: 用 MockBrokerAdapter, 不实盘下单
- 数据缺失优雅降级: QLib 数据缺失时跳过对应步骤

使用方式:
    # 模拟模式完整运行
    python system_integration.py

    # 集成到主系统
    from system_integration import IntegratedExecutionSystem
    system = IntegratedExecutionSystem(total_capital=5_000_000)
    system.start_system()
"""
import os
import sys
import json
import logging
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 路径设置
_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE)

# v7.5_institutional src (P0 模块)
_V75_SRC = os.path.join(_BASE, "v7.5_institutional", "src")
if _V75_SRC not in sys.path:
    sys.path.insert(0, _V75_SRC)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('system_integration')


# ============================================================
# P0 模块导入 (容错)
# ============================================================
try:
    from alpha.signal_fusion import SignalFusion
    _SIGNAL_FUSION_AVAILABLE = True
except Exception as e:
    logger.warning(f"SignalFusion 不可用: {e}")
    SignalFusion = None
    _SIGNAL_FUSION_AVAILABLE = False

try:
    from ml.drift_detector import ModelDriftDetector, DriftType, Severity
    _DRIFT_DETECTOR_AVAILABLE = True
except Exception as e:
    logger.warning(f"ModelDriftDetector 不可用: {e}")
    ModelDriftDetector = None
    _DRIFT_DETECTOR_AVAILABLE = False

try:
    # 优先尝试 backtest.cost_aware_backtest (v7.5 src 在 sys.path)
    from backtest.cost_aware_backtest import CostAwareBacktest
    _COST_AWARE_BACKTEST_AVAILABLE = True
except Exception as _e1:
    # 回退: 直接 importlib 加载文件 (绕过包结构问题)
    try:
        import importlib.util
        _bt_path = os.path.join(_V75_SRC, "backtest", "cost_aware_backtest.py")
        if os.path.exists(_bt_path):
            _spec = importlib.util.spec_from_file_location(
                "cost_aware_backtest", _bt_path
            )
            _mod = importlib.util.module_from_spec(_spec)
            # 确保 cost_model 也可加载
            _cm_path = os.path.join(_V75_SRC, "backtest", "cost_model.py")
            if os.path.exists(_cm_path):
                _spec2 = importlib.util.spec_from_file_location(
                    "backtest.cost_model", _cm_path
                )
                _mod2 = importlib.util.module_from_spec(_spec2)
                sys.modules["backtest.cost_model"] = _mod2
                _spec2.loader.exec_module(_mod2)
            _spec.loader.exec_module(_mod)
            CostAwareBacktest = _mod.CostAwareBacktest
            _COST_AWARE_BACKTEST_AVAILABLE = True
        else:
            raise FileNotFoundError(f"cost_aware_backtest.py not found at {_bt_path}")
    except Exception as e:
        logger.warning(f"CostAwareBacktest 不可用 (cause: {_e1} | {e})")
        CostAwareBacktest = None
        _COST_AWARE_BACKTEST_AVAILABLE = False

# 主系统
try:
    from automated_execution_system import AutomatedExecutionSystem
    _AUTO_SYSTEM_AVAILABLE = True
except Exception as e:
    logger.error(f"AutomatedExecutionSystem 不可用: {e}")
    AutomatedExecutionSystem = object  # type: ignore
    _AUTO_SYSTEM_AVAILABLE = False

# 步骤4 止损监控
try:
    from stop_loss_monitor import StopLossMonitor
    _STOP_LOSS_AVAILABLE = True
except Exception as e:
    logger.warning(f"StopLossMonitor 不可用: {e}")
    StopLossMonitor = None
    _STOP_LOSS_AVAILABLE = False


# ============================================================
# QLib 数据读取工具
# ============================================================
def code_to_qlib(code: str) -> str:
    """600019 → sh600019, 000425 → sz000425, 510300 → sh510300"""
    s = str(code).strip()
    for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    if s.startswith(("51", "58", "60", "68", "11", "13")):
        return f"sh{s}"
    if s.startswith(("00", "30", "15", "16")):
        return f"sz{s}"
    if s.startswith(("4", "8")):
        return f"bj{s}"
    return f"sh{s}"


def load_qlib_bin(field: str, qlib_code: str) -> Optional[pd.Series]:
    """读取 QLib bin 文件单字段 (close/open/high/low/volume/change/factor)

    QLib bin 格式: 头部 9 个 float (start_date, end_date, float_count, etc.)
    之后是 float32 数据, 长度对齐 calendars/day.txt
    """
    bin_path = os.path.join(
        _BASE, "qlib_data", "cn_data", "features", qlib_code, f"{field}.day.bin"
    )
    if not os.path.exists(bin_path):
        return None

    cal_path = os.path.join(_BASE, "qlib_data", "cn_data", "calendars", "day.txt")
    if not os.path.exists(cal_path):
        return None

    try:
        with open(cal_path, "r", encoding="utf-8") as f:
            dates = [line.strip() for line in f if line.strip()]
    except Exception:
        return None

    try:
        arr = np.fromfile(bin_path, dtype=np.float32)
    except Exception as e:
        logger.debug(f"读取 {bin_path} 失败: {e}")
        return None

    # 跳过头部 9 个 float (QLib 标准格式)
    if len(arr) <= 9:
        return None
    values = arr[9:]
    n = min(len(values), len(dates))
    if n == 0:
        return None

    # 过滤无效值 (QLib 用 0 表示缺失, 但 close 可能为 0 表示停牌)
    series = pd.Series(values[:n], index=pd.to_datetime(dates[:n]))
    # 替换 inf 为 NaN
    series = series.replace([np.inf, -np.inf], np.nan)
    return series


def load_close_prices(codes: List[str]) -> pd.DataFrame:
    """批量加载多只标的的收盘价 DataFrame

    Args:
        codes: 标的代码列表 (如 ['600019', '000425'])

    Returns:
        DataFrame (date x code), 缺失的标的会被跳过
    """
    data = {}
    for code in codes:
        qlib_code = code_to_qlib(code)
        series = load_qlib_bin("close", qlib_code)
        if series is not None and len(series) > 0:
            # 过滤掉 0 值 (停牌)
            series = series.where(series > 0, np.nan)
            data[code] = series

    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)
    # 截取最近 5 年数据 (节省计算)
    if len(df) > 252 * 5:
        df = df.iloc[-252 * 5:]
    return df


# ============================================================
# 集成执行系统
# ============================================================
class IntegratedExecutionSystem(AutomatedExecutionSystem):
    """带 P0 修复的自动化执行系统 (模拟模式)

    在 AutomatedExecutionSystem 基础上, 覆盖 _execute_daily_trading:
      前置钩子 (步骤5a): 更新 SignalFusion, 注入 IC 动态权重
      父类执行: 原有 1-11 步骤 (对冲/路由/再平衡)
      后置钩子:
        - morning_review: 步骤4 止损止盈自动触发
        - daily_execution: 步骤5b 漂移检测 + 自动重训练
        - 周一: 步骤5c 成本感知回测验证
    """

    def __init__(self, total_capital: float = 5_000_000):
        # 报告目录必须最先初始化 (子组件初始化时需要)
        self.report_dir = os.path.join(_BASE, "reports")
        os.makedirs(self.report_dir, exist_ok=True)

        # 在 super().__init__() 之前, 规范化本地 positions.json
        # 父类期望 dict 格式 {"code": {...}}, 但 28 目录的可能是 list 格式 ["sz588000", ...]
        self._normalize_local_positions_file()

        super().__init__(total_capital=total_capital)

        # P0-1: 信号融合器
        self.signal_fusion = self._init_signal_fusion()
        self.current_fused_signal: Optional[pd.Series] = None
        self.last_signal_update: Optional[str] = None

        # P0-2: 漂移检测器
        self.drift_detector = self._init_drift_detector()
        self.last_drift_check: Optional[str] = None
        self.last_retrain_trigger: Optional[Dict] = None

        # 步骤4: 止损监控器
        self.stop_loss_monitor = self._init_stop_loss_monitor()
        self.last_stop_loss_check: Optional[str] = None

        # P0-3: 成本感知回测
        self.cost_aware_backtest = None
        self.last_backtest_date: Optional[str] = None

        logger.info(
            f"集成执行系统初始化完成 "
            f"(signal_fusion={bool(self.signal_fusion)}, "
            f"drift_detector={bool(self.drift_detector)}, "
            f"stop_loss_monitor={bool(self.stop_loss_monitor)})"
        )

    # ---------- 初始化 ----------

    def _normalize_local_positions_file(self) -> None:
        """规范化本地 positions.json — list 格式 → dict 格式

        父类 AutomatedExecutionSystem 期望 dict 格式:
            {"positions": {"588000.SH": {"code": "588000.SH", "shares": ..., ...}}}

        但 28 目录的 positions.json 可能是 list 格式:
            {"positions": ["sz588000", "sh688041", ...]}

        本方法把 list 格式转换为 dict 格式, 数据从 11_量化策略/config/positions.json 合并.
        原始 list 备份到 config/positions_list_backup.json.
        """
        local_pos_path = os.path.join(_BASE, "config", "positions.json")
        if not os.path.exists(local_pos_path):
            return

        try:
            with open(local_pos_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"读取本地 positions.json 失败: {e}")
            return

        positions = data.get("positions")

        # 已经是 dict 格式 — 跳过
        if isinstance(positions, dict):
            return
        # 不是 list — 跳过
        if not isinstance(positions, list):
            return

        # 加载 11_量化策略 的 positions.json 作为数据源 (dict 格式, 含详细信息)
        src_pos_path = os.path.join(
            _BASE, "..", "11_量化策略", "config", "positions.json"
        )
        src_data = {}
        if os.path.exists(src_pos_path):
            try:
                with open(src_pos_path, "r", encoding="utf-8") as f:
                    src_data = json.load(f).get("positions", {})
            except Exception as e:
                logger.warning(f"读取 11_量化策略 positions.json 失败: {e}")

        # 转换 list → dict
        normalized = {}
        missing_in_src = []
        for code in positions:
            s = str(code).strip().lower()
            # 拆分前缀和数字
            exchange = ""
            pure = s
            for prefix in ("sh", "sz", "bj"):
                if s.startswith(prefix):
                    exchange = prefix
                    pure = s[len(prefix):]
                    break

            # 标准代码格式 (688041.SH)
            std_code = f"{pure}.{exchange.upper()}" if exchange else pure

            # 从 11_量化策略 找数据 (按多种 key 格式尝试)
            src_info = (
                src_data.get(std_code)
                or src_data.get(pure)
                or src_data.get(f"{exchange.upper()}{pure}")
                or src_data.get(f"{exchange.lower()}{pure}")
                or {}
            )

            if not src_info:
                missing_in_src.append(std_code)

            shares = int(src_info.get("shares", 0) or 0)
            avg_cost = float(src_info.get("avg_cost", 0.0) or 0.0)
            target_weight = float(src_info.get("target_weight", 0.0) or 0.0)
            sector = src_info.get("sector", "其他")
            name = src_info.get("name", "")

            normalized[std_code] = {
                "code": std_code,
                "name": name,
                "shares": shares,
                "phase1_shares": shares,  # 父类优先读 phase1_shares
                "total_shares": shares,
                "est_price": avg_cost,    # 父类读 est_price
                "avg_cost": avg_cost,
                "target_weight": target_weight,
                "style": sector,          # 父类读 style
                "sector": sector,
            }

        # 备份原始 list 格式
        backup_path = os.path.join(_BASE, "config", "positions_list_backup.json")
        try:
            with open(backup_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        # 覆盖 positions.json 为 dict 格式 (保留 meta)
        data["positions"] = normalized
        try:
            with open(local_pos_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(
                f"本地 positions.json 已规范化: list→dict, "
                f"{len(normalized)} 个持仓"
                + (f", 未在 11_量化策略 找到: {missing_in_src}" if missing_in_src else "")
            )
        except Exception as e:
            logger.warning(f"写回 positions.json 失败: {e}")

    def _init_signal_fusion(self) -> Optional[object]:
        """初始化 IC-based 信号融合器"""
        if not _SIGNAL_FUSION_AVAILABLE:
            return None
        try:
            # 从 QLib 训练报告读取 IC 信息, 调整初始权重
            weights = self._load_signal_weights()
            fusion = SignalFusion(
                weights=weights,
                ic_lookback=20,
                min_ic_samples=5,
            )
            logger.info(f"SignalFusion 已初始化, 权重: {fusion.weights}")
            return fusion
        except Exception as e:
            logger.warning(f"SignalFusion 初始化失败: {e}")
            return None

    def _load_signal_weights(self) -> Dict[str, float]:
        """从 QLib 训练报告读取 IC, 据此分配初始权重

        如果 IC > 0, 提升 ML 权重; 否则降低
        """
        # 默认权重 (与 SignalFusion 默认值一致)
        weights = {
            'alpha': 0.45,
            'ml': 0.10,
            'qlib': 0.10,
            'ai': 0.05,
            'macro': 0.25,
            'causal': 0.05,
        }

        # 查找最新的 QLib 训练报告
        report_path = self._find_latest_qlib_report()
        if report_path is None:
            return weights

        try:
            with open(report_path, "r", encoding="utf-8") as f:
                report = json.load(f)
            mean_ic = float(report.get("mean_daily_ic", 0) or 0)
            ic_ir = float(report.get("ic_ir", 0) or 0)
            logger.info(
                f"QLib 报告 IC={mean_ic:.4f}, IC_IR={ic_ir:.4f}, 调整权重"
            )

            # 如果 IC > 0, 提升 ML/QLib 权重
            if mean_ic > 0 and ic_ir > 0:
                weights['ml'] = 0.20
                weights['qlib'] = 0.20
                weights['alpha'] = 0.30
                # 归一化
                total = sum(weights.values())
                weights = {k: v / total for k, v in weights.items()}
        except Exception as e:
            logger.warning(f"读取 QLib 报告失败: {e}")

        return weights

    def _find_latest_qlib_report(self) -> Optional[str]:
        """查找最新的 QLib 训练报告"""
        if not os.path.isdir(self.report_dir):
            return None
        candidates = []
        for fname in os.listdir(self.report_dir):
            if fname.startswith("qlib_") and fname.endswith(".json"):
                full = os.path.join(self.report_dir, fname)
                candidates.append((os.path.getmtime(full), full))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def _init_drift_detector(self) -> Optional[object]:
        """初始化漂移检测器, 并用 QLib 报告 IC 预热"""
        if not _DRIFT_DETECTOR_AVAILABLE:
            return None
        try:
            detector = ModelDriftDetector(
                ic_window=20,
                ic_threshold=0.02,
                ic_consecutive_days=5,
                ks_pvalue=0.05,
                psi_threshold=0.25,
                adwin_delta=0.002,
            )

            # 用 QLib 报告的 IC 预热 (作为单一初始观测, 不触发告警)
            report_path = self._find_latest_qlib_report()
            if report_path:
                try:
                    with open(report_path, "r", encoding="utf-8") as f:
                        report = json.load(f)
                    initial_ic = float(report.get("mean_daily_ic", 0) or 0)
                    if initial_ic != 0:
                        # 注入 5 次 IC, 让 ADWIN 有足够样本
                        seed_date = datetime.now() - timedelta(days=5)
                        for i in range(5):
                            d = seed_date + timedelta(days=i)
                            detector.update_ic(d, initial_ic)
                        logger.info(
                            f"漂移检测器预热: IC={initial_ic:.4f} (5 次注入)"
                        )
                except Exception as e:
                    logger.warning(f"漂移检测器预热失败: {e}")

            return detector
        except Exception as e:
            logger.warning(f"ModelDriftDetector 初始化失败: {e}")
            return None

    def _init_stop_loss_monitor(self) -> Optional[object]:
        """初始化止损监控器"""
        if not _STOP_LOSS_AVAILABLE:
            return None
        try:
            monitor = StopLossMonitor()
            logger.info(
                f"止损监控器已初始化: {len(monitor.rules)} 条规则"
            )
            return monitor
        except Exception as e:
            logger.warning(f"StopLossMonitor 初始化失败: {e}")
            return None

    # ---------- 主执行钩子 ----------

    def _execute_daily_trading(self, execution_name: str = 'daily_execution',
                               force_step5c: bool = False):
        """覆盖父类方法, 注入 P0 钩子

        执行顺序:
          1. (前置) 更新信号融合 — 步骤5a
          2. (父类) 原有执行流程
          3. (后置) morning_review: 止损监控 — 步骤4
          4. (后置) daily_execution: 漂移检测 — 步骤5b
          5. (后置) 周一 (或 force_step5c=True): 成本感知回测 — 步骤5c

        Args:
            execution_name: 'daily_execution' / 'morning_review' / 'afternoon_adjustment'
            force_step5c: 模拟模式下强制运行成本感知回测 (跳过周一判断)
        """
        # 前置钩子: 信号融合
        try:
            self._pre_trade_signal_fusion(execution_name)
        except Exception as e:
            logger.warning(f"前置信号融合钩子失败: {e}")

        # 调用父类执行
        try:
            super()._execute_daily_trading(execution_name)
        except Exception as e:
            logger.error(f"父类执行失败: {e}")
            traceback.print_exc()

        # 后置钩子
        try:
            if execution_name == 'morning_review':
                # 步骤4: 盘中止损止盈触发
                self._post_trade_stop_loss(execution_name)
            elif execution_name == 'daily_execution':
                # 步骤5b: 漂移检测
                self._post_trade_drift_detection(execution_name)
                # 步骤5c: 周一成本感知回测 (或 force_step5c 强制运行)
                if force_step5c or datetime.now().weekday() == 0:  # Monday
                    self._post_trade_cost_aware_backtest(execution_name)
        except Exception as e:
            logger.warning(f"后置钩子失败: {e}")

    # ---------- 步骤5a: 信号融合 ----------

    def _pre_trade_signal_fusion(self, execution_name: str):
        """更新 SignalFusion 并生成融合信号

        步骤:
          1. 加载持仓标的 QLib 收盘价
          2. 计算前置收益 forward_returns = close.pct_change().shift(-1)
          3. 注入到 SignalFusion
          4. 加载 ML 信号 (从 QLib 训练报告)
          5. 计算 alpha 信号 (RSI/MACD 简化版)
          6. 融合 → 保存到 reports/
        """
        if not self.signal_fusion:
            return

        # 只在 daily_execution 时更新 (避免 morning_review 重复计算)
        if execution_name != 'daily_execution':
            return

        logger.info("步骤5a: 更新 IC-based 信号融合")

        # 1. 加载持仓代码
        codes = self._get_position_codes()
        if not codes:
            logger.warning("无持仓代码, 跳过信号融合")
            return

        # 2. 加载收盘价
        prices = load_close_prices(codes)
        if prices.empty or len(prices) < 30:
            logger.warning(f"价格数据不足 ({len(prices)} 天), 跳过信号融合")
            return

        # 3. 计算 forward_returns (T+1 收益率)
        forward_returns = prices.pct_change().shift(-1).iloc[:-1]
        # 用市场组合 (等权) 作为代表
        market_forward_ret = forward_returns.mean(axis=1)

        try:
            self.signal_fusion.inject_forward_returns(market_forward_ret)
        except Exception as e:
            logger.warning(f"注入 forward_returns 失败: {e}")
            return

        # 4. 加载 ML 信号 (从 QLib 报告)
        ml_signals = self._load_ml_signals(codes, prices.index)
        if ml_signals is not None:
            try:
                self.signal_fusion.inject_ml_signal(ml_signals, name='ml')
            except Exception as e:
                logger.warning(f"注入 ML 信号失败: {e}")

        # 5. 计算 alpha 信号 (简化: 20日动量)
        alpha_signal = self._compute_alpha_signal(prices)
        if alpha_signal is not None:
            try:
                self.signal_fusion.inject_alpha_signal(alpha_signal, name='alpha')
            except Exception as e:
                logger.warning(f"注入 alpha 信号失败: {e}")

        # 6. 融合 (动态权重)
        try:
            fused = self.signal_fusion.fuse(method='dynamic')
            self.current_fused_signal = fused
            self.last_signal_update = datetime.now().isoformat()

            # 保存到 reports/
            date_str = datetime.now().strftime("%Y%m%d")
            out_path = os.path.join(
                self.report_dir, f"fused_signal_{date_str}.json"
            )
            # 取最后一天信号
            if len(fused) > 0:
                latest = fused.iloc[-1]
                signal_data = {
                    'timestamp': datetime.now().isoformat(),
                    'method': 'dynamic_ic',
                    'latest_signal': float(latest) if not np.isnan(latest) else 0.0,
                    'n_signals': len(fused),
                    'mean_signal': float(fused.mean()) if len(fused) > 0 else 0.0,
                    'std_signal': float(fused.std()) if len(fused) > 0 else 0.0,
                    'explanation': self.signal_fusion.explain(),
                }
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(signal_data, f, ensure_ascii=False, indent=2, default=str)
                logger.info(
                    f"融合信号已生成: latest={signal_data['latest_signal']:.4f}, "
                    f"保存到 {out_path}"
                )
        except Exception as e:
            logger.warning(f"信号融合失败: {e}")

    def _get_position_codes(self) -> List[str]:
        """从 11_量化策略/config/positions.json 读取持仓代码 (纯数字)"""
        positions_path = os.path.join(
            _BASE, "..", "11_量化策略", "config", "positions.json"
        )
        if not os.path.exists(positions_path):
            return []

        try:
            with open(positions_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            positions = data.get("positions", {})
            codes = []
            for key, info in positions.items():
                if not isinstance(info, dict):
                    continue
                shares = info.get("shares", 0)
                if not shares:
                    continue
                # key 已经是 '600019.SH' 形式
                pure = str(key).split(".")[0]
                codes.append(pure)
            return codes
        except Exception as e:
            logger.warning(f"读取持仓失败: {e}")
            return []

    def _load_ml_signals(self, codes: List[str], date_index) -> Optional[pd.Series]:
        """从 QLib 训练报告加载 ML 信号, 展开为时间序列

        报告中 stock_signals 字段含每只股票的 latest_signal, 我们假设信号在最近 20 天有效
        """
        report_path = self._find_latest_qlib_report()
        if not report_path:
            return None

        try:
            with open(report_path, "r", encoding="utf-8") as f:
                report = json.load(f)
            stock_signals = report.get("stock_signals", [])
            if not stock_signals:
                return None

            # 提取每只股票的 latest_signal
            signal_map = {}
            for item in stock_signals:
                qlib_code = item.get("code", "")  # SH600019
                if not qlib_code:
                    continue
                # 转纯数字
                pure = qlib_code[2:] if qlib_code.startswith(("SH", "SZ", "BJ")) else qlib_code
                signal_map[pure] = float(item.get("latest_signal", 0))

            # 构造 Series (最近 20 天都使用 latest_signal)
            recent_dates = date_index[-20:] if len(date_index) >= 20 else date_index
            # 用组合平均信号作为代表
            avg_signal = np.mean(list(signal_map.values())) if signal_map else 0.0
            return pd.Series(avg_signal, index=recent_dates)
        except Exception as e:
            logger.warning(f"加载 ML 信号失败: {e}")
            return None

    def _compute_alpha_signal(self, prices: pd.DataFrame) -> Optional[pd.Series]:
        """计算简化版 alpha 信号: 20日动量 + RSI 反转

        signal = 0.5 * momentum_20d + 0.5 * (1 - RSI_14d) 反转
        """
        try:
            # 20日动量
            momentum = prices.pct_change(20).iloc[-1]
            # 组合平均
            momentum_signal = momentum.mean()

            # RSI (14日)
            delta = prices.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))
            latest_rsi = rsi.iloc[-1].mean()
            # 反转信号: RSI 高 → 看空
            rsi_signal = (50 - latest_rsi) / 50  # -1 ~ +1

            alpha = 0.5 * momentum_signal + 0.5 * rsi_signal

            # 取最近 20 天平均作为 alpha 信号
            recent_dates = prices.index[-20:]
            return pd.Series(float(alpha), index=recent_dates)
        except Exception as e:
            logger.warning(f"计算 alpha 信号失败: {e}")
            return None

    # ---------- 步骤5b: 漂移检测 ----------

    def _post_trade_drift_detection(self, execution_name: str):
        """每日运行后做漂移检测"""
        if not self.drift_detector:
            return

        if execution_name != 'daily_execution':
            return

        logger.info("步骤5b: 漂移检测")
        today_str = datetime.now().strftime("%Y-%m-%d")
        if self.last_drift_check == today_str:
            logger.debug("今日已检测过, 跳过")
            return

        # 用 SignalFusion 的 IC 信息更新 (如果有)
        try:
            if self.signal_fusion is not None:
                explanation = self.signal_fusion.explain()
                ic_info = explanation.get('ic_info', {})
                # 取 alpha 信号的 IC 作为代表 (字段名: mean_ic)
                # 优先 alpha, 其次 ml, 兜底用 QLib 报告 IC
                alpha_ic = ic_info.get('alpha', {}).get('mean_ic')
                if alpha_ic is None:
                    alpha_ic = ic_info.get('ml', {}).get('mean_ic')
                if alpha_ic is None:
                    # 兜底: 从最新 QLib 报告读取
                    report_path = self._find_latest_qlib_report()
                    if report_path:
                        with open(report_path, "r", encoding="utf-8") as f:
                            qlib_report = json.load(f)
                        alpha_ic = float(qlib_report.get("mean_daily_ic", 0) or 0)
                if alpha_ic is not None and alpha_ic == alpha_ic:  # NaN 检查
                    self.drift_detector.update_ic(datetime.now(), float(alpha_ic))
                    logger.debug(f"漂移检测器 IC 更新: {alpha_ic:.4f}")
        except Exception as e:
            logger.debug(f"更新 IC 失败: {e}")

        # 执行检查
        alerts = self.drift_detector.check_all()
        should_retrain, reason = self.drift_detector.should_retrain()

        # 生成报告
        try:
            report = self.drift_detector.generate_report()
            date_str = datetime.now().strftime("%Y%m%d")
            out_path = os.path.join(
                self.report_dir, f"drift_report_{date_str}.json"
            )
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2, default=str)
            logger.info(
                f"漂移检测完成: alerts={len(alerts)}, "
                f"should_retrain={should_retrain}, 保存到 {out_path}"
            )
        except Exception as e:
            logger.warning(f"漂移报告保存失败: {e}")

        # 自动重训练触发 (仅记录, 不实际执行训练)
        if should_retrain:
            logger.warning(f"⚠️ 触发自动重训练: {reason}")
            self.last_retrain_trigger = {
                'timestamp': datetime.now().isoformat(),
                'reason': reason,
                'alerts': [
                    {
                        'type': a.drift_type.value,
                        'severity': a.severity.value,
                        'message': a.message,
                    } for a in alerts
                ],
            }
            # 保存重训练触发记录
            try:
                trigger_path = os.path.join(
                    self.report_dir, f"retrain_trigger_{date_str}.json"
                )
                with open(trigger_path, "w", encoding="utf-8") as f:
                    json.dump(self.last_retrain_trigger, f, ensure_ascii=False, indent=2)
                logger.info(f"重训练触发记录已保存: {trigger_path}")
            except Exception:
                pass

        self.last_drift_check = today_str

    # ---------- 步骤4: 止损监控 ----------

    def _post_trade_stop_loss(self, execution_name: str):
        """盘中止损止盈自动触发"""
        if not self.stop_loss_monitor:
            return

        if execution_name != 'morning_review':
            return

        logger.info("步骤4: 止损止盈监控")
        today_str = datetime.now().strftime("%Y-%m-%d")
        if self.last_stop_loss_check == today_str:
            logger.debug("今日已检查过止损, 跳过")
            return

        triggered = self.stop_loss_monitor.check_and_execute()

        if triggered:
            logger.warning(f"⚠️ 止损止盈触发 {len(triggered)} 条:")
            for t in triggered:
                logger.warning(
                    f"  {t.name} ({t.code}) {t.trigger_type.value} "
                    f"@ ¥{t.current_price:.2f} (P&L {t.pnl_pct:+.1%}) "
                    f"{'✅已执行' if t.executed else '❌未执行'}"
                )
        else:
            logger.info("✓ 止损监控完成: 无触发, 所有持仓安全")

        self.last_stop_loss_check = today_str

    # ---------- 步骤5c: 成本感知回测 ----------

    def _post_trade_cost_aware_backtest(self, execution_name: str):
        """周度成本感知回测验证"""
        if not _COST_AWARE_BACKTEST_AVAILABLE:
            return

        if execution_name != 'daily_execution':
            return

        today_str = datetime.now().strftime("%Y-%m-%d")
        if self.last_backtest_date == today_str:
            return

        logger.info("步骤5c: 周度成本感知回测验证")

        try:
            # 加载持仓代码
            codes = self._get_position_codes()
            if not codes:
                logger.warning("无持仓代码, 跳过回测")
                return

            # 加载最近 60 天价格 (回测窗口)
            prices = load_close_prices(codes)
            if prices.empty or len(prices) < 30:
                logger.warning(f"价格数据不足 ({len(prices)} 天), 跳过回测")
                return
            prices = prices.iloc[-60:]

            # 构造目标权重 (从 positions.json 读取 target_weight)
            target_weights = self._build_target_weights(codes, prices.index)
            if target_weights.empty:
                logger.warning("无法构造目标权重, 跳过回测")
                return

            # 初始化回测引擎 (如果尚未初始化)
            if self.cost_aware_backtest is None:
                self.cost_aware_backtest = CostAwareBacktest(
                    initial_capital=self.total_capital,
                )

            # 运行回测
            result = self.cost_aware_backtest.run_strategy(
                prices=prices,
                target_weights=target_weights,
                rebalance_threshold=0.05,
            )

            # 保存报告
            report = {
                'timestamp': datetime.now().isoformat(),
                'period': f"{prices.index[0].date()} ~ {prices.index[-1].date()}",
                'n_days': len(prices),
                'n_codes': len(codes),
                'total_return': result.total_return,
                'annual_return': result.annual_return,
                'max_drawdown': result.max_drawdown,
                'sharpe_ratio': result.sharpe_ratio,
                'total_cost': result.total_cost,
                'cost_as_return_pct': result.cost_as_return_pct,
                'n_trades': result.n_trades,
                'turnover': result.turnover,
                'cost_breakdown': {
                    'commission': result.total_commission,
                    'stamp_duty': result.total_stamp_duty,
                    'market_impact': result.total_market_impact,
                },
            }

            date_str = datetime.now().strftime("%Y%m%d")
            out_path = os.path.join(
                self.report_dir, f"cost_aware_backtest_{date_str}.json"
            )
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2, default=str)

            logger.info(
                f"✓ 成本感知回测完成: "
                f"年化={result.annual_return:.2%}, "
                f"夏普={result.sharpe_ratio:.3f}, "
                f"最大回撤={result.max_drawdown:.2%}, "
                f"成本拖累={result.cost_as_return_pct:.2%}, "
                f"交易次数={result.n_trades}, 保存到 {out_path}"
            )

            self.last_backtest_date = today_str

        except Exception as e:
            logger.error(f"成本感知回测失败: {e}")
            traceback.print_exc()

    def _build_target_weights(self, codes: List[str], date_index) -> pd.DataFrame:
        """从 positions.json 构造目标权重 DataFrame"""
        positions_path = os.path.join(
            _BASE, "..", "11_量化策略", "config", "positions.json"
        )
        if not os.path.exists(positions_path):
            return pd.DataFrame()

        try:
            with open(positions_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            positions = data.get("positions", {})

            # 提取每只股票的目标权重
            weight_map = {}
            for key, info in positions.items():
                if not isinstance(info, dict):
                    continue
                pure = str(key).split(".")[0]
                weight = float(info.get("target_weight", 0) or 0)
                if pure in codes:
                    weight_map[pure] = weight

            # 构造 DataFrame (每天都是同一目标权重)
            weight_series = pd.Series(weight_map).reindex(codes).fillna(0.0)
            df = pd.DataFrame(
                {d: weight_series for d in date_index}
            ).T
            df.index = date_index
            return df
        except Exception as e:
            logger.warning(f"构造目标权重失败: {e}")
            return pd.DataFrame()

    # ---------- 系统状态摘要 ----------

    def get_integrated_summary(self) -> Dict:
        """获取集成系统状态摘要"""
        summary = {
            'timestamp': datetime.now().isoformat(),
            'total_capital': self.total_capital,
            'system_enabled': self.system_enabled,
            'is_running': self.is_running,
            'current_market_state': self.current_market_state,
            'components': {
                'signal_fusion': {
                    'available': self.signal_fusion is not None,
                    'last_update': self.last_signal_update,
                    'latest_signal': (
                        float(self.current_fused_signal.iloc[-1])
                        if self.current_fused_signal is not None and len(self.current_fused_signal) > 0
                        else None
                    ),
                },
                'drift_detector': {
                    'available': self.drift_detector is not None,
                    'last_check': self.last_drift_check,
                    'last_retrain_trigger': self.last_retrain_trigger is not None,
                },
                'stop_loss_monitor': {
                    'available': self.stop_loss_monitor is not None,
                    'last_check': self.last_stop_loss_check,
                    'status': (
                        self.stop_loss_monitor.get_monitoring_status()
                        if self.stop_loss_monitor else None
                    ),
                },
                'cost_aware_backtest': {
                    'available': self.cost_aware_backtest is not None,
                    'last_check': self.last_backtest_date,
                },
            },
            'reports_dir': self.report_dir,
        }
        return summary


# ============================================================
# 主入口: 模拟模式单次执行
# ============================================================
def run_mock_once():
    """模拟模式单次执行: 触发一次 daily_execution + morning_review

    不进入持续循环, 用于验证 P0 集成是否工作
    """
    # 确保 stdout 使用 utf-8 编码 (避免 Windows gbk 控制台报错)
    try:
        sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]
    except Exception:
        pass

    print("=" * 70)
    print("集成执行系统 v1.0 — 模拟模式单次验证")
    print("=" * 70)

    system = IntegratedExecutionSystem(total_capital=5_000_000)

    print("\n[1] 系统初始化完成")
    summary = system.get_integrated_summary()
    for name, info in summary['components'].items():
        status = "[OK]" if info['available'] else "[FAIL]"
        print(f"  {status} {name}: available={info['available']}")

    print("\n[2] 执行 daily_execution (含步骤5a/5b/5c, 强制运行5c)")
    system._execute_daily_trading('daily_execution', force_step5c=True)

    print("\n[3] 执行 morning_review (含步骤4 止损监控)")
    system._execute_daily_trading('morning_review')

    print("\n[4] 集成系统最终状态")
    final_summary = system.get_integrated_summary()
    print(json.dumps(final_summary, ensure_ascii=False, indent=2, default=str))

    print("\n" + "=" * 70)
    print("✓ 模拟模式单次执行完成")
    print("=" * 70)
    return system


if __name__ == "__main__":
    run_mock_once()
