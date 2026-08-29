"""
简单密钥扫描脚本 (gitleaks 替代)
扫描代码库中的硬编码密钥/Token/凭证
"""

import os
import re
from datetime import datetime
from pathlib import Path

ROOT = Path("e:/各种PY程序/28-终极量化交易系统8.4")
EXCLUDE_DIRS = {
    ".venv",
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "qlib",
    "external",
    "unsloth_compiled_cache",
    "ifind-finance-data-1.3.0",
    "data_cache",
    "models",
    "mlruns",
    ".qlib_experiments",
    "node_modules",
    "build",
    "dist",
    "量化系统升级为实盘系统—1783205278319",
    ".codeartsdoer",
    ".codebuddy",
    ".workbuddy",
    ".loopx_handoffs",
    ".cairn",
    ".agents",
    ".arts",
    ".claude",
    ".ocr_home",
    ".tmp_pip",
    "sim_snapshots",
    "downloads",
    "cache",
    "logs",
    ".benchmarks",
}

# 密钥模式 (高置信度)
PATTERNS = [
    (r'(?:api_key|apikey|api-key)\s*[=:]\s*["\'][a-zA-Z0-9]{20,}["\']', "API Key"),
    (
        r'(?:secret|token|password|passwd|pwd)\s*[=:]\s*["\'][a-zA-Z0-9]{16,}["\']',
        "Secret/Token",
    ),
    (r"(?:AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}", "AWS Access Key"),
    (r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", "Private Key"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub PAT"),
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI/Stripe Key"),
    (r"xox[baprs]-[a-zA-Z0-9-]{10,}", "Slack Token"),
    (r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}", "JWT Token"),
]

# 误报过滤 (这些是配置键名而非实际密钥)
FALSE_POSITIVES = [
    "os.environ",
    "os.getenv",
    "os.environ.get",
    "config.get",
    "settings.get",
    "getenv",
    "YOUR_API_KEY",
    "YOUR_TOKEN",
    "EXAMPLE",
    "example",
    "placeholder",
    "PLACEHOLDER",
    "os.environ[",
    "environ.get",
]


def scan_file(filepath: Path) -> list:
    """扫描单个文件"""
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    findings = []
    for line_num, line in enumerate(content.splitlines(), 1):
        # 跳过注释行和导入行
        stripped = line.strip()
        if (
            stripped.startswith("#")
            or stripped.startswith("import")
            or stripped.startswith("from")
        ):
            continue

        for pattern, key_type in PATTERNS:
            matches = re.findall(pattern, line, re.IGNORECASE)
            if matches:
                # 检查是否是误报
                is_false_positive = any(fp in line for fp in FALSE_POSITIVES)
                if not is_false_positive:
                    findings.append(
                        {
                            "file": str(filepath.relative_to(ROOT)),
                            "line": line_num,
                            "type": key_type,
                            "match": (
                                matches[0][:60] + "..."
                                if len(matches[0]) > 60
                                else matches[0]
                            ),
                        }
                    )
    return findings


def main():
    print("=== 密钥扫描 (gitleaks 替代) ===")
    print(f"扫描根目录: {ROOT}")
    print(f"时间: {datetime.now().isoformat()}")
    print()

    all_findings = []
    files_scanned = 0

    for dirpath, dirnames, filenames in os.walk(ROOT):
        # 排除目录
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        for filename in filenames:
            if not filename.endswith(
                (".py", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".env")
            ):
                continue
            if filename == ".env.example" or filename == ".env":
                continue  # .env 已 gitignore, .env.example 是模板

            filepath = Path(dirpath) / filename
            files_scanned += 1
            findings = scan_file(filepath)
            all_findings.extend(findings)

    print(f"扫描文件数: {files_scanned}")
    print(f"发现疑似密钥: {len(all_findings)}")
    print()

    if all_findings:
        print("=== 疑似密钥清单 ===")
        for f in all_findings:
            print(f"  [{f['type']}] {f['file']}:{f['line']} → {f['match']}")
    else:
        print("✅ 未发现硬编码密钥 (代码库清洁)")

    return 0 if not all_findings else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
