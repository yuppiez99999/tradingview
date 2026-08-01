# -*- coding: utf-8 -*-
"""生成代码质量评估报告 (扫描后自动产出)"""
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
d = json.load(open(ROOT / 'scripts' / '_bug_scan_results.json', 'r', encoding='utf-8'))

PROD_PREFIXES = ['utils\\', 'utils/', 'v8.3_institutional\\src\\', 'v8.3_institutional/src/']

def is_prod(fp):
    norm = fp.replace('/', '\\')
    return any(norm.startswith(p) for p in PROD_PREFIXES)

# 分类统计
total_all = 0
total_prod = 0
cats = {}
for cat, items in d.items():
    prod = [it for it in items if is_prod(it['file'])]
    cats[cat] = {'total': len(items), 'prod': len(prod)}
    total_all += len(items)
    total_prod += len(prod)

# 文件级统计 (生产代码)
file_issues = defaultdict(list)
for cat, items in d.items():
    for it in items:
        if is_prod(it['file']):
            file_issues[it['file']].append((cat, it['line']))

# 按模块统计
module_stats = defaultdict(lambda: {'count': 0, 'cats': defaultdict(int)})
for f, items in file_issues.items():
    # 提取模块名
    parts = f.replace('/', '\\').split('\\')
    if len(parts) >= 2:
        module = '\\'.join(parts[:2])
    else:
        module = parts[0]
    for cat, _ in items:
        module_stats[module]['count'] += 1
        module_stats[module]['cats'][cat] += 1

# 计算"生产代码质量评分"
# 评分维度: P0 安全 (40分) + P1 健壮性 (30分) + P2 可维护性 (20分) + P3 整洁 (10分)
p0_score = 40 - min(40, (cats.get('EVAL', {}).get('prod', 0) * 15 + cats.get('EXEC', {}).get('prod', 0) * 10 + cats.get('TODO', {}).get('prod', 0) * 0.5))
p1_score = 30 - min(30, cats.get('BROAD_EXCEPT_SILENT', {}).get('prod', 0) * 0.1 + cats.get('DIV_ZERO_RISK', {}).get('prod', 0) * 0.05 + cats.get('HARDCODED_PATH', {}).get('prod', 0) * 2)
p2_score = 20 - min(20, cats.get('LONG_FUNCTION', {}).get('prod', 0) * 0.1 + cats.get('HIGH_COMPLEXITY', {}).get('prod', 0) * 0.15)
p3_score = 10 - min(10, cats.get('PRINT_DEBUG', {}).get('prod', 0) * 0.01 + cats.get('TOO_MANY_ARGS', {}).get('prod', 0) * 0.1)
total_score = p0_score + p1_score + p2_score + p3_score

# 模块 TOP
module_top = sorted(module_stats.items(), key=lambda x: -x[1]['count'])[:15]

# 输出
print('# 量化交易系统 v8.4 — 代码质量评估报告')
print()
print(f'> **扫描时间**: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
print('> **扫描工具**: `scripts/_scan_bugs.py`')
print('> **数据来源**: `scripts/_bug_scan_results.json`')
print('> **扫描范围**: utils/ + v8.3_institutional/src/ + research/ + tests/')
print('> **用途**: IDE 对照修改参考')
print()
print('---')
print()
print('## 一、代码质量评分')
print()
print('### 1.1 综合评分')
print()
print('| 维度 | 满分 | 实际得分 | 扣分原因 |')
print('|------|------|----------|----------|')
print(f'| P0 安全 (EVAL/EXEC/TODO) | 40 | **{p0_score:.1f}** | EVAL×15 + EXEC×10 + TODO×0.5 |')
print(f'| P1 健壮性 (异常/除零/路径) | 30 | **{p1_score:.1f}** | 宽泛异常×0.1 + 除零×0.05 + 硬编码路径×2 |')
print(f'| P2 可维护性 (长函数/复杂度) | 20 | **{p2_score:.1f}** | 长函数×0.1 + 高复杂度×0.15 |')
print(f'| P3 整洁 (print/参数过多) | 10 | **{p3_score:.1f}** | print×0.01 + 参数过多×0.1 |')
print(f'| **总分** | **100** | **{total_score:.1f}** | |')
print()
score_grade = 'A' if total_score >= 85 else 'B' if total_score >= 70 else 'C' if total_score >= 55 else 'D'
print(f'**评级**: **{score_grade}** 级', end='')
if score_grade == 'A':
    print(' (优秀, 可放心迭代)')
elif score_grade == 'B':
    print(' (良好, 有改进空间)')
elif score_grade == 'C':
    print(' (一般, 需要计划性改进)')
else:
    print(' (较差, 需要立即重构)')
print()
print('### 1.2 问题数量统计')
print()
print('| 类别 | 全量 | 生产代码 | 占比 | 优先级 |')
print('|------|------|----------|------|--------|')
for cat in ['EVAL', 'EXEC', 'TODO', 'BROAD_EXCEPT_SILENT', 'DIV_ZERO_RISK', 'HARDCODED_PATH', 'LONG_FUNCTION', 'HIGH_COMPLEXITY', 'PRINT_DEBUG', 'TOO_MANY_ARGS']:
    if cat in cats:
        c = cats[cat]
        ratio = c['prod'] / c['total'] * 100 if c['total'] else 0
        priority = {'EVAL':'P0','EXEC':'P0','TODO':'P0','BROAD_EXCEPT_SILENT':'P1','DIV_ZERO_RISK':'P1','HARDCODED_PATH':'P1','LONG_FUNCTION':'P2','HIGH_COMPLEXITY':'P2','PRINT_DEBUG':'P2','TOO_MANY_ARGS':'P3'}[cat]
        print(f'| {cat} | {c["total"]} | {c["prod"]} | {ratio:.1f}% | {priority} |')
print(f'| **合计** | **{total_all}** | **{total_prod}** | {total_prod/total_all*100:.1f}% | |')
print()
print('---')
print()
print('## 二、模块代码质量热力图 (生产代码 TOP 15)')
print()
print('| 模块路径 | 问题数 | 主要问题类别 |')
print('|----------|--------|--------------|')
for mod, stats in module_top:
    top_cats = sorted(stats['cats'].items(), key=lambda x: -x[1])[:3]
    cat_str = ', '.join(f'{c}({n})' for c, n in top_cats)
    print(f'| {mod} | {stats["count"]} | {cat_str} |')
print()
print('---')
print()
print('## 三、关键问题清单')
print()
print('### 3.1 P0 — 安全与实盘阻断 (必须立即修复)')
print()
print('#### EVAL 代码注入风险')
print()
eval_prod = [it for it in d.get('EVAL', []) if is_prod(it['file'])]
for it in eval_prod:
    print(f'- **[{it["file"]}]({it["file"].replace(chr(92),"/")})** 第 {it["line"]} 行: `{it["issue"]}`')
print()
print('#### 实盘 TODO (未实现接口)')
print()
todo_prod = [it for it in d.get('TODO', []) if is_prod(it['file'])]
for it in todo_prod:
    print(f'- **{it["file"]}:{it["line"]}** — {it["issue"]}')
print()
print('### 3.2 P1 — 生产代码健壮性问题')
print()
print('#### 静默宽泛异常 (BROAD_EXCEPT_SILENT)')
print()
bes_prod = [it for it in d.get('BROAD_EXCEPT_SILENT', []) if is_prod(it['file'])]
bes_by_file = defaultdict(list)
for it in bes_prod:
    bes_by_file[it['file']].append(it)
print('| 文件 | 问题数 |')
print('|------|--------|')
for f, items in sorted(bes_by_file.items(), key=lambda x: -len(x[1]))[:15]:
    print(f'| {f} | {len(items)} |')
print()
print('#### 除零风险 (DIV_ZERO_RISK)')
print()
dz_prod = [it for it in d.get('DIV_ZERO_RISK', []) if is_prod(it['file'])]
dz_by_file = defaultdict(list)
for it in dz_prod:
    dz_by_file[it['file']].append(it)
print('| 文件 | 问题数 |')
print('|------|--------|')
for f, items in sorted(dz_by_file.items(), key=lambda x: -len(x[1]))[:15]:
    print(f'| {f} | {len(items)} |')
print()
print('### 3.3 P2 — 可维护性问题')
print()
print('#### 长函数 TOP 10 (生产代码)')
print()
lf_prod = [it for it in d.get('LONG_FUNCTION', []) if is_prod(it['file'])]
print('| 文件 | 行号 | 函数 | 行数 |')
print('|------|------|------|------|')
for it in sorted(lf_prod, key=lambda x: -int(''.join(c for c in x['issue'].split('行')[0].split()[-1] if c.isdigit())) if any(c.isdigit() for c in x['issue']) else 0, reverse=True)[:10]:
    print(f'| {it["file"]} | {it["line"]} | {it["issue"]} |')
print()
print('#### 高复杂度 TOP 10 (生产代码)')
print()
hc_prod = [it for it in d.get('HIGH_COMPLEXITY', []) if is_prod(it['file'])]
print('| 文件 | 行号 | 函数 | 复杂度 |')
print('|------|------|------|--------|')
for it in hc_prod[:10]:
    print(f'| {it["file"]} | {it["line"]} | {it["issue"]} |')
print()
print('---')
print()
print('## 四、修改优先级建议')
print()
print('### 4.1 立即修复 (P0, 30分钟)')
print()
print('1. **[v8.3_institutional/src/signals/rule_engine.py:607](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/src/signals/rule_engine.py#L607)** — `eval()` 代码注入风险')
print('2. **[v8.3_institutional/src/derivatives/futures_scan.py:209](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/src/derivatives/futures_scan.py#L209)** — 硬编码 Windows 路径')
print()
print('### 4.2 本周修复 (P1)')
print()
print('3. 修复 144 处静默宽泛异常 (改为精确异常 + 上抛或降级)')
print('4. 修复 220 处除零风险 (用 `safe_div` 或 `if x > 0:` 防护)')
print('5. 接入 broker_adapters.py 的 iFinD 实盘接口 (22 处 TODO)')
print()
print('### 4.3 下个迭代 (P2)')
print()
print('6. 重构 `generate_report` (353 行) 和 `_execute_order` (169 行)')
print('7. 将 360 处 `print` 迁移到 `logger`')
print()
print('### 4.4 按需处理 (P3)')
print()
print('8. 重构 23 处参数过多的函数 (改为 dataclass 封装)')
print()
print('---')
print()
print('## 五、详细数据文件')
print()
print('- 全量扫描结果: `scripts/_bug_scan_results.json`')
print('- 生产代码清单: `scripts/_prod_bug_scan_results.json`')
print('- 详细修复模板: [docs/ecc_audit/BUGFIX_REPORT_20260729.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/docs/ecc_audit/BUGFIX_REPORT_20260729.md)')
print()
print('---')
print()
print(f'**报告生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
