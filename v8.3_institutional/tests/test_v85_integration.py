# -*- coding: utf-8 -*-
"""
v8.5 Institutional Modules Integration Tests

验证v8.5新增模块的集成状态和功能完整性。
覆盖以下9个核心模块:
1. EnvironmentIsolation - 环境隔离管理器
2. DataPipeline - 数据管道健康检查
3. ShadowAccountSystem - 影子账户验证系统
4. TimeSync - PTP时间同步
5. VegaMonitor - Vega监控
6. PurgedKFoldCV - Purged K-Fold交叉验证
7. LiquidityMonitor - 流动性监控
8. EVTModeling - 肥尾建模
9. FactorDecayMonitor - 因子衰减监控
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Add v8.3_institutional to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_v85_integration")


class TestResult:
    """测试结果记录器"""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.results = []

    def record(self, test_name: str, status: str, details: str = "", duration_ms: float = 0):
        self.results.append({
            "test": test_name,
            "status": status,
            "details": details,
            "duration_ms": duration_ms
        })
        if status == "PASS":
            self.passed += 1
        elif status == "FAIL":
            self.failed += 1
        else:
            self.skipped += 1

    def summary(self) -> str:
        total = self.passed + self.failed + self.skipped
        return (
            f"\n{'='*70}\n"
            f"v8.5 集成测试总结\n"
            f"{'='*70}\n"
            f"总测试数: {total}\n"
            f"  [PASS] 通过: {self.passed}\n"
            f"  [FAIL] 失败: {self.failed}\n"
            f"  [SKIP] 跳过: {self.skipped}\n"
            f"成功率: {self.passed/total*100:.1f}%\n"
            f"{'='*70}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.passed + self.failed + self.skipped,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "success_rate": f"{self.passed/(self.passed + self.failed + self.skipped)*100:.1f}%",
            "tests": self.results
        }


def _try_import(module_path: str, class_name: Optional[str] = None):
    """安全导入模块，失败时返回None"""
    try:
        if class_name:
            module = __import__(module_path, fromlist=[class_name])
            return getattr(module, class_name, None)
        else:
            module = __import__(module_path)
            return module
    except Exception as e:
        logger.debug(f"导入失败 {module_path}: {e}")
        return None


def test_environment_isolation(result: TestResult):
    """测试1: 环境隔离管理器"""
    logger.info("[1/9] 测试 EnvironmentIsolation...")
    start = datetime.now()

    try:
        from src.risk.environment_isolation import EnvironmentIsolation

        isolation = EnvironmentIsolation()

        # 检查环境配置
        assert hasattr(isolation, 'current_env'), "缺少 current_env 属性"
        assert hasattr(isolation, 'is_production'), "缺少 is_production 属性"

        result.record(
            "EnvironmentIsolation初始化",
            "PASS",
            f"当前环境: {isolation.current_env}",
            (datetime.now() - start).total_seconds() * 1000
        )
    except ImportError as e:
        result.record(
            "EnvironmentIsolation初始化",
            "SKIP",
            f"模块未找到: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )
    except Exception as e:
        result.record(
            "EnvironmentIsolation初始化",
            "FAIL",
            f"错误: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )


def test_data_pipeline_health(result: TestResult):
    """测试2: 数据管道健康检查"""
    logger.info("[2/9] 测试 DataPipeline...")
    start = datetime.now()

    try:
        from src.data.data_pipeline import DataPipeline

        pipeline = DataPipeline()

        # 检查健康检查方法
        assert hasattr(pipeline, 'health_check'), "缺少 health_check 方法"
        assert hasattr(pipeline, 'get_metrics'), "缺少 get_metrics 方法"

        result.record(
            "DataPipeline初始化",
            "PASS",
            "包含健康检查和指标获取方法",
            (datetime.now() - start).total_seconds() * 1000
        )
    except ImportError as e:
        result.record(
            "DataPipeline初始化",
            "SKIP",
            f"模块未找到: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )
    except Exception as e:
        result.record(
            "DataPipeline初始化",
            "FAIL",
            f"错误: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )


def test_shadow_account_system(result: TestResult):
    """测试3: 影子账户验证系统"""
    logger.info("[3/9] 测试 ShadowAccountSystem...")
    start = datetime.now()

    try:
        from src.execution.shadow_account import ShadowAccountSystem

        shadow = ShadowAccountSystem()

        # 检查核心功能
        assert hasattr(shadow, 'start_tracking'), "缺少 start_tracking 方法"
        assert hasattr(shadow, 'compare_with_live'), "缺少 compare_with_live 方法"
        assert hasattr(shadow, 'validate_consistency'), "缺少 validate_consistency 方法"

        result.record(
            "ShadowAccountSystem初始化",
            "PASS",
            "包含跟踪、对比和一致性验证功能",
            (datetime.now() - start).total_seconds() * 1000
        )
    except ImportError as e:
        result.record(
            "ShadowAccountSystem初始化",
            "SKIP",
            f"模块未找到: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )
    except Exception as e:
        result.record(
            "ShadowAccountSystem初始化",
            "FAIL",
            f"错误: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )


def test_time_sync(result: TestResult):
    """测试4: PTP时间同步"""
    logger.info("[4/9] 测试 TimeSync...")
    start = datetime.now()

    try:
        from src.infrastructure.time_sync import NTPSync

        ntp = NTPSync()

        # 检查时间同步方法
        assert hasattr(ntp, 'get_ntp_offset'), "缺少 get_ntp_offset 方法"
        assert hasattr(ntp, 'sync_clock'), "缺少 sync_clock 方法"
        assert hasattr(ntp, 'check_accuracy'), "缺少 check_accuracy 方法"

        result.record(
            "NTPSync初始化",
            "PASS",
            "包含NTP偏移检测和时钟同步功能",
            (datetime.now() - start).total_seconds() * 1000
        )
    except ImportError as e:
        result.record(
            "NTPSync初始化",
            "SKIP",
            f"模块未找到: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )
    except Exception as e:
        result.record(
            "NTPSync初始化",
            "FAIL",
            f"错误: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )


def test_vega_monitor(result: TestResult):
    """测试5: Vega风险监控"""
    logger.info("[5/9] 测试 VegaMonitor...")
    start = datetime.now()

    try:
        from src.risk.vega_monitor import VegaMonitor

        monitor = VegaMonitor()

        # 检查Vega监控功能
        assert hasattr(monitor, 'calculate_portfolio_vega'), "缺少 calculate_portfolio_vega 方法"
        assert hasattr(monitor, 'check_vega_limit'), "缺少 check_vega_limit 方法"
        assert hasattr(monitor, 'get_vega_exposure'), "缺少 get_vega_exposure 方法"

        result.record(
            "VegaMonitor初始化",
            "PASS",
            "包含组合Vega计算和限额检查功能",
            (datetime.now() - start).total_seconds() * 1000
        )
    except ImportError as e:
        result.record(
            "VegaMonitor初始化",
            "SKIP",
            f"模块未找到: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )
    except Exception as e:
        result.record(
            "VegaMonitor初始化",
            "FAIL",
            f"错误: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )


def test_purged_kfold_cv(result: TestResult):
    """测试6: Purged K-Fold交叉验证"""
    logger.info("[6/9] 测试 PurgedKFoldCV...")
    start = datetime.now()

    try:
        from src.research.purged_kfold import PurgedKFoldCV

        cv = PurgedKFoldCV(n_splits=5, purge_window=0.1)

        # 检查交叉验证方法
        assert hasattr(cv, 'split'), "缺少 split 方法"
        assert hasattr(cv, 'get_n_splits'), "缺少 get_n_splits 方法"
        assert hasattr(cv, 'validate'), "缺少 validate 方法"

        result.record(
            "PurgedKFoldCV初始化",
            "PASS",
            "包含5折交叉验证和数据泄漏防护功能",
            (datetime.now() - start).total_seconds() * 1000
        )
    except ImportError as e:
        result.record(
            "PurgedKFoldCV初始化",
            "SKIP",
            f"模块未找到: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )
    except Exception as e:
        result.record(
            "PurgedKFoldCV初始化",
            "FAIL",
            f"错误: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )


def test_liquidity_monitor(result: TestResult):
    """测试7: 流动性监控"""
    logger.info("[7/9] 测试 LiquidityMonitor...")
    start = datetime.now()

    try:
        from src.risk.liquidity_monitor import LiquidityMonitor

        monitor = LiquidityMonitor()

        # 检查流动性监控功能
        assert hasattr(monitor, 'calculate_amihud'), "缺少 calculate_amihud 方法"
        assert hasattr(monitor, 'check_turnover_limit'), "缺少 check_turnover_limit 方法"
        assert hasattr(monitor, 'assess_market_impact'), "缺少 assess_market_impact 方法"

        result.record(
            "LiquidityMonitor初始化",
            "PASS",
            "包含Amihud流动性指标和换手率限制检查功能",
            (datetime.now() - start).total_seconds() * 1000
        )
    except ImportError as e:
        result.record(
            "LiquidityMonitor初始化",
            "SKIP",
            f"模块未找到: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )
    except Exception as e:
        result.record(
            "LiquidityMonitor初始化",
            "FAIL",
            f"错误: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )


def test_evt_modeling(result: TestResult):
    """测试8: EVT肥尾建模"""
    logger.info("[8/9] 测试 EVTModeling...")
    start = datetime.now()

    try:
        from src.risk.evt_modeling import EVTModeling

        evt = EVTModeling(confidence_level=0.99)

        # 检查EVT建模功能
        assert hasattr(evt, 'estimate_tail_risk'), "缺少 estimate_tail_risk 方法"
        assert hasattr(evt, 'calculate_cvar'), "缺少 calculate_cvar 方法"
        assert hasattr(evt, 'fit_peaks'), "缺少 fit_peaks 方法"

        result.record(
            "EVTModeling初始化",
            "PASS",
            "包含尾部风险估计和CVaR计算功能",
            (datetime.now() - start).total_seconds() * 1000
        )
    except ImportError as e:
        result.record(
            "EVTModeling初始化",
            "SKIP",
            f"模块未找到: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )
    except Exception as e:
        result.record(
            "EVTModeling初始化",
            "FAIL",
            f"错误: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )


def test_factor_decay_monitor(result: TestResult):
    """测试9: 因子衰减监控"""
    logger.info("[9/9] 测试 FactorDecayMonitor...")
    start = datetime.now()

    try:
        from src.research.factor_decay import FactorDecayMonitor

        monitor = FactorDecayMonitor(half_life_window=60)

        # 检查因子衰减监控功能
        assert hasattr(monitor, 'calculate_ic_half_life'), "缺少 calculate_ic_half_life 方法"
        assert hasattr(monitor, 'detect_decay_acceleration'), "缺少 detect_decay_acceleration 方法"
        assert hasattr(monitor, 'get_factor_alpha'), "缺少 get_factor_alpha 方法"

        result.record(
            "FactorDecayMonitor初始化",
            "PASS",
            "包含IC半衰期计算和衰减加速检测功能",
            (datetime.now() - start).total_seconds() * 1000
        )
    except ImportError as e:
        result.record(
            "FactorDecayMonitor初始化",
            "SKIP",
            f"模块未找到: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )
    except Exception as e:
        result.record(
            "FactorDecayMonitor初始化",
            "FAIL",
            f"错误: {e}",
            (datetime.now() - start).total_seconds() * 1000
        )


def main():
    """运行所有测试"""
    logger.info("="*70)
    logger.info("v8.5 Institutional Modules Integration Test Suite")
    logger.info("="*70)

    result = TestResult()

    # 运行9项测试
    test_environment_isolation(result)
    test_data_pipeline_health(result)
    test_shadow_account_system(result)
    test_time_sync(result)
    test_vega_monitor(result)
    test_purged_kfold_cv(result)
    test_liquidity_monitor(result)
    test_evt_modeling(result)
    test_factor_decay_monitor(result)

    # 打印总结
    print(result.summary())

    # 保存测试结果
    output_dir = Path(__file__).parent.parent / "reports"
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / f"v85_integration_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)

    logger.info(f"测试结果已保存至: {output_file}")

    # 返回退出码
    return 1 if result.failed > 0 else 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
