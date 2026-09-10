#!/usr/bin/env python
"""
_check_coverage_trend.py — 覆盖率趋势门禁 (真实实现)

R1 修复项。CI "Coverage Trend" 阶段引用本脚本, 缺失导致 CI 必然失败。

真实语义:
    读取 pytest-cov 生成的 reports/coverage.xml (Cobertura 格式), 提取总体
    行覆盖率与关键模块的覆盖率, 与基线 (覆盖率下限契约) 比较, 验证:
        1. 总体行覆盖率 >= 最小阈值 (缺省取 .coveragerc fail_under, 渐进提升)
        2. 关键模块 (执行/风控/对冲闭环) 覆盖率不退化
        3. 覆盖率报告文件存在且可解析 (事实源就位)

作用域 (2026-09-10 gate-hardening, 审计 item 6):
    --scope full    全量测试口径: 任何 FAIL 一律硬阻断 (CI push(main) / nightly)。
    --scope subset  GAP-5 子集口径: 只跑受影响测试时覆盖率与全量不可比、必然低于
                    阈值 → 测量类判定记为**显式豁免 (waived)** 并写入报告 JSON,
                    退出码 0 但带 waiver_reason/waived_checks (可被机器读取)。
                    **不豁免**的两类仍硬阻断: ① 报告缺失/不可解析 (COV-0);
                    ② 关键模块配置错误 (路径陈旧 / 不在 .coveragerc source 内)。
                    子集口径**严禁**固化基线 (子集覆盖率不可比)。

    设计取舍: 旧写法由 ci.yml 在子集分支直接 `exit 0` —— 不调用门禁、不落任何产物,
    "无记录 = 通过" 与"缺数据 = 通过"同类, 属门禁假 PASS 高发形态。现改为必须
    调用门禁并落盘显式豁免标记。

退出码:
    0 = 覆盖率达标 / 不退化 / (子集口径) 仅有可豁免项且已显式记录
    1 = 覆盖率低于阈值 / 关键模块退化或空洞 / 报告缺失 (含子集口径的不可豁免项)
    2 = 阈值口径非法 (拒绝执行, 不静默松弛)

用法:
    python scripts/_check_coverage_trend.py \
        [--coverage-xml reports/coverage.xml] \
        [--min-line-rate 0.38] \
        [--scope full|subset] \
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

# 总体阈值缺省回退 (.coveragerc fail_under 缺失时): Sprint4 目标 0.80
DEFAULT_MIN_LINE_RATE = 0.80

# 作用域 (见模块 docstring)
SCOPE_FULL = "full"
SCOPE_SUBSET = "subset"

# 子集口径的豁免理由 (写入报告 JSON 的 waiver_reason, 机器可读)
WAIVER_REASON_SUBSET = (
    "GAP-5 子集口径: 仅运行受影响测试, 覆盖率必然低于全量阈值、与全量口径不可比, "
    "故测量类判定按设计豁免 (显式记录, 不计入通过); 全量硬门禁由 push(main) / "
    "nightly schedule (无 base_ref ⇒ run_full=true) 保证"
)


class CovResult(NamedTuple):
    cid: str
    desc: str
    passed: bool
    detail: str
    # waivable=True 表示该判定属"覆盖率测量类", 仅在全量口径下有可比性;
    # 结构性配置错误 (报告缺失 / 清单陈旧 / 不可测量) 恒为 False, 子集口径也不豁免。
    waivable: bool = True


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


def _load_fail_under() -> float | None:
    """从 .coveragerc 的 [report] fail_under 读取总体阈值 (百分比 → 0.xx)。

    2026-09-10 gate-hardening: 阈值原先在三个地方各写一份 (pytest-cov 的
    .coveragerc fail_under、ci.yml 的 --min-line-rate、本脚本 argparse 缺省
    0.80), 三处不一致即"本地跑的门禁 ≠ CI 跑的门禁" —— 门禁口径漂移会让
    "本地绿" 与 "CI 红" 互相矛盾, 最终被当成噪声忽略 (等价于放行)。
    现以 .coveragerc fail_under 为单一事实源, 缺省才回退 0.80。
    """
    cfg = ROOT / ".coveragerc"
    if not cfg.exists():
        return None
    try:
        lines = cfg.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    in_report = False
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_report = stripped == "[report]"
            continue
        if not in_report or not stripped.startswith("fail_under"):
            continue
        _, _, value = stripped.partition("=")
        try:
            pct = float(value.strip())
        except ValueError:
            return None
        return pct / 100.0 if pct > 1.0 else pct
    return None


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


def _write(
    out_path: Path,
    results: list[CovResult],
    cov_path: Path,
    *,
    scope: str,
    blocking_fail: list[CovResult],
    waived_checks: list[str],
    waiver_reason: str,
) -> None:
    """落盘门禁报告。

    新增字段 (2026-09-10 gate-hardening): scope / waived / waiver_reason /
    waived_checks —— 子集口径的豁免必须是**可机器读取的显式记录**, 而不是
    "没有任何产物" (后者与"门禁通过"不可区分)。
    """
    report = {
        "timestamp": now_bj().strftime("%Y%m%d_%H%M%S"),
        "coverage_xml": str(cov_path),
        "scope": scope,
        "waived": bool(waived_checks),
        "waiver_reason": waiver_reason,
        "waived_checks": waived_checks,
        "fail": len(blocking_fail),
        "results": [r._asdict() for r in results],
    }
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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
        default=None,
        help=(
            "总体行覆盖率下限; 缺省取 .coveragerc 的 fail_under (单一事实源), "
            "再缺省 0.80 (Sprint4 目标, v8.7 发布门禁 D9)"
        ),
    )
    parser.add_argument(
        "--scope",
        choices=(SCOPE_FULL, SCOPE_SUBSET),
        default=SCOPE_FULL,
        help=(
            "full=全量口径, 任何 FAIL 硬阻断 (缺省); "
            "subset=子集口径, 测量类 FAIL 显式豁免并落盘, 仅报告缺失/配置错误硬阻断"
        ),
    )
    parser.add_argument(
        "--baseline-json",
        default=str(ROOT / "reports" / "ci" / "coverage_baseline.json"),
    )
    parser.add_argument(
        "--output", default=str(ROOT / "reports" / "ci" / "coverage_trend.json")
    )
    args = parser.parse_args(argv)

    threshold_source = "--min-line-rate"
    min_line_rate = args.min_line_rate
    if min_line_rate is None:
        min_line_rate = _load_fail_under()
        threshold_source = ".coveragerc fail_under"
        if min_line_rate is None:
            min_line_rate = DEFAULT_MIN_LINE_RATE
            threshold_source = f"内置缺省 {DEFAULT_MIN_LINE_RATE}"

    if not 0.0 < min_line_rate <= 1.0:
        print(
            f"[COVERAGE] 阈值口径错误: min-line-rate={min_line_rate} 不在 (0,1] 内。"
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
        # 报告缺失/不可解析 = 事实源未就位。两种口径下都不豁免 (缺数据 ≠ 通过)。
        results.append(
            CovResult(
                "COV-0",
                "coverage.xml present & parseable",
                False,
                f"missing or unparseable: {cov_path}",
                waivable=False,
            )
        )
        blocking_fail = [r for r in results if not r.passed]
        _write(
            out_path,
            results,
            cov_path,
            scope=args.scope,
            blocking_fail=blocking_fail,
            waived_checks=[],
            waiver_reason="",
        )
        print(
            f"[COVERAGE] scope={args.scope} 报告缺失/不可解析 → 硬阻断 (缺数据不等于通过): {cov_path}"
        )
        return 1

    line_rate = cov["line_rate"]
    results.append(
        CovResult(
            "COV-1",
            f"overall line-rate ({line_rate:.4f}) >= {min_line_rate:.4f}",
            line_rate >= min_line_rate,
            f"line_rate={line_rate:.4f} min={min_line_rate:.4f} source={threshold_source}",
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
                    waivable=False,
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
                    waivable=False,
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
                    "文件存在但不在 coverage.xml 中 —— 无任何测试导入 (覆盖空洞, 非合法跳过)"
                    " 注: 子集口径下本项可豁免 (可能确未运行该模块的测试)",
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
    if not baseline_path.exists():
        # 显式记录"检查未执行", 不再静默丢弃 (缺失的检查 = 假 PASS 的另一形态)
        results.append(
            CovResult(
                "COV-base",
                f"line-rate ({line_rate:.4f}) not degraded vs base",
                True,
                f"SKIP: 基线文件缺失 ({baseline_path}), 退化检测未执行 — 非阻断但须显式可见",
            )
        )
    else:
        try:
            base = json.loads(
                baseline_path.read_text(encoding="utf-8", errors="replace")
            )
            base_lr = float(base.get("line_rate", 0.0))
            degraded = line_rate < base_lr - 0.02  # 允许 2pp 波动
            results.append(
                CovResult(
                    "COV-base",
                    f"line-rate ({line_rate:.4f}) not degraded vs base ({base_lr:.4f})",
                    not degraded,
                    f"delta={line_rate - base_lr:+.4f}",
                )
            )
        except Exception as exc:  # noqa: BLE001  # 基线损坏不该让门禁崩, 但必须显式可见
            results.append(
                CovResult(
                    "COV-base",
                    f"line-rate ({line_rate:.4f}) not degraded vs base",
                    True,
                    f"SKIP: 基线解析失败 ({type(exc).__name__}), 退化检测未执行 — 非阻断但须显式可见",
                )
            )

    blocking_fail = [
        r
        for r in results
        if not r.passed and (args.scope == SCOPE_FULL or not r.waivable)
    ]
    waived_checks = (
        [r.cid for r in results if not r.passed and r.waivable]
        if args.scope == SCOPE_SUBSET
        else []
    )
    waiver_reason = WAIVER_REASON_SUBSET if waived_checks else ""

    _write(
        out_path,
        results,
        cov_path,
        scope=args.scope,
        blocking_fail=blocking_fail,
        waived_checks=waived_checks,
        waiver_reason=waiver_reason,
    )
    print(
        f"[COVERAGE] scope={args.scope} line_rate={line_rate:.4f} "
        f"threshold={min_line_rate:.4f} ({threshold_source}) "
        f"blocking_fail={len(blocking_fail)} waived={len(waived_checks)} report={out_path}"
    )
    for r in results:
        if r.passed:
            continue
        tag = "[WAIVED]" if (args.scope == SCOPE_SUBSET and r.waivable) else "[FAIL]"
        print(f"  {tag} {r.cid}: {r.desc} -> {r.detail}")
    if waived_checks:
        print(f"[COVERAGE] 显式豁免 (waived) 共 {len(waived_checks)} 项: {','.join(waived_checks)}")
        print(f"[COVERAGE] 豁免理由: {waiver_reason}")

    if not blocking_fail:
        if args.scope == SCOPE_FULL:
            # 更新基线为当前值 (趋势上扬时固化) + Sprint4 达标标记
            base_out = {
                "line_rate": line_rate,
                "updated": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
                "sprint4_threshold_met": line_rate >= 0.80,
            }
            baseline_path.write_text(
                json.dumps(base_out, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        else:
            print("[COVERAGE] 子集口径: 跳过基线固化 (子集覆盖率与全量不可比, 不得写入基线)")
    return 1 if blocking_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
