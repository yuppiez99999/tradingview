"""
止损止盈自动触发引擎 v1.0 — 步骤4

盘中监控持仓价格, 当触及止损/止盈线时自动触发卖出:
1. 加载波动率调整止损规则 (vol_adjusted 或 auto)
2. 获取实时价格 (Wind MCP / 通达信 / AKShare / 缓存)
3. 判断是否触发止损/止盈
4. 通过 BrokerAdapter 执行卖出
5. 移动止损 (trailing stop) 更新

使用方式:
    # 独立运行
    python stop_loss_monitor.py

    # 集成到自动执行系统
    from stop_loss_monitor import StopLossMonitor
    monitor = StopLossMonitor(broker=mock_broker)
    monitor.check_and_execute()
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any

import yaml

from utils.datetime_utils import now_bj

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("stop_loss_monitor")

# 添加路径
_BASE = os.path.dirname(os.path.abspath(__file__))
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
sys.path.insert(0, _BASE)  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402

setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils

# P0-C1: 原子写 JSON (回退到非原子写以保证模块独立可用)
try:
    from utils.concurrency import atomic_write_json as _atomic_write_json
except ImportError:

    def _atomic_write_json(path: str, data: dict) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


class TriggerType(Enum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    ATR_STOP = "atr_stop"


@dataclass
class TriggerRecord:
    """触发记录"""

    timestamp: str
    code: str
    name: str
    trigger_type: TriggerType
    entry_price: float
    current_price: float
    trigger_price: float
    shares: int
    action: str  # SELL / HOLD / ALERT
    pnl_pct: float
    executed: bool = False
    order_id: str = ""


class StopLossMonitor:
    """止损止盈自动触发引擎

    监控持仓标的的实时价格, 触发止损/止盈/移动止损
    """

    def __init__(
        self,
        rules_file: str | None = None,
        positions_file: str | None = None,
        broker: Any = None,
        water_mark_file: str | None = None,
    ):
        """
        Args:
            rules_file: 止损规则 YAML 文件路径
            positions_file: 持仓 JSON 文件路径
            broker: BrokerAdapter 实例 (MockBrokerAdapter 或 QMT)
            water_mark_file: 水位线状态文件路径 (None 用默认)
        """
        # 规则文件: 本项目 config/ (P3-3: 移除已删除的 11_量化策略 历史回退路径)
        if rules_file is None:
            candidates = [
                os.path.join(_BASE, "config", "stop_loss_vol_adjusted.yaml"),
                os.path.join(_BASE, "config", "stop_loss_rules_auto.yaml"),
            ]
            rules_file = next((p for p in candidates if os.path.exists(p)), None)

        self.rules_file = rules_file
        self.rules = self._load_rules(rules_file)

        # P1-2 修复: 加载 risk.yaml 的 default_stop_pct 作为兜底止损比例
        # 此前 risk.yaml 的 default_stop_pct=0.08 是死配置, 从未被任何代码引用
        self.default_stop_pct = self._load_default_stop_pct()

        # 持仓文件: 本项目 config/
        _pos_candidates = [
            os.path.join(_BASE, "config", "positions.json"),
        ]
        self.positions_file = positions_file or next(
            (p for p in _pos_candidates if os.path.exists(p)), _pos_candidates[0]
        )

        # Broker
        # P2-4 修复: 无真实 broker 时静默降级为 mock, "看似已执行"实则仅模拟。
        # 现在显式标记 EXECUTION_MODE=MOCK 并告警。
        self.execution_mode = "LIVE"
        self.broker = broker
        if self.broker is None:
            self.broker = self._create_mock_broker()
            self.execution_mode = "MOCK"
            logger.warning(
                "[WARN] 止损监控器使用 MockBroker (EXECUTION_MODE=MOCK), "
                "触发止损时仅模拟成交, 真实持仓不会下单。请注入真实 broker。"
            )

        # 最高价记录 (多头移动止损用)
        self._high_water_mark: dict[str, float] = {}
        # 最低价记录 (空头移动止损用, S2 修复)
        self._low_water_mark: dict[str, float] = {}

        # P1-2 修复 (2026-09-01): 水位线持久化 — 此前纯内存, 监控进程重启后
        # 盈利持仓的移动止损线大幅回落 (HWM 丢失 → trailing stop 从高点回落到成本价),
        # 锁盈保护失效。重启时从状态文件恢复。
        self._water_mark_file = water_mark_file or os.path.join(
            _BASE, "reports", "stop_loss_water_marks.json"
        )
        self._load_water_marks()

        # 触发历史
        self.trigger_history: list[TriggerRecord] = []

        logger.info(
            f"止损监控器初始化: {len(self.rules)} 条规则, broker={self.broker.__class__.__name__}"
        )

    def _load_water_marks(self) -> None:
        """从状态文件恢复移动止损水位线 (P1-2).

        容错: 文件不存在 (首次运行) 或损坏时静默使用空水位线, 不阻断启动。
        """
        path = getattr(self, "_water_mark_file", None)
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self._high_water_mark = {
                str(k): float(v) for k, v in (data.get("high_water_marks") or {}).items()
            }
            self._low_water_mark = {
                str(k): float(v) for k, v in (data.get("low_water_marks") or {}).items()
            }
            logger.info(
                "水位线状态已恢复: %d 多头 / %d 空头 (来源: %s)",
                len(self._high_water_mark),
                len(self._low_water_mark),
                path,
            )
        except (OSError, ValueError, TypeError):
            logger.exception("[StopLoss] 水位线状态文件加载失败, 使用空水位线: %s", path)
            self._high_water_mark = {}
            self._low_water_mark = {}

    def _save_water_marks(self) -> None:
        """水位线落盘 (原子写)。失败仅告警, 不影响监控主流程。"""
        path = getattr(self, "_water_mark_file", None)
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _atomic_write_json(
                path,
                {
                    "updated_at": now_bj().isoformat(),
                    "high_water_marks": self._high_water_mark,
                    "low_water_marks": self._low_water_mark,
                },
            )
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.exception("[StopLoss] 水位线状态保存失败: %s", path)

    @staticmethod
    def _sanitize_numpy_tags(text: str) -> str:
        """预处理 YAML 文本，移除 numpy 标签以确保 safe_load 可用。

        将 ``!!python/object/apply:numpy.<dtype> [value]`` 形式的标签
        替换为纯标量 ``value``，避免使用 ``yaml.unsafe_load`` 带来的
        远程代码执行风险。

        安全考量: 此方法仅做正则替换，不执行任何 YAML 标签对应的
        Python 对象实例化逻辑，因此即使 YAML 文件被篡改也无法触发
        任意代码执行。
        """
        import re

        # 匹配 !!python/object/apply:numpy.<dtype> [value] 或 !!python/object/apply:numpy.<dtype> value
        # 例: !!python/object/apply:numpy.float64 [1.23] → 1.23
        pattern = re.compile(
            r"!!python/object/apply:numpy\.\w+(?:\s*\[(.+?)\]|\s+(.+?))(\s|$)"
        )

        def _replace(match: re.Match) -> str:
            val = match.group(1) if match.group(1) is not None else match.group(2)
            return f"{val}{match.group(3)}"

        return pattern.sub(_replace, text)

    def _load_rules(self, rules_file: str) -> dict[str, dict]:
        """加载止损规则

        兼容两种 YAML 格式:
        - 纯文本 (safe_load): 标量已转换好的版本
        - numpy 标签: vol_adjusted_stop_loss.py 生成的版本, 含 !!python/object/apply:numpy.* 标签
          通过 _sanitize_numpy_tags 预处理后用 safe_load 加载 (不再使用 unsafe_load)

        安全: 全程使用 safe_load, 拒绝 unsafe_load 以消除 RCE 风险。
        """
        if not rules_file:
            # P1-2 降级闭环: 规则文件缺失 = 无任何止损规则, 监控形同虚设
            from utils.degradation_audit import record_degradation

            record_degradation(
                scope="stop_loss_monitor",
                key="(止损规则文件未指定)",
                default="无规则 (监控器不会触发任何止损)",
                reason="rules_file 未指定且候选路径均不存在",
            )
            logger.warning(
                "[风控配置降级] 未找到止损规则文件 — 监控器无规则可用, 不会触发任何止损; "
                "降级事件已记录 reports/degradation_log.jsonl"
            )
            return {}
        if not os.path.exists(rules_file):
            from utils.degradation_audit import record_degradation

            record_degradation(
                scope="stop_loss_monitor",
                key=rules_file,
                default="无规则 (监控器不会触发任何止损)",
                reason="止损规则文件不存在",
            )
            logger.warning(
                f"[风控配置降级] 止损规则文件不存在: {rules_file} — "
                "监控器无规则可用, 不会触发任何止损; 降级事件已记录 reports/degradation_log.jsonl"
            )
            return {}

        data = None
        with open(rules_file, encoding="utf-8") as f:
            raw_text = f.read()
            try:
                data = yaml.safe_load(raw_text)
            except yaml.YAMLError as e:
                # safe_load 失败可能因 numpy 标签; 预处理后再试一次 (不再 unsafe_load)
                logger.warning(f"safe_load 失败 ({e}), 尝试预处理 numpy 标签后重新加载")
                sanitized = self._sanitize_numpy_tags(raw_text)
                try:
                    data = yaml.safe_load(sanitized)
                except yaml.YAMLError as e2:
                    logger.error(f"预处理后 safe_load 仍失败: {e2}")
                    from utils.degradation_audit import record_degradation

                    record_degradation(
                        scope="stop_loss_monitor",
                        key=rules_file,
                        default="无规则 (监控器不会触发任何止损)",
                        reason=f"YAML 解析失败: {e2}",
                    )
                    return {}

        if not isinstance(data, dict):
            logger.error(f"止损规则文件格式错误: {rules_file}")
            from utils.degradation_audit import record_degradation

            record_degradation(
                scope="stop_loss_monitor",
                key=rules_file,
                default="无规则 (监控器不会触发任何止损)",
                reason="规则文件顶层不是 dict",
            )
            return {}

        rules = {}
        assets = data.get("assets", []) or []
        for asset in assets:
            code = asset.get("code", "")
            # 去掉后缀: 600019.SH → 600019
            pure_code = str(code).split(".")[0]
            # 统一将 numpy 标量转为 Python 原生类型, 便于后续比较
            cleaned = {}
            for k, v in asset.items():
                try:
                    if hasattr(v, "item"):
                        v = v.item()
                except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
                    logger.warning(f"转换 NumPy 类型失败 ({k}={v}): {e}")
                cleaned[k] = v
            rules[pure_code] = cleaned

        logger.info(f"加载止损规则: {rules_file} ({len(rules)} 条)")
        return rules

    def _load_default_stop_pct(self) -> float:
        """从 risk.yaml 加载 default_stop_pct 作为兜底止损比例

        P1-2 修复: risk.yaml 的 stop_loss.default_stop_pct=0.08 此前是死配置,
        从未被任何代码引用。现在加载它作为 per-asset 规则缺失时的兜底。
        """
        try:
            risk_path = os.path.join(_BASE, "config", "risk.yaml")
            if not os.path.exists(risk_path):
                return 0.08
            with open(risk_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            pct = cfg.get("stop_loss", {}).get("default_stop_pct", 0.08)
            logger.info(f"加载 risk.yaml default_stop_pct={pct}")
            return float(pct)
        except Exception as e:
            logger.warning(f"加载 risk.yaml default_stop_pct 失败, 使用默认 0.08: {e}")
            return 0.08

    def _create_mock_broker(self) -> Any:
        """创建 MockBroker (模拟盘适配器)"""
        try:
            from bridges.broker_adapter import BrokerFactory

            # P2-4 修复 (2026-09-01): 原调用 BrokerFactory.create("mock") 双重错误 —
            # ① "mock" 不在工厂注册表 (仅 "simulated") ② 缺少必填的 config 参数
            # → MockBroker 降级路径自身必然失败 (broker=NoneType), 降级形同虚设
            return BrokerFactory.create("simulated", config={})
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"创建 MockBroker 失败: {e}")
            return None

    def _get_positions(self) -> dict[str, dict]:
        """获取当前持仓"""
        if self.broker:
            return self.broker.get_positions()

        if os.path.exists(self.positions_file):
            with open(self.positions_file, encoding="utf-8") as f:
                data = json.load(f)
                return data.get("positions", {})
        return {}

    def _get_current_price(self, code: str) -> float | None:
        """获取实时价格 (多源回退)"""
        pure_code = code.split(".")[0]

        # 1. 尝试 Wind MCP
        try:
            from wind_mcp_fetcher import wind_get_quote

            if wind_get_quote:
                wind_code = self._to_wind_code(pure_code)
                quote = wind_get_quote(wind_code)
                if quote and "current" in quote:
                    return float(quote["current"])
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.exception("[StopLoss] Wind MCP 获取价格失败 code=%s", pure_code)

        # 2. 尝试从 positions.json 读取最新价格
        try:
            if os.path.exists(self.positions_file):
                with open(self.positions_file, encoding="utf-8") as f:
                    data = json.load(f)
                positions = data.get("positions", {})
                if pure_code in positions:
                    # 用 `or 0` 防 null: JSON 中 "current_price": null 时 get 返回 None 而非默认值
                    price = positions[pure_code].get("current_price") or 0
                    if price > 0:
                        return float(price)
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.exception(
                "[StopLoss] 读取 positions.json 价格失败 code=%s", pure_code
            )

        # 3. 尝试从 price_history 读取 (P3-3: 移除已删除的 11_量化策略 历史回退路径)
        try:
            history_path = os.path.join(_BASE, "config", "price_history.jsonl")
            if os.path.exists(history_path):
                import pandas as pd

                df = pd.read_json(history_path, lines=True)
                df = df[df["code"] == pure_code].tail(1)
                if len(df) > 0:
                    return float(df.iloc[0].get("close") or 0)
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.exception("[StopLoss] 读取 price_history 失败 code=%s", pure_code)

        return None

    @staticmethod
    def _to_wind_code(code: str) -> str:
        if code.startswith("6"):
            return f"{code}.SH"
        if code.startswith(("0", "3")):
            return f"{code}.SZ"
        return f"{code}.SH"

    def _evaluate_short(
        self,
        pure_code: str,
        name: str,
        shares: int,
        entry_price: float,
        current_price: float,
        rule: dict,
    ) -> TriggerRecord | None:
        """评估空头持仓的止损/止盈 (S2 修复).

        空头语义与多头镜像: 价格上涨=亏损 (止损线在上方), 价格下跌=盈利 (止盈线在下方)。
        触发平仓时 action=BUY (买回平仓), shares 取绝对值。
        移动止损跟踪最低价 (利好方向), 并随价格下跌收紧上方止损线。

        Returns:
            TriggerRecord 如果触发, 否则 None
        """
        stop_loss_pct = float(rule.get("stop_loss_pct") or -12.0) / 100
        # 空头止损线 = entry × (1 - stop_loss_pct); stop_loss_pct 为负(如-12%) → ×1.12 (上方)
        stop_loss_price = entry_price * (1 - stop_loss_pct)

        take_profit_pct = float(rule.get("take_profit_pct") or 25.0) / 100
        # 空头止盈线 = entry × (1 - take_profit_pct) (下方)
        take_profit_price = entry_price * (1 - take_profit_pct)

        trailing_stop = rule.get("trailing_stop", True)
        if trailing_stop:
            low_mark = self._low_water_mark.get(pure_code)
            if low_mark is None:
                low_mark = entry_price
            low_mark = min(low_mark, current_price)
            self._low_water_mark[pure_code] = low_mark
            self._save_water_marks()  # P1-2: 水位线更新即落盘, 重启后不回落
            # 移动止损线 = 最低价 × (1 - stop_loss_pct) (上方且随下跌收紧)
            trailing_stop_price = low_mark * (1 - stop_loss_pct)
            # 空头取更低 (更紧) 的上方止损线
            stop_loss_price = min(stop_loss_price, trailing_stop_price)

        pnl_pct = (current_price - entry_price) / entry_price  # 价格上涨为正 (空头亏损)
        abs_shares = abs(shares)

        # 止损: 价格上涨触及上方止损线
        if current_price >= stop_loss_price:
            trigger_type = (
                TriggerType.TRAILING_STOP
                if trailing_stop and current_price < entry_price
                else TriggerType.STOP_LOSS
            )
            return TriggerRecord(
                timestamp=now_bj().isoformat(),
                code=pure_code,
                name=name,
                trigger_type=trigger_type,
                entry_price=entry_price,
                current_price=current_price,
                trigger_price=stop_loss_price,
                shares=abs_shares,
                action="BUY",
                pnl_pct=pnl_pct,
            )

        # 止盈: 价格下跌触及下方止盈线
        if current_price <= take_profit_price:
            return TriggerRecord(
                timestamp=now_bj().isoformat(),
                code=pure_code,
                name=name,
                trigger_type=TriggerType.TAKE_PROFIT,
                entry_price=entry_price,
                current_price=current_price,
                trigger_price=take_profit_price,
                shares=abs_shares,
                action="BUY",
                pnl_pct=pnl_pct,
            )

        return None

    def check_position(self, code: str, position: dict) -> TriggerRecord | None:
        """检查单个持仓是否触发止损/止盈

        Args:
            code: 股票代码 (纯数字)
            position: {shares, avg_cost, name, current_price}

        Returns:
            TriggerRecord 如果触发, 否则 None
        """
        pure_code = code.split(".")[0]
        rule = self.rules.get(pure_code)
        if not rule:
            return None

        shares = position.get("shares") or 0
        if shares == 0:
            self._high_water_mark.pop(pure_code, None)  # 清仓后重置最高价记录
            self._low_water_mark.pop(pure_code, None)
            self._save_water_marks()  # P1-2: 清仓同步清除持久化状态
            return None
        is_short = shares < 0  # S2 修复: 空头持仓 (shares<0) 需反向止损/止盈

        entry_price = position.get("avg_cost") or 0
        if entry_price <= 0:
            entry_price = rule.get("entry_price") or 0
        if entry_price <= 0:
            return None

        # 获取当前价格
        current_price = position.get("current_price") or 0
        if current_price <= 0:
            current_price = self._get_current_price(code)
        if current_price is None or current_price <= 0:
            return None

        name = position.get("name", rule.get("name", code))

        # S2 修复: 空头持仓反向逻辑 (价格上涨止损, 价格下跌止盈, 平仓=BUY 买回)
        if is_short:
            return self._evaluate_short(
                pure_code, name, shares, entry_price, current_price, rule
            )

        # 止损线
        # P1-2 修复: 优先用 per-asset 规则的 stop_loss_pct, 缺失时用 risk.yaml 的 default_stop_pct
        # 此前 risk.yaml 的 default_stop_pct=0.08 是死配置, 从未被引用
        rule_stop_pct = rule.get("stop_loss_pct")
        if rule_stop_pct is not None:
            stop_loss_pct = float(rule_stop_pct) / 100
        else:
            stop_loss_pct = -self.default_stop_pct  # default_stop_pct=0.08 → -0.08
        stop_loss_price = entry_price * (1 + stop_loss_pct)

        # 止盈线
        take_profit_pct = float(rule.get("take_profit_pct") or 25.0) / 100
        take_profit_price = entry_price * (1 + take_profit_pct)

        # 移动止损 (trailing stop)
        trailing_stop = rule.get("trailing_stop", True)
        if trailing_stop:
            if pure_code not in self._high_water_mark:
                self._high_water_mark[pure_code] = entry_price
            self._high_water_mark[pure_code] = max(
                self._high_water_mark[pure_code], current_price
            )
            self._save_water_marks()  # P1-2: 水位线更新即落盘, 重启后不回落
            high = self._high_water_mark[pure_code]
            # 移动止损线 = 最高价 × (1 + stop_loss_pct)
            trailing_stop_price = high * (1 + stop_loss_pct)
            # 取更高的止损线 (原始 vs 移动)
            stop_loss_price = max(stop_loss_price, trailing_stop_price)

        pnl_pct = (current_price - entry_price) / entry_price

        # 检查止损
        if current_price <= stop_loss_price:
            trigger_type = (
                TriggerType.TRAILING_STOP
                if trailing_stop and current_price > entry_price
                else TriggerType.STOP_LOSS
            )
            return TriggerRecord(
                timestamp=now_bj().isoformat(),
                code=pure_code,
                name=name,
                trigger_type=trigger_type,
                entry_price=entry_price,
                current_price=current_price,
                trigger_price=stop_loss_price,
                shares=shares,
                action="SELL",
                pnl_pct=pnl_pct,
            )

        # 检查止盈
        if current_price >= take_profit_price:
            return TriggerRecord(
                timestamp=now_bj().isoformat(),
                code=pure_code,
                name=name,
                trigger_type=TriggerType.TAKE_PROFIT,
                entry_price=entry_price,
                current_price=current_price,
                trigger_price=take_profit_price,
                shares=shares,
                action="SELL",
                pnl_pct=pnl_pct,
            )

        # ATR 止损检查
        atr_stop_price = rule.get("atr_stop_loss_price") or 0
        if atr_stop_price > 0 and current_price <= atr_stop_price:
            return TriggerRecord(
                timestamp=now_bj().isoformat(),
                code=pure_code,
                name=name,
                trigger_type=TriggerType.ATR_STOP,
                entry_price=entry_price,
                current_price=current_price,
                trigger_price=atr_stop_price,
                shares=shares,
                action="SELL",
                pnl_pct=pnl_pct,
            )

        return None

    def check_and_execute(self, dry_run: bool = False) -> list[TriggerRecord]:
        """检查所有持仓并执行止损止盈

        Args:
            dry_run: 干跑模式 — 只检查并报告会触发什么, 不发送平仓订单

        Returns:
            触发记录列表
        """
        positions = self._get_positions()
        if not positions:
            logger.info("无持仓, 跳过止损检查")
            return []

        triggered = []
        for code, position in positions.items():
            record = self.check_position(code, position)
            if record:
                triggered.append(record)

        # 执行平仓 (支持多头卖出 SELL 和空头买回 BUY)
        for record in triggered:
            if record.action in ("SELL", "BUY"):
                if dry_run:
                    logger.warning(
                        f"[dry-run] 将触发平仓但不执行: {record.name} ({record.code}) "
                        f"{record.trigger_type.value} @ ¥{record.current_price:.2f} "
                        f"(入场 ¥{record.entry_price:.2f}, P&L {record.pnl_pct:+.1%}, 方向 {record.action})"
                    )
                    record.executed = False
                    record.order_id = "dry-run"
                    continue

                side = "sell" if record.action == "SELL" else "buy"
                success, order_id = self._execute_close(
                    record.code, side, record.shares, record.current_price
                )
                record.executed = success
                record.order_id = order_id

                if success:
                    logger.warning(
                        f"止损止盈触发: {record.name} ({record.code}) "
                        f"{record.trigger_type.value} @ ¥{record.current_price:.2f} "
                        f"(入场 ¥{record.entry_price:.2f}, P&L {record.pnl_pct:+.1%}, 方向 {record.action})"
                    )
                else:
                    logger.error(f"执行平仓失败: {record.code} - {order_id}")

        self.trigger_history.extend(triggered)

        if triggered and not dry_run:
            self._save_trigger_log(triggered)

        return triggered

    def _execute_close(
        self, code: str, side: str, shares: int, price: float
    ) -> tuple[bool, str]:
        """通过 broker 执行平仓 (side='sell' 多头卖出 / 'buy' 空头买回)"""
        if not self.broker:
            logger.warning(
                f"无 broker, 仅记录: {side.upper()} {code} {shares}@{price:.2f}"
            )
            return False, "no_broker"

        try:
            success, order_id = self.broker.send_order(code, side, shares, price)
            # P2-4: mock 模式下显式标记, 避免上游误认为已真实成交
            if success and self.execution_mode == "MOCK":
                logger.warning(
                    "[WARN] %s %s %d@%.2f 通过 MockBroker 模拟成交 (EXECUTION_MODE=MOCK, 真实持仓未变)",
                    side.upper(),
                    code,
                    shares,
                    price,
                )
            return success, order_id
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"平仓异常: {code} - {e}")
            return False, str(e)

    def _save_trigger_log(self, records: list[TriggerRecord]) -> None:
        """保存触发日志 (P0-C1: 原子写 + 异常隔离, 防止日志写坏影响主流程)"""
        log_dir = os.path.join(_BASE, "reports")
        os.makedirs(log_dir, exist_ok=True)

        log_path = os.path.join(
            log_dir, f"stop_loss_trigger_{now_bj().strftime('%Y%m%d')}.json"
        )
        existing = []
        if os.path.exists(log_path):
            try:
                with open(log_path, encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, list):
                    logger.warning("触发日志文件非数组格式, 重置为空数组: %s", log_path)
                    existing = []
            except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                logger.exception("[StopLoss] 读取触发日志失败, 将覆盖: %s", log_path)
                existing = []

        for r in records:
            existing.append(
                {
                    "timestamp": r.timestamp,
                    "code": r.code,
                    "name": r.name,
                    "trigger_type": r.trigger_type.value,
                    "entry_price": r.entry_price,
                    "current_price": r.current_price,
                    "trigger_price": r.trigger_price,
                    "shares": r.shares,
                    "action": r.action,
                    "pnl_pct": round(r.pnl_pct, 4),
                    "executed": r.executed,
                    "order_id": r.order_id,
                }
            )

        try:
            _atomic_write_json(log_path, existing)
            logger.info("触发日志已保存: %s", log_path)
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.exception("[StopLoss] 触发日志原子写失败: %s", log_path)

    def get_monitoring_status(self) -> dict:
        """获取监控状态摘要"""
        positions = self._get_positions()
        n_monitored = sum(1 for c in positions if c.split(".")[0] in self.rules)
        return {
            "total_positions": len(positions),
            "monitored": n_monitored,
            "rules_loaded": len(self.rules),
            "triggers_today": len(
                [
                    t
                    for t in self.trigger_history
                    if t.timestamp.startswith(now_bj().strftime("%Y-%m-%d"))
                ]
            ),
            "high_water_marks": dict(self._high_water_mark),
            "low_water_marks": dict(self._low_water_mark),
        }


def main() -> None:
    """独立运行止损监控 — argparse 契约: --help 只展示用法, 不触发监控"""
    import argparse

    from utils.runtime_mode import env_flag, set_mode

    parser = argparse.ArgumentParser(
        prog="stop_loss_monitor",
        description="止损止盈自动触发引擎: 检查持仓价格, 触发规则时通过 broker 发送平仓订单。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=env_flag("QUANT_DRY_RUN"),
        help="干跑模式: 只检查会触发什么, 不发送平仓订单、不写触发日志"
             " (可用 QUANT_DRY_RUN=1 预设)",
    )
    args = parser.parse_args()

    # P1-1: CLI/env 解析结果广播到统一三态开关 (深层模块经 is_dry_run() 感知)
    set_mode(dry_run=args.dry_run)

    logger.info("=" * 60)
    logger.info("止损止盈自动触发引擎 v1.0")
    if args.dry_run:
        logger.info("DRY-RUN 模式: 只检查, 不执行平仓")
    logger.info("=" * 60)

    monitor = StopLossMonitor()

    # 检查并执行
    triggered = monitor.check_and_execute(dry_run=args.dry_run)

    # 打印状态
    status = monitor.get_monitoring_status()
    logger.info("\n监控状态:")
    logger.info(f"  总持仓: {status['total_positions']}")
    logger.info(f"  已监控: {status['monitored']}")
    logger.info(f"  规则数: {status['rules_loaded']}")
    logger.info(f"  今日触发: {status['triggers_today']}")

    if triggered:
        logger.info(f"\n触发 {len(triggered)} 条:")
        for t in triggered:
            executed_label = "✅ 已执行" if t.executed else (
                "⏸️ dry-run 未执行" if args.dry_run else "❌ 未执行"
            )
            logger.info(
                f"  {t.name} ({t.code}) {t.trigger_type.value} "
                f"@ ¥{t.current_price:.2f} (P&L {t.pnl_pct:+.1%}) "
                f"{executed_label}"
            )
    else:
        logger.info("\n无触发 — 所有持仓在安全范围内")


if __name__ == "__main__":
    main()
