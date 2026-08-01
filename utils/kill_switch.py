"""
三级熔断协议 (Kill Switch Protocol)
==================================

对冲基金视角风控核心:
    L1 一级警戒: 保证金占用率达50% → 停止开仓, 进入防守模式
    L2 二级熔断: 保证金占用率达75% → 强平深虚值期权空头, 释放流动性
    L3 三级互盲: 极端Margin Call → 变现10%红利ETF, 跨品种清算注入

与 portfolio.yaml kill_switch 配置对齐。

用法:
    from utils.kill_switch import KillSwitch
    ks = KillSwitch()
    status = ks.check_margin_status()
    if status["level"] >= 1:
        ks.execute_kill_switch(status["level"])
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger("kill_switch")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "configs" / "portfolio.yaml"
KILL_SWITCH_LOG = BASE_DIR / "logs" / "kill_switch_events.jsonl"


class KillSwitch:
    """三级熔断协议

    支持两种使用模式:
        1. 独立模式（CLI）: 从环境变量读取模拟保证金数据
        2. 集成模式: 外部调用方传入真实 margin_usage 参数
    """

    def __init__(self, config_path: Path | None = None, margin_limit: float = 0.50):
        """
        Args:
            config_path: 配置文件路径
            margin_limit: 兼容 alpha_hedge_engine 传入的保证金限额（仅存储，不改变熔断阈值）
        """
        self.config_path = config_path or CONFIG_PATH
        self.margin_limit = margin_limit
        self.config = self._load_config()
        self._broker_callback = None  # 实盘执行回调函数
        KILL_SWITCH_LOG.parent.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> dict:
        """加载 kill_switch 配置 (P1-Q8: 通过 ConfigManager 统一加载)

        优先级:
            1. 显式传入的 config_path (向后兼容测试场景)
            2. ConfigManager 自动解析 (v8.3 唯一事实源 > configs/ 历史回退)

        历史背景:
            v8.6.7 之前 kill_switch.py 硬编码读取 configs/portfolio.yaml (v7.7 旧版),
            与 v8.3_institutional/config/portfolio.yaml (v8.4 唯一事实源) 存在配置漂移.
            P1-Q8: 通过 ConfigManager 统一加载, 优先使用 v8.3 唯一事实源.

        Returns:
            kill_switch 配置字典, 加载失败返回空 dict (fail-safe)
        """
        # 路径 1: 调用方显式指定了 config_path (测试场景, 向后兼容)
        if self.config_path != CONFIG_PATH:
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                return cfg.get("kill_switch", {}) if isinstance(cfg, dict) else {}
            except (FileNotFoundError, yaml.YAMLError, OSError) as e:
                logger.error(f"加载配置失败 (显式路径 {self.config_path}): {e}")
                return {}

        # 路径 2: 通过 ConfigManager 统一加载 (P1-Q8, 生产路径)
        try:
            # 延迟导入避免循环依赖
            from utils.config_manager import get_kill_switch_config

            cfg = get_kill_switch_config()
            if cfg:
                return cfg
            # ConfigManager 全部失败, 回退到旧路径 (保底)
            with open(self.config_path, encoding="utf-8") as f:
                fallback_cfg = yaml.safe_load(f)
            return fallback_cfg.get("kill_switch", {}) if isinstance(fallback_cfg, dict) else {}
        except (ImportError, AttributeError, OSError, yaml.YAMLError) as e:
            logger.error(f"ConfigManager 加载失败, 回退到旧路径: {e}", exc_info=True)
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                return cfg.get("kill_switch", {}) if isinstance(cfg, dict) else {}
            except (FileNotFoundError, yaml.YAMLError, OSError) as e2:
                logger.error(f"全部加载路径失败: {e2}")
                return {}

    def _get_margin_status(self) -> dict:
        """获取保证金占用情况

        优先级:
            1. 环境变量 KILL_SWITCH_MARGIN_RATIO (用于测试/模拟)
            2. 环境变量 KILL_SWITCH_SIM_MODE=l1/l2/l3 (模拟熔断场景)
            3. 读取 config/positions.json 估算真实保证金占用 (v8.6.1 修复)
            4. 保守默认值 0.50 (L1 阈值, 持仓文件不存在时使用)

        Returns:
            {
                "total_margin": ...,
                "available_margin": ...,
                "margin_usage_ratio": float,
                "margin_call": bool,
                "extreme_margin_call": bool
            }
        """
        # 优先从环境变量读取 (用于模拟不同场景)
        sim_mode = os.environ.get("KILL_SWITCH_SIM_MODE", "").lower()
        env_ratio = os.environ.get("KILL_SWITCH_MARGIN_RATIO")

        if env_ratio is not None:
            try:
                ratio = float(env_ratio)
                ratio = max(0.0, min(1.0, ratio))
            except ValueError:
                ratio = 0.20
        elif sim_mode == "l1":
            # 模拟 L1 触发场景 (保证金占用 50%+)
            ratio = 0.55
        elif sim_mode == "l2":
            # 模拟 L2 熔断场景 (保证金占用 75%+)
            ratio = 0.78
        elif sim_mode == "l3":
            # 模拟 L3 极端场景
            ratio = 0.95
        else:
            # v8.6.1 修复: 读取 config/positions.json 估算真实保证金占用
            ratio = self._estimate_margin_from_positions()

        # P2-Q11 修复: 总保证金配置化, 不再硬编码 5_000_000
        total_margin = self._get_total_margin()
        used_margin = total_margin * ratio
        available_margin = total_margin - used_margin

        return {
            "total_margin": total_margin,
            "available_margin": available_margin,
            "margin_usage_ratio": ratio,
            "margin_call": ratio >= 0.90,
            "extreme_margin_call": ratio >= 0.95,
        }

    def _estimate_margin_from_positions(self) -> float:
        """从 config/positions.json 读取持仓估算保证金占用率 (v8.6.1 修复 / P1-Q7 重构)

        P1-Q7 重构 (2026-07-26): 拆分为编排器 + 4 个职责单一子方法
            原函数: 117 行, 圈复杂度 > 20, 5 层嵌套, 难以单元测试
            重构后: 编排器 ~30 行 + 4 个子方法, 每个可独立测试

        计算逻辑:
            - type=STOCK/ETF: 不计入保证金占用 (全额交易)
            - type=FUTURE: 按合约价值的 12% 估算保证金
            - type=OPTION: 按权利金价值的 100% 估算保证金

        Returns:
            保证金占用率 (0.0-1.0), 文件不存在时返回保守值 0.50
        """
        # === 编排器: 加载 → 预算估算 → 持仓估算 ===
        data = self._load_positions_data()
        if data is None:
            return 0.50  # 文件不存在, 保守值

        # 优先尝试预算汇总估算 (含期货模式)
        budget_ratio = self._estimate_from_budget_summary(data)
        if budget_ratio is not None:
            return budget_ratio

        # 回退到真实持仓估算 (OPTIONS_ONLY 或无 budget_summary)
        return self._estimate_from_real_positions(data)

    def _load_positions_data(self) -> dict | None:
        """加载持仓配置文件 (P1-Q7 拆分)

        Returns:
            配置字典, 文件不存在或解析失败时返回 None
        """
        project_root = Path(__file__).resolve().parent.parent
        positions_file = project_root / "config" / "positions.json"

        if not positions_file.exists():
            logger.warning(f"持仓文件不存在: {positions_file}, 使用保守保证金占用率 0.50")
            return None

        try:
            with open(positions_file, encoding="utf-8") as f:
                return json.load(f)  # type: ignore[no-any-return]
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            logger.error(f"读取持仓文件失败: {e}, 使用保守值 0.50")
            return None

    def _estimate_from_budget_summary(self, data: dict) -> float | None:
        """从 budget_summary 估算保证金占用率 (P1-Q7 拆分)

        v8.6.7 CRITICAL FIX (2026-07-26): 期权买方模式下, 权利金已现金扣减,
        不构成保证金占用. 此前将 budget_summary.usage_pct (预算消耗进度)
        误当作 margin_usage_ratio, 导致每次启动都触发 L2 熔断.

        概念区分:
            - budget_summary.usage_pct = 已花权利金 / 对冲预算 (预算消耗进度, 正常 50-90%)
            - margin_usage_ratio       = 实际保证金占用 / 总资金 (风险指标, ≥75% 才熔断)

        纯期权对冲模式 (OPTIONS_ONLY) 下, 买方不付保证金, 仅卖方才需保证金.
        因此遇到 budget_summary.usage_pct 时, 应跳过此分支, 落到下方真实持仓估算.

        Returns:
            估算的保证金占用率 (0.0-1.0), 不适用时返回 None (回退到真实持仓估算)
        """
        hedge_mode = data.get("meta", {}).get("hedge_mode", "")
        hedge_positions = data.get("hedge_positions", {})
        budget_summary = hedge_positions.get("budget_summary", {})
        hedge_capital = float(data.get("meta", {}).get("hedge_capital", 2_000_000))

        if not budget_summary:
            return None

        # 检查是否存在真实期货持仓 (type=FUTURE)
        positions = data.get("positions", {})
        has_real_futures = any(
            isinstance(p, dict) and p.get("type", "").upper() == "FUTURE"
            for p in positions.values()
        ) if isinstance(positions, dict) else False

        # OPTIONS_ONLY 模式 或 无真实期货持仓:
        # budget_summary.usage_pct 是期权权利金预算消耗进度 (正常 50-90%), 非保证金占用率.
        # 将其当作 margin_usage_ratio 会导致 L2 误触发 (82.5% > 75% 阈值).
        if hedge_mode == "OPTIONS_ONLY" or not has_real_futures:
            reason = "OPTIONS_ONLY 模式" if hedge_mode == "OPTIONS_ONLY" else "无真实期货持仓"
            logger.info(
                "[KillSwitch] %s: 预算消耗 %.1f%% (非保证金占用), 跳过预算估算, 落入实际持仓估算",
                reason,
                float(budget_summary.get("usage_pct", 0.0)),
            )
            return None  # 落到 _estimate_from_real_positions

        # 非纯期权模式 (含期货): 尝试用 usage_pct 估算
        usage_pct = budget_summary.get("usage_pct")
        if usage_pct is not None:
            try:
                ratio = max(0.0, min(1.0, float(usage_pct) / 100.0))
                logger.info(
                    "[KillSwitch] 对冲预算估算保证金占用: usage_pct=%.1f%%, ratio=%.1f%%",
                    float(usage_pct),
                    ratio * 100,
                )
                return ratio
            except (TypeError, ValueError):
                pass  # 落到下一个估算方式

        # 回退: 用 total_put_premium / total_hedge_capital 估算
        total_put_premium = budget_summary.get("total_put_premium")
        total_hedge_capital = budget_summary.get("total_hedge_capital", hedge_capital)
        if total_put_premium is not None and total_hedge_capital:
            try:
                ratio = max(0.0, min(1.0, float(total_put_premium) / float(total_hedge_capital)))
                logger.info(
                    "[KillSwitch] 对冲预算估算保证金占用: "
                    "total_put_premium=¥%.0f, total_hedge_capital=¥%.0f, ratio=%.1f%%",
                    float(total_put_premium),
                    float(total_hedge_capital),
                    ratio * 100,
                )
                return ratio
            except (TypeError, ValueError):
                pass

        return None  # 全部失败, 回退到真实持仓估算

    def _estimate_from_real_positions(self, data: dict) -> float:
        """从真实持仓数据估算保证金占用率 (P1-Q7 拆分)

        计算逻辑:
            - type=STOCK/ETF: 不计入保证金占用 (全额交易)
            - type=FUTURE: 按合约价值的 12% 估算保证金
            - type=OPTION: 按权利金价值的 100% 估算保证金

        Returns:
            保证金占用率 (0.0-1.0), 数据异常时返回保守值 0.50
        """
        total_capital = float(data.get("meta", {}).get("total_capital", 5_000_000))
        hedge_capital = float(data.get("meta", {}).get("hedge_capital", 2_000_000))
        positions = data.get("positions", {})

        total_position_value, estimated_margin_usage = self._compute_position_margin(positions)

        if total_capital <= 0:
            logger.warning("total_capital <= 0, 使用保守保证金占用率 0.50")
            return 0.50

        ratio = estimated_margin_usage / hedge_capital if hedge_capital > 0 else 0.0
        ratio = max(0.0, min(1.0, ratio))

        logger.info(
            f"[KillSwitch] 持仓估算保证金: "
            f"总持仓市值=¥{total_position_value:,.0f}, "
            f"估算保证金占用=¥{estimated_margin_usage:,.0f}, "
            f"对冲资本=¥{hedge_capital:,.0f}, "
            f"占用率={ratio:.1%}"
        )
        return ratio

    @staticmethod
    def _compute_position_margin(positions: dict) -> tuple:
        """计算持仓总市值和保证金占用 (P1-Q7 拆分, 纯函数)

        Args:
            positions: {code: {"amount": float, "type": str}} 持仓字典

        Returns:
            (total_position_value, estimated_margin_usage)
            - total_position_value: 所有持仓的总市值
            - estimated_margin_usage: 估算的保证金占用金额
              (STOCK/ETF=0, FUTURE=12% × amount, OPTION=100% × amount)
        """
        total_position_value = 0.0
        estimated_margin_usage = 0.0

        for _code, pos in positions.items():
            if not isinstance(pos, dict):
                continue
            amount = pos.get("amount", 0)
            pos_type = pos.get("type", "").upper()

            if not (amount and isinstance(amount, (int, float))):
                continue

            amount_val = float(amount)
            total_position_value += amount_val

            if pos_type == "FUTURE":
                # 期货: 按合约价值的 12% 估算保证金
                estimated_margin_usage += amount_val * 0.12
            elif pos_type == "OPTION":
                # 期权: 按权利金价值的 100% 估算保证金
                estimated_margin_usage += amount_val
            # STOCK/ETF: 全额交易, 不计入保证金占用

        return total_position_value, estimated_margin_usage

    def set_broker_callback(self, callback) -> None:
        """注册实盘执行回调函数

        当回调存在时, execute_kill_switch 将通过回调执行真实交易操作,
        而非抛出 RuntimeError.

        Args:
            callback: 可调用对象, 签名为 callback(level: int, actions: list) -> Dict
        """
        self._broker_callback = callback

    # ============================================================
    # P1-Q4 修复 (2026-07-26): fail-closed 响应工厂 + 配置化总保证金
    # ============================================================
    def _fail_closed_response(self, reason: str = "UNSPECIFIED") -> dict:
        """生成 fail-closed 响应 (L3 强制熔断, 阻止一切交易)

        P1-Q4 修复: 统一 fail-closed 响应生成, 避免多路径重复代码
        所有 fail-closed 场景 (数据不可用/异常低值/类型错误) 共用此方法

        Args:
            reason: 触发原因, 用于审计与日志

        Returns:
            标准 L3 熔断响应字典
        """
        logger.critical(
            f"[KillSwitch] FAIL-CLOSED 触发 | reason={reason} | "
            f"TRADING_ENV={os.environ.get('TRADING_ENV', 'dev')} | "
            f"全部交易已被阻止"
        )
        return {
            "timestamp": datetime.now().isoformat(),
            "margin_usage_ratio": 1.0,
            "margin_call": True,
            "extreme_margin_call": True,
            "level": 3,
            "level_name": f"数据不可用-强制熔断 ({reason})",
            "actions": ["数据不可用, 强制停止一切交易"],
            "auto_execute": True,
            "can_trade": False,
            "can_open": False,
            "action": f"FAIL_CLOSED_{reason}",
            "fail_closed_reason": reason,
        }

    def _get_total_margin(self) -> float:
        """获取账户总保证金 (P2-Q11 修复: 配置化, 不再硬编码 5_000_000)

        优先级:
            1. 环境变量 KILL_SWITCH_TOTAL_MARGIN (用于运维快速覆盖, 测试场景)
            2. config/portfolio.yaml → kill_switch.total_margin (self.config)
            3. config/positions.json → meta.total_capital
            4. 默认值 5_000_000 (与历史行为兼容)

        Returns:
            总保证金金额 (float)
        """
        # 1. 环境变量优先 (运维快速覆盖 + 测试场景注入)
        env_margin = os.environ.get("KILL_SWITCH_TOTAL_MARGIN")
        if env_margin:
            try:
                val = float(env_margin)
                if val > 0:
                    return val
            except ValueError:
                logger.warning(f"KILL_SWITCH_TOTAL_MARGIN 非法值: {env_margin}, 忽略")

        # 2. 从 kill_switch 配置读取 (self.config 来自 portfolio.yaml)
        cfg_margin = self.config.get("total_margin") if isinstance(self.config, dict) else None
        if isinstance(cfg_margin, (int, float)) and cfg_margin > 0:
            return float(cfg_margin)

        # 3. 回退到 positions.json 的 total_capital
        try:
            project_root = Path(__file__).resolve().parent.parent
            positions_file = project_root / "config" / "positions.json"
            if positions_file.exists():
                with open(positions_file, encoding="utf-8") as f:
                    data = json.load(f)
                total_capital = float(data.get("meta", {}).get("total_capital", 0))
                if total_capital > 0:
                    return total_capital
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, OSError) as e:
            logger.debug(f"读取 positions.json total_capital 失败: {e}")

        # 4. 兼容默认值
        return 5_000_000

    def check_margin_status(self, margin_usage: float | None = None) -> dict:
        """检查保证金状态, 判断熔断级别

        Args:
            margin_usage: 外部传入的真实保证金占用率(0-1).
                          若为 None, 则从 _get_margin_status() 获取(环境变量/模拟).

        Returns:
            {
                "timestamp": "...",
                "margin_usage_ratio": 0.60,
                "margin_call": false,
                "level": 1,  # 0=正常, 1=一级, 2=二级, 3=三级
                "level_name": "一级警戒线",
                "actions": [...],
                "auto_execute": true,
                "can_trade": true,   # level < 2 时为 True
                "can_open": true,    # level == 0 时为 True
                "action": "..."      # 首条 action 文本, 兼容 alpha_hedge_engine
            }
        """
        trading_env = os.environ.get("TRADING_ENV", "dev").lower()

        # P1-Q4 修复 (2026-07-26): fail-closed 完整覆盖所有路径
        # 原始 bug: 仅在 margin_usage=None 时检查 fail-closed, 显式传参路径绕过校验
        # 风险: caller 传入 margin_usage=0.0 (数据源故障默认值) 会被判为"正常"
        # 修复: production 模式下, 显式传入的 margin_usage 也需校验合理性
        if margin_usage is not None:
            try:
                ratio = max(0.0, min(1.0, float(margin_usage)))
            except (TypeError, ValueError):
                logger.critical(
                    f"margin_usage 类型异常 ({type(margin_usage).__name__}): {margin_usage}, 进入 FAIL-CLOSED"
                )
                return self._fail_closed_response("INVALID_MARGIN_USAGE_TYPE")

            # P1-Q4: 生产模式下校验传入值的合理性
            # 异常低值 (< 0.01) 几乎不可能发生, 疑数据源故障或测试数据泄漏到生产
            if trading_env == "production" and ratio < 0.01:
                logger.critical(
                    f"生产环境传入异常低 margin_usage={ratio:.4f} (< 0.01), "
                    f"疑数据源故障或测试数据泄漏, 进入 FAIL-CLOSED"
                )
                return self._fail_closed_response("SUSPICIOUS_LOW_MARGIN_USAGE")

            margin = {
                "total_margin": self._get_total_margin(),
                "available_margin": self._get_total_margin() * (1 - ratio),
                "margin_usage_ratio": ratio,
                "margin_call": ratio >= 0.90,
                "extreme_margin_call": ratio >= 0.95,
            }
        else:
            # 修复 BUG-K1: 生产环境 fail-closed, 数据不可用时视为满仓熔断
            if trading_env == "production":
                logger.critical(
                    "保证金数据不可用 (未传入 margin_usage 且券商API未对接)! "
                    "Kill Switch 进入 FAIL-CLOSED 模式, 阻止一切交易"
                )
                return self._fail_closed_response("DATA_UNAVAILABLE")
            # 开发/测试环境: 使用模拟值
            margin = self._get_margin_status()
            ratio = margin.get("margin_usage_ratio", 0)

        extreme_call = margin.get("extreme_margin_call", False)

        level = 0
        level_name = "正常"
        actions = []
        auto_execute = False

        if extreme_call:
            # 三级互盲机制
            level = 3
            level_name = self.config.get("level_3", {}).get("name", "三级互盲机制")
            actions = self.config.get("level_3", {}).get("action", [])
            auto_execute = self.config.get("level_3", {}).get("auto_execute", True)
        elif ratio >= 0.75:
            # 二级熔断线
            level = 2
            level_name = self.config.get("level_2", {}).get("name", "二级熔断线")
            actions = self.config.get("level_2", {}).get("action", [])
            auto_execute = self.config.get("level_2", {}).get("auto_execute", True)
        elif ratio >= 0.50:
            # 一级警戒线
            level = 1
            level_name = self.config.get("level_1", {}).get("name", "一级警戒线")
            actions = self.config.get("level_1", {}).get("action", [])
            auto_execute = self.config.get("level_1", {}).get("auto_execute", True)

        # 兼容字段: can_trade / can_open / action
        can_trade = level < 2  # L0/L1 可交易(但不可开仓), L2+ 不可交易
        can_open = level == 0  # 仅正常状态可开仓
        action_str = actions[0] if actions else ("正常" if level == 0 else f"L{level}熔断")

        result = {
            "timestamp": datetime.now().isoformat(),
            "margin_usage_ratio": ratio,
            "margin_call": margin.get("margin_call", False),
            "extreme_margin_call": extreme_call,
            "level": level,
            "level_name": level_name,
            "actions": actions,
            "auto_execute": auto_execute,
            "can_trade": can_trade,
            "can_open": can_open,
            "action": action_str,
        }

        if level > 0:
            self._log_event(result)

        return result

    def _log_event(self, event: dict) -> None:
        """记录熔断事件"""
        try:
            with open(KILL_SWITCH_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError) as e:
            logger.error(f"写入熔断日志失败: {e}")

    def execute_kill_switch(self, level: int) -> dict:
        """执行熔断协议

        Args:
            level: 熔断级别 (1/2/3)

        Returns:
            {
                "executed": true,
                "level": 2,
                "actions_taken": [...],
            }

        Raises:
            RuntimeError: 当未注册 broker_callback 时触发熔断级别 >= 1,
                          防止在无真实执行通道下静默通过。
        """
        if level not in (1, 2, 3):
            return {"executed": False, "reason": "invalid_level"}

        level_cfg = self.config.get(f"level_{level}", {})
        actions = level_cfg.get("action", [])

        actions_taken = []

        # P1-1 修复: 原代码在 callback 前就标记 status="executed", callback 失败时
        # actions_taken 中仍有虚假 "executed" 记录, 误导调用方认为熔断动作已执行.
        # 改为: 先标记 "pending", callback 成功后改为 "executed", 失败时改为 "failed".
        if level == 1:
            # L1: 切断开仓权限, 进入防守模式
            actions_taken.append(
                {
                    "action": "disable_new_positions",
                    "status": "pending",
                    "note": "中控切断所有新开仓权限",
                }
            )
            actions_taken.append(
                {
                    "action": "enter_defensive_mode",
                    "status": "pending",
                    "note": "进入'只平仓不计数'防守模式",
                }
            )

        elif level == 2:
            # L2: 强平深虚值期权空头
            actions_taken.append(
                {
                    "action": "force_close_deep_otm_short",
                    "status": "pending",
                    "note": "中控强平最深虚值期权空头",
                    "positions_closed": "deepest_otm_short_calls",
                }
            )
            actions_taken.append(
                {
                    "action": "release_liquidity",
                    "status": "pending",
                    "note": "瞬间释放账户流动性",
                }
            )

        elif level == 3:
            # L3: 变现10%红利ETF, 跨品种注入
            source_etfs = level_cfg.get("source_etfs", ["512890", "515180"])
            actions_taken.append(
                {
                    "action": "liquidate_red_etf",
                    "status": "pending",
                    "note": f"日内闪电变现 10% 红利ETF: {source_etfs}",
                    "amount_liquidated": "10% of source_etfs",
                }
            )
            actions_taken.append(
                {
                    "action": "cross_asset_inject",
                    "status": "pending",
                    "note": "跨品种清算注入期权账户",
                }
            )

        # 修复 BUG-K3: callback 失败时返回 executed=False, 而非静默通过
        # 先检查执行通道是否可用
        if self._broker_callback is None:
            # fail-fast: 无实盘执行通道时, 不允许静默通过
            raise RuntimeError(
                f"Kill Switch L{level} 已触发但未注册 broker_callback. "
                f"请先调用 ks.set_broker_callback(callback) 注册实盘执行函数. "
                f"拒绝在无执行通道下静默通过熔断协议."
            )

        # 执行真实交易操作
        try:
            broker_result = self._broker_callback(level, actions)
            # P1-1 修复: callback 成功后, 将 pending 状态的 action 改为 executed
            for a in actions_taken:
                if a.get("status") == "pending":
                    a["status"] = "executed"
            actions_taken.append(
                {
                    "action": "broker_callback_executed",
                    "status": "executed",
                    "detail": broker_result,
                }
            )
            executed = True
        except Exception as e:  # broker API 异常类型不可预知, 必须 fail-closed
            logger.critical(f"Kill Switch L{level} broker callback 执行失败! 熔断协议未真正执行: {e}")
            # P1-1 修复: callback 失败时, 将 pending 状态的 action 改为 failed
            for a in actions_taken:
                if a.get("status") == "pending":
                    a["status"] = "failed"
            actions_taken.append(
                {
                    "action": "broker_callback_failed",
                    "status": "failed",
                    "error": str(e),
                }
            )
            # 关键修复: callback 失败时返回 executed=False
            return {
                "executed": False,
                "timestamp": datetime.now().isoformat(),
                "level": level,
                "level_name": level_cfg.get("name", f"Level {level}"),
                "actions_taken": actions_taken,
                "error": f"broker_callback_failed: {e}",
                "critical_note": "熔断协议未真正执行, 需人工介入!",
            }

        result = {
            "executed": executed,
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "level_name": level_cfg.get("name", f"Level {level}"),
            "actions_taken": actions_taken,
        }

        logger.warning(
            f"⚠️ 执行熔断协议 L{level}: {level_cfg.get('name', '')}, executed={executed}, 动作数={len(actions_taken)}"
        )

        return result

    def check_concentration(self, positions: dict) -> dict:
        """检查持仓集中度

        单票集中度风控阈值:
            L1 预警:  单票 >= 25% → 禁止加仓
            L2 熔断:  单票 >= 35% → 禁止开仓
            L3 强平:  单票 >= 50% → 强制减仓

        Args:
            positions: {code: {'market_value': float, ...}} 或 {code: float}

        Returns:
            {
                "level": "OK" / "L1" / "L2" / "L3",
                "max_concentration": float,
                "max_concentration_code": str,
                "action": str,
            }
        """
        CONCENTRATION_L1 = 0.25
        CONCENTRATION_L2 = 0.35
        CONCENTRATION_L3 = 0.50

        # 计算总市值 (T01 FIX: 强制 float, 防御 None 导致风控误判)
        total_value = 0.0
        pos_values: dict[str, float] = {}
        for code, pos in positions.items():
            if isinstance(pos, dict):
                raw_mv = pos.get("market_value", pos.get("est_market_value", 0))
            else:
                raw_mv = pos
            # 强制 float 转换, None/异常值一律按 0 处理 (风控保守)
            try:
                mv = float(raw_mv) if raw_mv is not None else 0.0
            except (TypeError, ValueError):
                mv = 0.0
            pos_values[code] = mv
            total_value += mv

        if total_value <= 0:
            return {"level": "OK", "max_concentration": 0, "max_concentration_code": "", "action": "无持仓"}

        # 找最大集中度 (T01 FIX: 显式 float 类型, 避免 None 运算)
        max_code = max(pos_values, key=lambda k: pos_values.get(k, 0.0))
        max_conc: float = pos_values[max_code] / total_value

        if max_conc >= CONCENTRATION_L3:
            level = "L3"
            action = f"单票 {max_code} 集中度 {max_conc:.1%} >= 50%, 强制减仓"
        elif max_conc >= CONCENTRATION_L2:
            level = "L2"
            action = f"单票 {max_code} 集中度 {max_conc:.1%} >= 35%, 禁止开仓"
        elif max_conc >= CONCENTRATION_L1:
            level = "L1"
            action = f"单票 {max_code} 集中度 {max_conc:.1%} >= 25%, 禁止加仓"
        else:
            level = "OK"
            action = "正常"

        return {
            "level": level,
            "max_concentration": round(max_conc, 4),
            "max_concentration_code": max_code,
            "action": action,
        }

    def get_event_history(self, days: int = 30) -> list[dict]:
        """获取最近 N 天的熔断事件历史

        Args:
            days: 回看天数

        Returns:
            事件列表
        """
        if not KILL_SWITCH_LOG.exists():
            return []

        records = []
        cutoff = datetime.now().timestamp() - days * 86400

        try:
            with open(KILL_SWITCH_LOG, encoding="utf-8") as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        ts = record.get("timestamp", "")
                        if ts:
                            dt = datetime.fromisoformat(ts)
                            if dt.timestamp() >= cutoff:
                                records.append(record)
                    except (ValueError, KeyError, TypeError):
                        continue
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass

        return records


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="三级熔断协议")
    parser.add_argument("--check", action="store_true", help="检查保证金状态")
    parser.add_argument("--execute", type=int, choices=[1, 2, 3], help="执行熔断")
    parser.add_argument("--history", type=int, default=30, help="查看历史(天)")
    args = parser.parse_args()

    ks = KillSwitch()

    if args.check or (not args.execute and not args.history):
        status = ks.check_margin_status()
        logger.info(json.dumps(status, ensure_ascii=False, indent=2))
        if status["level"] > 0:
            logger.info(f"\n⚠️ 熔断级别: L{status['level']} - {status['level_name']}")
            for a in status["actions"]:
                logger.info(f"  - {a}")

    if args.execute:
        exec_result = ks.execute_kill_switch(args.execute)
        logger.info(json.dumps(exec_result, ensure_ascii=False, indent=2))

    if args.history:
        history = ks.get_event_history(args.history)
        logger.info(f"\n最近 {args.history} 天熔断事件: {len(history)} 次")
        for r in history:
            logger.info(f"  {r.get('timestamp', 'N/A')} - L{r.get('level', 0)} {r.get('level_name', '')}")
