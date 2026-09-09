"""
ai_decision.health — 模型健康检查 + 熔断器
==========================================

任务: 阶段一 — 模型 liveness probe + 熔断降级
责任层: L1 决策前置 (orchestrator.run_decision 开头探测)

设计原则 (路线图 ai_decision_roadmap_execution_plan.md 步骤 3):
  - 复用 v8.3_institutional.src.ai.model_router.CircuitBreaker (DRY, 不重写)
  - 为 ai_decision 的每个 role (bull/bear/judge/...) 维护独立熔断器
  - 熔断开启时自动降级 MockProvider (保证全链路可跑)
  - liveness probe 受 probe_interval_seconds 控制, 避免每决策都探测 (成本)
  - 业务调用失败被动记录 record_failure, 成功记录 record_success (双向反馈)

熔断器参数 (沿用 CircuitBreaker 默认):
  - max_failures=3 (连续失败 3 次熔断)
  - cooldown_seconds=300 (5 分钟冷却后尝试恢复)

接口:
  - HealthStatus: 单次探测结果 dataclass
  - ModelHealthMonitor: 健康监控器
    - check(role, timeout) -> HealthStatus       主动探测
    - is_circuit_open(role) -> bool              熔断状态查询
    - record_failure(role) / record_success(role) 被动记录
    - get_provider_with_fallback(role) -> BaseProvider  熔断降级
    - get_stats() -> Dict                        供看板消费

接入点:
  - orchestrator.run_decision(health_monitor=...)  开头探测 + provider 获取
  - 步骤 4 dashboard.generate_daily_dashboard()    消费 get_stats()

用法:
    from ai_decision.health import ModelHealthMonitor

    mon = ModelHealthMonitor()
    status = mon.check("judge", timeout=2.0)
    if not status.healthy:
        logger.warning("judge 模型不健康: %s", status.error)

    # 业务调用时获取 provider (熔断自动降级 Mock)
    prov = mon.get_provider_with_fallback("judge")
    result = prov.generate(prompt)
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ai_decision.providers import BaseProvider, MockProvider, get_active_provider

logger = logging.getLogger("ai_decision.health")


# ============================================================
# 复用 v8.3_institutional.src.ai.model_router.CircuitBreaker (DRY)
# ============================================================
# 动态加载: v8.3_institutional 目录名含点号, 非合法 Python 包名, 用 importlib 加载
# 降级: 加载失败时内联完全兼容的实现 (接口与 model_router.CircuitBreaker 一致)

_V83_AI_DIR = (
    Path(__file__).resolve().parent.parent / "v8.3_institutional" / "src" / "ai"
)
_MR_PATH = _V83_AI_DIR / "model_router.py"


class CircuitBreakerProtocol(Protocol):
    """熔断器实例接口 (静态类型检查用).

    运行时实例可能是 v8.3 model_router.CircuitBreaker 或下方内联兼容版本,
    两者结构一致故可用 Protocol 描述。
    """

    provider: str
    max_failures: int
    cooldown_seconds: int
    consecutive_failures: int
    last_failure_time: float
    is_open: bool

    def __init__(
        self,
        provider: str = "",
        max_failures: int = 3,
        cooldown_seconds: int = 300,
    ) -> None: ...

    def record_failure(self) -> None: ...
    def record_success(self) -> None: ...
    def should_try_reset(self) -> bool: ...
    def try_reset(self) -> bool: ...


def _load_circuit_breaker() -> type[CircuitBreakerProtocol]:
    """从 v8.3_institutional 动态加载 CircuitBreaker, 失败则返回内联兼容版本"""
    try:
        if _MR_PATH.exists():
            spec = importlib.util.spec_from_file_location("_v83_model_router", _MR_PATH)
            if spec is not None and spec.loader is not None:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                cb_cls = getattr(module, "CircuitBreaker", None)
                if cb_cls is not None:
                    logger.debug("[Health] 复用 v8.3 model_router.CircuitBreaker")
                    return cb_cls
    except (
        ImportError,
        OSError,
        AttributeError,
        TypeError,
        ValueError,
        SyntaxError,
        RuntimeError,
    ) as exc:
        # importlib 动态加载可能抛: 模块导入失败/文件读取错误/属性缺失/
        # 类型不匹配/spec 解析错误/目标文件语法错误/运行时错误
        logger.debug(
            "[Health] 加载 model_router.CircuitBreaker 失败, 降级内联版本: %s", exc
        )

    # 降级: 内联兼容版本 (与 model_router.CircuitBreaker 接口完全一致)
    @dataclass
    class CircuitBreaker:
        """熔断器 (内联兼容版本, 接口与 v8.3 model_router.CircuitBreaker 一致)"""

        provider: str = ""
        max_failures: int = 3
        cooldown_seconds: int = 300
        consecutive_failures: int = 0
        last_failure_time: float = 0.0
        is_open: bool = False

        def record_failure(self) -> None:
            self.consecutive_failures += 1
            self.last_failure_time = time.time()
            if self.consecutive_failures >= self.max_failures:
                self.is_open = True

        def record_success(self) -> None:
            self.consecutive_failures = 0
            self.is_open = False

        def should_try_reset(self) -> bool:
            if not self.is_open:
                return False
            elapsed = time.time() - self.last_failure_time
            return elapsed >= self.cooldown_seconds

        def try_reset(self) -> bool:
            if self.should_try_reset():
                self.consecutive_failures = 0
                self.is_open = False
                return True
            return False

    logger.debug("[Health] 使用内联 CircuitBreaker 兼容版本")
    return CircuitBreaker


# 运行时变量: v8.3 类优先, 失败降级内联 (两者均满足 CircuitBreakerProtocol);
# 因是变量而非模块级类, 不能直接用于类型注解, 注解处一律用 CircuitBreakerProtocol
CircuitBreaker: type[CircuitBreakerProtocol] = _load_circuit_breaker()


# ============================================================
# 数据结构
# ============================================================


@dataclass
class HealthStatus:
    """单次 liveness probe 结果

    Attributes:
        role: 角色 (bull/bear/judge/signal/research/...)
        healthy: 是否健康 (probe 成功且 provider 返回非 None)
        latency_ms: 探测延迟 (ms)
        error: 错误信息 (healthy=False 时填充)
        last_check: 上次检查时间戳 (time.time())
        provider_name: 实际探测的 provider 名称
        circuit_open: 探测时熔断器是否已开启
    """

    role: str
    healthy: bool
    latency_ms: float
    error: str = ""
    last_check: float = 0.0
    provider_name: str = ""
    circuit_open: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "healthy": self.healthy,
            "latency_ms": round(self.latency_ms, 2),
            "error": self.error,
            "last_check": self.last_check,
            "provider_name": self.provider_name,
            "circuit_open": self.circuit_open,
        }


# ============================================================
# 健康监控器
# ============================================================


class ModelHealthMonitor:
    """模型健康监控 + 熔断降级

    为 ai_decision 的每个 role 维护独立熔断器, 熔断时自动降级 MockProvider.
    支持主动探测 (check) 和被动记录 (record_failure/success) 双向反馈.

    用法:
        mon = ModelHealthMonitor(max_failures=3, cooldown_seconds=300)

        # 主动探测 (受 probe_interval 控制, 避免频繁调用 API)
        status = mon.maybe_probe("judge", timeout=2.0)
        if status and not status.healthy:
            logger.warning("judge 不健康: %s", status.error)

        # 业务调用获取 provider (熔断自动降级 Mock)
        prov = mon.get_provider_with_fallback("judge")
        result = prov.generate(prompt)

        # 业务调用结果反馈 (失败累计触发熔断)
        if result is None:
            mon.record_failure("judge")
        else:
            mon.record_success("judge")
    """

    # 轻量探测 prompt (最小 token 消耗)
    _PROBE_PROMPT = "ping"
    _PROBE_SYSTEM = ""

    def __init__(
        self,
        max_failures: int = 3,
        cooldown_seconds: int = 300,
        probe_interval_seconds: float = 60.0,
    ) -> None:
        """
        Args:
            max_failures: 连续失败多少次触发熔断 (默认 3)
            cooldown_seconds: 熔断冷却秒数, 过后尝试恢复 (默认 300=5分钟)
            probe_interval_seconds: 主动探测最小间隔, 避免频繁调用 API (默认 60s)
        """
        self._max_failures = int(max_failures)
        self._cooldown_seconds = int(cooldown_seconds)
        self._probe_interval = float(probe_interval_seconds)
        self._breakers: dict[str, CircuitBreakerProtocol] = {}
        self._last_probe: dict[str, float] = {}  # role -> last probe timestamp
        self._last_status: dict[str, HealthStatus] = {}  # role -> 最近状态缓存
        # 并发保护: 多线程 (如 run_batch 并行) 下保护 _breakers 字典初始化竞态
        self._lock = threading.Lock()

    # ------------------------------------------------------------
    # 熔断器管理
    # ------------------------------------------------------------

    def _get_or_create_breaker(self, role: str) -> CircuitBreakerProtocol:
        """获取或创建 role 对应的熔断器 (线程安全)"""
        # 双重检查锁定: 避免多线程下创建多个 breaker 实例
        cb = self._breakers.get(role)
        if cb is not None:
            return cb
        with self._lock:
            cb = self._breakers.get(role)
            if cb is not None:
                return cb
            cb = CircuitBreaker(
                provider=role,
                max_failures=self._max_failures,
                cooldown_seconds=self._cooldown_seconds,
            )
            self._breakers[role] = cb
            return cb

    def is_circuit_open(self, role: str) -> bool:
        """检查 role 的熔断器是否开启 (含冷却恢复尝试)

        Args:
            role: 角色名 (bull/bear/judge/...)
        Returns:
            True 如果熔断开启且未过冷却期
        """
        cb = self._breakers.get(role)
        if cb is None:
            return False
        if cb.is_open:
            cb.try_reset()  # 尝试冷却恢复
            return cb.is_open
        return False

    def record_failure(self, role: str) -> None:
        """记录业务调用失败 (被动反馈, 连续达到 max_failures 触发熔断)

        Args:
            role: 角色名
        """
        cb = self._get_or_create_breaker(role)
        cb.record_failure()
        if cb.is_open and cb.consecutive_failures == self._max_failures:
            logger.warning(
                "[HealthMonitor] %s 熔断触发 (连续失败 %d 次, 冷却 %ds)",
                role,
                cb.consecutive_failures,
                self._cooldown_seconds,
            )

    def record_success(self, role: str) -> None:
        """记录业务调用成功 (被动反馈, 重置连续失败计数)

        Args:
            role: 角色名
        """
        cb = self._get_or_create_breaker(role)
        cb.record_success()

    # ------------------------------------------------------------
    # 主动探测
    # ------------------------------------------------------------

    def check(self, role: str, timeout: float = 2.0) -> HealthStatus:
        """单次 liveness probe: 调用 provider.generate() 一次轻量 prompt

        Args:
            role: 角色名
            timeout: 探测超时秒数 (默认 2s)
        Returns:
            HealthStatus 探测结果
        """
        # 熔断已开启时直接返回不健康 (不浪费 API 调用)
        if self.is_circuit_open(role):
            status = HealthStatus(
                role=role,
                healthy=False,
                latency_ms=0.0,
                error=f"circuit open (冷却中, {self._cooldown_seconds}s)",
                last_check=time.time(),
                provider_name="N/A",
                circuit_open=True,
            )
            self._last_status[role] = status
            self._last_probe[role] = status.last_check
            return status

        # 获取 provider 探测
        provider = get_active_provider(role)
        provider_name = getattr(provider, "model_name", provider.__class__.__name__)
        start = time.perf_counter()
        try:
            result = provider.generate(
                self._PROBE_PROMPT,
                system=self._PROBE_SYSTEM,
                timeout=int(timeout),
            )
            latency_ms = (time.perf_counter() - start) * 1000.0
            if result is None:
                # provider 返回 None 视为不健康
                self.record_failure(role)
                status = HealthStatus(
                    role=role,
                    healthy=False,
                    latency_ms=latency_ms,
                    error="provider returned None",
                    last_check=time.time(),
                    provider_name=provider_name,
                    circuit_open=self.is_circuit_open(role),
                )
            else:
                # 探测成功
                self.record_success(role)
                status = HealthStatus(
                    role=role,
                    healthy=True,
                    latency_ms=latency_ms,
                    error="",
                    last_check=time.time(),
                    provider_name=provider_name,
                    circuit_open=False,
                )
        except (
            RuntimeError,
            OSError,
            ConnectionError,
            TimeoutError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as exc:
            # provider.generate 可能抛: 网络/超时/JSON 解析/响应格式错误/
            # 字段缺失/类型不匹配/属性缺失/运行时错误 (各 Provider 内部已做降级,
            # 此处仅作兜底防御)
            latency_ms = (time.perf_counter() - start) * 1000.0
            self.record_failure(role)
            status = HealthStatus(
                role=role,
                healthy=False,
                latency_ms=latency_ms,
                error=f"probe exception: {exc}",
                last_check=time.time(),
                provider_name=provider_name,
                circuit_open=self.is_circuit_open(role),
            )
            logger.debug("[HealthMonitor] %s 探测异常: %s", role, exc)

        self._last_status[role] = status
        self._last_probe[role] = status.last_check
        return status

    def maybe_probe(self, role: str, timeout: float = 2.0) -> HealthStatus | None:
        """受 probe_interval 控制的探测 (避免频繁调用 API)

        如果距上次探测不足 probe_interval_seconds, 返回缓存的最近状态.
        否则执行新探测.

        Args:
            role: 角色名
            timeout: 探测超时秒数
        Returns:
            HealthStatus 或 None (从未探测过且熔断未开启时可能返回 None)
        """
        now = time.time()
        last = self._last_probe.get(role, 0.0)
        if now - last >= self._probe_interval:
            return self.check(role, timeout=timeout)
        # 返回缓存
        return self._last_status.get(role)

    # ------------------------------------------------------------
    # 熔断降级
    # ------------------------------------------------------------

    def get_provider_with_fallback(self, role: str) -> BaseProvider:
        """获取 provider, 熔断开启时降级 MockProvider

        Args:
            role: 角色名
        Returns:
            BaseProvider (真实 provider 或 MockProvider)
        """
        if self.is_circuit_open(role):
            logger.warning(
                "[HealthMonitor] %s 熔断中, 降级 MockProvider (连续失败 %d 次)",
                role,
                self._breakers[role].consecutive_failures,
            )
            return MockProvider(role=role)
        return get_active_provider(role)

    # ------------------------------------------------------------
    # 统计 (供步骤 4 看板消费)
    # ------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """获取所有 role 的熔断器状态 + 最近探测结果

        Returns:
            {
                "roles": {
                    "judge": {
                        "is_open": bool,
                        "consecutive_failures": int,
                        "max_failures": int,
                        "cooldown_seconds": int,
                        "last_failure_time": float,
                        "last_status": {...},  # 最近 HealthStatus
                    },
                    ...
                },
                "config": {
                    "max_failures": int,
                    "cooldown_seconds": int,
                    "probe_interval_seconds": float,
                }
            }

        注意: is_open 字段必须通过 is_circuit_open() 获取 (会尝试冷却恢复),
        而非直接读 cb.is_open, 否则会与 get_health_summary 状态不一致
        (一个已过冷却期的熔断器在 get_stats 显示 open=True, 在 get_health_summary 显示已恢复).
        """
        roles: dict[str, Any] = {}
        for role, cb in self._breakers.items():
            # 统一通过 is_circuit_open() 判断 (含冷却恢复尝试), 与 get_health_summary 一致
            is_open = self.is_circuit_open(role)
            roles[role] = {
                "is_open": is_open,
                "consecutive_failures": cb.consecutive_failures,
                "max_failures": cb.max_failures,
                "cooldown_seconds": cb.cooldown_seconds,
                "last_failure_time": cb.last_failure_time,
                "last_status": self._last_status.get(
                    role,
                    HealthStatus(
                        role=role,
                        healthy=not is_open,
                        latency_ms=0.0,
                    ),
                ).to_dict(),
            }
        return {
            "roles": roles,
            "config": {
                "max_failures": self._max_failures,
                "cooldown_seconds": self._cooldown_seconds,
                "probe_interval_seconds": self._probe_interval,
            },
        }

    def get_health_summary(self) -> dict[str, Any]:
        """获取健康摘要 (简版, 供快速判断)

        Returns:
            {
                "total_roles": int,
                "open_breakers": List[str],
                "healthy_roles": List[str],
                "unhealthy_roles": List[str],
            }
        """
        open_breakers = []
        healthy = []
        unhealthy = []
        for role in self._breakers:
            if self.is_circuit_open(role):
                open_breakers.append(role)
                unhealthy.append(role)
            else:
                status = self._last_status.get(role)
                if status is None:
                    healthy.append(role)  # 未探测视为健康
                elif status.healthy:
                    healthy.append(role)
                else:
                    unhealthy.append(role)
        return {
            "total_roles": len(self._breakers),
            "open_breakers": open_breakers,
            "healthy_roles": healthy,
            "unhealthy_roles": unhealthy,
        }


# ============================================================
# 模块级单例 (供 orchestrator 默认使用)
# ============================================================

_default_monitor: ModelHealthMonitor | None = None


def get_default_monitor() -> ModelHealthMonitor:
    """获取默认全局 ModelHealthMonitor 单例

    orchestrator.run_decision() 未传入 health_monitor 时使用此单例.
    """
    global _default_monitor
    if _default_monitor is None:
        _default_monitor = ModelHealthMonitor()
    return _default_monitor
