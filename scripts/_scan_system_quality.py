"""全项目系统级代码质量扫描 — 综合性扫描器.

扫描维度:
    1. TYPE_IGNORE 残留 (# type: ignore)
    2. SYS_PATH 污染 (sys.path.insert)
    3. pickle.load / pickle.loads 反序列化风险
    4. print 调试语句残留
    5. 硬编码密钥/Token (basic auth/password)
    6. eval / exec 残留
    7. subprocess shell=True 命令注入
    8. os.system 命令执行
    9. bare except (except: 无类型)
    10. assert 在生产代码中 (非测试)

用法: py -3.11 scripts/_scan_system_quality.py [dir1] [dir2] ...
默认扫描: utils/ ms_strategy/ ai_decision/ scripts/
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 默认扫描目录
DEFAULT_DIRS = ["utils", "ms_strategy", "ai_decision", "scripts"]

# 扫描规则 (pattern, severity, category, description)
SCAN_RULES: list[tuple[str, str, str, str]] = [
    # P0 安全风险 (后置过滤排除字符串和 PyTorch 方法)
    (r"\beval\s*\(", "P0", "eval_usage", "eval() 调用 — 代码注入风险"),
    (r"\bexec\s*\(", "P0", "exec_usage", "exec() 调用 — 代码注入风险"),
    (r"\bos\.system\s*\(", "P0", "os_system", "os.system 命令执行 — 命令注入风险"),
    (r"subprocess\..*shell\s*=\s*True", "P0", "shell_injection", "subprocess shell=True — 命令注入"),
    (r"pickle\.loads?\s*\(", "P0", "pickle_load", "pickle.load 反序列化 — 不安全反序列化"),
    (r"yaml\.load\s*\(", "P0", "yaml_unsafe_load", "yaml.load 不安全加载 (应用 safe_load)"),
    (r"\bassert\s+", "P1", "assert_in_prod", "生产代码中的 assert (测试外)"),
    # P1 代码质量
    (r"#\s*type:\s*ignore", "P1", "type_ignore", "type: ignore 注释 — 类型逃逸"),
    (r"sys\.path\.insert\s*\(", "P2", "sys_path_pollution", "sys.path.insert — 路径污染"),
    (r"sys\.path\.append\s*\(", "P2", "sys_path_pollution", "sys.path.append — 路径污染"),
    (r"^\s*print\s*\(", "P2", "print_debug", "print 调试语句残留"),
    # P2 安全
    (r'password\s*=\s*["\'][^"\']+["\']', "P1", "hardcoded_password", "硬编码密码"),
    (r'token\s*=\s*["\'][^"\']+["\']', "P1", "hardcoded_token", "硬编码 Token"),
    (r'api_key\s*=\s*["\'][^"\']+["\']', "P1", "hardcoded_apikey", "硬编码 API Key"),
    # bare except
    (r"^\s*except\s*:", "P1", "bare_except", "bare except (无异常类型)"),
]


def _is_false_positive(line: str, category: str) -> bool:
    """判断是否为误报.

    误报场景:
        - 字符串字面量中的 "eval()" 描述
        - PyTorch model.eval() 链式调用 (方法名 .eval())
        - 注释中的描述
        - dict key 字符串 "eval": ...
        - 文档字符串 / 规则定义中的示例文本
    """
    stripped = line.strip()
    # 字符串字面量场景: eval/exec/os.system/shell_injection 在字符串中不算违规
    if category in ("eval_usage", "exec_usage", "os_system", "shell_injection"):
        # 跳过含中文描述性文本的串 ("调用"/"存在"/"风险"/"注入" 等)
        if any(k in stripped for k in ("调用", "存在", "风险", "注入", "命令", "描述", "示例")):
            return True
        # 跳过 dict 字面量 "eval": ... 这种字符串键
        if re.match(r'^["\']eval["\']\s*:', stripped) or re.match(r'^["\']exec["\']\s*:', stripped):
            return True
        # PyTorch model.eval() / tensor.eval() / self._model.eval() 等链式调用 (含 . 前缀)
        if re.search(r"\w+\.\s*eval\s*\(", stripped):
            return True
        # 注释行
        if stripped.startswith("#"):
            return True
        # os.system / shell_injection: 跳过模块级 docstring 中的固定常量命令描述
        if category in ("os_system", "shell_injection") and (
            stripped.startswith('"""') or stripped.startswith("'''")
        ):
            return True
        # shell_injection: 命令为固定常量列表字面量 (无 f-string/变量插值/字符串拼接) 视为安全用法
        #   例: subprocess.run(['chcp', '65001'], shell=True) — 无外部输入, 无注入风险
        if category == "shell_injection":
            if "f'" in stripped or 'f"' in stripped or "{_" in stripped or "+" in stripped \
                    or "format(" in stripped or "os.path.join" in stripped:
                return False  # 含动态拼接, 保留告警
            if re.search(r"subprocess\.\w+\(\s*\[", stripped):
                return True  # 参数为字面量列表常量, 误报
    return False

# 文件排除规则 (相对路径匹配)
EXCLUDE_PATTERNS = [
    r"[/\\]__pycache__[/\\]",
    r"[/\\]\.",
    r"\.bak",
    r"\.bak-",
    r"_archive[/\\]",
    r"[/\\]venv[/\\]",
    r"[/\\]env[/\\]",
    r"[/\\]\.venv[/\\]",
    r"[/\\]node_modules[/\\]",
    # 第三方依赖 / 虚拟环境 (非本项目代码, 误报噪音源)
    r"[/\\]qlib_env[/\\]",
    r"site-packages[/\\]",
    r"[/\\]\.codebuddy[/\\]",
    # 参考项目与测试样本 (含预期违规的样本数据, 非真实生产代码)
    r"[/\\]research[/\\]references[/\\]",
    r"[/\\]tests[/\\]",
]

# 测试文件路径模式 (assert 允许)
TEST_FILE_PATTERN = re.compile(r"(test_|_test\.py$|tests[/\\])")


@dataclass
class Violation:
    """单条违规记录."""

    file: str
    line: int
    column: int
    severity: str
    category: str
    description: str
    code: str


@dataclass
class ScanReport:
    """扫描报告."""

    total_files_scanned: int = 0
    violations: list[Violation] = field(default_factory=list)

    def by_severity(self) -> dict[str, list[Violation]]:
        """按严重程度分组."""
        groups: dict[str, list[Violation]] = defaultdict(list)
        for v in self.violations:
            groups[v.severity].append(v)
        return dict(groups)

    def by_category(self) -> dict[str, list[Violation]]:
        """按类别分组."""
        groups: dict[str, list[Violation]] = defaultdict(list)
        for v in self.violations:
            groups[v.category].append(v)
        return dict(groups)

    def by_file(self) -> dict[str, list[Violation]]:
        """按文件分组."""
        groups: dict[str, list[Violation]] = defaultdict(list)
        for v in self.violations:
            groups[v.file].append(v)
        return dict(groups)


def _is_excluded(path: Path) -> bool:
    """判断文件是否应被排除."""
    s = str(path)
    for pat in EXCLUDE_PATTERNS:
        if re.search(pat, s):
            return True
    return False


def _scan_file(path: Path, rules: list[tuple[str, str, str, str]]) -> list[Violation]:
    """扫描单个文件."""
    if path.suffix != ".py":
        return []
    is_test = bool(TEST_FILE_PATTERN.search(str(path)))

    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except (UnicodeDecodeError, OSError):
        return []

    violations: list[Violation] = []
    lines = content.splitlines()

    # 预扫描函数/方法定义, 记录每个 def 的起始行与名称 (用于 assert 误报排除)
    def_spans: list[tuple[int, str]] = []
    def_re = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    for idx, ln in enumerate(lines, 1):
        m = def_re.match(ln)
        if m:
            def_spans.append((idx, m.group(1)))

    def _func_name_at(line_no: int) -> str:
        name = ""
        for start, fname in def_spans:
            if start <= line_no:
                name = fname
            else:
                break
        return name

    for i, line in enumerate(lines, 1):
        for pattern, severity, category, description in rules:
            # 跳过测试文件中的 assert
            if category == "assert_in_prod" and is_test:
                continue
            # 跳过 self_test / _test / test_ 方法体内的 assert (非生产校验路径)
            if category == "assert_in_prod":
                fn = _func_name_at(i)
                if fn and ("self_test" in fn or fn.startswith("test_") or fn.endswith("_test")):
                    continue
                # 跳过注释行 / docstring / 字符串中的 "assert" 描述文本 (非真实断言语句)
                s_line = line.strip()
                if (s_line.startswith("#")
                        or s_line.startswith('"""') or s_line.startswith("'''")
                        or s_line.startswith('"') or s_line.startswith("'")
                        or "assert 在生产" in s_line or "assert_in_prod" in s_line
                        or "assert 允许" in s_line or "assert (测试" in s_line
                        or "跳转 self_test" in s_line):
                    continue
            # 跳过 docstring 中的示例 (粗略检测)
            if category in ("eval_usage", "exec_usage", "os_system", "shell_injection") and (
                line.strip().startswith("#")
                or line.strip().startswith('"""')
                or line.strip().startswith("'''")
                or line.strip().startswith('"')
                or line.strip().startswith("'")
            ):
                continue

            for m in re.finditer(pattern, line):
                col = m.start() + 1
                code = line.strip()
                # 限制 code 长度
                if len(code) > 120:
                    code = code[:117] + "..."
                # 后置过滤: 排除误报
                if _is_false_positive(line, category):
                    continue
                # 硬编码密钥: 排除占位符/示例值 (your_key / ollama / <...> / 空串 / env 引用)
                if category in ("hardcoded_apikey", "hardcoded_token", "hardcoded_password"):
                    # 从命中的同一处赋值提取真实值 (避免取到行内其他 '=' 引号串)
                    val_m = re.search(r'=\s*["\']([^"\']*)["\']', m.group(0))
                    raw = (val_m.group(1) if val_m else "")
                    if (not raw
                            or raw in ("your_key", "ollama", "changeme", "test", "dummy")
                            or (raw.startswith("<") and raw.endswith(">"))
                            or "os.environ" in line or "getenv" in line or "get(" in line):
                        continue
                violations.append(
                    Violation(
                        file=str(path.relative_to(PROJECT_ROOT)),
                        line=i,
                        column=col,
                        severity=severity,
                        category=category,
                        description=description,
                        code=code,
                    )
                )

    return violations


def scan_directory(target: Path) -> tuple[int, list[Violation]]:
    """扫描目录下所有 .py 文件."""
    if not target.exists():
        return 0, []
    if target.is_file():
        if _is_excluded(target):
            return 0, []
        return 1, _scan_file(target, SCAN_RULES)

    files_scanned = 0
    all_violations: list[Violation] = []

    for py_file in target.rglob("*.py"):
        if _is_excluded(py_file):
            continue
        files_scanned += 1
        all_violations.extend(_scan_file(py_file, SCAN_RULES))

    return files_scanned, all_violations


def main(argv: list[str] | None = None) -> int:
    """扫描入口."""
    args = argv if argv else DEFAULT_DIRS

    print("=" * 80)
    print("  全项目系统级代码质量扫描")
    print("=" * 80)
    print(f"  扫描目录: {args}")
    print(f"  扫描规则: {len(SCAN_RULES)} 条")
    print(f"  排除模式: {len(EXCLUDE_PATTERNS)} 条")
    print()

    total_files = 0
    all_violations: list[Violation] = []

    for d in args:
        target = PROJECT_ROOT / d
        if not target.exists():
            print(f"  [SKIP] 目录不存在: {d}")
            continue

        files_scanned, violations = scan_directory(target)
        total_files += files_scanned
        all_violations.extend(violations)
        print(f"  [{d}] 文件数: {files_scanned}, 违规数: {len(violations)}")

    print()
    print("=" * 80)
    print("  汇总报告")
    print("=" * 80)
    print(f"  总扫描文件: {total_files}")
    print(f"  总违规数: {len(all_violations)}")
    print()

    # 按严重程度分组
    by_sev = defaultdict(list)
    for v in all_violations:
        by_sev[v.severity].append(v)

    print("--- 按严重程度 ---")
    for sev in ("P0", "P1", "P2"):
        vs = by_sev.get(sev, [])
        print(f"  {sev}: {len(vs)} 处")

    print()
    print("--- 按类别 ---")
    by_cat = defaultdict(list)
    for v in all_violations:
        by_cat[v.category].append(v)
    for cat, vs in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        print(f"  {cat:25s}: {len(vs):4d} 处")

    # P0 详情
    p0 = by_sev.get("P0", [])
    if p0:
        print()
        print("=" * 80)
        print(f"  P0 严重违规详情 (共 {len(p0)} 处)")
        print("=" * 80)
        for v in p0:
            print(f"  [{v.category}] {v.file}:{v.line}")
            print(f"    {v.description}")
            print(f"    > {v.code}")
            print()

    # P1 概要 (Top 20)
    p1 = by_sev.get("P1", [])
    if p1:
        print()
        print("--- P1 违规 Top 20 ---")
        for v in p1[:20]:
            print(f"  [{v.category}] {v.file}:{v.line}: {v.code[:80]}")
        if len(p1) > 20:
            print(f"  ... 还有 {len(p1) - 20} 处 P1 违规")

    # 输出 JSONL 报告
    report_path = PROJECT_ROOT / "reports" / "quality_scan" / f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    with open(report_path, "w", encoding="utf-8") as f:
        for v in all_violations:
            f.write(json.dumps({
                "file": v.file,
                "line": v.line,
                "severity": v.severity,
                "category": v.category,
                "description": v.description,
                "code": v.code,
            }, ensure_ascii=False) + "\n")
    print()
    print(f"  JSONL 报告已写入: {report_path}")

    # 退出码: 有 P0 → 1, 有 P1 → 2, 仅 P2 → 0
    if p0:
        return 1
    if p1:
        return 2
    return 0


if __name__ == "__main__":
    from datetime import datetime  # noqa: E402
    raise SystemExit(main())
