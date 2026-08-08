"""生成交易计划的 Markdown 版本报告"""
import json
import sys
from pathlib import Path

# 强制 UTF-8 输出
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        raise  # Re-raise unknown exception


def generate_trade_plan_md(plan_path: Path, output_path: Path) -> None:
    """生成交易计划的 Markdown 报告"""
    plan = json.loads(plan_path.read_text(encoding='utf-8'))

    lines = [
        '# 次日交易计划 (2026-07-28)',
        '',
        f'**生成时间**: {plan.get("metadata", {}).get("generated_at", "")}',
        '**报告日期**: 2026-07-27',
        '**AI 引擎**: DeepSeek (deepseek-chat)',
        '',
    ]

    # LLM 决策摘要
    overrides = plan.get('llm_overrides', {})
    if overrides:
        lines.append('## 🤖 DeepSeek LLM 决策摘要')
        lines.append('')
        if_contracts = overrides.get('futures_if_contracts', 0)
        if if_contracts:
            lines.append(f'- **期货对冲**: IF空头 {if_contracts} 手')
        puts = overrides.get('put_protection', [])
        if puts:
            lines.append('- **Put 尾部保护**:')
            for p in puts:
                lines.append(
                    f'  - {p.get("code", "")} {p.get("direction", "")} '
                    f'{p.get("contracts", 0)}手 ({p.get("strike_basis", "")})'
                )
        build_seq = overrides.get('build_sequence')
        if build_seq:
            lines.append(f'- **建仓顺序**: {build_seq}')
        lines.append('')

    # AI 建议
    adjustments = (
        plan.get('metadata', {}).get('llm_adjustments', {}).get('adjustments', [])
    )
    if adjustments:
        lines.append('## 📋 AI 决策建议详情 (DeepSeek 生成)')
        lines.append('')
        for i, rec in enumerate(adjustments, 1):
            lines.append(f'{i}. {rec}')
        lines.append('')

    # 执行计划
    exec_plan = plan.get('execution_plan', {})
    morning = exec_plan.get('morning_orders', [])
    afternoon = exec_plan.get('afternoon_orders', [])
    lines.append('## 📊 执行计划')
    lines.append('')
    lines.append(f'- 早盘订单: {len(morning)} 笔')
    lines.append(f'- 午盘订单: {len(afternoon)} 笔')
    lines.append('')

    # 早盘订单明细
    if morning:
        lines.append('### 早盘订单明细')
        lines.append('')
        lines.append('| 序号 | 代码 | 名称 | 方向 | 数量 | 价格 |')
        lines.append('|------|------|------|------|------|------|')
        for i, order in enumerate(morning, 1):
            code = order.get('code', '')
            name = order.get('name', '')
            direction = order.get('direction', '')
            shares = order.get('shares', 0)
            est_price = order.get('est_price', 0)
            lines.append(
                f'| {i} | {code} | {name} | {direction} | {shares} | {est_price} |'
            )
        lines.append('')

    # 午盘订单明细
    if afternoon:
        lines.append('### 午盘订单明细')
        lines.append('')
        lines.append('| 序号 | 代码 | 名称 | 方向 | 数量 | 价格 |')
        lines.append('|------|------|------|------|------|------|')
        for i, order in enumerate(afternoon, 1):
            code = order.get('code', '')
            name = order.get('name', '')
            direction = order.get('direction', '')
            shares = order.get('shares', 0)
            est_price = order.get('est_price', 0)
            lines.append(
                f'| {i} | {code} | {name} | {direction} | {shares} | {est_price} |'
            )
        lines.append('')

    lines.append('---')
    lines.append('*本报告由 DeepSeek AI 决策引擎自动生成*')

    output_path.write_text('\n'.join(lines), encoding='utf-8')
    print(f'交易计划 Markdown 已生成: {output_path}')


if __name__ == '__main__':
    from pathlib import Path
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    plan_path = PROJECT_ROOT / 'v8.3_institutional' / 'trade_plans' / 'trade_plan_20260728.json'
    output_path = Path.home() / '每日报告归档' / '2026-07-27' / 'trade_plan_20260728.md'
    generate_trade_plan_md(plan_path, output_path)
