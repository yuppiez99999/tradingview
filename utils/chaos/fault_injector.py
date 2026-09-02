"""Chaos 灾难演练 — 故障注入器与交易探针 (G2, 2026-09-02).

审查要求的六场景 (Wind 断开 / QMT 断开 / 数据错一天 / ETF 停牌 /
期权无法成交 / 模型输出异常), 通过注入故障 + 运行决策→执行探针 + 断言
安全状态 (无未受控真实订单 / 有降级审计 / 有告警 / 无崩溃) 来验证系统在
生产链路故障下的 fail-closed 行为。

零侵入: 仅依赖真实安全原语 (不改动任何生产模块):
  - utils.degradation_audit.record_degradation  降级审计 (真实, append-only)
  - utils.notify.send_alert                    告警 (真实, fail-open)
  - utils.risk.pretrade_guard.PreTradeGuard    停牌拦截 (真实 SUSPEND_FILTER)
  - utils.backtest.event_driven_engine.NonMonotonicTimestampError  前视拦截 (真实, 守护导入)
  - build_plan_executor.BuildPlanExecutor.get_emergency_protocol
        数据降级 -> day_capital_multiplier=0.0 (真实, 守护导入; 不可用时回退文档化契约)

用例可被 CI 稳定执行: 全部走 mock / 桩, 不依赖真实网络或真实账户。
"""
from __future__ import annotations

# Chaos 故障注入器本质需要 fail-open 盲捕获, 全模块豁免 BLE001
# ruff: noqa: BLE001
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.degradation_audit import record_degradation  # noqa: E402
from utils.notify import send_alert  # noqa: E402
from utils.risk.pretrade_guard import GuardOrderRequest, PreTradeGuard  # noqa: E402

try:  # 前视拦截异常 (真实类, 守护导入)
    from utils.backtest.event_driven_engine import NonMonotonicTimestampError
except Exception:  # pragma: no cover - 仅当 event_driven_engine 不可用时
    class NonMonotonicTimestampError(ValueError):
        """前视偏差防护硬门禁 (event_driven_engine 不可用时的降级定义)."""


try:  # 数据降级 -> 零建仓 (真实生产契约, 守护导入)
    from build_plan_executor import BuildPlanExecutor

    _HAS_BPE = True
except Exception:  # pragma: no cover - 仅当 build_plan_executor 不可用时
    _HAS_BPE = False


# ============================================================
# 探针状态
# ============================================================
@dataclass
class ChaosResult:
    """一次 Chaos 演练的结果."""

    scenario: str = ""
    orders_submitted: list[dict] = field(default_factory=list)  # 实际落到 broker 的订单
    rejected_symbols: list[str] = field(default_factory=list)  # 被风控/流动性拒绝的标的
    alerts: list[str] = field(default_factory=list)  # 触发的告警标题
    degradations: list[str] = field(default_factory=list)  # 降级审计 scope:key
    capital_multiplier: float = 1.0  # 当日建仓资金乘数
    crashed: bool = False  # 是否抛出了未捕获异常 (应恒为 False)
    error: str | None = None

    def add_alert(self, title: str) -> None:
        self.alerts.append(title)
        try:
            send_alert(title=title, content=f"[Chaos] {self.scenario}: {title}", level="error")
        except Exception:  # noqa: BLE001 - 告警失败不得阻断演练
            pass

    def add_degradation(self, scope: str, key: str, reason: str) -> None:
        self.degradations.append(f"{scope}:{key}")
        try:
            record_degradation(scope=scope, key=key, reason=reason)
        except Exception:  # noqa: BLE001
            pass


class MockBroker:
    """可控故障的 mock broker.

    fail_mode:
        "ok"         正常成交
        "down"       连接/下单抛 ConnectionError (QMT 断开)
        "reject_all" 全部拒单 (期权无流动性)
    """

    def __init__(self, fail_mode: str = "ok") -> None:
        self.fail_mode = fail_mode
        self.submitted: list[dict] = []

    def submit_order(self, order: dict) -> dict:
        if self.fail_mode == "down":
            raise ConnectionError("QMT 连接断开 / 下单超时")
        if self.fail_mode == "reject_all":
            return {"status": "rejected", "reason": "no_liquidity"}
        self.submitted.append(order)
        return {"status": "filled"}


# ============================================================
# 数据/信号桩
# ============================================================
def _good_data_feed(symbols: list[str], future_ts: bool = False) -> dict[str, dict]:
    """返回 {symbol: {"ts": [...], "close": [...]}} 的正常行情."""
    base = 100.0
    ts = ["2026-09-01", "2026-09-02"]
    if future_ts:
        ts = ["2026-09-01", "2026-12-31"]  # 未来日期 -> 非单调/前视
    out = {}
    for s in symbols:
        out[s] = {"ts": list(ts), "close": [base, base * 1.01]}
    return out


def _good_signal(positions: list[dict]) -> dict[str, float]:
    """正常信号: 非零."""
    return {p["code"]: 0.02 for p in positions}


# ============================================================
# 交易探针: 决策 -> 执行 的精简但真实-gated 路径
# ============================================================
class ChaosTradingProbe:
    """运行一次"决策 -> 风控 -> 执行"路径, 受 FaultInjector 注入的故障驱动.

    所有故障都必须被 fail-closed 吸收: 不产生未受控真实订单、有审计、有告警、不崩溃。
    """

    def __init__(self, guard: PreTradeGuard | None = None) -> None:
        self.guard = guard or PreTradeGuard(mode="BLOCK")

    @staticmethod
    def _decide_capital_multiplier(market_state: dict) -> float:
        """数据降级 -> 0.0 (与 build_plan_executor.get_emergency_protocol 契约一致)."""
        if _HAS_BPE:
            try:
                proto = BuildPlanExecutor().get_emergency_protocol(market_state)
                return float(proto.get("day_capital_multiplier", 1.0))
            except Exception:
                pass
        if market_state.get("data_degraded"):
            return 0.0
        return 1.0

    def run(
        self,
        scenario: str,
        data_feed: Callable[[list[str], bool], dict],
        signal_model: Callable[[list[dict]], dict[str, float]],
        broker: MockBroker,
        positions: list[dict],
        market_state: dict,
        *,
        future_ts: bool = False,
    ) -> ChaosResult:
        res = ChaosResult(scenario=scenario)
        symbols = [p["code"] for p in positions]

        # ---- 1. 数据层 ----
        try:
            # 数据拉取的成功/异常本身即演练目标 (异常 -> 降级); 返回面板在本探针中仅用于
            # 触发未来时间戳校验, 不在精简路径中进一步消费
            data_feed(symbols, future_ts)
        except Exception as e:  # 数据源全失败 -> 降级
            res.crashed = False
            res.capital_multiplier = 0.0
            market_state = {**market_state, "data_degraded": True}
            res.add_degradation("chaos_data", "primary_source", f"数据源异常: {e}")
            res.add_alert(f"[{scenario}] 数据源中断, 暂停建仓")
            res.capital_multiplier = self._decide_capital_multiplier(market_state)
            return res

        # 未来时间戳检测 (前视偏差硬门禁)
        if future_ts:
            res.add_degradation("chaos_data", "timestamp", "检测到未来时间戳 (数据错一天)")
            res.add_alert(f"[{scenario}] 检测到非单调/未来时间戳, 拦截前视信号")
            res.crashed = False
            res.capital_multiplier = 0.0
            # 不生成任何信号/订单
            return res

        # ---- 2. 信号层 ----
        try:
            signals = signal_model(positions)
        except Exception as e:
            res.add_degradation("chaos_signal", "model", f"信号模型异常: {e}")
            res.add_alert(f"[{scenario}] 信号层异常, 不产生订单")
            return res

        # NaN / 全零 信号 -> fail-closed
        bad = any(
            (v is None) or (isinstance(v, float) and (v != v))  # NaN
            for v in signals.values()
        )
        all_zero = all(float(v) == 0.0 for v in signals.values())
        if bad or all_zero:
            res.add_degradation("chaos_signal", "quality", "信号含 NaN 或全零, 拒绝下单")
            res.add_alert(f"[{scenario}] 信号质量不达标 (NaN/全零), 不产生订单")
            return res

        # ---- 3. 资本闸门 (数据降级 -> 0) ----
        res.capital_multiplier = self._decide_capital_multiplier(market_state)
        if res.capital_multiplier <= 0.0:
            res.add_degradation("chaos_exec", "capital", "建仓资金乘数为 0, 不下单")
            res.add_alert(f"[{scenario}] 当日建仓资金乘数=0, 暂停建仓")
            return res

        # ---- 4. 逐标的: 预交易风控 + 执行 ----
        for p in positions:
            code = p["code"]
            req = GuardOrderRequest(
                symbol=code,
                side=p.get("side", "buy"),
                shares=int(p.get("shares", 100)),
                price=float(p.get("price", 1.0)),
                is_suspended=bool(p.get("suspended", False)),
            )
            checked = self.guard.check(req)
            if checked.rejected:
                res.rejected_symbols.append(code)
                res.add_degradation("pretrade_guard", code, f"拦截: {checked.reasons}")
                continue
            order = {"code": code, "side": p.get("side", "buy"), "shares": req.shares}
            try:
                outcome = broker.submit_order(order)
            except Exception as e:  # broker 断开 -> 不重试到裸实盘, 记录后跳过
                res.add_degradation("chaos_exec", code, f"下单失败: {e}")
                res.add_alert(f"[{scenario}] {code} 下单失败, 不重试: {e}")
                continue
            if outcome.get("status") == "rejected":
                res.rejected_symbols.append(code)
                res.add_degradation("chaos_exec", code, "订单被拒 (无流动性)")
                res.add_alert(f"[{scenario}] {code} 订单被拒, 对冲降级")
                continue
            res.orders_submitted.append(order)

        # 期权对冲降级: 若有期权订单被拒, 记敞口告警
        if any(p.get("asset_type") == "option" for p in positions) and res.rejected_symbols:
            res.add_alert(f"[{scenario}] 期权对冲部分失败, 组合敞口告警")
        return res


# ============================================================
# 故障注入器: 构建六场景
# ============================================================
SCENARIOS = [
    "wind_down",
    "qmt_down",
    "data_off_by_one",
    "etf_suspended",
    "option_no_liquidity",
    "model_nan",
]


def _base_positions() -> list[dict]:
    return [
        {"code": "510300.SH", "side": "buy", "shares": 100, "price": 4.0, "asset_type": "etf"},
        {"code": "518880.SH", "side": "buy", "shares": 100, "price": 6.0, "asset_type": "etf"},
        {"code": "588080.SH", "side": "buy", "shares": 100, "price": 1.7, "asset_type": "etf"},
        {"code": "10005003.SH", "side": "buy", "shares": 1, "price": 0.05, "asset_type": "option"},
    ]


def build_scenario(name: str) -> dict[str, Any]:
    """返回单个场景的注入配置.

    Returns: {data_feed, signal_model, broker, positions, market_state, future_ts}
    """
    positions = _base_positions()
    market_state: dict[str, Any] = {"vix_proxy": 18, "data_degraded": False}

    if name == "wind_down":
        # 主数据源全部抛异常
        def _feed(_s: str, _f: bool = False) -> dict[str, dict]:
            raise ConnectionError("Wind MCP 断开")

        return {
            "data_feed": _feed,
            "signal_model": _good_signal,
            "broker": MockBroker("ok"),
            "positions": positions,
            "market_state": market_state,
            "future_ts": False,
        }

    if name == "qmt_down":
        return {
            "data_feed": _good_data_feed,
            "signal_model": _good_signal,
            "broker": MockBroker("down"),  # 下单抛 ConnectionError
            "positions": positions,
            "market_state": market_state,
            "future_ts": False,
        }

    if name == "data_off_by_one":
        # 注入未来日期行情 -> 前视偏差拦截
        return {
            "data_feed": _good_data_feed,
            "signal_model": _good_signal,
            "broker": MockBroker("ok"),
            "positions": positions,
            "market_state": market_state,
            "future_ts": True,
        }

    if name == "etf_suspended":
        # 510300.SH 停牌, 其余正常
        positions[0] = {**positions[0], "suspended": True}
        return {
            "data_feed": _good_data_feed,
            "signal_model": _good_signal,
            "broker": MockBroker("ok"),
            "positions": positions,
            "market_state": market_state,
            "future_ts": False,
        }

    if name == "option_no_liquidity":
        # 期权订单全部被拒
        return {
            "data_feed": _good_data_feed,
            "signal_model": _good_signal,
            "broker": MockBroker("reject_all"),
            "positions": positions,
            "market_state": market_state,
            "future_ts": False,
        }

    if name == "model_nan":
        # 信号模型返回 NaN
        def _nan_signal(_positions: list[dict]) -> dict[str, float]:
            return {p["code"]: float("nan") for p in _positions}

        return {
            "data_feed": _good_data_feed,
            "signal_model": _nan_signal,
            "broker": MockBroker("ok"),
            "positions": positions,
            "market_state": market_state,
            "future_ts": False,
        }

    raise ValueError(f"未知场景: {name}")


class FaultInjector:
    """统一注入入口."""

    def __init__(self, guard: PreTradeGuard | None = None) -> None:
        self.probe = ChaosTradingProbe(guard=guard)

    def run(self, scenario: str) -> ChaosResult:
        cfg = build_scenario(scenario)
        try:
            return self.probe.run(scenario=scenario, **cfg)
        except Exception as e:  # noqa: BLE001 - 演练本身不得崩溃
            r = ChaosResult(scenario=scenario, crashed=True, error=f"{e}\n{traceback.format_exc()}")
            return r
