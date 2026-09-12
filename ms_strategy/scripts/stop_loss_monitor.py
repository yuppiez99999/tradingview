"""
止损止盈自动触发引擎 v1.0 — 步骤4

盘中监控持仓价格, 当触及止损/止盈线时自动触发卖出:
1. 加载波动率调整止损规则 (vol_adjusted 或 auto)
2. 获取实时价格 (Wind MCP / iFinD / 缓存)
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

import yaml

from utils.datetime_utils import now_bj

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('stop_loss_monitor')

# 添加路径
_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE)


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

    def __init__(self,
                 rules_file: str = None,
                 positions_file: str = None,
                 broker=None):
        """
        Args:
            rules_file: 止损规则 YAML 文件路径
            positions_file: 持仓 JSON 文件路径
            broker: BrokerAdapter 实例 (MockBrokerAdapter 或 QMT)
        """
        # 规则文件: 优先本子项目 ms_strategy/config/, 其次项目根 config/, 再回退 11_量化策略
        if rules_file is None:
            candidates = [
                os.path.join(_BASE, "..", "config", "stop_loss_vol_adjusted.yaml"),
                os.path.join(_BASE, "..", "..", "config", "stop_loss_vol_adjusted.yaml"),
                os.path.join(_BASE, "..", "..", "..", "11_量化策略", "config", "stop_loss_vol_adjusted.yaml"),
                os.path.join(_BASE, "..", "config", "stop_loss_rules_auto.yaml"),
                os.path.join(_BASE, "..", "..", "config", "stop_loss_rules_auto.yaml"),
                os.path.join(_BASE, "..", "..", "..", "11_量化策略", "config", "stop_loss_rules_auto.yaml"),
            ]
            rules_file = next((p for p in candidates if os.path.exists(p)), None)

        self.rules_file = rules_file
        self.rules = self._load_rules(rules_file)

        # 持仓文件: 优先本子项目 ms_strategy/config/, 其次项目根 config/, 再回退 11_量化策略
        _pos_candidates = [
            os.path.join(_BASE, "..", "config", "positions.json"),
            os.path.join(_BASE, "..", "..", "config", "positions.json"),
            os.path.join(_BASE, "..", "..", "..", "11_量化策略", "config", "positions.json"),
        ]
        self.positions_file = positions_file or next(
            (p for p in _pos_candidates if os.path.exists(p)), _pos_candidates[0])

        # Broker
        self.broker = broker
        if self.broker is None:
            self.broker = self._create_mock_broker()

        # 最高价记录 (移动止损用)
        self._high_water_mark: dict[str, float] = {}

        # 触发历史
        self.trigger_history: list[TriggerRecord] = []

        logger.info(f"止损监控器初始化: {len(self.rules)} 条规则, broker={self.broker.__class__.__name__}")

    def _load_rules(self, rules_file: str) -> dict[str, dict]:
        """加载止损规则

        兼容两种 YAML 格式:
        - 纯文本 (safe_load): 标量已转换好的版本
        - numpy 标签 (unsafe_load): vol_adjusted_stop_loss.py 生成的版本, 含 !!python/object/apply:numpy.* 标签
        """
        if not os.path.exists(rules_file):
            logger.error(f"止损规则文件不存在: {rules_file}")
            return {}

        data = None
        with open(rules_file, encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f)
            except yaml.constructor.ConstructorError as e:
                logger.warning(
                    f"safe_load 失败 ({e.problem}), 尝试 unsafe_load 加载 numpy 标签"
                )
                f.seek(0)
                try:
                    data = yaml.unsafe_load(f)
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e2:  # noqa: E501
                    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                    logger.error(f"unsafe_load 也失败: {e2}")
                    return {}

        if not isinstance(data, dict):
            logger.error(f"止损规则文件格式错误: {rules_file}")
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
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):  # noqa: E501
                    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                    pass
                cleaned[k] = v
            rules[pure_code] = cleaned

        logger.info(f"加载止损规则: {rules_file} ({len(rules)} 条)")
        return rules

    def _create_mock_broker(self):
        """创建 MockBroker"""
        try:
            quant_dir = os.path.join(_BASE, "..", "11_量化策略")
            sys.path.insert(0, quant_dir)
            from quant_modules.broker_adapter import BrokerFactory
            return BrokerFactory.create("mock")
        except (ImportError, AttributeError, RuntimeError) as e:
            # ImportError: quant_modules.broker_adapter 缺失
            # AttributeError: BrokerFactory.create 接口不匹配
            # RuntimeError: BrokerFactory.create 内部异常
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
                if quote and 'current' in quote:
                    return float(quote['current'])
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass

        # 2. 尝试从 positions.json 读取最新价格
        try:
            if os.path.exists(self.positions_file):
                with open(self.positions_file, encoding="utf-8") as f:
                    data = json.load(f)
                positions = data.get("positions", {})
                if pure_code in positions:
                    price = positions[pure_code].get("current_price", 0)
                    if price > 0:
                        return float(price)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass

        # 3. 尝试从 price_history 读取
        try:
            history_path = os.path.join(_BASE, "..", "11_量化策略", "config", "price_history.jsonl")
            if os.path.exists(history_path):
                import pandas as pd
                df = pd.read_json(history_path, lines=True)
                df = df[df['code'] == pure_code].tail(1)
                if len(df) > 0:
                    return float(df.iloc[0].get('close', 0))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass

        return None

    @staticmethod
    def _to_wind_code(code: str) -> str:
        if code.startswith("6"):
            return f"{code}.SH"
        if code.startswith(("0", "3")):
            return f"{code}.SZ"
        return f"{code}.SH"

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

        shares = position.get("shares", 0)
        if shares <= 0:
            return None

        entry_price = position.get("avg_cost", 0)
        if entry_price <= 0:
            entry_price = rule.get("entry_price", 0)
        if entry_price <= 0:
            return None

        # 获取当前价格
        current_price = position.get("current_price", 0)
        if current_price <= 0:
            current_price = self._get_current_price(code)
        if current_price is None or current_price <= 0:
            return None

        name = position.get("name", rule.get("name", code))

        # 止损线
        stop_loss_pct = rule.get("stop_loss_pct", -12.0) / 100  # 转小数
        stop_loss_price = entry_price * (1 + stop_loss_pct)

        # 止盈线
        take_profit_pct = rule.get("take_profit_pct", 25.0) / 100
        take_profit_price = entry_price * (1 + take_profit_pct)

        # 移动止损 (trailing stop)
        trailing_stop = rule.get("trailing_stop", True)
        if trailing_stop:
            if pure_code not in self._high_water_mark:
                self._high_water_mark[pure_code] = entry_price
            self._high_water_mark[pure_code] = max(
                self._high_water_mark[pure_code], current_price
            )
            high = self._high_water_mark[pure_code]
            # 移动止损线 = 最高价 × (1 + stop_loss_pct)
            trailing_stop_price = high * (1 + stop_loss_pct)
            # 取更高的止损线 (原始 vs 移动)
            stop_loss_price = max(stop_loss_price, trailing_stop_price)

        safe_entry = entry_price if entry_price != 0 else 1.0
        pnl_pct = (current_price - entry_price) / safe_entry

        # 检查止损
        if current_price <= stop_loss_price:
            trigger_type = TriggerType.TRAILING_STOP if trailing_stop and current_price > entry_price else TriggerType.STOP_LOSS  # noqa: E501
            return TriggerRecord(
                timestamp=now_bj().isoformat(),
                code=pure_code, name=name,
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
                code=pure_code, name=name,
                trigger_type=TriggerType.TAKE_PROFIT,
                entry_price=entry_price,
                current_price=current_price,
                trigger_price=take_profit_price,
                shares=shares,
                action="SELL",
                pnl_pct=pnl_pct,
            )

        # ATR 止损检查
        atr_stop_price = rule.get("atr_stop_loss_price", 0)
        if atr_stop_price > 0 and current_price <= atr_stop_price:
            return TriggerRecord(
                timestamp=now_bj().isoformat(),
                code=pure_code, name=name,
                trigger_type=TriggerType.ATR_STOP,
                entry_price=entry_price,
                current_price=current_price,
                trigger_price=atr_stop_price,
                shares=shares,
                action="SELL",
                pnl_pct=pnl_pct,
            )

        return None

    def check_and_execute(self) -> list[TriggerRecord]:
        """检查所有持仓并执行止损止盈

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

        # 执行卖出
        for record in triggered:
            if record.action == "SELL":
                success, order_id = self._execute_sell(record.code, record.shares, record.current_price)
                record.executed = success
                record.order_id = order_id

                if success:
                    logger.warning(
                        f"止损止盈触发: {record.name} ({record.code}) "
                        f"{record.trigger_type.value} @ ¥{record.current_price:.2f} "
                        f"(入场 ¥{record.entry_price:.2f}, P&L {record.pnl_pct:+.1%})"
                    )
                else:
                    logger.error(f"执行卖出失败: {record.code} - {order_id}")

        self.trigger_history.extend(triggered)

        if triggered:
            self._save_trigger_log(triggered)

        return triggered

    def _execute_sell(self, code: str, shares: int, price: float) -> tuple[bool, str]:
        """通过 broker 执行卖出"""
        if not self.broker:
            logger.warning(f"无 broker, 仅记录: SELL {code} {shares}@{price:.2f}")
            return False, "no_broker"

        try:
            success, order_id = self.broker.send_order(code, "sell", shares, price)
            return success, order_id
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"卖出异常: {code} - {e}")
            return False, str(e)

    def _save_trigger_log(self, records: list[TriggerRecord]):
        """保存触发日志"""
        log_dir = os.path.join(_BASE, "reports")
        os.makedirs(log_dir, exist_ok=True)

        log_path = os.path.join(log_dir, f"stop_loss_trigger_{now_bj().strftime('%Y%m%d')}.json")
        existing = []
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8") as f:
                existing = json.load(f)

        for r in records:
            existing.append({
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
            })

        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        logger.info(f"触发日志已保存: {log_path}")

    def get_monitoring_status(self) -> dict:
        """获取监控状态摘要"""
        positions = self._get_positions()
        n_monitored = sum(1 for c in positions if c.split(".")[0] in self.rules)
        return {
            "total_positions": len(positions),
            "monitored": n_monitored,
            "rules_loaded": len(self.rules),
            "triggers_today": len([t for t in self.trigger_history
                                   if t.timestamp.startswith(now_bj().strftime("%Y-%m-%d"))]),
            "high_water_marks": dict(self._high_water_mark),
        }


def main():
    """独立运行止损监控"""
    logger.info("=" * 60)
    logger.info("止损止盈自动触发引擎 v1.0")
    logger.info("=" * 60)

    monitor = StopLossMonitor()

    # 检查并执行
    triggered = monitor.check_and_execute()

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
            logger.info(
                f"  {t.name} ({t.code}) {t.trigger_type.value} "
                f"@ ¥{t.current_price:.2f} (P&L {t.pnl_pct:+.1%}) "
                f"{'✅ 已执行' if t.executed else '❌ 未执行'}"
            )
    else:
        logger.info("\n无触发 — 所有持仓在安全范围内")


if __name__ == "__main__":
    main()
