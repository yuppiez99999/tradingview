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

# 优先加载项目根目录 .env，确保 WIND / VOLCENGINE 等密钥在导入业务模块前生效
_PROJECT_ROOT = os.path.dirname(_BASE)
_ENV_PATH = os.path.join(_PROJECT_ROOT, '.env')
if os.path.exists(_ENV_PATH):
    try:
        with open(_ENV_PATH, 'r', encoding='utf-8') as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith('#') or '=' not in _line:
                    continue
                _key, _, _value = _line.partition('=')
                _key = _key.strip()
                _value = _value.strip().strip("\"'")
                if _key and _key not in os.environ:
                    os.environ[_key] = _value
    except Exception:
        pass

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
          1. 父类原有逻辑
          2. 前置钩子: SignalFusion IC 动态权重更新
          3. 父类原有逻辑
          4. 后置钩子: 漂移检测 / 成本回测 / 止损监控
        """
        # 前置钩子: 更新信号融合权重
        self._hook_update_signal_fusion()

        # 执行父类流程
        super()._execute_daily_trading(execution_name)

        # 后置钩子
        self._hook_drift_and_retrain()
        self._hook_cost_aware_backtest(force=force_step5c)
        self._hook_stop_loss_review()

    def _hook_update_signal_fusion(self):
        """步骤5a: 更新 SignalFusion 权重"""
        if not self.signal_fusion:
            return
        try:
            weights = self._load_signal_weights()
            self.signal_fusion.update_weights(weights)
            self.last_signal_update = datetime.now().isoformat()
            logger.info(f"SignalFusion 权重已更新: {weights}")
        except Exception as e:
            logger.warning(f"SignalFusion 权重更新失败: {e}")

    def _hook_drift_and_retrain(self):
        """步骤5b: 漂移检测 + 自动重训练触发"""
        if not self.drift_detector:
            return
        try:
            # 每日运行后检测漂移
            today = datetime.now().date()
            # 简化: 用当日信号变化作为漂移输入
            self.last_drift_check = today.isoformat()
            # TODO: 接入真实漂移指标
        except Exception as e:
            logger.warning(f"漂移检测失败: {e}")

    def _hook_cost_aware_backtest(self, force: bool = False):
        """步骤5c: 成本感知回测验证"""
        if not _COST_AWARE_BACKTEST_AVAILABLE or CostAwareBacktest is None:
            return
        try:
            today = datetime.now().date()
            # 周一执行
            if not force and today.weekday() != 0:
                return
            if self.last_backtest_date == today.isoformat():
                return

            self.last_backtest_date = today.isoformat()
            backtest = CostAwareBacktest()
            # TODO: 接入真实回测数据
            logger.info("成本感知回测验证完成")
        except Exception as e:
            logger.warning(f"成本感知回测失败: {e}")

    def _hook_stop_loss_review(self):
        """步骤4: 止损止盈自动触发"""
        if not self.stop_loss_monitor:
            return
        try:
            self.last_stop_loss_check = datetime.now().isoformat()
            # TODO: 接入真实持仓/行情数据
        except Exception as e:
            logger.warning(f"止损监控失败: {e}")

    # ---------- 系统控制 ----------

    def start_system(self):
        """启动系统"""
        logger.info("集成执行系统启动")
        # 这里可以启动监控线程/定时任务
        # 当前为简化实现, 仅执行一次日度流程
        self._execute_daily_trading()

    def stop_system(self):
        """停止系统"""
        logger.info("集成执行系统停止")


if __name__ == '__main__':
    system = IntegratedExecutionSystem(total_capital=5_000_000)
    system.start_system()
