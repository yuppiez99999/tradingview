"""
v7.5 Institutional — 主入口
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL

日度运行：风控检查 → Alpha 信号 → 对冲评估 → 执行
"""
import argparse
import logging
import os
import sys
from datetime import datetime
from typing import Optional

import yaml

# 添加 src 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# 优先加载项目根目录 .env，确保 WIND / VOLCENGINE 等密钥在导入业务模块前生效
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_PATH = os.path.join(_PROJECT_ROOT, '.env')
try:
    from dotenv import load_dotenv
    load_dotenv(_ENV_PATH, override=False)
except ImportError:
    # python-dotenv 不可用时的手动后备解析
    try:
        if os.path.exists(_ENV_PATH):
            with open(_ENV_PATH, encoding='utf-8') as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line or _line.startswith('#') or '=' not in _line:
                        continue
                    _key, _, _value = _line.partition('=')
                    _key = _key.strip()
                    _value = _value.strip().strip("\"'")
                    if _key and _key not in os.environ:
                        os.environ[_key] = _value
    except (OSError, UnicodeDecodeError, ValueError):
        pass

from alpha.factor_library import FactorLibrary  # noqa: E402
from alpha.signal_fusion import SignalFusion  # noqa: E402
from alpha.signal_generator import SignalGenerator  # noqa: E402
from execution.algo_engine import AlgoEngine  # noqa: E402
from execution.broker_api import SimulatedBroker  # noqa: E402
from execution.ntp_sync import NTPSync  # noqa: E402
from execution.smart_order_router import MockBroker, SmartOrderRouter  # noqa: E402
from hedging.hedge_coordinator import HedgeCoordinator  # noqa: E402
from risk.circuit_breaker import CircuitBreaker, SlippageCircuitBreaker  # noqa: E402
from risk.risk_budgeter import RiskBudgeter  # noqa: E402
from risk.risk_manager import RiskManager  # noqa: E402
from risk.stress_tester import StressTester  # noqa: E402

# 日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), 'logs', 'system.log'),
            encoding='utf-8'
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('v7.5_main')


class V75InstitutionalSystem:
    """v7.5 机构级交易系统主控制器"""

    def __init__(self, config_dir: Optional[str] = None):
        if config_dir is None:
            config_dir = os.path.join(os.path.dirname(__file__), 'config')

        self.config_dir = config_dir
        self.config = self._load_configs()

        # 初始化模块
        self._init_modules()

        logger.info("v7.5 Institutional System 初始化完成")

    def _load_configs(self) -> dict:
        """加载全部 YAML 配置 (P1-Q8: 优先使用 ConfigManager, 失败回退到本地加载)

        优先级:
            1. ConfigManager 统一入口 (支持 QUANT_CONFIG_DIR 环境变量覆盖)
            2. 显式 config_dir (默认 v8.3_institutional/config/, 唯一事实源)
        """
        # 路径 1: 尝试通过 ConfigManager 统一加载 (享受缓存 + 环境变量覆盖)
        try:
            # 确保 utils 模块可访问
            if _PROJECT_ROOT not in sys.path:
                sys.path.insert(0, _PROJECT_ROOT)
            from utils.config_manager import get_config

            configs = {}
            for name in ['settings', 'portfolio', 'execution', 'backtest', 'risk_budget']:
                cfg = get_config(name)
                if cfg:
                    configs[name] = cfg
                else:
                    # ConfigManager 未找到, 回退到本地 config_dir
                    path = os.path.join(self.config_dir, f'{name}.yaml')
                    try:
                        with open(path, encoding='utf-8') as f:
                            configs[name] = yaml.safe_load(f)
                    except (FileNotFoundError, yaml.YAMLError, OSError) as e:
                        logger.error(f"加载配置 {name}.yaml 失败: {e}")
                        configs[name] = {}
            return configs
        except ImportError:
            # utils.config_manager 不可访问, 使用原有逻辑
            pass
        except Exception as e:
            logger.warning(f"ConfigManager 加载失败, 回退到本地加载: {e}")

        # 路径 2: 原有逻辑 (config_dir 直接读取, 向后兼容)
        configs = {}
        for name in ['settings', 'portfolio', 'execution', 'backtest', 'risk_budget']:
            path = os.path.join(self.config_dir, f'{name}.yaml')
            try:
                with open(path, encoding='utf-8') as f:
                    configs[name] = yaml.safe_load(f)
            except (FileNotFoundError, yaml.YAMLError, OSError) as e:
                logger.error(f"加载配置 {name}.yaml 失败: {e}")
                configs[name] = {}
        return configs

    def _init_modules(self):
        """初始化所有模块"""
        settings = self.config.get('settings', {})
        risk_cfg = settings.get('risk', {})
        hedge_cfg = settings.get('hedging', {})
        exec_cfg = settings.get('execution', {})
        sys_cfg = settings.get('system', {})

        self.capital = sys_cfg.get('base_capital', 5_000_000)
        self.max_dd = sys_cfg.get('max_drawdown', 0.15)

        # 动态目标收益率(替代硬编码0.08)
        from src.config.dynamic_target import get_dynamic_target_from_config
        target_config = sys_cfg.get('target_return_config', {})
        self.target_return_calculator = get_dynamic_target_from_config(target_config)
        self.target_return = self.target_return_calculator.get_target_for_risk_manager()

        # --- 时间同步 ---
        self.ntp = NTPSync(
            server=exec_cfg.get('ntp_server', 'pool.ntp.org'),
            max_drift_ms=exec_cfg.get('ntp_max_drift_ms', 50),
        )

        # --- 风险模块 ---
        self.risk_budgeter = RiskBudgeter(
            total_capital=self.capital,
            target_return=self.target_return,
            max_dd=self.max_dd,
            single_trade_risk=risk_cfg.get('single_trade_risk', 0.015),
        )

        self.risk_manager = RiskManager(
            total_capital=self.capital,
            target_return=self.target_return,
            max_dd=self.max_dd,
            single_trade_risk=risk_cfg.get('single_trade_risk', 0.015),
        )

        self.circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            cooldown_seconds=300,
        )
        self.slip_cb = SlippageCircuitBreaker()

        self.stress_tester = StressTester()

        # --- 对冲模块 ---
        self.hedge_coordinator = HedgeCoordinator(
            max_total_hedge_pct=hedge_cfg.get('max_total_hedge_pct', 0.40),
        )

        # --- 执行模块 ---
        self.broker = SimulatedBroker(initial_capital=self.capital)

        # SOR 参数
        exec_cfg = self.config.get('execution', {})
        slip_cfg = exec_cfg.get('slippage', {})
        exec_cfg.get('sor', {})

        # MockBroker 需要价格字典
        self.mock_broker = MockBroker(price_dict={
            '600519.SH': 1800.0, '000858.SZ': 160.0, '300750.SZ': 240.0,
            '601318.SH': 60.0, '600036.SH': 42.0,
            '510300.SH': 4.0, '518880.SH': 5.0,
        })

        self.sor = SmartOrderRouter(
            broker=self.mock_broker,
            ntp=self.ntp,
            slippage_break=slip_cfg.get('per_trade_break', 0.005),
            daily_slippage_break=slip_cfg.get('daily_break', 0.010),
            global_slow_threshold=slip_cfg.get('global_slow_threshold', 0.003),
            pause_minutes=30,
        )

        self.algo_engine = AlgoEngine(
            sor=self.sor,
            config_path=os.path.join(self.config_dir, 'execution.yaml'),
        )

        # --- Alpha 模块 ---
        self.factor_lib = FactorLibrary()
        self.signal_gen = SignalGenerator(self.factor_lib)
        self.signal_fusion = SignalFusion()

    # ========== 日度运行流程 ==========

    def morning_routine(self) -> dict:
        """盘前准备"""
        logger.info("=" * 50)
        logger.info("盘前准备开始")

        # 1. NTP 时间同步
        self.ntp.sync()
        logger.info(f"NTP 同步: offset={self.ntp.get_offset():.3f}s")

        # 2. 风控状态检查
        status = {
            'timestamp': self.ntp.server_ts().isoformat(),
            'ntp_offset': self.ntp.get_offset(),
        }

        # 3. 熔断检查
        if self.risk_budgeter.mode == 'CIRCUIT_BREAKER':
            if self.risk_budgeter.circuit_break_until and \
               datetime.now() < self.risk_budgeter.circuit_break_until:
                logger.warning("熔断期间，暂停交易")
                status['action'] = 'HALT'
                return status

        # 4. 数据源可用性
        # (此处可接入 Wind / iFinD 检查)

        logger.info(f"盘前状态: {status.get('action', 'READY')}")
        return status

    def trading_session(self,
                        positions: dict,
                        market_data: dict,
                        vix: float = 20.0) -> list:
        """
        交易时段主逻辑

        Args:
            positions: 当前持仓 {symbol: qty}
            market_data: 行情数据 {symbol: DataFrame[OHLCV]}
            vix: VIX / 波动率指数

        Returns:
            执行订单列表
        """
        logger.info("交易时段开始")

        all_orders = []

        # 1. 风控周期
        equity = self._calc_equity(positions, market_data)
        risk_result = self.risk_manager.run_risk_cycle(
            equity=equity,
            positions=positions,
            market_data=market_data,
            vix=vix,
            ts=datetime.now()
        )
        logger.info(f"风控结果: mode={risk_result.get('mode')}")
        all_orders.extend(risk_result.get('hedge_orders', []))

        # 2. Alpha 信号
        if risk_result.get('mode') in ('NORMAL',):
            signals = self._generate_signals(market_data)
            signal_orders = self._signals_to_orders(signals, positions)
            all_orders.extend(signal_orders)

        # 3. 执行
        if all_orders:
            fills = self.algo_engine.execute_batch(all_orders)
            logger.info(f"执行完成: {len(fills)} 笔成交")
            return fills

        logger.info("无待执行订单")
        return []

    def closing_routine(self):
        """盘后清理"""
        logger.info("盘后清理开始")
        self.algo_engine.end_of_day()
        self.slip_cb.reset()
        logger.info("盘后清理完成")

    # ---------- 内部方法 ----------

    def _calc_equity(self, positions: dict, market_data: dict) -> float:
        total = self.capital
        for symbol, qty in positions.items():
            price = 0
            if symbol in market_data:
                df = market_data[symbol]
                if 'close' in df:
                    price = df['close'].iloc[-1]
                elif len(df.columns) > 0:
                    price = df.iloc[-1, 0]
            total += qty * price
        return total

    def _generate_signals(self, market_data: dict) -> dict:
        """生成 Alpha 信号"""
        try:
            factor_matrix = self.factor_lib.build_all_factors(market_data)
            signal = self.signal_gen.generate(factor_matrix, retrain=False)
            return {'signal': signal, 'latest': self.signal_gen.latest_signals}
        except (ValueError, RuntimeError, KeyError):
            logger.exception("信号生成失败, 完整堆栈:")
            return {}

    def _signals_to_orders(self, signals: dict, positions: dict) -> list:
        """信号转换为订单"""
        orders = []
        if not signals or 'latest' not in signals:
            return orders

        for symbol, signal_val in signals['latest'].items():
            # 从因子名解析标的
            actual_symbol = symbol.split('_')[0] if '_' in symbol else symbol
            if actual_symbol not in positions:
                continue

            if signal_val > 0.3:
                orders.append({
                    'symbol': actual_symbol,
                    'qty': 100,
                    'side': 'BUY',
                    'decision_price': 0,
                    'algo': 'TWAP',
                })
            elif signal_val < -0.3:
                orders.append({
                    'symbol': actual_symbol,
                    'qty': 100,
                    'side': 'SELL',
                    'decision_price': 0,
                    'algo': 'TWAP',
                })

        return orders

    # ---------- 压力测试 ----------

    def run_stress_tests(self, positions: dict, prices: dict) -> dict:
        """运行三段合规压力测试"""
        from backtest.scenario_lib import ScenarioLibrary

        lib = ScenarioLibrary()
        results = lib.run_compliance_tests(positions, prices)
        all_pass, needs_cro, _details = lib.check_pass_criteria(results)

        logger.info(f"压力测试: all_passed={all_pass}, CRO签字={needs_cro}")

        if not all_pass:
            logger.error("压力测试未通过！需要重新调整参数！")
            for name, r in results.items():
                if not r['passed']:
                    logger.error(f"  {name}: DD={r['max_dd']:.2%} 超限")

        return {
            'all_passed': all_pass,
            'needs_cro_signoff': needs_cro,
            'details': results,
        }


# ========== CLI ==========

def main():
    parser = argparse.ArgumentParser(description='v7.5 Institutional Trading System')
    parser.add_argument('--config', type=str, default=None, help='配置目录路径')
    parser.add_argument('--mode', type=str, default='run',
                        choices=['run', 'stress', 'backtest', 'check'],
                        help='运行模式')
    parser.add_argument('--capital', type=float, default=5_000_000, help='资金规模')
    parser.add_argument('--real-broker', action='store_true',
                        help='启用同花顺客户端真实下单（期货/期权）')
    args = parser.parse_args()

    system = V75InstitutionalSystem(config_dir=args.config)

    if args.mode == 'check':
        status = system.morning_routine()
        print(f"系统状态: {status.get('action', 'OK')}")
        print(f"NTP Offset: {system.ntp.get_offset():.3f}s")
        print(f"风险模式: {system.risk_budgeter.mode}")
        print(f"电路状态: {system.circuit_breaker._states}")

    elif args.mode == 'stress':
        # 使用默认持仓做压力测试
        prices = {
            '600519.SH': 1800, '000858.SZ': 160, '300750.SZ': 240,
            '601318.SH': 60, '600036.SH': 42,
            '510300.SH': 4.0, '518880.SH': 5.0,
        }
        positions = {
            '600519.SH': 500, '000858.SZ': 3000, '300750.SZ': 2000,
            '601318.SH': 5000, '600036.SH': 10000,
            '510300.SH': 20000, '518880.SH': 30000,
        }
        result = system.run_stress_tests(positions, prices)
        print("=== 压力测试结果 ===")
        for name, r in result['details'].items():
            status = "PASS" if r['passed'] else "FAIL"
            print(f"  {name}: DD={r['max_dd']:.2%} [{status}]")

    elif args.mode == 'run':
        status = system.morning_routine()
        if status.get('action') == 'HALT':
            print("系统暂停: 熔断中")
            return

        # 模拟交易会话
        import numpy as np
        import pandas as pd

        np.random.seed(42)
        dates = pd.date_range('2026-06-01', '2026-07-04', freq='B')
        mock_data = {
            '600519.SH': pd.DataFrame({'close': 1800 + np.cumsum(np.random.randn(len(dates)) * 5)}, index=dates),
            '000858.SZ': pd.DataFrame({'close': 160 + np.cumsum(np.random.randn(len(dates)) * 0.5)}, index=dates),
            '300750.SZ': pd.DataFrame({'close': 240 + np.cumsum(np.random.randn(len(dates)) * 1.5)}, index=dates),
        }
        mock_positions = {'600519.SH': 500, '000858.SZ': 3000, '300750.SZ': 2000}

        fills = system.trading_session(mock_positions, mock_data, vix=22)
        print(f"成交数量: {len(fills)}")

        system.closing_routine()

    elif args.mode == 'backtest':
        print("回测模式: 使用 Walk-Forward Analysis")
        # 此处可接入 WalkForward.run()


if __name__ == '__main__':
    main()
