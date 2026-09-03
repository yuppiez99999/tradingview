#!/usr/bin/env python
"""
validate_configs.py — config/*.yaml 轻量 schema 校验 (ROADMAP AUTO-8).

背景 (ROADMAP §云端自动开发任务池 AUTO-8):
    config/ 是主业务活跃配置目录 (由 utils/config_manager.py 搜索路径第 3 优先级
    加载), 含 feature_flags / mlops / llm_pricing / lgb_training / 归因与组合等
    异构 YAML。PyYAML 默认 safe_load 遇到重复 key 时**静默保留最后一个** ——
    这是最容易出现的"配置腐化不报错"来源之一; 另需保证文件可解析、顶层为映射、
    且关键顶层区块未被误删。

本脚本提供纯 stdlib + PyYAML 的轻量校验, 供本地与 CI 变更检测使用:

    1. 可解析性: 每个 config/*.yaml 必须是合法 YAML 且顶层为**非空映射**。
    2. 重复 key 检测 (任意嵌套深度): 复用 yaml.compose 的节点树, 不经过
       safe_load 的"后值覆盖前值", 显式捕获会被静默吞掉的重复 key。
    3. 关键顶层区块白名单: 每个已知配置文件要求其**当前已在产**的顶层区块存在
       (防止误删/回归), 详见 REQUIRED_TOP_SECTIONS。
    4. gnn_factor 子目录仅做可解析性冒烟, 不强制顶层结构。

退出码:
    0 = 全部通过; 1 = 存在解析错误 / 重复 key / 缺失关键区块。

用法:
    python scripts/validate_configs.py                # 校验 config/*.yaml
    python scripts/validate_configs.py --verbose      # 逐文件输出明细
    python scripts/validate_configs.py --file mlops.yaml   # 仅校验指定文件
    python scripts/validate_configs.py --selftest     # 内建自测 (无需 pytest)

只读、不触碰任何运行配置/资金数据; 缺文件时以当前 git 追踪清单为准 (skipped 不失败),
避免 .gitignore 内本地运行配置 (configs/, 复数) 干扰。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, Node, SequenceNode

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

#: 每个已知配置要求存在的顶层区块 (取自当前在产结构, 防误删回归)。
REQUIRED_TOP_SECTIONS: dict[str, tuple[str, ...]] = {
    "feature_flags.yaml": ("settings", "flags"),
    "mlops.yaml": ("auto_retrain", "retrain_workflow", "drift_monitor", "ab_testing"),
    "llm_pricing.yaml": ("models", "daily_token_budget"),
    "lgb_training.yaml": (
        "lookback_days",
        "min_samples",
        "test_ratio",
        "n_splits",
        "model_quality_threshold",
        "lgb_params",
    ),
    "brinson_attribution.yaml": ("benchmark_sector_weights", "sectors"),
    "factor_attribution.yaml": ("benchmark_factor_exposures", "factors"),
    "portfolio_200w_etf.yaml": ("portfolio", "target"),
}


def _collect_duplicate_keys(doc: Node) -> list[str]:
    """递归遍历 yaml.compose 节点树, 返回重复出现的 key (含嵌套层级)。"""
    dups: list[str] = []

    def walk(node: Node) -> None:
        if isinstance(node, MappingNode):
            seen: set[str] = set()
            for key_node, value_node in node.value:
                key = key_node.value
                if key in seen:
                    dups.append(str(key))
                seen.add(key)
                walk(value_node)
        elif isinstance(node, SequenceNode):
            for item in node.value:
                walk(item)

    walk(doc)
    return dups


def _check_file(path: Path, verbose: bool) -> list[str]:
    """校验单个 YAML, 返回错误消息列表 (空 = 通过)。"""
    errors: list[str] = []
    fname = path.name

    # 1. 可解析性 + 顶层非空映射
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:  # 解析错误 → 直接失败
        errors.append(f"{fname}: YAML 解析失败 — {exc}")
        return errors
    except OSError as exc:
        errors.append(f"{fname}: 读取失败 — {exc}")
        return errors

    if data is None or not isinstance(data, dict):
        errors.append(f"{fname}: 顶层必须是映射 (当前为 {type(data).__name__})")
        return errors
    if not data:
        errors.append(f"{fname}: 顶层映射为空")
        return errors

    # 2. 重复 key 检测 (基于 compose 节点树)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = yaml.compose(fh)
        if doc is not None:
            for key in _collect_duplicate_keys(doc):
                errors.append(f"{fname}: 重复 key 检测到 {key!r} (后值将静默覆盖前值)")
    except yaml.YAMLError as exc:
        errors.append(f"{fname}: 重复 key 扫描解析失败 — {exc}")

    # 3. 关键顶层区块白名单
    for section in REQUIRED_TOP_SECTIONS.get(fname, ()):
        if section not in data:
            errors.append(f"{fname}: 缺少关键顶层区块 {section!r}")

    if verbose:
        kind = "OK" if not errors else "FAIL"
        print(f"  [{kind}] {fname}  top={list(data.keys())}")
    return errors


def _discover_targets(args) -> list[Path]:
    """确定待校验文件集合: --file 指定或默认 config/*.yaml (存在的顶层文件)。"""
    if args.file:
        return [CONFIG_DIR / f for f in args.file if (CONFIG_DIR / f).exists()]
    return sorted(p for p in CONFIG_DIR.glob("*.yaml") if p.is_file())


def _selftest() -> int:
    """构造临时畸形配置, 断言检测逻辑正确返回错误 (供无 pytest 云端沙箱自验证)。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1) 重复 key (safe_load 静默后覆盖) → 必须检出
        dup = tmp / "dup.yaml"
        dup.write_text("settings:\n  x: 1\nsettings:\n  y: 2\n", encoding="utf-8")
        if not any("重复 key" in e for e in _check_file(dup, False)):
            print("selftest FAIL: 未检出重复 key"); return 1

        # 2) 已知配置缺失关键区块 → 必须检出
        missing = tmp / "llm_pricing.yaml"
        missing.write_text("models:\n  glm5:\n    cost_per_1k_input: 1\n", encoding="utf-8")
        if not any("daily_token_budget" in e for e in _check_file(missing, False)):
            print("selftest FAIL: 未检出缺失关键区块"); return 1

        # 3) 顶层非映射 (序列) → 必须检出
        seq = tmp / "seq.yaml"
        seq.write_text("- just\n- a\n- list\n", encoding="utf-8")
        if not any("顶层必须是映射" in e for e in _check_file(seq, False)):
            print("selftest FAIL: 未检出非映射顶层"); return 1

        # 4) 非法 YAML → 必须检出
        broken = tmp / "broken.yaml"
        broken.write_text("settings: [unclosed\n", encoding="utf-8")
        if not any("解析失败" in e for e in _check_file(broken, False)):
            print("selftest FAIL: 未检出解析错误"); return 1

    print("selftest PASS: 重复 key / 缺失区块 / 非映射顶层 / 解析错误 均正确检出")
    return 0


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="逐文件输出明细")
    parser.add_argument(
        "--file", nargs="*", default=[], help="仅校验指定文件名 (config 目录下)"
    )
    parser.add_argument(
        "--strict-gnn", action="store_true",
        help="同时对 config/gnn_factor/*.yaml 做可解析性冒烟",
    )
    parser.add_argument(
        "--selftest", action="store_true",
        help="运行内建自测 (构造临时畸形配置断言检测逻辑), 无需 pytest",
    )
    args = parser.parse_args()

    if args.selftest:
        return _selftest()

    targets = _discover_targets(args)
    if not targets:
        print("没有找到待校验的 config/*.yaml")
        return 0

    errors: list[str] = []
    print(f"校验 {len(targets)} 个 config/*.yaml ...")
    for path in targets:
        errors.extend(_check_file(path, args.verbose))

    if args.strict_gnn:  # gnn_factor 子目录: 可选冒烟
        for path in sorted((CONFIG_DIR / "gnn_factor").glob("*.yaml")):
            errors.extend(_check_file(path, args.verbose))

    if errors:
        print("\n配置校验失败:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
