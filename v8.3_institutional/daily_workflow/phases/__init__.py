# Phases package entry point.
#
# ============================================================================
# ⚠️  警告: 本目录是未完成的拆分半成品, 当前不可运行 (B3.1 子任务待完成)
# ============================================================================
# 已知 bug (2026-07-30 扫描):
#   1. 14 个 phase 文件共 30+ 处未导入的模块级常量/函数
#      (FACTOR_KS_READY / AUTOLEARN_READY / V85_READY / _run_autolearn / ...)
#      这些都定义在 v8.3_institutional/daily_workflow.py 顶层, 本目录未 import
#   2. 6 个 phase 文件 (market/risk/signal/execute/report/hedge) 函数体内
#      仍使用 `self.` 但首参名为 `workflow` → 运行时 NameError
#   3. core/workflow_orchestrator.py 引用了不存在的 config/workflow_config.py
#   4. core/workflow_orchestrator.py 函数名导入错误:
#      - phase_factor_kill (实际应为 phase_factor_kill_switch)
#      - phase_shadow (实际应为 phase_shadow_monitor)
#   5. phase 函数签名不统一: 5 个返回 bool, 9 个返回 Dict/Path/CircuitLevel
#   6. phase_execute 多了 signal 参数, 与 orchestrator 的 func(self) 调用不兼容
#
# 生产路径仍使用 v8.3_institutional/daily_workflow.py (8943 行 God Class)
# 本目录仅供 B3.1 后续子任务参考, 不得在生产路径 import
#
# B3.1 子任务规划:
#   B3.1.1 (已完成 2026-07-30): 清理 dead code (根外壳 + 生成脚本)
#   B3.1.2: 创建 config/workflow_config.py, 迁移 WorkflowConfig/CircuitLevel
#   B3.1.3: 创建 _globals.py, 集中导出模块级常量 (FACTOR_KS_READY 等)
#   B3.1.4: 修复 14 个 phase 文件的 import + self→workflow 替换
#   B3.1.5: 修复 workflow_orchestrator.py 的断裂 import + 函数名
#   B3.1.6: 统一 phase 函数签名为 (workflow) -> bool
#   B3.1.7: 让 daily_workflow.py 变 thin wrapper, 调用 phases/ 模块
#   B3.1.8: E2E 测试验证 (test_eod_full_chain_e2e.py)
# ============================================================================

# 为避免误导后续开发者, 本 __init__.py 不再自动 import 各 phase 实现
# (因为 import 会立即抛 NameError / ImportError)
#
# 如需访问各 phase 实现, 请直接从 v8.3_institutional/daily_workflow.py 导入:
#   from daily_workflow import DailyWorkflow
#   workflow = DailyWorkflow(...)
#   workflow.phase_check()  # 等等

__all__: list[str] = []  # 空列表, 不导出任何符号
