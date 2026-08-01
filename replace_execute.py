# -*- coding: utf-8 -*-
import re

file_path = r'e:\各种PY程序\28-终极量化交易系统8.4\ai_decision\execution_bridge.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

new_body = (
    "    mode = force_mode or decision.mode\n"
    "\n"
    "    # 初始化 escalation 从 decision.escalation 继承 (保留 L1 已设)\n"
    "    # 设计原则: veto 是硬阈值 (风控直接拦截), escalation 是软阈值 (人工确认)\n"
    "    escalation: bool = bool(decision.escalation)\n"
    "    escalation_reason: str = decision.escalation_reason or ''\n"
    "\n"
    "    # Step A: 生成执行计划\n"
    "    execution_plan = _generate_execution_plan(\n"
    "        decision, portfolio_value, price,\n"
    "        max_single_pct=float(get_config('gate.max_single_pct', 0.02))\n"
    "    )\n"
    "\n"
    "    # Step B: L2 执行层硬风控 (不可绕过)\n"
    "    risk_result = _execution_risk_check(\n"
    "        execution_plan, market_state, portfolio_value,\n"
    "        risk_context=risk_context, decision=decision,\n"
    "        mode=mode,\n"
    "    )\n"
    "\n"
    "    if risk_result.veto:\n"
    "        return _build_l2_veto_return(\n"
    "            decision, mode, risk_result, escalation, escalation_reason, execution_plan\n"
    "        )\n"
    "\n"
    "    # Step B+: TCA 执行前预筛 (步骤 2, Feature Flag 控制)\n"
    "    tca_pre_estimate, escalation, escalation_reason, tca_error = _run_tca_pre_trade(\n"
    "        decision, execution_plan, market_data_for_tca, tca_pre_trade_estimator\n"
    "    )\n"
    "\n"
    "    # Step C: 按模式分派 (shadow/paper/auto/unknown)\n"
    "    execution_result, msg, veto, veto_reason, mode_escalation, mode_escalation_reason = _dispatch_execution_mode(\n"
    "        decision, execution_plan, mode, price, order_router, broker, market_state,\n"
    "        tca_pre_estimate, tca_error, risk_result\n"
    "    )\n"
    "    escalation = escalation or mode_escalation\n"
    "    escalation_reason = escalation_reason or mode_escalation_reason\n"
    "\n"
    "    if veto:\n"
    "        return _build_grayscale_veto_return(\n"
    "            decision, mode, execution_plan, risk_result, tca_pre_estimate, tca_error,\n"
    "            veto_reason, escalation, escalation_reason, msg\n"
    "        )\n"
    "\n"
    "    # Step D: TCA 事后归因 (步骤 2, 仅执行成功后, Feature Flag 控制)\n"
    "    tca_post_report, post_tca_error = _run_tca_post_trade(\n"
    "        tca_post_trade_manager, execution_plan, execution_result, market_data_for_tca, decision\n"
    "    )\n"
    "    if post_tca_error:\n"
    "        tca_error = '{}; {}'.format(tca_error, post_tca_error) if tca_error else post_tca_error\n"
    "\n"
    "    # 写入执行审计\n"
    "    record = _build_success_audit_record(\n"
    "        decision, mode, execution_plan, execution_result, risk_result,\n"
    "        veto, veto_reason, escalation, escalation_reason,\n"
    "        tca_pre_estimate, tca_post_report, tca_error, msg\n"
    "    )\n"
    "    audit_path = _write_execution_audit(record)\n"
    "\n"
    "    return _build_success_return(\n"
    "        decision, mode, execution_plan, execution_result, risk_result,\n"
    "        audit_path, msg, veto, veto_reason, escalation, escalation_reason,\n"
    "        tca_pre_estimate, tca_post_report, tca_error\n"
    "    )\n"
    "\n"
    "\n"
    "def _simulate_fill"
)

pattern = r'(def execute_decision\([^)]*\)[^:]*:.*?""".*?"""\s*)(.*?)(\n\ndef _simulate_fill\()'
match = re.search(pattern, content, re.DOTALL)
if match:
    prefix = match.group(1)
    old_body = match.group(2)
    suffix = match.group(3)
    new_content = prefix + new_body + suffix
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('替换成功')
else:
    print('未找到匹配')
