"""T03: 生成覆盖率基线报告 markdown."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

JSON_FILE = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\docs\工程化达标_8.4\T03_COVERAGE_BASELINE.json")
OUTPUT_MD = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\docs\工程化达标_8.4\T03_COVERAGE_BASELINE.md")

def main() -> None:
    data = json.loads(JSON_FILE.read_text(encoding="utf-8"))
    totals = data["totals"]
    files = data["files"]

    file_list = []
    for path, info in files.items():
        pct = info["summary"]["percent_covered"]
        stmts = info["summary"]["num_statements"]
        missing = info["summary"]["missing_lines"]
        file_list.append((path, pct, stmts, missing))

    file_list.sort(key=lambda x: x[3], reverse=True)  # 未覆盖行数多的在前

    top_need_test = [f for f in file_list if f[3] >= 50][:20]
    best_covered = [f for f in file_list if f[1] >= 80 and f[2] >= 20][:20]

    pct_display = totals['percent_covered_display']
    branch_pct = totals['percent_branches_covered_display']
    stmt_pct = totals['percent_statements_covered_display']

    md = f"""# T03 覆盖率基线报告

> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 任务: T03 pytest 覆盖率基线测量
> 阶段: Phase 1 - 诚实回测 (M1-M3)

## 一、总体覆盖率

| 指标 | 数值 |
|------|------|
| **行覆盖率** | {pct_display}% |
| 语句覆盖率 | {stmt_pct}% |
| 分支覆盖率 | {branch_pct}% |
| 总语句数 | {totals['num_statements']:,} |
| 已覆盖语句 | {totals['covered_lines']:,} |
| 未覆盖语句 | {totals['missing_lines']:,} |
| 总分支数 | {totals['num_branches']:,} |
| 已覆盖分支 | {totals['covered_branches']:,} |
| 部分覆盖分支 | {totals['num_partial_branches']:,} |

### 与阈值对比

| 项目 | 当前 | 阈值 | 差距 |
|------|------|------|------|
| 行覆盖率 | {pct_display}% | 60% (.coveragerc) | -{60 - float(pct_display):.2f}% |
| 目标 (T13) | {pct_display}% | 80% | -{80 - float(pct_display):.2f}% |

**结论**: 当前覆盖率 **{pct_display}%**, 远低于 60% 阈值 (CI 会 fail), 距离 T13 的 80% 目标缺口 {80 - float(pct_display):.2f} 个百分点.

## 二、最需要补测试的文件 (Top 20, 按未覆盖行数排序)

| 文件 | 覆盖率 | 语句数 | 未覆盖 |
|------|--------|-------|-------|
"""

    for path, pct, stmts, missing in top_need_test:
        short = path.replace("\\", "/").split("/")[-1]
        md += f"| `{short}` | {pct:.1f}% | {stmts} | {missing} |\n"

    md += """
## 三、覆盖最好的文件 (Top 20, >=80%)

| 文件 | 覆盖率 | 语句数 |
|------|--------|-------|
"""

    for path, pct, stmts, _missing in best_covered:
        short = path.replace("\\", "/").split("/")[-1]
        md += f"| `{short}` | {pct:.1f}% | {stmts} |\n"

    md += f"""
## 四、基线意义与后续策略

### 基线意义
- **当前起点**: {pct_display}% ({totals['covered_lines']:,}/{totals['num_statements']:,} 行已覆盖)
- **目标终点**: 80% (T13 任务, Phase 2)
- **缺口**: 需新增覆盖约 **{int(totals['num_statements'] * 0.8 - totals['covered_lines']):,} 行** 才能达到 80%

### 修复策略 (T13 任务执行时参考)

**P0 优先 (Phase 2 M4-M5)**:
1. `utils/kill_switch.py` - 风控核心, 已有 15 个 unit test, 需补到 95%+
2. `utils/risk/` 全部模块 - 已有 50+ 个 unit test
3. `utils/execution/broker_adapters.py` - 实盘交易路径, 已有 70+ 个 test
4. `utils/config_manager.py` - 配置加载, 影响全局

**P1 重要 (Phase 2 M5-M6)**:
5. `utils/data_provider.py` - 数据层核心
6. `utils/portfolio_optimizer.py` - 组合优化
7. `utils/risk_metrics.py` - 风险指标
8. `utils/alpha/` 目录 - alpha 因子

**P2 普通 (Phase 2 持续)**:
9. `utils/attribution/` - 归因分析
10. `utils/reporting/` - 报告生成
11. `v8.3_institutional/` - 旧版本模块

### 工作量估算
- 假设每 50 行未覆盖代码需要 1 个测试用例
- {int(totals['num_statements'] * 0.8 - totals['covered_lines']):,} 行 / 50 = **{int((totals['num_statements'] * 0.8 - totals['covered_lines']) / 50)} 个测试用例**
- 按每天写 15-20 个高质量测试用例估算, 约需 **{int((totals['num_statements'] * 0.8 - totals['covered_lines']) / 50 / 18)} 个工作日**
- 建议分配在 Phase 2 (M4-M6) 3 个月内完成

## 五、CI 集成建议

当前阶段 (T03) 仅测量基线, **不**将 60% 阈值启用为 CI 失败条件 (否则会阻塞所有 PR).
等 T13 任务补齐测试后再启用 `--cov-fail-under=80`.

## 六、数据文件

- 原始 JSON: [T03_COVERAGE_BASELINE.json](./T03_COVERAGE_BASELINE.json)
- 本报告: [T03_COVERAGE_BASELINE.md](./T03_COVERAGE_BASELINE.md)
"""

    OUTPUT_MD.write_text(md, encoding="utf-8")
    print(f"报告已生成: {OUTPUT_MD}")
    print(f"  - 总覆盖率: {pct_display}%")
    print(f"  - 文件数: {len(file_list)}")
    print(f"  - 最需要补测试的文件: {len(top_need_test)}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
