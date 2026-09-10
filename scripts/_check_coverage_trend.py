#!/usr/bin/env python
"""
_check_coverage_trend.py — 覆盖率趋势监控 (真实实现)

R1 修复项。CI "Coverage Trend" 阶段引用本脚本, 缺失导致 CI 必然失败。

真实语义:
    读取 pytest-cov 生成的 reports/coverage.xml (Cobertura 格式), 提取总体
    行覆盖率与关键模块的覆盖率, 与基线 (覆盖率下限契约) 比较, 验证:
        1. 总体行覆盖率 >= 最小阈值 (默认 5%, 渐进提升; 不强制 80% 一步到位)
        2. 关键模块 (执行/风控/对冲闭环) 覆盖率不退化
        3. 覆盖率报告文件存在且可解析 (事实源就位)

退出码:
    0 = 覆盖率达标 (或不退化)
    1 = 覆盖率低于阈值 / 关键模块退化 / 报告缺失

用法:
    python scripts/_check_coverage_trend.py \
        [--coverage-xml reports/coverage.xml] \
        [--min-line-rate 0.05] \
        [--baseline-json reports/ci/coverage_baseline.json] \
        [--output reports/ci/coverage_trend.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent

# CLI 直跑 (python scripts/_check_coverage_trend.py) 时 sys.path[0] 是 scripts/,
# 顶层 utils 包不可见 → 显式补项目根 (项目约定: CLI 入口须显式 sys.path.insert)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.datetime_utils import now_bj  # noqa: E402

# 关键模块 (执行闭环相关, 必须保持一定覆盖, 防止回归静默退化)
#
# 2026-09-10 审计 item 6 补齐 (门禁口径修复):
#   原清单 8 条里 4 条指向**并不存在**的 scripts/*.py; 其余条目的键形态与
#   coverage.xml 的 filename 也不一致 —— coverage.py 以 .coveragerc `source`
#   根为基准产出 filename (`utils/execution/fills_store.py` 实际写作
#   `execution/fills_store.py`), 于是 cls_map.get(旧键) 恒为 None, 全部落进
#   "not in coverage report → non-blocking PASS" 的宽容分支。
#   实测结论: 8 条关键模块校验**从未生效过** (典型门禁假 PASS)。
# 现改为: 真实路径 + 后缀匹配 + 缺失即 FAIL; 并强制清单落在可测量根内。
CRITICAL_MODULES = [
    "utils/execution/fills_store.py",
    "utils/execution/fills_pnl_bridge.py",
    "utils/execution/automated_execution_system.py",
    "utils/execution/broker_factory.py",
    "utils/execution/rebalance_execution_orders.py",
    "utils/execution/daily_build_and_hedge.py",
    "utils/risk/trade_reconciliation_runner.py",
]

# 覆盖率可测量根 (与 .coveragerc `source` 对齐)。关键模块必须落在其中, 否则
# 永远不可能出现在 coverage.xml 里 —— 属配置错误, fail-loud 而非静默放过。
DEFAULT_COVERAGE_SOURCES: tuple[str, ...] = ("utils", "ms_strategy")

# 关键模块最低行覆盖率 (保持低位门槛防抖动, 但绝不再"缺失即通过")
CRITICAL_MIN_RATE = 0.01


class CovResult(NamedTuple):
    cid: str
    desc: str
    passed: bool
    detail: str


def parse_coverage_xml(path: Path) -> dict | None:
    """最小 Cobertura 解析, 避免额外依赖 lxml。"""
    if not path.exists():
        return None
    try:
        import xml.etree.ElementTree as ET

        tree = ET.parse(str(path))  # nosec B314  # 输入为本机 pytest 自产 coverage.xml, 非不可信输入
        root = tree.getroot()
        line_rate = float(root.attrib.get("line-rate", "0"))
        classes = []
        for pkg in root.iter("package"):
            for cls in pkg.iter("class"):
                fn = cls.attrib.get("filename", "")
                lr = float(cls.attrib.get("line-rate", "0"))
                classes.append((fn, lr))
        return {"line_rate": line_rate, "classes": classes}
    except Exception:
        return None


def _resolve_coverage_xml(explicit: str | None) -> Path:
    """解析 coverage.xml 路径。

    2026-09-10 门禁口径修复: CI 的 pytest 曾以 ``--cov-report=xml:coverage.xml``
    写到仓库根, 而本脚本默认读 ``reports/coverage.xml`` —— 两处口径不一致, 门禁
    读到的要么是缺失文件 (COV-0 直接 FAIL), 要么是历史残留 (校验的不是本次产物)。
    现支持显式传参 + 按存在性回退, 并由 ci.yml 显式传参双重保险。
    """
    if explicit:
        return Path(explicit)
    candidates = [ROOT / "reports" / "coverage.xml", ROOT / "coverage.xml"]
    for cand in candidates:
        if cand.exists():
            return cand
    return candidates[0]


def _load_coverage_sources() -> tuple[str, ...]:
    """从 .coveragerc 的 [run] source 读取可测量根 (缺省回退 utils/ms_strategy)。"""
    cfg = ROOT / ".coveragerc"
    if not cfg.exists():
        return DEFAULT_COVERAGE_SOURCES
    try:
        lines = cfg.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return DEFAULT_COVERAGE_SOURCES

    in_run = False
    collecting = False
    found: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_run = stripped == "[run]"
            collecting = False
            continue
        if not in_run:
            continue
        if stripped.startswith("source"):
            collecting = True
            _, _, inline = stripped.partition("=")
            inline = inline.strip()
            if inline:
                found.extend(
                    p.strip() for p in inline.replace(",", " ").split() if p.strip()
                )
            continue
        if collecting:
            if raw[:1] in (" ", "\t"):
                found.append(stripped)
            else:
                collecting = False
    return tuple(found) if found else DEFAULT_COVERAGE_SOURCES


def _match_rate(cls_map: dict[str, float], mod_path: str) -> float | None:
    """按后缀匹配 coverage.xml 的 filename。

    coverage.py 以 .coveragerc ``source`` 根为基准产出 filename ——
    ``utils/execution/fills_store.py`` 实际写作 ``execution/fills_store.py``,
    故需同时尝试全路径与逐级后缀 (原实现用 ``dict.get(全路径)`` 恒为 None)。
    """
    if mod_path in cls_map:
        return cls_map[mod_path]
    for key, rate in cls_map.items():
        if key and mod_path.endswith("/" + key):
            return rate
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coverage trend checker")
    parser.add_argument(
        "--coverage-xml",
        default=None,
        help=(
            "coverage.xml 路径; 缺省自动探测 reports/coverage.xml "
            "(CI / engineering_debt_gate T8 的约定路径) 后回退 coverage.xml"
        ),
    )
    parser.add_argument(
        "--min-line-rate",
        type=float,
        default=0.80,
        help="Sprint4 目标: line-rate ≥0.80 (v8.7 发布门禁 D9)",
    )
    parser.add_argument(
        "--baseline-json",
        default=str(ROOT / "reports" / "ci" / "coverage_baseline.json"),
    )
    parser.add_argument(
        "--output", default=str(ROOT / "reports" / "ci" / "coverage_trend.json")
    )
    args = parser.parse_args(argv)

    if not 0.0 < args.min_line_rate <= 1.0:
        print(
            f"[COVERAGE] 阈值口径错误: --min-line-rate={args.min_line_rate} 不在 (0,1] 内。"
            "拒绝执行 —— 与 item 14 '不可达阈值' 同类陷阱: 永不通过的阈值等于没有门禁,"
            "而永不失败的阈值等于假 PASS。",
            file=sys.stderr,
        )
        return 2

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cov_path = _resolve_coverage_xml(args.coverage_xml)
    cov = parse_coverage_xml(cov_path)

    results: list[CovResult] = []

    if cov is None:
        results.append(
            CovResult(
                "COV-0",
                "coverage.xml present & parseable",
                False,
                f"missing or unparseable: {cov_path}",
            )
        )
        _write(out_path, results, cov_path)
        return 1

    line_rate = cov["line_rate"]
    results.append(
        CovResult(
            "COV-1",
            f"overall line-rate ({line_rate:.4f}) >= {args.min_line_rate:.4f}",
            line_rate >= args.min_line_rate,
            f"line_rate={line_rate:.4f} min={args.min_line_rate:.4f}",
        )
    )

    # 关键模块覆盖 (2026-09-10: 缺失即 FAIL, 不再静默放过)
    cls_map = {fn.replace("\\", "/"): lr for fn, lr in cov["classes"]}
    sources = _load_coverage_sources()
    for mod in CRITICAL_MODULES:
        cid = f"COV-{mod}"
        mod_path = mod.replace("\\", "/")

        if not (ROOT / mod_path).exists():
            results.append(
                CovResult(
                    cid,
                    f"critical module path valid: {mod}",
                    False,
                    "路径陈旧: 文件不存在, 关键模块清单须修正 (旧清单 4 条即因此从未生效)",
                )
            )
            continue

        if not any(mod_path.startswith(s + "/") for s in sources):
            results.append(
                CovResult(
                    cid,
                    f"critical module measurable: {mod}",
                    False,
                    f"不在 .coveragerc source={list(sources)} 内, 不可能出现在报告中 (配置错误)",
                )
            )
            continue

        lr = _match_rate(cls_map, mod_path)
        if lr is None:
            results.append(
                CovResult(
                    cid,
                    f"critical module present in report: {mod}",
                    False,
                    "文件存在但不在 coverage.xml 中 —— 无任何测试导入 (覆盖空洞, 非合法跳过)",
                )
            )
            continue

        ok = lr >= CRITICAL_MIN_RATE
        results.append(
            CovResult(
                cid,
                f"critical module {mod} line-rate={lr:.4f} >= {CRITICAL_MIN_RATE}",
                ok,
                f"line_rate={lr:.4f}",
            )
        )

    # 基线退化检测
    baseline_path = Path(args.baseline_json)
    if baseline_path.exists():
        try:
            base = json.loads(
                baseline_path.read_text(encoding="utf-8", errors="replace")
            )
            base_lr = base.get("line_rate", 0.0)
            degraded = line_rate < base_lr - 0.02  # 允许 2pp 波动
            results.append(
                CovResult(
                    "COV-base",
                    f"line-rate ({line_rate:.4f}) not degraded vs base ({base_lr:.4f})",
                    not degraded,
                    f"delta={line_rate - base_lr:+.4f}",
                )
            )
        except Exception:
            pass

    n_fail = sum(1 for r in results if not r.passed)
    _write(out_path, results, cov_path)
    print(f"[COVERAGE] line_rate={line_rate:.4f} fail={n_fail} report={out_path}")
    for r in results:
        if not r.passed:
            print(f"  [FAIL] {r.cid}: {r.desc} -> {r.detail}")
    if n_fail == 0:
        # 更新基线为当前值 (趋势上扬时固化) + Sprint4 达标标记
        base_out = {
            "line_rate": line_rate,
            "updated": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
            "sprint4_threshold_met": line_rate >= 0.80,
        }
        baseline_path.write_text(
            json.dumps(base_out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 1 if n_fail > 0 else 0


def _write(out_path: Path, results: list[CovResult], cov_path: Path) -> None:
    report = {
        "timestamp": now_bj().strftime("%Y%m%d_%H%M%S"),
        "coverage_xml": str(cov_path),
        "fail": sum(1 for r in results if not r.passed),
        "results": [r._asdict() for r in results],
    }
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit(main())
