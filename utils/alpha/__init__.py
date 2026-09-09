"""L3 Alpha 层 — 因子/信号/ML/LLM/决策理论.

模块整合 8.4 — ARCHITECTURE §2.1
当前阶段：T4.2 已完成, 准备进入 T4.3 (整合 managers.py 组合优化/大宗/ETF)
已完成：
  - T2.1: llm_router.py（多模型路由器, 4 provider fallback 链, 54 单测 PASS）
  - T2.2: decision_theories.py（四大决策理论引擎 + 融合, 84.72% 覆盖率, 54 单测 PASS）
  - T2.3: multi_factor_signal.py（IC 加权融合 + 反向信号处理, 92.68% 覆盖率, 52 单测 PASS）
  - T4.1: fast_backtest.py（ML 回测验证引擎, 65 单测 PASS, 2.23s）
  - T4.2: ml_enhanced_selector.py（numpy 逻辑回归 + joblib 持久化, 45 单测 PASS, 94.89% 覆盖率）
后续任务：
  - T2.4: 启动 Shadow 准入流程（14 天观察期, P0 阻塞 Phase 3）
  - T4.3: 整合 managers.py（组合优化/大宗/ETF）
  - T4.4: 整合宏观与行业轮动模块
  - 后续迁移: factor_library / factor_evaluator / gtja191_factors / signal_fusion
"""

from __future__ import annotations

# Re-export API (按需显式导入, 避免循环依赖)
# T2.1: from utils.alpha.llm_router import LLMRouter, chat, chat_deep, test_connection, list_providers, reload
# T2.2: from utils.alpha.decision_theories import TheoryFusionEngine, SorosReflexivityEngine, DalioEconomicMachine,
# FirstPrinciplesAnalyzer, BuffettMungerFramework
# T2.3: from utils.alpha.multi_factor_signal import MultiFactorSignal, combine_factors, detect_inverted_factors,
# FactorICMetrics, CombinationResult

# P1-2 兼容 re-export (P0-3 2026-09-01 改为 PEP 562 懒加载):
# qlib_signal_adapter 实际位于 ms_strategy/src/alpha, 当 utils 路径遮蔽 ms_strategy/src 时
# (sys.path 顺序), from alpha import qlib_signal_adapter 会解析到本包 (utils/alpha) 却
# 找不到子模块. 首次访问时加载并注册为 alpha.qlib_signal_adapter, 使两个 alpha 包都能
# 访问该子模块, 保持审计前视偏差测试可运行.
# 为何懒加载: 该适配器模块级 import qlib + torch + lightgbm (~5s), 原实现 eager 加载导致
# 任何 import utils.alpha.* (含 LLM 路由等纯网络模块) 都被拖慢 — generate_daily_report
# import 耗时 22.7s.


def __getattr__(name: str):
    if name == 'qlib_signal_adapter':
        # 注意: 不能用 from . import qlib_signal_adapter — from-import 会
        # getattr 本包从而再次触发 __getattr__ 造成无限递归, 故用 find_spec
        import importlib
        import importlib.util as _ilu
        import sys as _sys
        from pathlib import Path as _Path

        _spec = _ilu.find_spec('utils.alpha.qlib_signal_adapter')
        if _spec is not None:
            _mod = importlib.import_module('utils.alpha.qlib_signal_adapter')
        else:
            _qsa_path = (
                _Path(__file__).resolve().parents[2]
                / 'ms_strategy' / 'src' / 'alpha' / 'qlib_signal_adapter.py'
            )
            if not _qsa_path.exists():
                raise AttributeError(
                    f'module {__name__!r} has no attribute {name!r}'
                ) from None
            _spec = _ilu.spec_from_file_location('alpha.qlib_signal_adapter', _qsa_path)
            if _spec is None:
                raise AttributeError(
                    f'module {__name__!r} has no attribute {name!r}'
                ) from None
            _loader = _spec.loader
            if _loader is None:
                raise AttributeError(
                    f'module {__name__!r} has no attribute {name!r}'
                ) from None
            _mod = _ilu.module_from_spec(_spec)
            _sys.modules['alpha.qlib_signal_adapter'] = _mod
            _loader.exec_module(_mod)
        globals()['qlib_signal_adapter'] = _mod
        return _mod
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
