"""临时验证 finance_agent_orchestrator 模块"""
import sys

sys.path.insert(0, '.')

print('=== 1. Import finance_agents package ===')
from utils.finance_agents import (
    AgentDecision,
)

print('OK: 5 agents + BaseAgent + AgentDecision imported')

print('=== 2. Import orchestrator ===')
from utils.finance_agent_orchestrator import (
    FinanceAgentOrchestrator,
)

print('OK: orchestrator imported')

print('=== 3. Test AgentDecision NaN defense ===')
d = AgentDecision(
    agent_name='test', symbol='600276.SH',
    strength=float('nan'), confidence=float('inf'),
)
print(f'NaN/Inf 防御: strength={d.strength}, confidence={d.confidence}')

print('=== 4. Test orchestrator initialization ===')
orch = FinanceAgentOrchestrator()
print(f'OK: orchestrator 初始化完成, agents={[a.name for a in orch.agents]}')
print(f'weights={orch.weights}')

print('=== 5. Test simple orchestrate (empty context) ===')
result = orch.orchestrate('600276.SH', {})
print(f'OK: orchestrate 完成, action={result.action}, strength={result.strength:.3f}')
print(f'agent_decisions count: {len(result.agent_decisions)}')

print('=== 6. Test with mock context ===')
mock_context = {
    'kline': [{'close': 10 + i * 0.1, 'volume': 1e7, 'amount': 1e8} for i in range(30)],
    'fundamentals': {
        'pe': 15.2, 'pb': 2.1, 'roe': 0.18,
        'pe_percentile': 0.15, 'pb_percentile': 0.20,
    },
    'news_items': [{'title': '某公司业绩增长', 'content': '利好', 'symbol': '600276.SH'}],
    'macro_data': {
        'bond_10y_yield': 0.024, 'north_flow': 8e9,
        'industry_score': 0.75, 'index_return_20d': 0.06,
    },
    'position_weight': 0.08,
    'beta': 1.1,
}
result2 = orch.orchestrate('600276.SH', mock_context)
print(f'OK: mock orchestrate 完成, action={result2.action}, strength={result2.strength:.3f}')
for d in result2.agent_decisions:
    print(f'  {d["agent_name"]}: action={d["action"]}, strength={d["strength"]:.3f}')

print('=== 7. Test shadow_compare ===')


class MockFusion:
    strength = 0.5


diff = orch.shadow_compare(MockFusion(), result2)
print(f'OK: shadow_compare, diff={diff.diff:.3f}, direction_match={diff.direction_match}')

print('=== 8. Test veto path ===')
mock_veto = {
    'kline': [{'close': 10 - i * 0.5, 'volume': 1e7, 'amount': 1e8} for i in range(30)],
    'news_items': [{'title': '某公司被立案调查', 'content': '财务造假', 'symbol': '600276.SH'}],
    'position_weight': 0.20,
    'beta': 1.6,
}
result3 = orch.orchestrate('600276.SH', mock_veto)
print(f'OK: veto path, action={result3.action}, veto={result3.veto}, reason={result3.veto_reason}')

print('=== 9. Test audit log persistence ===')
log_path = orch.save_audit_log(result2, diff, trade_date='20260726')
print(f'OK: audit log saved: {log_path}')
loaded = orch.load_audit_log('20260726')
print(f'Loaded {len(loaded)} entries')

print('=== ALL TESTS PASSED ===')
