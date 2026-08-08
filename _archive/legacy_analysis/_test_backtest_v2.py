"""验证: alpha 评估修复后是否产生 real alpha"""
import sys

sys.path.insert(0, '.')
import logging

logging.basicConfig(level=logging.WARNING, format='%(name)s: %(message)s')

from institutional_pipeline_runner import InstitutionalPipelineRunner, PipelineContext  # noqa: E402

ctx = PipelineContext(
    mode='backtest',
    symbols=['600519', '000858', '601318', '000001', '600036', '601398', '600276', '000063'],
    report_date='2025-06-01',
)
runner = InstitutionalPipelineRunner(ctx)
result = runner.run()

print('status:', result.get('status'))
integ = result.get('integrity', {})
print('integrity valid:', integ.get('valid'))
print('alpha_provenance:', integ.get('alpha_provenance'))
alpha = result.get('steps', {}).get('alpha_evaluation', {})
print('alpha category:', alpha.get('category'))
print('alpha active_factors:', alpha.get('active_factors'))
evals = alpha.get('evaluations', [])
print('evaluations count:', len(evals))
for e in evals[:4]:
    name = e.get('factor_name', '?')
    ic = e.get('ic_1d', 0)
    ir = e.get('ic_ir', 0)
    print(f'  {name}: IC={ic:.4f} IC_IR={ir:.4f}')
