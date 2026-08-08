"""生产模块函数质量评估 + HTML 可视化报告

融合 mattpocock/skills `improve-codebase-architecture` 的 HTML 报告思路:
- 保留本地 AST 扫描核心(已成熟, 数据准确)
- 新增三轴问题分类: 超长(>80行) + 高CC(>15) + 多参数(>5)
- 新增 HTML 卡片式报告: 写到 OS temp 目录, 自动浏览器打开
- 适配本地量化场景: 不依赖 CDN(内联 CSS), 不调用外部 agent

用法:
    python scripts/_scan_func_quality.py            # 终端输出 + HTML 报告
    python scripts/_scan_func_quality.py --no-html  # 仅终端输出
    python scripts/_scan_func_quality.py --open     # 生成后强制打开(默认就开)
"""
from __future__ import annotations

import argparse
import ast
import os
import time
import webbrowser
from html import escape
from pathlib import Path

ROOT = Path(r"e:\各种PY程序\28-终极量化交易系统8.4")

# 生产模块路径模式(扫描白名单)
PROD_PATTERNS = [
    "utils/execution",
    "utils/risk",
    "utils/alpha",
    "utils/fineng",
    "utils/infra",
    "ms_strategy/core",
    "ms_strategy/scripts",
    "ai_decision",
]

# 问题阈值(三轴)
THRESH_LONG = 80        # 行数
THRESH_CC = 15          # 圈复杂度
THRESH_ARGS = 5         # 参数数


# ============================================================
# 1. AST 扫描核心(保留原逻辑)
# ============================================================

def is_prod(fpath: Path) -> bool:
    """判断是否生产模块"""
    s = str(fpath).replace("\\", "/")
    return any(p in s for p in PROD_PATTERNS)


def complexity(node: ast.AST) -> int:
    """计算圈复杂度"""
    c = 1
    for n in ast.walk(node):
        if isinstance(n, (ast.If, ast.For, ast.While, ast.ExceptHandler,
                          ast.With, ast.Assert, ast.comprehension)):
            c += 1
        elif isinstance(n, ast.BoolOp):
            c += max(0, len(n.values) - 1)
    return c


def func_length(func_node: ast.FunctionDef) -> int:
    """计算函数体长度(扣除装饰器)"""
    start = func_node.lineno
    end = func_node.end_lineno
    decorator_lines = sum(
        d.end_lineno - d.lineno + 1
        for d in getattr(func_node, 'decorator_list', [])
    )
    return max(1, (end - start + 1) - decorator_lines)


def scan_all_functions() -> tuple[list[dict], int]:
    """扫描所有生产模块函数, 返回 (函数列表, 文件数)"""
    results: list[dict] = []
    files_scanned = 0

    for rel_dir in ["utils", "ms_strategy", "ai_decision"]:
        d = ROOT / rel_dir
        if not d.exists():
            continue
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if x not in ("__pycache__",)]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = Path(root) / fname
                if not is_prod(fpath):
                    continue
                try:
                    with open(fpath, encoding='utf-8', errors='ignore') as f:
                        src = f.read()
                    tree = ast.parse(src)
                except (ValueError, TypeError, KeyError, AttributeError,
                        RuntimeError, OSError, TimeoutError, ConnectionError):
                    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                    continue
                files_scanned += 1

                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args_to_count = [
                            a for a in node.args.args
                            if a.arg not in ('self', 'cls')
                        ]
                        n_args = (len(args_to_count)
                                  + len(node.args.posonlyargs)
                                  + len(node.args.kwonlyargs))
                        rel = fpath.relative_to(ROOT)
                        results.append({
                            'file': str(rel).replace('\\', '/'),
                            'line': node.lineno,
                            'name': node.name,
                            'args': n_args,
                            'length': func_length(node),
                            'cc': complexity(node),
                        })

    return results, files_scanned


# ============================================================
# 2. 严重度分类(mattpocock 思路: 三轴 → Strong/Worth/Speculative)
# ============================================================

SEVERITY_ORDER = {'Strong': 0, 'Worth exploring': 1, 'Speculative': 2}
SEVERITY_COLOR = {
    'Strong': '#dc2626',          # 红
    'Worth exploring': '#f59e0b',  # 橙
    'Speculative': '#10b981',      # 绿
}


def classify_severity(func: dict) -> tuple[str | None, list[str]]:
    """三轴问题分类: 同时命中越多越严重"""
    flags: list[str] = []
    if func['length'] > THRESH_LONG:
        flags.append('LONG')
    if func['cc'] > THRESH_CC:
        flags.append('HIGH_CC')
    if func['args'] > THRESH_ARGS:
        flags.append('MANY_ARGS')

    if len(flags) >= 3:
        return 'Strong', flags
    if len(flags) == 2:
        return 'Worth exploring', flags
    if len(flags) == 1:
        return 'Speculative', flags
    return None, flags


def generate_suggestions(func: dict) -> list[str]:
    """根据问题类型生成 plain English 重构建议(量化场景适配)"""
    suggestions: list[str] = []
    flags = func['flags']

    if 'LONG' in flags:
        if func['length'] > 200:
            suggestions.append(
                "🔴 函数过长(>200行), 强烈建议按职责拆分为多个 helper 函数. "
                "参考本地模式: ops_diagnoser._diagnose_from_health 用规则配置表+循环 "
                "从 154 行降到 49 行(-68%)."
            )
        elif func['length'] > 120:
            suggestions.append(
                "🟡 函数偏长(>120行), 建议识别重复模板提取为 helper. "
                "参考: execution_selector.choose_execution_algorithm 提取 "
                "_compute_adaptive_weights + _make_result, 129→86 行(-33%)."
            )
        else:
            suggestions.append("🟢 函数略长, 可考虑提取 1-2 个 helper 降低单函数认知负荷.")

    if 'HIGH_CC' in flags:
        if func['cc'] > 25:
            suggestions.append(
                "🔴 圈复杂度过高(>25), 通常意味着硬编码分支判断. "
                "建议用规则配置表(list[dict]) + for 循环替代 if/elif 链. "
                "参考: _OPS_HEALTH_RULES / _STRATEGY_HEALTH_RULES."
            )
        else:
            suggestions.append(
                "🟡 圈复杂度偏高, 检查是否有重复的条件判断可表驱动化."
            )

    if 'MANY_ARGS' in flags:
        if func['args'] >= 8:
            suggestions.append(
                "🔴 参数过多(≥8), 建议引入参数对象(Parameter Object) 或 dataclass 封装. "
                "注意: 避免简单加 *args/**kwargs, 那只是把问题藏起来."
            )
        else:
            suggestions.append(
                "🟡 参数偏多(>5), 可考虑把相关参数分组为 dataclass."
            )

    if not suggestions:
        suggestions.append("无明显问题(可能是误报或边界情况).")

    return suggestions


def infer_module_tag(file_path: str) -> str:
    """从文件路径推断所属模块(用于卡片标签)"""
    parts = file_path.split('/')
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0] if parts else "unknown"


# ============================================================
# 3. HTML 报告生成(融合 mattpocock 卡片式可视化)
# ============================================================

def render_metric(value: int, label: str, threshold: int, alert: bool) -> str:
    """渲染单个指标方块"""
    cls = "metric alert" if alert else "metric"
    return f"""
    <div class="{cls}">
      <span class="metric-value">{value}</span>
      <span class="metric-label">{label}</span>
      <span class="metric-thresh">阈>{threshold}</span>
    </div>
    """


def render_card(func: dict, idx: int) -> str:
    """渲染单个问题函数卡片"""
    severity = func['severity']
    badge_color = SEVERITY_COLOR[severity]
    module_tag = infer_module_tag(func['file'])

    # 指标方块
    metrics_html = (
        render_metric(func['length'], '行数', THRESH_LONG, func['length'] > THRESH_LONG)
        + render_metric(func['cc'], '圈复杂度', THRESH_CC, func['cc'] > THRESH_CC)
        + render_metric(func['args'], '参数数', THRESH_ARGS, func['args'] > THRESH_ARGS)
    )

    # 问题诊断
    flags_label = {
        'LONG': '超长函数', 'HIGH_CC': '高复杂度', 'MANY_ARGS': '多参数'
    }
    problems = "、".join(flags_label[f] for f in func['flags'])
    problem_html = f"<p>该函数命中以下问题: <strong>{problems}</strong>。"

    # 添加量化场景特有问题诊断
    if 'LONG' in func['flags'] and 'HIGH_CC' in func['flags']:
        problem_html += (
            " 长函数 + 高复杂度通常意味着硬编码的分支判断堆叠, "
            "这是量化系统诊断器/路由器类模块的典型技术债."
        )
    if 'LONG' in func['flags'] and 'MANY_ARGS' in func['flags']:
        problem_html += (
            " 长函数 + 多参数暗示函数承担了过多职责, "
            "调用方也很难复用, 容易在多处复制粘贴变体."
        )
    problem_html += "</p>"

    # 重构建议
    suggestions_html = "".join(f"<li>{escape(s)}</li>" for s in generate_suggestions(func))

    # Before/After 可视化(纯 CSS 柱状图)
    before_height = min(100, int(func['length'] / 4))  # 缩放到 0-100px
    after_height = min(100, int(func['length'] * 0.4 / 4))  # 预估降 60%
    before_color = "#dc2626" if func['length'] > 200 else "#f59e0b"
    after_color = "#10b981"

    before_after_html = f"""
    <div class="before-after">
      <div class="ba-side">
        <h5>Before</h5>
        <div class="ba-bar-container">
          <div class="ba-bar" style="height:{before_height}px;background:{before_color}"></div>
        </div>
        <div class="ba-label">{func['length']} 行 / CC={func['cc']}</div>
      </div>
      <div class="ba-arrow">→</div>
      <div class="ba-side">
        <h5>After(预估)</h5>
        <div class="ba-bar-container">
          <div class="ba-bar" style="height:{after_height}px;background:{after_color}"></div>
        </div>
        <div class="ba-label">~{int(func['length']*0.4)} 行 / CC≤10</div>
      </div>
    </div>
    """

    # 文件路径(可点击 — Windows file:// 协议)
    file_uri = f"file:///{str(ROOT / func['file']).replace(chr(92), '/')}"
    location_html = (
        f'<a href="{file_uri}" class="location" title="点击打开文件">'
        f'{escape(func["file"])}:{func["line"]}</a>'
    )

    return f"""
    <div class="card" id="card-{idx}">
      <div class="card-header">
        <span class="badge" style="background:{badge_color}">{severity}</span>
        <h3>{escape(func['name'])}()</h3>
        <span class="module-tag">{escape(module_tag)}</span>
      </div>
      <div class="location-row">{location_html}</div>
      <div class="metrics-row">{metrics_html}</div>
      <div class="section">
        <h4>🔍 问题</h4>
        {problem_html}
      </div>
      <div class="section">
        <h4>💡 建议</h4>
        <ul class="suggestions">{suggestions_html}</ul>
      </div>
      {before_after_html}
    </div>
    """


def render_html_report(
    all_funcs: list[dict],
    files_scanned: int,
    problem_funcs: list[dict],
    top_func: dict | None,
) -> str:
    """生成完整 HTML 报告"""
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')

    # 统计
    strong_count = sum(1 for f in problem_funcs if f['severity'] == 'Strong')
    worth_count = sum(1 for f in problem_funcs if f['severity'] == 'Worth exploring')
    spec_count = sum(1 for f in problem_funcs if f['severity'] == 'Speculative')

    long_count = sum(1 for f in all_funcs if f['length'] > THRESH_LONG)
    cc_count = sum(1 for f in all_funcs if f['cc'] > THRESH_CC)
    args_count = sum(1 for f in all_funcs if f['args'] > THRESH_ARGS)

    # 卡片 HTML
    cards_html = "".join(
        render_card(f, i) for i, f in enumerate(problem_funcs, 1)
    )

    # Top recommendation
    if top_func:
        top_html = f"""
        <section class="top-recommendation">
          <h2>⭐ Top Recommendation</h2>
          <div class="top-card">
            <p>建议优先处理: <strong>{escape(top_func['name'])}()</strong></p>
            <p class="top-location">{escape(top_func['file'])}:{top_func['line']}</p>
            <p>原因: 该函数命中 <strong>{len(top_func['flags'])}</strong> 项问题
            ({", ".join(top_func['flags'])}), 长度 {top_func['length']} 行,
            圈复杂度 {top_func['cc']}。</p>
            <p>处理它能把"规则配置表+循环"的模式确立为团队共识, 后续类似函数可批量套用。</p>
          </div>
        </section>
        """
    else:
        top_html = '<section class="top-recommendation"><h2>⭐ Top Recommendation</h2><p>无明显问题函数.</p></section>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>函数质量深化机会报告 — {timestamp}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
                 'Microsoft YaHei', sans-serif;
    background: #f8fafc; color: #1e293b; line-height: 1.6; padding: 24px;
  }}
  header {{
    background: linear-gradient(135deg, #1e40af 0%, #3b82f6 100%);
    color: white; padding: 32px; border-radius: 12px; margin-bottom: 24px;
    box-shadow: 0 4px 12px rgba(30,64,175,0.15);
  }}
  header h1 {{ font-size: 28px; margin-bottom: 8px; }}
  header .meta {{ font-size: 14px; opacity: 0.9; }}
  header .meta span {{ margin-right: 16px; }}

  .summary {{ background: white; padding: 24px; border-radius: 12px;
              margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
  .summary h2 {{ font-size: 20px; margin-bottom: 16px; color: #1e40af; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 16px; }}
  .stat-card {{ padding: 20px; border-radius: 8px; text-align: center; color: white; }}
  .stat-strong {{ background: #dc2626; }}
  .stat-worth {{ background: #f59e0b; }}
  .stat-spec {{ background: #10b981; }}
  .stat-other {{ background: #64748b; }}
  .stat-value {{ font-size: 36px; font-weight: bold; display: block; }}
  .stat-label {{ font-size: 13px; opacity: 0.9; }}

  .axis-stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px;
                margin-top: 16px; }}
  .axis-card {{ background: #f1f5f9; padding: 12px; border-radius: 6px; text-align: center; }}
  .axis-card .axis-val {{ font-size: 24px; font-weight: bold; color: #1e40af; }}
  .axis-card .axis-lbl {{ font-size: 12px; color: #64748b; }}

  .candidates {{ margin-bottom: 24px; }}
  .candidates h2 {{ font-size: 22px; color: #1e40af; margin-bottom: 16px;
                   padding-bottom: 8px; border-bottom: 2px solid #e2e8f0; }}

  .card {{
    background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06); border-left: 4px solid #3b82f6;
  }}
  .card-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 12px;
                 flex-wrap: wrap; }}
  .badge {{ padding: 4px 12px; border-radius: 12px; color: white;
           font-size: 12px; font-weight: 600; }}
  .card h3 {{ font-size: 20px; color: #1e293b; font-family: 'Consolas', 'Monaco', monospace; }}
  .module-tag {{ background: #e0e7ff; color: #3730a3; padding: 2px 10px;
                border-radius: 4px; font-size: 12px; }}

  .location-row {{ margin-bottom: 16px; }}
  .location {{ color: #3b82f6; text-decoration: none; font-family: 'Consolas', monospace;
               font-size: 13px; word-break: break-all; }}
  .location:hover {{ text-decoration: underline; }}

  .metrics-row {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
  .metric {{
    background: #f1f5f9; padding: 12px 20px; border-radius: 8px; text-align: center;
    min-width: 100px;
  }}
  .metric.alert {{ background: #fef2f2; border: 1px solid #fecaca; }}
  .metric-value {{ display: block; font-size: 28px; font-weight: bold; color: #1e293b; }}
  .metric.alert .metric-value {{ color: #dc2626; }}
  .metric-label {{ display: block; font-size: 12px; color: #64748b; margin-top: 4px; }}
  .metric-thresh {{ display: block; font-size: 10px; color: #94a3b8; margin-top: 2px; }}

  .section {{ margin-bottom: 16px; }}
  .section h4 {{ font-size: 15px; color: #374151; margin-bottom: 8px; }}
  .suggestions {{ padding-left: 20px; }}
  .suggestions li {{ font-size: 14px; margin-bottom: 6px; line-height: 1.6; }}

  .before-after {{
    display: flex; align-items: center; gap: 24px; padding: 16px;
    background: #f8fafc; border-radius: 8px; margin-top: 16px;
  }}
  .ba-side {{ text-align: center; flex: 1; }}
  .ba-side h5 {{ font-size: 13px; color: #64748b; margin-bottom: 8px; }}
  .ba-bar-container {{
    height: 100px; display: flex; align-items: flex-end; justify-content: center;
    background: #e2e8f0; border-radius: 4px; padding: 4px;
  }}
  .ba-bar {{ width: 60px; border-radius: 2px; transition: height 0.3s; }}
  .ba-label {{ font-size: 12px; color: #475569; margin-top: 6px;
               font-family: 'Consolas', monospace; }}
  .ba-arrow {{ font-size: 28px; color: #3b82f6; font-weight: bold; }}

  .top-recommendation {{
    background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
    padding: 24px; border-radius: 12px; margin-top: 32px;
    border: 2px solid #f59e0b;
  }}
  .top-recommendation h2 {{ color: #92400e; margin-bottom: 12px; }}
  .top-card p {{ margin-bottom: 8px; }}
  .top-location {{ font-family: 'Consolas', monospace; color: #78350f; font-size: 13px; }}

  footer {{ text-align: center; padding: 24px; color: #64748b; font-size: 12px;
           margin-top: 32px; border-top: 1px solid #e2e8f0; }}
  footer a {{ color: #3b82f6; text-decoration: none; }}
</style>
</head>
<body>

<header>
  <h1>📐 函数质量深化机会报告</h1>
  <div class="meta">
    <span>⏰ {timestamp}</span>
    <span>📁 {files_scanned} 个文件</span>
    <span>🔢 {len(all_funcs)} 个函数</span>
    <span>🎯 {len(problem_funcs)} 个深化机会</span>
  </div>
</header>

<section class="summary">
  <h2>📊 总览</h2>
  <div class="stats-grid">
    <div class="stat-card stat-strong">
      <span class="stat-value">{strong_count}</span>
      <span class="stat-label">Strong (三项都超标)</span>
    </div>
    <div class="stat-card stat-worth">
      <span class="stat-value">{worth_count}</span>
      <span class="stat-label">Worth exploring (两项超标)</span>
    </div>
    <div class="stat-card stat-spec">
      <span class="stat-value">{spec_count}</span>
      <span class="stat-label">Speculative (一项超标)</span>
    </div>
    <div class="stat-card stat-other">
      <span class="stat-value">{len(all_funcs) - len(problem_funcs)}</span>
      <span class="stat-label">健康函数</span>
    </div>
  </div>

  <div class="axis-stats">
    <div class="axis-card">
      <div class="axis-val">{long_count}</div>
      <div class="axis-lbl">超长函数 (&gt;{THRESH_LONG}行)</div>
    </div>
    <div class="axis-card">
      <div class="axis-val">{cc_count}</div>
      <div class="axis-lbl">高复杂度 (CC&gt;{THRESH_CC})</div>
    </div>
    <div class="axis-card">
      <div class="axis-val">{args_count}</div>
      <div class="axis-lbl">多参数 (&gt;{THRESH_ARGS}个)</div>
    </div>
  </div>
</section>

<section class="candidates">
  <h2>🎯 深化候选 (按优先级排序)</h2>
  {cards_html if cards_html else '<p style="padding:24px;text-align:center;color:#64748b;">🎉 无问题函数.</p>'}
</section>

{top_html}

<footer>
  <p>本报告由 <code>scripts/_scan_func_quality.py</code> 自动生成</p>
  <p>融合 <a href="https://github.com/mattpocock/skills/blob/main/skills/engineering/improve-codebase-architecture/SKILL.md" target="_blank">mattpocock/skills improve-codebase-architecture</a> 的 HTML 报告思路</p>
  <p>适配本地量化场景: 内联 CSS(无 CDN 依赖) + AST 三轴分类 + 量化术语建议</p>
</footer>

</body>
</html>"""


# ============================================================
# 4. 终端文本输出(保留原逻辑, 兼容现有用法)
# ============================================================

def print_terminal_report(all_funcs: list[dict], files_scanned: int) -> None:
    """终端文本输出(保留原脚本行为)"""
    print(f"扫描生产模块: {files_scanned} 个文件, {len(all_funcs)} 个函数/方法")
    print()

    long_funcs = sorted(
        [r for r in all_funcs if r['length'] > THRESH_LONG],
        key=lambda x: -x['length']
    )
    print(f"=== 超长函数 (>{THRESH_LONG}行): {len(long_funcs)} 个 ===")
    for r in long_funcs[:20]:
        print(f"  {r['length']:4}行  CC={r['cc']:2}  args={r['args']}  "
              f"{r['file']}:{r['line']}  {r['name']}()")
    if len(long_funcs) > 20:
        print(f"  ... 还有 {len(long_funcs)-20} 个")
    print()

    high_cc = sorted(
        [r for r in all_funcs if r['cc'] > THRESH_CC],
        key=lambda x: -x['cc']
    )
    print(f"=== 高复杂度函数 (CC>{THRESH_CC}): {len(high_cc)} 个 ===")
    for r in high_cc[:20]:
        print(f"  CC={r['cc']:3}  {r['length']:4}行  args={r['args']}  "
              f"{r['file']}:{r['line']}  {r['name']}()")
    if len(high_cc) > 20:
        print(f"  ... 还有 {len(high_cc)-20} 个")
    print()

    many_args = sorted(
        [r for r in all_funcs if r['args'] > THRESH_ARGS],
        key=lambda x: -x['args']
    )
    print(f"=== 多参数函数 (args>{THRESH_ARGS}): {len(many_args)} 个 ===")
    for r in many_args[:20]:
        print(f"  args={r['args']:2}  {r['length']:4}行  CC={r['cc']:2}  "
              f"{r['file']}:{r['line']}  {r['name']}()")
    if len(many_args) > 20:
        print(f"  ... 还有 {len(many_args)-20} 个")
    print()

    print("=== TOP 30 最长函数(完整列表) ===")
    for r in sorted(all_funcs, key=lambda x: -x['length'])[:30]:
        flag = ""
        if r['length'] > 200:
            flag += " 🔴"
        elif r['length'] > 120:
            flag += " 🟡"
        if r['cc'] > 20:
            flag += " [高CC]"
        if r['args'] > 5:
            flag += " [多参数]"
        print(f"  {r['length']:4}行  CC={r['cc']:2}  args={r['args']:2}  "
              f"{r['file']}:{r['line']}  {r['name']}(){flag}")


# ============================================================
# 5. 主流程
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="生产模块函数质量评估 + HTML 可视化报告"
    )
    parser.add_argument(
        '--no-html', action='store_true',
        help='仅输出终端文本, 不生成 HTML 报告'
    )
    parser.add_argument(
        '--no-open', action='store_true',
        help='生成 HTML 但不自动打开浏览器'
    )
    args = parser.parse_args()

    # 1. AST 扫描
    all_funcs, files_scanned = scan_all_functions()

    # 2. 终端输出(始终打印)
    print_terminal_report(all_funcs, files_scanned)

    if args.no_html:
        return

    # 3. 严重度分类
    for f in all_funcs:
        f['severity'], f['flags'] = classify_severity(f)

    # 4. 筛选问题函数并排序
    problem_funcs = [f for f in all_funcs if f['severity'] is not None]
    problem_funcs.sort(
        key=lambda x: (SEVERITY_ORDER[x['severity']], -x['length'], -x['cc'])
    )

    # 5. Top recommendation: 选 Strong 中最长的
    top_func = None
    strong_funcs = [f for f in problem_funcs if f['severity'] == 'Strong']
    if strong_funcs:
        top_func = max(strong_funcs, key=lambda x: (x['length'], x['cc']))
    elif problem_funcs:
        # 没有 Strong 就选 Worth exploring 中最长的
        worth_funcs = [f for f in problem_funcs if f['severity'] == 'Worth exploring']
        if worth_funcs:
            top_func = max(worth_funcs, key=lambda x: (x['length'], x['cc']))

    # 6. 生成 HTML 报告
    html = render_html_report(all_funcs, files_scanned, problem_funcs, top_func)

    # 7. 写到 OS temp 目录(Windows %TEMP%, Linux /tmp, macOS /tmp)
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    temp_dir = os.environ.get('TEMP') or os.environ.get('TMP') or '/tmp'
    output_path = Path(temp_dir) / f'func-quality-report-{timestamp}.html'
    output_path.write_text(html, encoding='utf-8')

    print()
    print(f"=" * 60)
    print(f"📋 HTML 报告已生成:")
    print(f"   {output_path}")
    print(f"=" * 60)
    print(f"   问题函数: {len(problem_funcs)} 个")
    strong_count = sum(1 for f in problem_funcs if f['severity'] == 'Strong')
    worth_count = sum(1 for f in problem_funcs if f['severity'] == 'Worth exploring')
    spec_count = sum(1 for f in problem_funcs if f['severity'] == 'Speculative')
    print(f"   Strong: {strong_count} | Worth exploring: {worth_count} | Speculative: {spec_count}")
    if top_func:
        print(f"   ⭐ Top: {top_func['name']}() ({top_func['length']}行, CC={top_func['cc']})")

    # 8. 自动打开浏览器
    if not args.no_open:
        try:
            webbrowser.open(output_path.as_uri())
            print(f"   已自动在浏览器中打开")
        except (OSError, ValueError, RuntimeError) as e:
            print(f"   ⚠️ 自动打开失败: {e}, 请手动打开上述路径")


if __name__ == '__main__':
    main()
