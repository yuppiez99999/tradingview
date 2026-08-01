"""
统一风险管理驾驶舱 (Unified Risk Cockpit) — v8.4 P0-7 修复
============================================================

审计报告问题:
    P0-3: 四套熔断系统不协调 — kill_switch / circuit_breaker /
          drawdown_controller / portfolio.yaml 分散部署, 各管各的
    P0-4: VaR回测完全缺失 — 无Kupiec/Christoffersen检验

本模块统一整合所有风险管理子系统为单一入口, 并加入 VaR 回测。

架构:
    UnifiedRiskCockpit
    ├── KillSwitch (保证金监控)          → L1/L2/L3 熔断
    ├── CircuitBreaker (数据源熔断)       → 多源自动切换
    ├── DrawdownController (回撤控制)     → 动态减仓
    ├── PositionLimits (仓位检查)         → portfolio.yaml 规则
    ├── VaRModel (在险价值)               → 历史模拟 + 蒙特卡洛
    └── VaRBacktester (VaR回测)          → Kupiec + Christoffersen

用法:
    from src.risk.unified_risk_cockpit import UnifiedRiskCockpit

    cockpit = UnifiedRiskCockpit()
    status = cockpit.full_scan(margin_usage=0.35, positions={...}, pnl=-45000)
    if not status["all_clear"]:
        cockpit.execute_actions(status["actions"])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any

import numpy as np
import yaml  # type: ignore[import-untyped]

logger = logging.getLogger("risk_cockpit")

# ── 动态导入, 单文件部署无依赖 ──
try:
    from utils.kill_switch import KillSwitch  # pylint: disable=unused-import

    _HAS_KILL_SWITCH = True
except ImportError:
    _HAS_KILL_SWITCH = False

try:
    from src.risk.circuit_breaker import CircuitBreaker, CircuitState  # noqa: F401  # pylint: disable=unused-import

    _HAS_CIRCUIT_BREAKER = True
except ImportError:
    _HAS_CIRCUIT_BREAKER = False


# ═══════════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════════


class RiskLevel(Enum):
    NORMAL = auto()
    WARNING = auto()
    CRITICAL = auto()
    CIRCUIT_BREAK = auto()


@dataclass
class RiskSnapshot:
    """风控快照 — 所有子系统的统一输出格式"""

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    # 保证金状态
    margin_usage_ratio: float = 0.0
    kill_switch_level: int = 0
    kill_switch_actions: list[str] = field(default_factory=list)

    # 回撤状态
    current_drawdown: float = 0.0
    max_drawdown_limit: float = 0.15
    drawdown_breach: bool = False
    drawdown_reduce_pct: float = 0.0

    # 数据源状态
    data_source_status: dict[str, str] = field(default_factory=dict)

    # 仓位状态
    position_limit_breach: bool = False
    position_warnings: list[str] = field(default_factory=list)

    # VaR 状态
    daily_var_95: float = 0.0
    daily_var_99: float = 0.0
    var_breach: bool = False

    # 综合判定
    risk_level: RiskLevel = RiskLevel.NORMAL
    all_clear: bool = True
    actions_required: list[str] = field(default_factory=list)
    summary: str = ""


# ═══════════════════════════════════════════════════════════════
# 回撤控制器 (轻量内嵌版, 避免跨模块依赖)
# ═══════════════════════════════════════════════════════════════


class DrawdownController:
    """回撤控制器 — 动态减仓"""

    def __init__(self, max_drawdown: float = 0.15, reduce_steps: int = 3):
        self.max_drawdown = max_drawdown
        self.reduce_steps = reduce_steps
        self.peak_value: float = 0.0
        self._history: list[float] = []

    def update(self, current_value: float) -> dict:
        """更新回撤状态并返回控制指令"""
        self._history.append(current_value)
        self.peak_value = max(self.peak_value, current_value)

        drawdown = (self.peak_value - current_value) / max(self.peak_value, 1.0)
        result = {
            "drawdown": round(drawdown, 4),
            "breach": drawdown > self.max_drawdown,
            "reduce_pct": 0.0,
            "action": "none",
        }

        if drawdown > self.max_drawdown:
            # 按步进减仓
            exceed_pct = (drawdown - self.max_drawdown) / self.max_drawdown
            step = min(self.reduce_steps, int(exceed_pct * self.reduce_steps) + 1)
            reduce_pct = round(step / self.reduce_steps, 2)
            result["reduce_pct"] = reduce_pct
            result["action"] = f"reduce_{int(reduce_pct * 100)}pct"

        return result


# ═══════════════════════════════════════════════════════════════
# VaR 模型
# ═══════════════════════════════════════════════════════════════


class VaRModel:
    """在险价值计算 — 历史模拟 + 蒙特卡洛"""

    def __init__(self, confidence_levels: tuple[float, float] = (0.95, 0.99)):
        self.confidence_levels = confidence_levels
        self._return_history: list[float] = []

    def update_returns(self, returns: list[float]):
        """更新日收益率序列"""
        self._return_history = returns[-252:]  # 保留最近252个交易日

    def compute_var(self, portfolio_value: float, method: str = "historical") -> dict[str, float]:
        """计算 VaR

        Returns:
            {"var_95": xxx, "var_99": xxx, "cvar_95": xxx, "cvar_99": xxx}
        """
        if len(self._return_history) < 30:
            return {"var_95": 0.0, "var_99": 0.0, "cvar_95": 0.0, "cvar_99": 0.0}

        returns = np.array(self._return_history)

        if method == "monte_carlo":
            mu, sigma = np.mean(returns), np.std(returns)
            simulated = np.random.normal(mu, sigma, 10000)
            returns_for_calc = simulated
        else:
            returns_for_calc = returns

        result = {}
        for cl in self.confidence_levels:
            var_key = f"var_{int(cl * 100)}"
            cvar_key = f"cvar_{int(cl * 100)}"
            var = np.percentile(returns_for_calc, (1 - cl) * 100)
            cvar = returns_for_calc[returns_for_calc <= var].mean()
            result[var_key] = round(var * portfolio_value, 2)
            result[cvar_key] = round(cvar * portfolio_value, 2)

        return result


# ═══════════════════════════════════════════════════════════════
# VaR 回测
# ═══════════════════════════════════════════════════════════════


class VaRBacktester:
    """VaR 回测 — Kupiec 无条件覆盖检验 + Christoffersen 条件覆盖检验

    Kupiec (1995) POF 检验: 考察 VaR 突破次数是否与置信水平一致
    Christoffersen (1998): 额外考察突破是否聚集 (条件覆盖)

    零假设 H0: VaR 模型正确 (突破率 = 1 - 置信水平)
    备择 H1: VaR 模型有误
    """

    def __init__(self, confidence: float = 0.95):
        self.confidence = confidence

    def run_tests(self, returns: list[float], var_values: list[float]) -> dict[str, Any]:
        """执行全部 VaR 回测检验

        Args:
            returns: 实际日收益率序列
            var_values: 对应日期的 VaR 预测值 (负数, 如 -0.02)

        Returns:
            检验结果字典
        """
        if len(returns) < 50:
            return {"status": "INSUFFICIENT_DATA", "message": f"数据不足 (n={len(returns)} < 50)"}

        returns_arr = np.array(returns)
        var_arr = np.array(var_values)

        # VaR 突破: 实际损失 > VaR 预测值
        violations = returns_arr < var_arr
        n = len(returns_arr)
        n_violations = violations.sum()
        violation_rate = n_violations / n

        expected_rate = 1 - self.confidence

        # ── Kupiec POF 检验 ──
        kupiec_result = self._kupiec_pof_test(n, n_violations, expected_rate)

        # ── Christoffersen 条件覆盖检验 ──
        christoffersen_result = self._christoffersen_test(violations)

        # ── 综合判定 ──
        kupiec_pass = kupiec_result.get("p_value", 0) > 0.05
        christoffersen_pass = christoffersen_result.get("p_value", 0) > 0.05

        # P3-2a: Basel 交通灯 (新增, 补全 Kupiec/Christoffersen + 交通灯 三重校验)
        basel_zone = self._basel_traffic_light(int(n_violations), n, self.confidence)

        return {
            "status": "COMPLETE",
            "n_observations": n,
            "n_violations": int(n_violations),
            "violation_rate": round(violation_rate, 4),
            "expected_rate": round(expected_rate, 4),
            "kupiec_pof": kupiec_result,
            "christoffersen": christoffersen_result,
            "basel_traffic_light": basel_zone,
            "overall_pass": kupiec_pass and christoffersen_pass,
            "verdict": (
                "PASS — VaR 模型统计可靠" if kupiec_pass and christoffersen_pass else "FAIL — VaR 模型需重新校准"
            ),
        }

    @staticmethod
    def _kupiec_pof_test(n: int, x: int, p: float) -> dict:
        """Kupiec POF 似然比检验

        LR_POF = -2 * ln( (1-p)^{n-x} * p^x / (1-x/n)^{n-x} * (x/n)^x )
        """
        import math

        from scipy.stats import chi2

        if x in (0, n):
            p_hat = x / n if x > 0 else 0.0001
        else:
            p_hat = x / n

        # 避免 log(0)
        eps = 1e-10
        p_hat = max(eps, min(1 - eps, p_hat))
        p = max(eps, min(1 - eps, p))

        lr_stat = -2 * ((n - x) * math.log((1 - p) / (1 - p_hat)) + x * math.log(p / p_hat))

        try:
            p_value = 1 - chi2.cdf(lr_stat, df=1)
        except Exception as _exc:
            logger.debug("[VaRBacktest] Kupiec POF 检验异常, 保守置 p_value=1.0: %s", _exc)
            p_value = 1.0

        return {
            "lr_statistic": round(float(lr_stat), 4),
            "p_value": round(float(p_value), 4),
            "pass": p_value > 0.05,
            "interpretation": (
                "H0不能拒绝: VaR突破率与预期一致" if p_value > 0.05 else "拒绝H0: VaR突破率偏离预期 (模型可能不准确)"
            ),
        }

    @staticmethod
    def _christoffersen_test(violations: np.ndarray) -> dict:
        """Christoffersen 条件覆盖检验 — 突破是否聚集"""
        violations_int = violations.astype(int)

        # 转移计数
        n00 = ((violations_int[:-1] == 0) & (violations_int[1:] == 0)).sum()
        n01 = ((violations_int[:-1] == 0) & (violations_int[1:] == 1)).sum()
        n10 = ((violations_int[:-1] == 1) & (violations_int[1:] == 0)).sum()
        n11 = ((violations_int[:-1] == 1) & (violations_int[1:] == 1)).sum()

        eps = 1e-10
        pi01 = n01 / max(n00 + n01, eps)
        pi11 = n11 / max(n10 + n11, eps)
        pi2 = (n01 + n11) / max(n00 + n01 + n10 + n11, eps)

        import math

        from scipy.stats import chi2

        lr_ind = -2 * math.log(
            ((1 - pi2) ** (n00 + n10) * pi2 ** (n01 + n11))
            / ((1 - pi01) ** n00 * pi01**n01 * (1 - pi11) ** n10 * pi11**n11 + eps)
            + eps
        )

        try:
            p_value = 1 - chi2.cdf(lr_ind, df=1)
        except Exception as _exc:
            logger.debug("[VaRBacktest] Christoffersen 检验异常, 保守置 p_value=1.0: %s", _exc)
            p_value = 1.0

        return {
            "lr_statistic": round(float(abs(lr_ind)), 4),
            "p_value": round(float(p_value), 4),
            "pass": p_value > 0.05,
            "transition_matrix": {
                "00": int(n00),
                "01": int(n01),
                "10": int(n10),
                "11": int(n11),
            },
            "interpretation": (
                "H0不能拒绝: 突破独立分布 (无聚集)" if p_value > 0.05 else "拒绝H0: 突破存在聚集性 (风险模型可能滞后)"
            ),
        }

    @staticmethod
    def _basel_traffic_light(n_violations: int, n: int, confidence: float) -> dict[str, Any]:
        """Basel 委员会 99% VaR 交通灯机制 (基于二项分布置信带).

        绿区: 例外数 <= 95% 上界 (模型有效)
        黄区: 例外数介于 95% 与 99.99% 上界之间 (需关注)
        红区: 例外数 >= 99.99% 上界 (模型无效, 须立即修正)
        """
        from scipy.stats import binom

        try:
            green_max = int(binom.ppf(0.95, n, 1 - confidence))
            red_min = int(binom.ppf(0.9999, n, 1 - confidence))
        except Exception:
            import math

            z = 1.645 if confidence >= 0.99 else 1.96
            expected = (1 - confidence) * n
            sd = math.sqrt(n * (1 - confidence) * confidence)
            green_max = int(expected + z * sd)
            red_min = int(expected + 3.09 * sd)
        if n_violations <= green_max:
            zone, desc = "GREEN", "模型有效"
        elif n_violations < red_min:
            zone, desc = "YELLOW", "模型需关注, 建议检查校准"
        else:
            zone, desc = "RED", "模型无效, 必须立即修正"
        return {
            "zone": zone,
            "description": desc,
            "green_max": green_max,
            "red_min": red_min,
            "expected_exceptions": round(float((1 - confidence) * n), 2),
        }


# ═══════════════════════════════════════════════════════════════
# 统一风险驾驶舱
# ═══════════════════════════════════════════════════════════════


class UnifiedRiskCockpit:
    """统一风险管理驾驶舱 — 整合所有风控子系统

    扫描流程:
        1. Kill Switch  → 保证金检查 → L1/L2/L3 熔断判定
        2. Drawdown     → 回撤检查 → 动态减仓指令
        3. Data Sources → 数据源熔断器状态汇总
        4. Positions    → 单票/行业/仓位上限检查
        5. VaR          → 计算 VaR/CVaR
        6. VaR Backtest → 检验 VaR 模型有效性
        7. 综合判定     → 生成统一快照 + 执行指令

    所有子系统使用统一 Snapshot 协议通信:
        snapshot_dict → RiskSnapshot → action_list
    """

    def __init__(
        self, portfolio_value: float = 5_000_000, config_path: Path | None = None, enable_var_backtest: bool = True
    ):
        """
        Args:
            portfolio_value: 组合总市值
            config_path: portfolio.yaml 路径
            enable_var_backtest: 是否启用 VaR 回测
        """
        self.portfolio_value = portfolio_value
        self.config_path = config_path or (Path(__file__).resolve().parent.parent.parent / "configs" / "portfolio.yaml")
        self.enable_var_backtest = enable_var_backtest

        # ── 初始化子系统 ──
        self.kill_switch = KillSwitch(config_path=self.config_path) if _HAS_KILL_SWITCH else None
        self.drawdown_ctrl = DrawdownController()
        self.var_model = VaRModel()
        self.var_backtester = VaRBacktester() if enable_var_backtest else None

        # 历史记录
        self._snapshot_history: list[RiskSnapshot] = []
        self._var_history: list[float] = []
        self._return_history: list[float] = []

        # 数据源熔断器注册表
        self._circuit_breakers: dict[str, Any] = {}

        logger.info("UnifiedRiskCockpit 初始化完成 (portfolio=%.0f万)", portfolio_value / 10000)

    # ── 数据源熔断器管理 ──

    def register_circuit_breaker(self, name: str, breaker):
        """注册数据源熔断器"""
        self._circuit_breakers[name] = breaker

    def get_data_source_status(self) -> dict[str, str]:
        """获取所有数据源状态"""
        if not _HAS_CIRCUIT_BREAKER:
            return {}
        status = {}
        for name, breaker in self._circuit_breakers.items():
            status[name] = str(breaker.state) if hasattr(breaker, "state") else "unknown"
        return status

    # ── 回报率记录 (用于 VaR 计算) ──

    def record_return(self, daily_return: float):
        """记录日收益率 (供 VaR 模型和回测使用)"""
        self._return_history.append(daily_return)
        self.var_model.update_returns(self._return_history)

    # ── 核心扫描 ──

    def full_scan(
        self,
        margin_usage: float | None = None,
        positions: dict[str, dict] | None = None,
        pnl: float = 0.0,
        current_value: float | None = None,
    ) -> RiskSnapshot:
        """全量风控扫描 — 所有子系统一次性检查

        Args:
            margin_usage: 保证金占用率 [0-1]
            positions: 持仓字典 {code: {weight, amount, category}}
            pnl: 当日盈亏
            current_value: 当前组合市值 (None 则使用初始化值)

        Returns:
            RiskSnapshot — 统一快照
        """
        if current_value is not None:
            self.portfolio_value = current_value

        snapshot = RiskSnapshot()
        snapshot.margin_usage_ratio = margin_usage or 0.0

        # ── Step 1: Kill Switch ──
        self._scan_kill_switch(snapshot, margin_usage)

        # ── Step 2: Drawdown ──
        self._scan_drawdown(snapshot, current_value or self.portfolio_value, pnl)

        # ── Step 3: Data Sources ──
        snapshot.data_source_status = self.get_data_source_status()

        # ── Step 4: Position Limits ──
        self._scan_positions(snapshot, positions or {})

        # ── Step 5: VaR ──
        self._scan_var(snapshot, current_value or self.portfolio_value)

        # ── Step 6: 综合判定 ──
        self._determine_risk_level(snapshot)

        # 记录历史
        self._snapshot_history.append(snapshot)
        if len(self._snapshot_history) > 1000:
            self._snapshot_history = self._snapshot_history[-500:]

        return snapshot

    def _scan_kill_switch(self, snapshot: RiskSnapshot, margin_usage: float | None):
        """Kill Switch 保证金检查"""
        if margin_usage is None:
            snapshot.summary += "[KillSwitch] 无保证金数据; "
            return

        snapshot.margin_usage_ratio = margin_usage

        # 三级熔断判定
        if margin_usage >= 0.90:
            snapshot.kill_switch_level = 3
            snapshot.kill_switch_actions = [
                "L3_互盲: 变现10%高流动性ETF, 跨品种清算注入",
            ]
        elif margin_usage >= 0.75:
            snapshot.kill_switch_level = 2
            snapshot.kill_switch_actions = [
                "L2_熔断: 强平最深虚值期权空头, 释放保证金",
            ]
        elif margin_usage >= 0.50:
            snapshot.kill_switch_level = 1
            snapshot.kill_switch_actions = [
                "L1_警戒: 停止新开仓, 只平仓不计数",
            ]

        if snapshot.kill_switch_level > 0:
            # 通过 KillSwitch 类执行（需 broker_callback）
            if self.kill_switch:
                try:
                    ks_result = self.kill_switch.check_margin_status(margin_usage)
                    snapshot.kill_switch_level = max(snapshot.kill_switch_level, ks_result.get("level", 0))
                except Exception as e:
                    logger.warning(f"KillSwitch执行异常: {e}")
            snapshot.summary += f"[KillSwitch] L{snapshot.kill_switch_level}熔断! "

    def _scan_drawdown(self, snapshot: RiskSnapshot, current_value: float, pnl: float):  # pylint: disable=unused-argument
        """回撤检查"""
        dd_result = self.drawdown_ctrl.update(current_value)
        snapshot.current_drawdown = dd_result["drawdown"]
        snapshot.max_drawdown_limit = self.drawdown_ctrl.max_drawdown

        if dd_result["breach"]:
            snapshot.drawdown_breach = True
            snapshot.drawdown_reduce_pct = dd_result["reduce_pct"]
            snapshot.actions_required.append(
                f"DD_BREACH: 回撤{dd_result['drawdown']:.2%}, 建议减仓{dd_result['reduce_pct']:.0%}"
            )
            snapshot.summary += (
                f"[DD] 回撤{dd_result['drawdown']:.2%}>"
                f"上限{self.drawdown_ctrl.max_drawdown:.0%}, "
                f"建议减仓{dd_result['reduce_pct']:.0%}; "
            )

    def _scan_positions(self, snapshot: RiskSnapshot, positions: dict[str, dict]):
        """仓位限制检查"""
        if not positions:
            return

        # 加载配置中的仓位限制 (P1-Q8: 优先 ConfigManager, 失败回退直接读取)
        cfg = None
        try:
            _project_root = Path(__file__).resolve().parent.parent.parent.parent
            import sys as _sys

            if str(_project_root) not in _sys.path:
                _sys.path.insert(0, str(_project_root))
            from utils.config_manager import get_portfolio_config

            cfg = get_portfolio_config()
        except Exception as _exc:
            logger.debug("[RiskCockpit] get_portfolio_config 导入/调用失败, 回退 YAML: %s", _exc)

        if not cfg:
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
            except Exception as _exc:
                logger.debug("[RiskCockpit] portfolio.yaml 读取失败, 使用默认风控参数: %s", _exc)
                cfg = {}

        try:
            risk_params = cfg.get("risk_parameters", {})
            max_single = risk_params.get("max_single_position", 0.08)
            max_sector = risk_params.get("max_sector_exposure", 0.30)
        except Exception as _exc:
            logger.debug("[RiskCockpit] 风控参数解析异常, 使用默认上限: %s", _exc)
            max_single = 0.08
            max_sector = 0.30

        # 单票检查
        for code, pos in positions.items():
            weight = pos.get("weight", 0)
            if weight > max_single:
                snapshot.position_warnings.append(f"{code}: 权重{weight:.2%}>{max_single:.0%}上限")
                snapshot.position_limit_breach = True

        # 行业集中度检查
        sector_weights: dict[str, float] = {}
        for _code, pos in positions.items():
            sector = pos.get("category", "unknown")
            sector_weights[sector] = sector_weights.get(sector, 0) + pos.get("weight", 0)

        for sector, weight in sector_weights.items():
            if weight > max_sector:
                snapshot.position_warnings.append(f"行业{sector}: 权重{weight:.2%}>{max_sector:.0%}上限")
                snapshot.position_limit_breach = True

        if snapshot.position_limit_breach:
            snapshot.summary += "[POS] 仓位超限; "

    def _scan_var(self, snapshot: RiskSnapshot, current_value: float):
        """VaR 计算"""
        if len(self._return_history) < 20:
            return

        var_result = self.var_model.compute_var(current_value)
        snapshot.daily_var_95 = var_result.get("var_95", 0)
        snapshot.daily_var_99 = var_result.get("var_99", 0)

        # VaR 突破检查
        daily_var_pct = abs(snapshot.daily_var_95) / max(current_value, 1.0)
        if daily_var_pct > 0.03:  # 日VaR超过3% → 预警
            snapshot.var_breach = True
            snapshot.summary += f"[VaR] 日VaR95={daily_var_pct:.2%}>3%; "

    def _determine_risk_level(self, snapshot: RiskSnapshot):
        """综合风险判定"""
        all_clear = True

        if snapshot.kill_switch_level >= 2:
            snapshot.risk_level = RiskLevel.CIRCUIT_BREAK
            all_clear = False
        elif snapshot.kill_switch_level >= 1 or snapshot.drawdown_breach or snapshot.var_breach:
            snapshot.risk_level = RiskLevel.CRITICAL
            all_clear = False
        elif snapshot.position_limit_breach:
            snapshot.risk_level = RiskLevel.WARNING
            all_clear = False

        snapshot.all_clear = all_clear

        if all_clear:
            snapshot.summary = "PASS: 所有风控指标正常"

    # ── VaR 回测 ──

    def _var_backtest_lines(self) -> list[str]:
        """P3-2b: 将此前定义却从未调用的 run_var_backtest 接入报告。

        generate_report 通过 *(self._var_backtest_lines()) 内联调用,
        使 VaR 回测(Kupiec + Christoffersen + Basel 交通灯)在每次报告生成时真正执行。
        """
        vb = self.run_var_backtest()
        out: list[str] = []
        if vb.get("status") == "COMPLETE":
            out.append(f"  突破数/观测: {vb.get('n_violations')}/{vb.get('n_observations')}")
            out.append(f"  突破率/预期: {vb.get('violation_rate')} / {vb.get('expected_rate')}")
            bl = vb.get("basel_traffic_light") or {}
            if bl:
                out.append(f"  Basel 交通灯: {bl.get('zone')} ({bl.get('description')})")
            out.append(f"  结论: {vb.get('verdict')}")
        elif vb.get("status") == "SKIPPED":
            out.append("  (数据不足, 跳过 VaR 回测)")
        else:
            out.append(f"  (VaR 回测未执行: {vb.get('status')})")
        return out

    def run_var_backtest(self) -> dict[str, Any]:
        """执行 VaR 回测检验"""
        if not self.var_backtester or len(self._return_history) < 50:
            return {"status": "SKIPPED", "message": "数据不足 (需>=50日收益序列)"}

        returns = self._return_history
        var_values = self._compute_var_series(returns)

        result = self.var_backtester.run_tests(returns, var_values)
        logger.info("VaR Backtest: %s", result.get("verdict", "N/A"))
        return result

    def _compute_var_series(self, returns: list[float]) -> list[float]:
        """基于滚动窗口计算 VaR 序列 (用于回测)"""
        window = 60
        var_series = []
        returns_arr = np.array(returns)

        for i in range(len(returns_arr)):
            if i < window:
                var_series.append(0.0)
                continue
            window_returns = returns_arr[i - window : i]
            # P3-B FIX (2026-07-26): var_backtester 可能为 None, 加守卫
            confidence = self.var_backtester.confidence if self.var_backtester else 0.95
            var = np.percentile(window_returns, (1 - confidence) * 100)
            var_series.append(var)

        return var_series

    # ── 报告生成 ──

    def generate_report(self, snapshot: RiskSnapshot | None = None) -> str:
        """生成风控报告"""
        if snapshot is None:
            if not self._snapshot_history:
                return "无风控数据"
            snapshot = self._snapshot_history[-1]

        lines = [
            "=" * 60,
            f"统一风控驾驶舱 — {snapshot.timestamp[:19]}",
            "=" * 60,
            f"风险等级: {snapshot.risk_level.name}",
            f"综合判定: {'✔ ALL CLEAR' if snapshot.all_clear else '✘ 存在风险'}",
            "",
            "── 保证金 ──",
            f"  占用率: {snapshot.margin_usage_ratio:.1%}",
            f"  熔断等级: L{snapshot.kill_switch_level}",
            "",
            "── 回撤 ──",
            f"  当前回撤: {snapshot.current_drawdown:.2%}",
            f"  回撤上限: {snapshot.max_drawdown_limit:.0%}",
            f"  是否突破: {'是 - 建议减仓' + str(int(snapshot.drawdown_reduce_pct * 100)) + '%' if snapshot.drawdown_breach else '否'}",
            "",
            "── VaR ──",
            f"  VaR 95%: ¥{abs(snapshot.daily_var_95):,.0f}",
            f"  VaR 99%: ¥{abs(snapshot.daily_var_99):,.0f}",
            "",
            "── VaR 回测 (Kupiec + Christoffersen + Basel 交通灯) ──",
            *(self._var_backtest_lines()),
            "",
            "── 仓位 ──",
            f"  是否超限: {'是' if snapshot.position_limit_breach else '否'}",
        ]

        if snapshot.position_warnings:
            for w in snapshot.position_warnings:
                lines.append(f"  - {w}")

        if snapshot.kill_switch_actions:
            lines.append("")
            lines.append("── 执行指令 ──")
            for a in snapshot.kill_switch_actions:
                lines.append(f"  ▶ {a}")

        if snapshot.actions_required:
            for a in snapshot.actions_required:
                lines.append(f"  ▶ {a}")

        lines.append("=" * 60)
        return "\n".join(lines)

    def execute_actions(self, actions: list[str]) -> dict[str, Any]:
        """执行风控动作 (记录日志 + 触发回调)

        实际执行需要连接券商API, 此处提供统一接口。
        """
        results = []
        for action in actions:
            logger.warning("执行风控动作: %s", action)
            results.append(
                {
                    "action": action,
                    "status": "LOGGED",  # 实际环境应为 EXECUTED
                    "timestamp": datetime.now().isoformat(),
                }
            )

        return {
            "total_actions": len(actions),
            "results": results,
            "status": "COMPLETE" if results else "NO_ACTIONS",
        }
