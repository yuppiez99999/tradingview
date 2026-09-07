"""W7.3.3 G11 CVaR (Conditional Value at Risk) 风险计量模块.

责任层: L5 风控
依赖: utils.fineng.tail_risk_evt / utils.risk.risk_bus / utils.risk.risk_event /
      utils.risk.risk_audit_logger / utils.infra.feature_flags
设计原则:
    - 复用优先: EVT 调用既有 fit_evt()/evt_var_es(), 事件复用 VAR_BREACH, 审计复用 RiskAuditLogger
    - fail-closed: 所有失败路径返回 NaN + 告警, 不抛异常阻断主链路
    - Feature Flag: USE_CVAR_RISK_METRIC 默认 False, 关闭时仅计算不发布事件
    - 零回归: 不修改既有六件套接口签名
    - 不新增第三方依赖: 纯 Python 实现 (math/logging/dataclasses/datetime/typing)
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from utils.fineng.tail_risk_evt import evt_var_es, fit_evt
from utils.infra.feature_flags import is_enabled
from utils.risk.risk_audit_logger import RiskAuditLogger
from utils.risk.risk_bus import RiskBus, get_bus
from utils.risk.risk_event import RiskEvent, RiskEventType, RiskSeverity

logger = logging.getLogger("CVaR")

_NAN = float("nan")
_VALID_METHODS = {"historical", "parametric", "evt", "monte_carlo"}
_VALID_DISTRIBUTIONS = {"normal", "student_t"}
_VALID_FALLBACK_METHODS = {"historical", "parametric", "evt"}


def _utcnow_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _erfinv(y: float) -> float:
    """逆误差函数 (牛顿迭代法近似)."""
    if abs(y) >= 1.0:
        return float("inf") if y >= 1.0 else float("-inf")
    x = 0.0
    for _ in range(50):
        erf_x = math.erf(x)
        deriv = 2.0 / math.sqrt(math.pi) * math.exp(-x * x)
        if abs(deriv) < 1e-15:
            break
        delta = (erf_x - y) / deriv
        x -= delta
        if abs(delta) < 1e-12:
            break
    return x


def _norm_ppf(p: float) -> float:
    """标准正态分位数函数."""
    if p <= 0.0:
        return float("-inf")
    if p >= 1.0:
        return float("inf")
    return math.sqrt(2.0) * _erfinv(2.0 * p - 1.0)


def _student_t_ppf(p: float, dof: int) -> float:
    """Student-t 分位数 (Cornish-Fisher 近似)."""
    z = _norm_ppf(p)

    cf = (
        z
        + (z**3 - z) / (4.0 * dof)
        + (5.0 * z**5 - 16.0 * z**3 + 3.0 * z) / (96.0 * dof * dof)
    )
    return cf


@dataclass(frozen=True)
class CVaRConfig:
    """CVaR 计算配置 (不可变)."""

    method: str = "historical"
    distribution: str = "normal"
    dof: int = 5
    confidence_level: float = 0.95
    threshold_percentile: float = 0.95
    min_history: int = 30
    var_95_limit_pct: float = -0.04
    var_99_limit_pct: float = -0.06
    enable_var_comparison: bool = True
    fallback_chain: tuple[str, ...] = ("evt", "historical")
    feature_flag_name: str = "USE_CVAR_RISK_METRIC"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CVaRConfig:
        if not data:
            return cls()
        kwargs: dict[str, Any] = {}
        method = str(data.get("method", "historical"))
        kwargs["method"] = method if method in _VALID_METHODS else "historical"
        if method not in _VALID_METHODS:
            logger.warning("[CVaR] 非法 method=%s, 使用默认 historical", method)

        dist = str(data.get("distribution", "normal"))
        kwargs["distribution"] = dist if dist in _VALID_DISTRIBUTIONS else "normal"
        if dist not in _VALID_DISTRIBUTIONS:
            logger.warning("[CVaR] 非法 distribution=%s, 使用默认 normal", dist)

        dof = data.get("dof", 5)
        kwargs["dof"] = (
            int(dof) if isinstance(dof, (int, float)) and int(dof) >= 2 else 5
        )
        if not (isinstance(dof, (int, float)) and int(dof) >= 2):
            logger.warning("[CVaR] 非法 dof=%s, 使用默认 5", dof)

        cl = data.get("confidence_level", 0.95)
        kwargs["confidence_level"] = (
            float(cl) if isinstance(cl, (int, float)) and 0.90 <= cl <= 0.999 else 0.95
        )
        if not (isinstance(cl, (int, float)) and 0.90 <= cl <= 0.999):
            logger.warning("[CVaR] 非法 confidence_level=%s, 使用默认 0.95", cl)

        tp = data.get("threshold_percentile", 0.95)
        kwargs["threshold_percentile"] = (
            float(tp) if isinstance(tp, (int, float)) and 0.90 <= tp <= 0.99 else 0.95
        )

        mh = data.get("min_history", 30)
        kwargs["min_history"] = (
            int(mh) if isinstance(mh, (int, float)) and int(mh) >= 10 else 30
        )

        v95 = data.get("var_95_limit_pct", -0.04)
        kwargs["var_95_limit_pct"] = (
            float(v95) if isinstance(v95, (int, float)) and v95 < 0 else -0.04
        )

        v99 = data.get("var_99_limit_pct", -0.06)
        kwargs["var_99_limit_pct"] = (
            float(v99) if isinstance(v99, (int, float)) and v99 < 0 else -0.06
        )

        kwargs["enable_var_comparison"] = bool(data.get("enable_var_comparison", True))

        fc = data.get("fallback_chain", ["evt", "historical"])
        if isinstance(fc, (list, tuple)) and len(fc) > 0:
            valid_fc = tuple(str(m) for m in fc if str(m) in _VALID_FALLBACK_METHODS)
            kwargs["fallback_chain"] = valid_fc if valid_fc else ("evt", "historical")
        else:
            kwargs["fallback_chain"] = ("evt", "historical")

        kwargs["feature_flag_name"] = str(
            data.get("feature_flag_name", "USE_CVAR_RISK_METRIC")
        )
        return cls(**kwargs)

    @classmethod
    def from_system_config(cls) -> CVaRConfig:
        try:
            import json
            from pathlib import Path

            # 2026-09-07: 单一事实源 = 根 system_config.json
            # (原 config/system_config.json 已合并至根文件并删除, 勿再指向 config/ 子目录)
            cfg_path = Path(__file__).resolve().parents[2] / "system_config.json"
            if not cfg_path.exists():
                logger.warning("[CVaR] system_config.json 不存在, 使用全部默认值")
                return cls()
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cvar_cfg = cfg.get("risk_management", {}).get("cvar", {})
            risk_metric = cvar_cfg.get("risk_metric", {})
            if not risk_metric:
                logger.warning(
                    "[CVaR] risk_management.cvar.risk_metric 子段缺失, 使用全部默认值"
                )
                return cls()
            return cls.from_dict(risk_metric)
        except (OSError, ValueError, TypeError, KeyError) as e:
            logger.warning("[CVaR] 读取 system_config 失败: %s, 使用全部默认值", e)
            return cls()


@dataclass(frozen=True)
class CVaRResult:
    """CVaR 计算结果 (不可变)."""

    cvar_pct: float
    cvar_amount: float | None
    method: str
    method_used: str
    confidence: float
    breach: bool
    breach_level: str | None
    threshold: float | None
    warning: str
    evt_converged: bool
    var_comparison: dict[str, float] | None
    timestamp: str
    elapsed_ms: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CVaRCalculator:
    """CVaR 风险计量计算器.

    支持三种计算方式: historical / parametric / evt
    接入风控六件套: 超限发布 VAR_BREACH 事件 + 审计日志
    """

    def __init__(
        self,
        config: CVaRConfig | None = None,
        bus: RiskBus | None = None,
        audit_logger: RiskAuditLogger | None = None,
    ) -> None:
        self._config = config or CVaRConfig.from_system_config()
        self._bus = bus or get_bus()
        self._audit_logger = audit_logger or RiskAuditLogger()

    @property
    def config(self) -> CVaRConfig:
        return self._config

    def _validate_returns(
        self, returns: list[float], min_history: int
    ) -> tuple[bool, str]:
        if not returns:
            return (False, "输入序列为空")
        n = len(returns)
        if n < min_history:
            return (False, f"样本量不足: N={n} < {min_history}")
        for i, x in enumerate(returns):
            if x is None:
                return (False, f"输入序列含非法值: 位置 {i} (None)")
            try:
                if math.isnan(x):
                    return (False, f"输入序列含非法值: 位置 {i} (NaN)")
                if math.isinf(x):
                    return (False, f"输入序列含非法值: 位置 {i} (inf)")
            except (TypeError, ValueError):
                return (False, f"输入序列含非法值: 位置 {i} (非数值)")
        return (True, "")

    def _calculate_historical(
        self, returns: list[float], confidence: float
    ) -> tuple[float, str, bool]:
        try:
            n = len(returns)
            sorted_returns = sorted(returns)
            k = max(1, int(n * (1.0 - confidence)))
            cvar = sum(sorted_returns[:k]) / k
            return (cvar, "", False)
        except (ZeroDivisionError, OverflowError, TypeError, ValueError) as e:
            return (_NAN, f"historical 计算异常: {e}", False)

    def _calculate_parametric(
        self, returns: list[float], confidence: float, distribution: str, dof: int
    ) -> tuple[float, str, bool]:
        try:
            n = len(returns)
            mean = sum(returns) / n
            var = sum((x - mean) ** 2 for x in returns) / max(n - 1, 1)
            sigma = math.sqrt(var) if var > 0 else 0.0
            alpha = confidence

            if distribution == "normal":
                z_alpha = _norm_ppf(alpha)
                phi_z = math.exp(-z_alpha * z_alpha / 2.0) / math.sqrt(2.0 * math.pi)
                cvar = -sigma * phi_z / (1.0 - alpha)
                return (cvar, "", False)
            if distribution == "student_t":
                if dof < 2:
                    return (_NAN, f"Student-t 自由度不足: dof={dof} < 2", False)
                t_alpha = _student_t_ppf(alpha, dof)
                factor = t_alpha * (dof + t_alpha * t_alpha) / (dof - 1.0)
                cvar = -sigma * factor / (1.0 - alpha)
                return (cvar, "", False)
            return (_NAN, f"未知分布: {distribution}", False)
        except (ZeroDivisionError, OverflowError, TypeError, ValueError) as e:
            return (_NAN, f"parametric 计算异常: {e}", False)

    def _calculate_evt(
        self, returns: list[float], confidence: float, threshold_percentile: float
    ) -> tuple[float, str, bool, dict]:
        evt_info: dict[str, Any] = {}
        try:
            result = fit_evt(
                returns, threshold_percentile=threshold_percentile, min_history=120
            )
            evt_info = {
                "xi": result.xi,
                "n_excess": result.n_excess,
                "threshold_u": result.threshold_u,
                "error_message": result.error_message,
            }
            warnings = []
            if result.warning:
                warnings.append(result.warning)

            if not result.converged:
                return (_NAN, f"EVT 不收敛: {result.error_message}", False, evt_info)

            if abs(confidence - 0.975) < 1e-6:
                cvar = result.es_975
            elif abs(confidence - 0.99) < 1e-6:
                cvar = result.es_99
            else:
                evt_dict = evt_var_es(
                    returns,
                    confidence=confidence,
                    threshold_percentile=threshold_percentile,
                )
                cvar = evt_dict.get("es", _NAN)

            if result.xi > 0.5:
                warnings.append("ξ>0.5 估计极不稳定")

            return (cvar, "; ".join(warnings) if warnings else "", True, evt_info)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            return (_NAN, f"EVT 模块调用异常: {e}", False, evt_info)

    def _apply_fallback_chain(
        self,
        returns: list[float],
        confidence: float,
        failed_method: str,
        warning: str,
        **kwargs: Any,
    ) -> tuple[float, str, str, bool, dict]:
        chain = list(self._config.fallback_chain)
        if failed_method == "monte_carlo":
            candidates = ["parametric"] + [
                m for m in chain if m != failed_method and m != "parametric"
            ]
        else:
            candidates = [m for m in chain if m != failed_method]

        for method in candidates:
            if method == "historical":
                cvar, w, _ = self._calculate_historical(returns, confidence)
                converged = False
                evt_info: dict[str, Any] = {}
            elif method == "parametric":
                cvar, w, _ = self._calculate_parametric(
                    returns, confidence, self._config.distribution, self._config.dof
                )
                converged = False
                evt_info = {}
            elif method == "evt":
                cvar, w, converged, evt_info = self._calculate_evt(
                    returns, confidence, self._config.threshold_percentile
                )
            else:
                continue

            if not math.isnan(cvar):
                return (
                    cvar,
                    warning + f"→降级至 {method}",
                    method + "_fallback",
                    converged,
                    evt_info,
                )

        return (_NAN, warning + "降级链耗尽", failed_method + "_fail_closed", False, {})

    def _calculate_var_comparison(
        self, returns: list[float], confidence: float, cvar_pct: float
    ) -> dict[str, float] | None:
        if not self._config.enable_var_comparison:
            return None
        try:
            n = len(returns)
            sorted_returns = sorted(returns)
            idx = int(n * confidence)
            idx = min(idx, n - 1)
            var_pct = sorted_returns[idx]
            if var_pct == 0.0 or math.isnan(cvar_pct):
                ratio = _NAN
            else:
                ratio = cvar_pct / var_pct
            result = {"var_pct": var_pct, "cvar_to_var_ratio": ratio}
            if cvar_pct > var_pct + 1e-10:
                logger.warning(
                    "[CVaR] CVaR/VaR 不变量违反: cvar=%s > var=%s", cvar_pct, var_pct
                )
            return result
        except (IndexError, TypeError, ValueError, ZeroDivisionError) as e:
            logger.warning("[CVaR] VaR 对照计算异常: %s", e)
            return None

    def _check_breach(
        self, cvar_pct: float, confidence: float
    ) -> tuple[bool, str | None, float | None]:
        if math.isnan(cvar_pct):
            return (False, None, None)
        if abs(confidence - 0.95) < 1e-6:
            threshold = self._config.var_95_limit_pct
            breach_level = "cvar_95"
        elif abs(confidence - 0.99) < 1e-6:
            threshold = self._config.var_99_limit_pct
            breach_level = "cvar_99"
        else:
            return (False, None, None)
        breach = cvar_pct < threshold
        return (breach, breach_level, threshold)

    def _publish_breach_event(
        self,
        cvar_pct: float,
        threshold: float,
        confidence: float,
        method: str,
        breach_level: str,
    ) -> None:
        try:
            flag_enabled = is_enabled(self._config.feature_flag_name)
        except (ValueError, TypeError, KeyError, OSError) as e:
            logger.warning("[CVaR] Feature Flag 查询异常: %s, 默认不发布", e)
            return

        if not flag_enabled:
            logger.debug("[CVaR] Feature Flag 关闭, 不发布事件")
            return

        event = RiskEvent(
            event_type=RiskEventType.VAR_BREACH,
            source="cvar_calculator",
            severity=RiskSeverity.WARN,
            payload={
                "var_type": breach_level,
                "breach_pct": cvar_pct,
                "cvar_value": cvar_pct,
                "threshold": threshold,
                "confidence": confidence,
                "method": method,
            },
        )
        try:
            self._bus.publish(event)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            logger.error("[CVaR] 总线发布异常 (fail-open): %s", e)

    def _write_audit_log(
        self,
        cvar_pct: float,
        threshold: float,
        confidence: float,
        method: str,
        breach_level: str,
    ) -> None:
        try:
            self._audit_logger.log(
                module="T14_AUDIT",
                action="BLOCK",
                severity="WARN",
                symbol="PORTFOLIO",
                reason=f"cvar_breach_{breach_level}",
                context={
                    "cvar_value": cvar_pct,
                    "threshold": threshold,
                    "confidence": confidence,
                    "method": method,
                    "sub_module": "cvar",
                },
            )
        except (ValueError, TypeError, KeyError, OSError) as e:
            logger.error("[CVaR] 审计日志写入异常 (不阻断): %s", e)

    def calculate(
        self,
        returns: list[float],
        portfolio_value: float | None = None,
        method: str | None = None,
        confidence: float | None = None,
        distribution: str | None = None,
        dof: int | None = None,
        threshold_percentile: float | None = None,
        _skip_breach: bool = False,
    ) -> CVaRResult:
        start = time.perf_counter()
        cfg = self._config
        eff_method = method or cfg.method
        eff_conf = confidence if confidence is not None else cfg.confidence_level
        eff_dist = distribution or cfg.distribution
        eff_dof = dof if dof is not None else cfg.dof
        eff_tp = (
            threshold_percentile
            if threshold_percentile is not None
            else cfg.threshold_percentile
        )

        def _fail_closed(warning: str) -> CVaRResult:
            elapsed = (time.perf_counter() - start) * 1000
            return CVaRResult(
                cvar_pct=_NAN,
                cvar_amount=None,
                method=eff_method,
                method_used=eff_method,
                confidence=eff_conf,
                breach=False,
                breach_level=None,
                threshold=None,
                warning=warning,
                evt_converged=False,
                var_comparison=None,
                timestamp=_utcnow_iso(),
                elapsed_ms=elapsed,
            )

        if not (0.90 <= eff_conf <= 0.999):
            return _fail_closed(f"置信水平非法: {eff_conf} 不在 [0.90, 0.999]")

        valid_ok, valid_msg = self._validate_returns(returns, cfg.min_history)
        if not valid_ok:
            return _fail_closed(valid_msg)

        cvar_pct = _NAN
        warning = ""
        method_used = eff_method
        evt_converged = False
        evt_info: dict[str, Any] = {}

        if eff_method == "historical":
            cvar_pct, warning, evt_converged = self._calculate_historical(
                returns, eff_conf
            )
        elif eff_method == "parametric":
            cvar_pct, warning, evt_converged = self._calculate_parametric(
                returns, eff_conf, eff_dist, eff_dof
            )
        elif eff_method == "evt":
            cvar_pct, warning, evt_converged, evt_info = self._calculate_evt(
                returns, eff_conf, eff_tp
            )
        elif eff_method == "monte_carlo":
            cvar_pct, warning, evt_converged = self._calculate_parametric(
                returns, eff_conf, eff_dist, eff_dof
            )
            if not math.isnan(cvar_pct):
                method_used = "parametric_fallback"
        else:
            return _fail_closed(f"未知计算方式: {eff_method}")

        if math.isnan(cvar_pct):
            cvar_pct, warning, method_used, evt_converged, evt_info = (
                self._apply_fallback_chain(
                    returns,
                    eff_conf,
                    eff_method,
                    warning,
                    distribution=eff_dist,
                    dof=eff_dof,
                    threshold_percentile=eff_tp,
                )
            )

        breach = False
        breach_level: str | None = None
        threshold: float | None = None
        if not _skip_breach and not math.isnan(cvar_pct):
            breach, breach_level, threshold = self._check_breach(cvar_pct, eff_conf)
            if breach and breach_level:
                self._publish_breach_event(
                    cvar_pct, threshold or 0.0, eff_conf, method_used, breach_level
                )
                self._write_audit_log(
                    cvar_pct, threshold or 0.0, eff_conf, method_used, breach_level
                )

        var_comparison = None
        if not math.isnan(cvar_pct):
            var_comparison = self._calculate_var_comparison(returns, eff_conf, cvar_pct)

        cvar_amount = None
        if portfolio_value is not None and not math.isnan(cvar_pct):
            cvar_amount = cvar_pct * portfolio_value
            if portfolio_value <= 0:
                warning = (warning + "; " if warning else "") + "portfolio_value <= 0"

        elapsed = (time.perf_counter() - start) * 1000
        return CVaRResult(
            cvar_pct=cvar_pct,
            cvar_amount=cvar_amount,
            method=eff_method,
            method_used=method_used,
            confidence=eff_conf,
            breach=breach,
            breach_level=breach_level,
            threshold=threshold,
            warning=warning,
            evt_converged=evt_converged,
            var_comparison=var_comparison,
            timestamp=_utcnow_iso(),
            elapsed_ms=elapsed,
        )

    def calculate_scalar(
        self,
        returns: list[float],
        portfolio_value: float | None = None,
        method: str | None = None,
        confidence: float | None = None,
        return_amount: bool = False,
    ) -> float:
        result = self.calculate(
            returns,
            portfolio_value=portfolio_value,
            method=method,
            confidence=confidence,
            _skip_breach=True,
        )
        if return_amount:
            return result.cvar_amount if result.cvar_amount is not None else _NAN
        return result.cvar_pct

    def calculate_portfolio(
        self,
        positions: list[dict[str, Any]],
        returns_matrix: dict[str, list[float]],
        portfolio_value: float,
        method: str | None = None,
        confidence: float | None = None,
    ) -> CVaRResult:
        warnings: list[str] = []
        for pos in positions:
            code = pos.get("code", "")
            if code not in returns_matrix:
                warnings.append(f"持仓 {code} 缺少收益率序列")

        if warnings:
            start = time.perf_counter()
            elapsed = (time.perf_counter() - start) * 1000
            return CVaRResult(
                cvar_pct=_NAN,
                cvar_amount=None,
                method=method or self._config.method,
                method_used=method or self._config.method,
                confidence=confidence or self._config.confidence_level,
                breach=False,
                breach_level=None,
                threshold=None,
                warning="; ".join(warnings),
                evt_converged=False,
                var_comparison=None,
                timestamp=_utcnow_iso(),
                elapsed_ms=elapsed,
            )

        lengths = [len(returns_matrix[pos["code"]]) for pos in positions]
        min_len = min(lengths) if lengths else 0
        if len(set(lengths)) > 1:
            warnings.append(f"序列长度不一致, 以最短 {min_len} 对齐")

        weight_sum = sum(pos.get("weight", 0.0) for pos in positions)
        if abs(weight_sum - 1.0) > 0.05:
            warnings.append(f"权重和偏离 1.0: {weight_sum}")

        portfolio_returns: list[float] = []
        for i in range(min_len):
            r = sum(
                pos.get("weight", 0.0) * returns_matrix[pos["code"]][i]
                for pos in positions
            )
            portfolio_returns.append(r)

        result = self.calculate(
            portfolio_returns,
            portfolio_value=portfolio_value,
            method=method,
            confidence=confidence,
        )
        if warnings and result.warning:
            combined = result.warning + "; " + "; ".join(warnings)
            return CVaRResult(
                cvar_pct=result.cvar_pct,
                cvar_amount=result.cvar_amount,
                method=result.method,
                method_used=result.method_used,
                confidence=result.confidence,
                breach=result.breach,
                breach_level=result.breach_level,
                threshold=result.threshold,
                warning=combined,
                evt_converged=result.evt_converged,
                var_comparison=result.var_comparison,
                timestamp=result.timestamp,
                elapsed_ms=result.elapsed_ms,
            )
        if warnings:
            return CVaRResult(
                cvar_pct=result.cvar_pct,
                cvar_amount=result.cvar_amount,
                method=result.method,
                method_used=result.method_used,
                confidence=result.confidence,
                breach=result.breach,
                breach_level=result.breach_level,
                threshold=result.threshold,
                warning="; ".join(warnings),
                evt_converged=result.evt_converged,
                var_comparison=result.var_comparison,
                timestamp=result.timestamp,
                elapsed_ms=result.elapsed_ms,
            )
        return result
