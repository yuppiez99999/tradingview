"""WorkflowContext — Phase 共享上下文。

承载 DailyWorkflow 实例的共享状态, 供拆分出的 phase 子模块使用。
设计原则 (零行为变更, 见 cairn/refactoring-standards.md §1):

- 稳定属性 (标量/共享对象) 在构造时从 wf 复制/引用
- 动态属性 (rm/cb/ntp, phase_check 创建) 通过 property 代理到 wf,
  保证跨 phase 可见, 且 hasattr(ctx, "cb") 与 hasattr(wf, "cb") 语义一致
- 未列出的属性 (如 _get_portfolio_positions_for_stress_test) 通过
  __getattr__ 代理到 wf, 保证 phase_market 的 hasattr 调用生效
- 模块级常量/类/函数 (V75_READY, NTPSync 等) 通过 get_dw_module() 获取,
  兼容 daily_workflow 作为 __main__ 或模块导入两种运行方式
"""
from __future__ import annotations

import logging
import sys
from types import ModuleType
from typing import Any


def get_dw_module() -> ModuleType | None:
    """定位 daily_workflow 模块 (兼容 __main__/模块导入两种运行方式)。

    phase 子模块在 import 时调用 (此时 daily_workflow 已完整加载),
    用于获取 V75_READY/NTPSync/_run_calibration 等模块级符号, 避免循环导入。

    匹配条件: 同时拥有 V75_READY + DailyWorkflow 属性的 sys.modules 条目。
    """
    # 优先按常见模块名查找
    for name in ("daily_workflow", "__main__"):
        m = sys.modules.get(name)
        if m is not None and hasattr(m, "V75_READY") and hasattr(m, "DailyWorkflow"):
            return m
    # 兜底: 扫描 sys.modules (处理包导入如 v8_3_institutional.daily_workflow)
    for m in sys.modules.values():
        if m is not None and hasattr(m, "V75_READY") and hasattr(m, "DailyWorkflow"):
            return m
    return None


class WorkflowContext:
    """Phase 共享上下文 — 承载 DailyWorkflow 的共享状态。

    覆盖的属性 (phase_check/calibrate/market/autolearn 实际用到):
    - 标量: trade_date, capital, dry_run, sim_mode, external_reports_dir
    - 共享对象: config, state, trade_plan, phase_manager, current_phase_info,
              fusion_config, ifind_analyzer, signal_fusion, data_quality_monitor
    - phase_check 创建 (跨 phase 共享): rm, cb, ntp
    - 方法 (phase_market 通过 hasattr 调用): _get_portfolio_positions_for_stress_test
    """

    def __init__(self, wf: Any) -> None:
        # 用 object.__setattr__ 绕过 property setter, 存储 wf 引用
        object.__setattr__(self, "_wf", wf)
        # 稳定属性 — 从 wf 复制/引用
        self.trade_date = wf.trade_date
        self.capital = wf.capital
        self.dry_run = wf.dry_run
        self.sim_mode = wf.sim_mode
        self.external_reports_dir = wf.external_reports_dir
        self.config = wf.config
        self.state = wf.state  # 共享可变对象 — 同一引用, 原地修改即传播
        self.trade_plan = wf.trade_plan
        self.phase_manager = wf.phase_manager
        self.current_phase_info = wf.current_phase_info
        self.fusion_config = wf.fusion_config
        self.ifind_analyzer = wf.ifind_analyzer
        self.signal_fusion = wf.signal_fusion
        self.data_quality_monitor = wf.data_quality_monitor

    # === phase_check 创建的实例属性 (跨 phase 共享, 通过 wf 代理) ===
    # hasattr(ctx, "cb") 与 hasattr(wf, "cb") 语义一致:
    #   wf 未设置 cb 时, property getter 抛 AttributeError → hasattr 返回 False
    #   (phase_market 的 `if not hasattr(self, "cb")` 懒初始化逻辑依赖此语义)

    @property
    def rm(self) -> Any:
        if not hasattr(self._wf, "rm"):
            raise AttributeError("rm")
        return self._wf.rm

    @rm.setter
    def rm(self, value: Any) -> None:
        self._wf.rm = value

    @property
    def cb(self) -> Any:
        if not hasattr(self._wf, "cb"):
            raise AttributeError("cb")
        return self._wf.cb

    @cb.setter
    def cb(self, value: Any) -> None:
        self._wf.cb = value

    @property
    def ntp(self) -> Any:
        if not hasattr(self._wf, "ntp"):
            raise AttributeError("ntp")
        return self._wf.ntp

    @ntp.setter
    def ntp(self, value: Any) -> None:
        self._wf.ntp = value

    @property
    def logger(self) -> logging.Logger:
        """模块级 logger (与 daily_workflow.py L72 一致)"""
        return logging.getLogger("v75.daily_workflow")

    def __getattr__(self, name: str) -> Any:
        """未在 ctx 显式定义的属性 — 代理到 wf。

        覆盖场景:
        - phase_market 的 hasattr(ctx, "_get_portfolio_positions_for_stress_test")
          → getattr(wf, "_get_portfolio_positions_for_stress_test") (bound method)
        - 未来 phase 可能用到的其他 self.xxx
        """
        return getattr(self._wf, name)
