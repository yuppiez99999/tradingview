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
from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from utils.datetime_utils import now_bj

# 路径设置
_BASE = os.path.dirname(os.path.abspath(__file__))
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
sys.path.insert(0, _BASE)  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402

setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils

# 优先加载项目根目录 .env，确保 WIND / DEEPSEEK / ZHIPUAI 等密钥在导入业务模块前生效
_PROJECT_ROOT = os.path.dirname(_BASE)
_ENV_PATH = os.path.join(_PROJECT_ROOT, ".env")
if os.path.exists(_ENV_PATH):
    try:
        with open(_ENV_PATH, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _key, _, _value = _line.partition("=")
                _key = _key.strip()
                _value = _value.strip().strip("\"'")
                if _key and _key not in os.environ:
                    os.environ[_key] = _value
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        # logger 在第 73 行才定义, 此处 except 块若在模块加载早期触发会抛 NameError
        # 改用 logging.getLogger 直接获取, 避免模块加载顺序依赖 (同 BUG-08 修复模式)
        logging.getLogger("system_integration").exception("加载 .env 配置失败")

# IC1 修复: v7.5_institutional/src 目录为空, P0 模块实际位于 v8.3_institutional/src
# 原路径导致 SignalFusion/ModelDriftDetector/CostAwareBacktest 全部静默加载失败
# Wave 3 第三阶段: v8.3 src 路径已由 setup_sys_path() 统一注入, 保留 _V75_SRC 变量供后续模块加载使用
_V75_SRC = os.path.join(_BASE, "v8.3_institutional", "src")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("system_integration")


# ============================================================
# N1: 自我进化配置 与 重训冷却期工具
# ============================================================
EVOLUTION_CONFIG = {
    "retrain_cooldown_days": 7,  # 重训冷却期 (与 retrain_interval_days 对齐)
    "ic_min_abs_threshold": 0.001,  # IC 绝对值低于此值视为无效, 跳过更新
    "shadow_mode": True,  # 影子模式: 重训只生成模型不替换生产 (安全开关)
}


def _read_retrain_lock(symbol: str) -> datetime | None:
    """读取标的最近重训时间 (冷却期判断)

    Args:
        symbol: 标的代码

    Returns:
        最近重训时间, 或 None (无锁/读取失败)
    """
    lock_path = os.path.join(_BASE, "reports", "retrain_locks", f"{symbol}.json")
    if not os.path.exists(lock_path):
        return None
    try:
        with open(lock_path, encoding="utf-8") as f:
            return datetime.fromisoformat(json.load(f).get("last_retrain"))
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.debug(f"读取重训锁失败 {symbol}: {e}")
        return None


def _write_retrain_lock(symbol: str, when: datetime) -> None:
    """写入标的最近重训时间

    Args:
        symbol: 标的代码
        when: 重训时间
    """
    lock_dir = os.path.join(_BASE, "reports", "retrain_locks")
    os.makedirs(lock_dir, exist_ok=True)
    lock_path = os.path.join(lock_dir, f"{symbol}.json")
    try:
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump({"last_retrain": when.isoformat()}, f, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.warning(f"写入重训锁失败 {symbol}: {e}")


def _clear_retrain_lock(symbol: str) -> None:
    """清除标的重训锁 (训练失败时调用, 允许后续重试)

    Args:
        symbol: 标的代码
    """
    lock_path = os.path.join(_BASE, "reports", "retrain_locks", f"{symbol}.json")
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.warning(f"清除重训锁失败 {symbol}: {e}")


# ============================================================
# P0 模块导入 (容错)
# ============================================================
try:
    from alpha.signal_fusion import SignalFusion

    _SIGNAL_FUSION_AVAILABLE = True
except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
    logger.warning(f"SignalFusion 不可用: {e}")
    SignalFusion = None
    _SIGNAL_FUSION_AVAILABLE = False

try:
    from ml.drift_detector import ModelDriftDetector

    _DRIFT_DETECTOR_AVAILABLE = True
except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
    logger.warning(f"ModelDriftDetector 不可用: {e}")
    ModelDriftDetector = None
    _DRIFT_DETECTOR_AVAILABLE = False

try:
    # 优先尝试 backtest.cost_aware_backtest (v7.5 src 在 sys.path)
    from backtest.cost_aware_backtest import CostAwareBacktest

    _COST_AWARE_BACKTEST_AVAILABLE = True
except Exception as _e1:  # noqa: BLE001  # fail-safe, 待后续精确化
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
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.warning(f"CostAwareBacktest 不可用 (cause: {_e1} | {e})")
        CostAwareBacktest = None
        _COST_AWARE_BACKTEST_AVAILABLE = False

# 主系统
try:
    # T3.6 迁移: 优先从新路径 utils/execution/ 导入, 兼容旧路径回退
    try:
        from utils.execution.automated_execution_system import AutomatedExecutionSystem
    except ImportError:
        from automated_execution_system import AutomatedExecutionSystem  # type: ignore[misc]

    # P1-4 FIX: 原本 _AUTO_SYSTEM_AVAILABLE = True 缩进为 8 空格, 错误地落在 except ImportError
    # 块内, 导致新路径导入成功时该常量永不赋值. 移到外层 try 块内 (4 空格), 两条导入路径
    # 成功后均置 True.
    _AUTO_SYSTEM_AVAILABLE = True
except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
    logger.error(f"AutomatedExecutionSystem 不可用: {e}")
    AutomatedExecutionSystem = object  # type: ignore[misc]
    _AUTO_SYSTEM_AVAILABLE = False

# 步骤4 止损监控
try:
    from stop_loss_monitor import StopLossMonitor

    _STOP_LOSS_AVAILABLE = True
except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
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
            s = s[len(prefix) :]
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


def load_qlib_bin(field: str, qlib_code: str) -> pd.Series | None:
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
        with open(cal_path, encoding="utf-8") as f:
            dates = [line.strip() for line in f if line.strip()]
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.exception("[SysInt] 读取交易日历文件失败: %s", cal_path)
        return None

    try:
        arr = np.fromfile(bin_path, dtype=np.float32)
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
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


def load_close_prices(codes: list[str]) -> pd.DataFrame:
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
        df = df.iloc[-252 * 5 :]
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

        # v8.6.13 P1 FIX (2026-08-01 AI 扫描):
        # 原代码在父类不可用时回退到 object (第 188 行 AutomatedExecutionSystem = object),
        # 此时 super().__init__(total_capital=...) 会调用 object.__init__(total_capital=...),
        # 抛出 TypeError: object.__init__() takes exactly one argument (the instance to initialize).
        # 修复: 仅在父类真实可用时调用 super().__init__(), 否则跳过并记录警告.
        if _AUTO_SYSTEM_AVAILABLE:
            super().__init__(total_capital=total_capital)
        else:
            logger.warning(
                "[IntegratedExecutionSystem] AutomatedExecutionSystem 父类不可用, "
                "跳过 super().__init__() — 仅初始化子类自身属性 (P0 钩子仍可运行)"
            )

        # P0-1: 信号融合器
        self.signal_fusion = self._init_signal_fusion()
        self.current_fused_signal: pd.Series | None = None
        self.last_signal_update: str | None = None

        # P0-2: 漂移检测器
        self.drift_detector = self._init_drift_detector()
        self.last_drift_check: str | None = None
        self.last_retrain_trigger: dict | None = None

        # 步骤4: 止损监控器
        self.stop_loss_monitor = self._init_stop_loss_monitor()
        self.last_stop_loss_check: str | None = None

        # P0-3: 成本感知回测
        self.cost_aware_backtest = None
        self.last_backtest_date: str | None = None

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
            with open(local_pos_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
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
                with open(src_pos_path, encoding="utf-8") as f:
                    src_data = json.load(f).get("positions", {})
            except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
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
                    pure = s[len(prefix) :]
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
                "est_price": avg_cost,  # 父类读 est_price
                "avg_cost": avg_cost,
                "target_weight": target_weight,
                "style": sector,  # 父类读 style
                "sector": sector,
            }

        # 备份原始 list 格式
        backup_path = os.path.join(_BASE, "config", "positions_list_backup.json")
        try:
            with open(backup_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.exception("[SysInt] 写入 positions 备份失败: %s", backup_path)

        # 覆盖 positions.json 为 dict 格式 (保留 meta)
        data["positions"] = normalized
        try:
            with open(local_pos_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(
                f"本地 positions.json 已规范化: list→dict, "
                f"{len(normalized)} 个持仓"
                + (
                    f", 未在 11_量化策略 找到: {missing_in_src}"
                    if missing_in_src
                    else ""
                )
            )
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.warning(f"写回 positions.json 失败: {e}")

    def _init_signal_fusion(self) -> object | None:
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
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.warning(f"SignalFusion 初始化失败: {e}")
            return None

    def _load_signal_weights(self) -> dict[str, float]:
        """从 QLib 训练报告读取 IC, 据此分配初始权重

        如果 IC > 0, 提升 ML 权重; 否则降低
        """
        # 默认权重 (与 SignalFusion 默认值一致)
        weights = {
            "alpha": 0.45,
            "ml": 0.10,
            "qlib": 0.10,
            "ai": 0.05,
            "macro": 0.25,
            "causal": 0.05,
        }

        # 查找最新的 QLib 训练报告
        report_path = self._find_latest_qlib_report()
        if report_path is None:
            return weights

        try:
            with open(report_path, encoding="utf-8") as f:
                report = json.load(f)
            mean_ic = float(report.get("mean_daily_ic", 0) or 0)
            ic_ir = float(report.get("ic_ir", 0) or 0)
            logger.info(f"QLib 报告 IC={mean_ic:.4f}, IC_IR={ic_ir:.4f}, 调整权重")

            # 如果 IC > 0, 提升 ML/QLib 权重
            if mean_ic > 0 and ic_ir > 0:
                weights["ml"] = 0.20
                weights["qlib"] = 0.20
                weights["alpha"] = 0.30
                # 归一化
                total = sum(weights.values())
                weights = {k: v / total for k, v in weights.items()}
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.warning(f"读取 QLib 报告失败: {e}")

        return weights

    def _find_latest_qlib_report(self) -> str | None:
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

    def _init_drift_detector(self) -> object | None:
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
            _preheated = False
            report_path = self._find_latest_qlib_report()
            if report_path:
                try:
                    with open(report_path, encoding="utf-8") as f:
                        report = json.load(f)
                    initial_ic = float(report.get("mean_daily_ic", 0) or 0)
                    if initial_ic != 0:
                        # 注入 5 次 IC, 让 ADWIN 有足够样本
                        seed_date = now_bj() - timedelta(days=5)
                        for i in range(5):
                            d = seed_date + timedelta(days=i)
                            detector.update_ic(d, initial_ic)
                        logger.info(
                            f"漂移检测器预热: IC={initial_ic:.4f} (5 次注入, 源: QLib 报告)"
                        )
                        _preheated = True
                except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
                    logger.warning(f"漂移检测器预热失败: {e}")

            # N1-4: QLib 预热未成功时, 回退到历史模型 CV IC 预热
            if not _preheated:
                try:
                    # SC-33 修复 (2026-09-12): 原 import 为 `from lgb_enhanced_trainer import
                    # POSITION_SYMBOLS, load_model_meta` —— 后者**不存在**于该模块
                    # (真实所属: `lgb_trainer.persistence.load_model_meta`), 属幽灵符号;
                    # 且异常被 `except Exception` 以 logger.debug 静默吞掉, 于是
                    # "QLib 预热失败 -> 历史模型 CV IC 兜底预热"这条**唯一兜底路径从未生效**,
                    # 漂移检测器在 QLib 不可用时长期以 0 样本冷启动, 检测灵敏度形同虚设。
                    # POSITION_SYMBOLS 由 lgb_enhanced_trainer 再导出 (保留), load_model_meta
                    # 改从真实归属模块导入。
                    from lgb_enhanced_trainer import POSITION_SYMBOLS
                    from lgb_trainer.persistence import load_model_meta

                    hist_ic_values = []
                    for sym_tuple in POSITION_SYMBOLS[:5]:
                        meta = load_model_meta(sym_tuple[0])
                        if meta and meta.get("cv_after_selection"):
                            hist_ic_values.append(
                                float(meta["cv_after_selection"].get("mean_ic", 0) or 0)
                            )
                    if hist_ic_values:
                        avg_hist_ic = float(np.mean(hist_ic_values))
                        seed_date = now_bj() - timedelta(days=5)
                        for i in range(5):
                            d = seed_date + timedelta(days=i)
                            detector.update_ic(d, avg_hist_ic)
                        logger.info(
                            f"漂移检测器预热: IC={avg_hist_ic:.4f} (5 次注入, "
                            f"源: 历史模型 CV, {len(hist_ic_values)} 个模型均值)"
                        )
                except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
                    # SC-33: 该分支是 QLib 不可用时的唯一兜底, 失败须可见 (原为 debug 级 -> 长期隐形)
                    logger.warning(f"历史模型 IC 预热失败 (非致命, 漂移检测器将以冷启动运行): {e}")

            return detector
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.warning(f"ModelDriftDetector 初始化失败: {e}")
            return None

    def _init_stop_loss_monitor(self) -> object | None:
        """初始化止损监控器"""
        if not _STOP_LOSS_AVAILABLE:
            return None
        try:
            monitor = StopLossMonitor()
            logger.info(f"止损监控器已初始化: {len(monitor.rules)} 条规则")
            return monitor
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.warning(f"StopLossMonitor 初始化失败: {e}")
            return None

    # ---------- 主执行钩子 ----------

    def _execute_daily_trading(
        self, execution_name: str = "daily_execution", force_step5c: bool = False
    ) -> None:
        """覆盖父类方法, 注入 P0 钩子

        执行顺序:
          1. 父类原有逻辑
          2. 前置钩子: SignalFusion IC 动态权重更新
          3. 父类原有逻辑
          4. 后置钩子: 漂移检测 / 成本回测 / 止损监控

        v8.6.13 P1 FIX (2026-08-01 AI 扫描):
            原代码后置钩子不在 finally 块中, 父类 super()._execute_daily_trading() 抛异常时,
            所有后置风控钩子 (漂移检测/止损监控) 被跳过, 与文件头"主流程异常不影响系统集成"
            的设计意图相反. 修复: 后置钩子放入 finally 块, 保证无论主流程是否异常都执行.
            (止损监控是最关键的, 持仓跌破止损线却不触发卖出会扩大亏损)
        """
        # 前置钩子: 更新信号融合权重
        self._hook_update_signal_fusion()

        # 执行父类流程, 后置钩子放入 finally 保证主流程异常时仍执行风控
        try:
            # v8.6.13 P1 FIX (2026-08-01 AI 扫描):
            # 父类不可用时回退到 object, 调用 super()._execute_daily_trading() 会抛
            # AttributeError: 'object' object has no attribute '_execute_daily_trading'.
            # 修复: 仅在父类真实可用时调用父类方法, 否则跳过 (P0 钩子仍可在 finally 中执行).
            if _AUTO_SYSTEM_AVAILABLE:
                super()._execute_daily_trading(execution_name)
            else:
                logger.warning(
                    "[IntegratedExecutionSystem] AutomatedExecutionSystem 父类不可用, "
                    "跳过 super()._execute_daily_trading() — 仅执行 P0 风控钩子"
                )
        finally:
            # 后置钩子: 即使主流程异常也必须执行 (止损监控不可跳过)
            self._hook_drift_and_retrain()
            self._hook_cost_aware_backtest(force=force_step5c)
            self._hook_stop_loss_review()

    def _hook_update_signal_fusion(self) -> None:
        """步骤5a: 更新 SignalFusion 权重"""
        if not self.signal_fusion:
            return
        try:
            weights = self._load_signal_weights()
            self.signal_fusion.update_weights(weights)
            self.last_signal_update = now_bj().isoformat()
            logger.info(f"SignalFusion 权重已更新: {weights}")
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.warning(f"SignalFusion 权重更新失败: {e}")

    def _hook_drift_and_retrain(self) -> bool:
        """步骤5b: 漂移检测 + 自动重训练触发 (N1 重写)

        修复 Bug-A: check_drift() → check_all() (旧代码 hasattr 兜底让告警永远空)
        修复 Bug-B: IC 来源多源回退 (ic_recorder → QLib 报告 → 跳过)
        修复 Bug-C: 检测到漂移后真正触发重训, 带冷却期 (旧代码只记录不执行)
        修复 Bug-D: DriftAlert 是 dataclass, 旧代码 a.get('type') 当 dict 访问会崩

        新增: ADWIN 概念漂移注入 (用 IC 值喂给 ADWIN)
        安全: shadow_mode=True 时只记录不真正重训, 避免破坏生产模型
        """
        if not self.drift_detector:
            return
        try:
            today = now_bj().date()

            # ---------- 1. 获取当日 IC (多源回退, 修复 Bug-B) ----------
            daily_ic = self._fetch_daily_ic()
            if daily_ic is None:
                logger.info(
                    "跳过漂移检测: 无可用 IC 数据 (ic_recorder 与 QLib 报告均无)"
                )
                self.last_drift_check = today.isoformat()
                return

            # ---------- 2. 注入 IC 到检测器 (IC衰减 + ADWIN) ----------
            self.drift_detector.update_ic(today, daily_ic)
            # 同步喂给 ADWIN (用 IC 值检测概念漂移)
            try:
                self.drift_detector.update_adwin(daily_ic)
            except Exception as e_adwin:  # noqa: BLE001  # fail-safe, 待后续精确化
                logger.debug(f"ADWIN 更新失败 (非致命): {e_adwin}")

            # ---------- 3. 综合检查 (修复 Bug-A: check_all) ----------
            alerts = self.drift_detector.check_all()
            self.last_drift_check = today.isoformat()

            if not alerts:
                logger.info(f"漂移检测完成: IC={daily_ic:.4f}, 无显著漂移")
                return

            # ---------- 4. 触发重训 (修复 Bug-C, 带冷却期) ----------
            # 修复 Bug-D: DriftAlert 是 dataclass, 用属性访问而非 .get()
            critical_count = sum(
                1
                for a in alerts
                if str(getattr(getattr(a, "severity", None), "value", "")).lower()
                == "critical"
            )
            alert_summaries = [
                f"[{getattr(getattr(a, 'severity', None), 'value', '?')}] {getattr(a, 'message', str(a))}"
                for a in alerts[:3]
            ]
            logger.warning(
                f"漂移检测发现 {len(alerts)} 个告警 (critical={critical_count}): {alert_summaries}"
            )

            need_retrain, reason = self.drift_detector.should_retrain()
            if not need_retrain:
                self.last_retrain_trigger = {
                    "date": today.isoformat(),
                    "alert_count": len(alerts),
                    "critical_count": critical_count,
                    "action": "monitor_only",
                    "reason": reason,
                }
                logger.info(f"漂移告警未达重训阈值, 仅监控: {reason}")
                return

            retrained = self._trigger_retrain_with_cooldown(reason=reason)
            self.last_retrain_trigger = {
                "date": today.isoformat(),
                "alert_count": len(alerts),
                "critical_count": critical_count,
                "action": (
                    "retrain_triggered"
                    if retrained
                    else (
                        "shadow_recorded"
                        if EVOLUTION_CONFIG["shadow_mode"]
                        else "cooldown_skip"
                    )
                ),
                "reason": reason,
            }
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.warning(f"漂移检测失败: {e}")
            logger.debug(traceback.format_exc())

    def _fetch_daily_ic(self) -> float | None:
        """获取当日 IC (多源回退, 修复 Bug-B)

        优先级:
          1. reports/daily_ic_scores.json 的 latest_ic (N3 ic_recorder 写入)
          2. 最新 QLib 报告的 mean_daily_ic
          3. None (跳过漂移检测)

        Returns:
            IC 值, 或 None (无可用数据)
        """
        # 源1: ic_recorder 写入的当日 IC
        ic_path = os.path.join(self.report_dir, "daily_ic_scores.json")
        if os.path.exists(ic_path):
            try:
                with open(ic_path, encoding="utf-8") as f:
                    ic_data = json.load(f)
                latest = float(ic_data.get("latest_ic", 0) or 0)
                if abs(latest) >= EVOLUTION_CONFIG["ic_min_abs_threshold"]:
                    return latest
            except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
                logger.debug(f"读取 daily_ic_scores.json 失败: {e}")

        # 源2: QLib 报告 (回退)
        report_path = self._find_latest_qlib_report()
        if report_path:
            try:
                with open(report_path, encoding="utf-8") as f:
                    report = json.load(f)
                mean_ic = float(report.get("mean_daily_ic", 0) or 0)
                if abs(mean_ic) >= EVOLUTION_CONFIG["ic_min_abs_threshold"]:
                    logger.info(f"IC 来源回退到 QLib 报告: mean_daily_ic={mean_ic:.4f}")
                    return mean_ic
            except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
                logger.debug(f"读取 QLib 报告 IC 失败: {e}")

        return None

    def _trigger_retrain_with_cooldown(self, reason: str) -> bool:
        """触发重训 (带冷却期, 修复 Bug-C)

        安全策略:
          - shadow_mode=True (默认): 只记录"该重训"到日志和冷却锁, 不真正调用
            run_enhanced_training, 避免破坏生产模型. 待检测准确性验证后手动切换.
          - shadow_mode=False: 真正调用 run_enhanced_training 重训.

        Args:
            reason: 重训原因 (来自 should_retrain)

        Returns:
            True 如果实际触发了重训, False 如果影子记录/冷却跳过/失败
        """
        if EVOLUTION_CONFIG["shadow_mode"]:
            logger.warning(
                f"[影子模式] 检测到需重训 (原因: {reason}), 但 shadow_mode=True 仅记录不执行. "
                f"验证准确性后在 EVOLUTION_CONFIG 切换 shadow_mode=False 启用真实重训."
            )
            return False

        try:
            from lgb_enhanced_trainer import (
                LGB_ENHANCED_CONFIG,
                POSITION_SYMBOLS,
                run_enhanced_training,
            )
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.warning(f"无法导入训练模块, 重训取消: {e}")
            return False

        today = now_bj()
        cooled_symbols = []
        skipped_by_cooldown = []

        for sym_tuple in POSITION_SYMBOLS:
            code = sym_tuple[0]
            last = _read_retrain_lock(code)
            if last and (today - last).days < EVOLUTION_CONFIG["retrain_cooldown_days"]:
                skipped_by_cooldown.append(code)
                continue
            cooled_symbols.append(sym_tuple)

        if not cooled_symbols:
            logger.info(f"重训冷却中, 跳过 {len(skipped_by_cooldown)} 个标的")
            return False

        logger.info(
            f"触发自适应重训: {len(cooled_symbols)} 个标的 (冷却跳过 {len(skipped_by_cooldown)}), 原因: {reason}"
        )

        # P1-5 修复 (2026-09-09): 训练前先写"进行中"原子标记, 防止并发重复重训
        for sym_tuple in cooled_symbols:
            _write_retrain_lock(sym_tuple[0], today)

        try:
            result = run_enhanced_training(
                symbols=cooled_symbols,
                force_retrain=True,
                config=LGB_ENHANCED_CONFIG,
                use_news=True,
            )
            # P1-5 修复: 按 result 状态决定冷却锁 — 失败时清除锁允许重试, 成功时锁已写入
            train_status = result.get("status", "UNKNOWN")
            if train_status not in ("success", "ok", "completed"):
                logger.warning(
                    "重训结果非成功 (status=%s), 清除冷却锁允许后续重试", train_status
                )
                for sym_tuple in cooled_symbols:
                    _clear_retrain_lock(sym_tuple[0])

            logger.info(f"重训完成: status={train_status}")
            return True
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"重训执行失败: {e}")
            logger.debug(traceback.format_exc())
            # P1-5 修复: 异常时清除"进行中"标记, 允许后续重试
            for sym_tuple in cooled_symbols:
                _clear_retrain_lock(sym_tuple[0])
            return False

    def _hook_cost_aware_backtest(self, force: bool = False) -> None:
        """步骤5c: 成本感知回测验证"""
        if not _COST_AWARE_BACKTEST_AVAILABLE or CostAwareBacktest is None:
            return
        try:
            today = now_bj().date()
            if not force and today.weekday() != 0:
                return
            if self.last_backtest_date == today.isoformat():
                return

            self.last_backtest_date = today.isoformat()
            backtest = CostAwareBacktest()
            positions_path = os.path.join(_BASE, "config", "positions.json")
            result = {}
            if hasattr(backtest, "run_backtest"):
                try:
                    result = backtest.run_backtest(positions_path=positions_path)
                except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
                    logger.warning(f"成本回测 run_backtest 失败: {e}")
            elif hasattr(backtest, "evaluate"):
                try:
                    result = backtest.evaluate()
                except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
                    logger.warning(f"成本回测 evaluate 失败: {e}")

            report_path = os.path.join(
                self.report_dir, f"cost_backtest_{today.isoformat()}.json"
            )
            try:
                with open(report_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
            except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                logger.exception("[SysInt] 成本回测报告写入失败: %s", report_path)

            logger.info(f"成本感知回测验证完成, 报告: {report_path}")
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.warning(f"成本感知回测失败: {e}")

    def _hook_stop_loss_review(self) -> None:
        """步骤4: 止损止盈自动触发"""
        if not self.stop_loss_monitor:
            return
        try:
            self.last_stop_loss_check = now_bj().isoformat()
            triggers = self.stop_loss_monitor.check_and_execute()
            if triggers:
                executed = [t for t in triggers if getattr(t, "executed", False)]
                logger.warning(
                    f"止损监控触发 {len(triggers)} 条规则, 已执行 {len(executed)} 笔卖出"
                )
            else:
                logger.info("止损监控完成: 未触发任何止损/止盈")
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.warning(f"止损监控失败: {e}")

    # ---------- 系统控制 ----------

    def start_system(self) -> None:
        """启动系统"""
        logger.info("集成执行系统启动")
        # 这里可以启动监控线程/定时任务
        # 当前为简化实现, 仅执行一次日度流程
        self._execute_daily_trading()

    def stop_system(self) -> None:
        """停止系统"""
        logger.info("集成执行系统停止")


if __name__ == "__main__":
    system = IntegratedExecutionSystem(total_capital=5_000_000)
    system.start_system()
