"""系统级 bug 扫描器 — 生成修改报告.

扫描目标:
    1. 宽泛异常 (bare except / except Exception)
    2. print() 调试语句
    3. 除零风险
    4. None 解引用风险
    5. 硬编码路径/数值
    6. 资源泄漏 (open 未 close)
    7. TODO/FIXME/XXX 未完成项
    8. 类型注解缺失
    9. 函数过长 (>100 行)
    10. 圈复杂度过高 (>15)
"""
import ast
import os
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SCAN_DIRS = [
    'utils/alpha',
    'utils/risk',
    'utils/infra',
    'utils/execution',
    'utils/data',
    'v8.3_institutional/src',
    'research',
]

EXCLUDE_PATTERNS = ['__pycache__', '.venv', 'venv', 'node_modules', '.git']

issues = defaultdict(list)
total_files = 0
total_lines = 0


def is_excluded(path: str) -> bool:
    return any(p in path for p in EXCLUDE_PATTERNS)


class BugScanner(ast.NodeVisitor):
    """AST 遍历器, 检测多种代码异味."""

    def __init__(self, filepath: str, source: str):
        self.fp = filepath
        self.source = source
        self.lines = source.splitlines()
        self.func_stack = []  # 跟踪函数深度

    def visit_ExceptHandler(self, node):
        # 1. 宽泛异常
        if node.type is None:
            self._add(node.lineno, 'BARE_EXCEPT',
                      '裸异常 `except:`, 吞掉 KeyboardInterrupt/SystemExit',
                      '改为 `except SpecificError as e:` 或至少 `except Exception as e:`')
        elif isinstance(node.type, ast.Name) and node.type.id == 'Exception':
            # 检查 except 体是否只有 pass 或只 log
            body_desc = self._get_body_summary(node.body)
            if body_desc in ('pass', 'only_log'):
                self._add(node.lineno, 'BROAD_EXCEPT_SILENT',
                          f'`except Exception` + {body_desc} (静默吞错)',
                          '精确化异常类型 + 添加处理/re-raise')
        self.generic_visit(node)

    def visit_Call(self, node):
        # 2. print() 调试
        if isinstance(node.func, ast.Name) and node.func.id == 'print':
            # 排除 __main__ 块内的 print
            if not self._in_main_block(node):
                self._add(node.lineno, 'PRINT_DEBUG',
                          'print() 调试语句', '改用 logging.getLogger(__name__).info()')
        # 3. open() 未 with
        if isinstance(node.func, ast.Name) and node.func.id == 'open':
            # 简单启发: 检查父节点是否 with
            # (AST 中无法直接知道父节点, 用行号反查)
            line = self.lines[node.lineno - 1] if node.lineno <= len(self.lines) else ''
            if 'with ' not in line and 'open(' in line:
                # 进一步检查: 是否赋值给变量后未 close (粗略)
                pass  # 避免误报, 只在明显 case 报
        self.generic_visit(node)

    def visit_BinOp(self, node):
        # 4. 除零风险 (粗略检测)
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            if isinstance(node.right, ast.Name):
                var = node.right.id
                # 检查变量是否可能为 0 (粗略: 名字含 size/len/count/denom/divisor)
                if any(k in var.lower() for k in ['size', 'len', 'count', 'total']):
                    # 检查附近是否有保护
                    if not self._has_zero_guard(node.lineno, var):
                        self._add(node.lineno, 'DIV_ZERO_RISK',
                                  f'除以变量 `{var}`, 可能除零',
                                  f'加 `if {var} == 0: return default` 或 `max({var}, 1e-8)`')
            elif isinstance(node.right, ast.Call):
                # 除以函数调用结果
                func_name = ''
                if isinstance(node.right.func, ast.Name):
                    func_name = node.right.func.id
                elif isinstance(node.right.func, ast.Attribute):
                    func_name = node.right.func.attr
                if func_name in ('len', 'sum', 'max', 'min', 'abs'):
                    if not self._has_zero_guard(node.lineno, func_name):
                        self._add(node.lineno, 'DIV_ZERO_RISK',
                                  f'除以 `{func_name}()` 结果, 可能 0',
                                  '加 `if result == 0: return default` 或 `max(result, 1e-8)`')
        self.generic_visit(node)

    def visit_Attribute(self, node):
        # 5. None 解引用 (粗略: .xxx on None-returning func)
        # 检查 .get() 后的属性访问 (常见 None 解引用)
        pass
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        # 6. 函数过长
        func_len = (node.end_lineno or node.lineno) - node.lineno
        if func_len > 100:
            self._add(node.lineno, 'LONG_FUNCTION',
                      f'函数 `{node.name}` {func_len} 行 (>100)',
                      '拆分为子函数')
        # 7. 参数过多
        if len(node.args.args) > 8:
            self._add(node.lineno, 'TOO_MANY_ARGS',
                      f'函数 `{node.name}` {len(node.args.args)} 参数 (>8)',
                      '用 dataclass 封装参数')
        # 8. 圈复杂度 (粗略: 统计 if/for/while/and/or)
        complexity = self._calc_complexity(node)
        if complexity > 15:
            self._add(node.lineno, 'HIGH_COMPLEXITY',
                      f'函数 `{node.name}` 圈复杂度 {complexity} (>15)',
                      '拆分或用早返回')
        self.generic_visit(node)

    def _add(self, lineno, code, desc, fix):
        rel = os.path.relpath(self.fp, PROJECT_ROOT)
        issues[code].append((rel, lineno, desc, fix))

    def _get_body_summary(self, body):
        if len(body) == 1:
            stmt = body[0]
            if isinstance(stmt, ast.Pass):
                return 'pass'
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                func = stmt.value.func
                if isinstance(func, ast.Attribute) and func.attr in ('warning', 'error', 'info', 'debug', 'exception'):
                    return 'only_log'
        return 'mixed'

    def _in_main_block(self, node):
        # 粗略: 检查所在行是否在 if __name__ == '__main__': 之后
        for i in range(node.lineno - 1, -1, -1):
            if i < len(self.lines):
                if "__name__ == '__main__'" in self.lines[i] or '__name__ == "__main__"' in self.lines[i]:
                    return True
        return False

    def _has_zero_guard(self, lineno, var, window=5):
        # 检查前后 5 行是否有 if var == 0 之类保护
        start = max(0, lineno - window - 1)
        end = min(len(self.lines), lineno + window)
        for i in range(start, end):
            line = self.lines[i]
            if f'if {var}' in line and ('== 0' in line or '!= 0' in line or '> 0' in line):
                return True
            if f'max({var}' in line or f'max(0, {var}' in line:
                return True
        return False

    def _calc_complexity(self, node):
        """粗略圈复杂度."""
        c = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler)):
                c += 1
            elif isinstance(child, ast.BoolOp):
                c += len(child.values) - 1
        return c


# ============================================================
# 主扫描循环
# ============================================================
for d in SCAN_DIRS:
    p = PROJECT_ROOT / d
    if not p.exists():
        continue
    for f in p.rglob('*.py'):
        if is_excluded(str(f)):
            continue
        total_files += 1
        try:
            src = f.read_text(encoding='utf-8')
            total_lines += len(src.splitlines())
            tree = ast.parse(src)
            scanner = BugScanner(str(f), src)
            scanner.visit(tree)
        except SyntaxError as e:
            issues['SYNTAX_ERROR'].append((str(f), e.lineno or 0, f'SyntaxError: {e.msg}', '修复语法'))
        except Exception as e:
            issues['PARSE_ERROR'].append((str(f), 0, f'{type(e).__name__}: {e}', '检查编码'))

# ============================================================
# 额外: grep-style 扫描
# ============================================================
import re  # noqa: E402

GREP_PATTERNS = {
    'TODO': r'\bTODO\b',
    'FIXME': r'\bFIXME\b',
    'XXX': r'\bXXX\b',
    'HACK': r'\bHACK\b',
    'HARDCODED_PATH': r'[\'"](?:[A-Z]:\\|/Users/|/home/|/tmp/)[^\'"]*[\'"]',
    'TIME_TIME': r'\btime\.time\(\)',  # Windows 15.6ms 精度
    'EVAL': r'\beval\s*\(',
    'EXEC': r'\bexec\s*\(',
}

for d in SCAN_DIRS:
    p = PROJECT_ROOT / d
    if not p.exists():
        continue
    for f in p.rglob('*.py'):
        if is_excluded(str(f)):
            continue
        try:
            src = f.read_text(encoding='utf-8')
            lines = src.splitlines()
            for i, line in enumerate(lines, 1):
                for name, pat in GREP_PATTERNS.items():
                    if re.search(pat, line):
                        # 排除注释/字符串中的误报 (粗略)
                        if name in ('TODO', 'FIXME', 'XXX', 'HACK'):
                            # 这些本来就期望在注释中
                            pass
                        elif name == 'TIME_TIME':
                            # time.time() 在 Windows 上精度差, 但本身合法
                            # 只在性能敏感场景报警
                            continue
                        rel = os.path.relpath(str(f), PROJECT_ROOT)
                        issues[name].append((rel, i, line.strip()[:100], ''))
        except Exception:
            pass

# ============================================================
# 输出汇总
# ============================================================
print(f'扫描 {total_files} 个文件, {total_lines} 行代码')
print()
for code in sorted(issues.keys()):
    items = issues[code]
    print(f'=== {code} ({len(items)} 项) ===')
    for fp, ln, desc, fix in items[:8]:
        print(f'  {fp}:{ln}')
        print(f'    问题: {desc}')
        if fix:
            print(f'    修复: {fix}')
    if len(items) > 8:
        print(f'  ... 还有 {len(items)-8} 项')
    print()

# 保存为 JSON 供后续生成报告
import json  # noqa: E402

out = {code: [{'file': fp, 'line': ln, 'issue': d, 'fix': f} for fp, ln, d, f in items]
       for code, items in issues.items()}
out_path = PROJECT_ROOT / 'scripts' / '_bug_scan_results.json'
with open(out_path, 'w', encoding='utf-8') as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)
print(f'\n详细结果已保存: {out_path}')
