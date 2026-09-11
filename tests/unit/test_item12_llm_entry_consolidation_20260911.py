"""item 12 第一增量的回归护栏 (2026-09-11)。

范围
----
1. ``utils/_legacy/`` 空壳移除 —— 实测为纯 docstring 占位 (T1.2), 零 re-export,
   全文件类型 (py/yaml/toml/md/json/cfg/ini/bat/ps1/txt/sh) 精确零路径引用。
   原「严禁删除」规则是迁移期产物, 其宣称的兼容职责 (旧路径 re-export) 由空壳
   根本没有承担; ``archive_dead_code.py`` 的保护规则已同步改写。
2. LLM 孤儿入口归档 —— ``utils/llm_finetune.py`` (全树 0 导入方) 与
   ``utils/local_llm.py`` (仅自身测试消费; ``etf_flow_decision`` 的
   ``_local_llm_client`` 是变量名, 实际加载 ``15_每日工作流/llm_client.py``)
   归档至 ``_archive/dead_code/2026-09-11/`` (manifest.json 可回滚)。
3. ``check_llm_boundary.py`` 的 LLM_MODULE_PREFIXES 补上 ``utils.alpha.llm``
   多 provider 栈 (deepseek/doubao/ds4/glm/ollama/siliconflow + consensus +
   audit) —— 此前不在清单属门禁盲区。扩清单后实测执行链 0 违例。

现存 LLM 入口单一事实源 (收敛目标, 供后续增量):
    utils/glm5_client.py + utils/llm_gateway/   — GLM-5 路径 (gate 依赖 gateway)
    utils/alpha/llm/ + utils/alpha/llm_router.py — 多 provider 研究侧入口
    utils/llm_client.py                          — 统一门面 (ai_decision / ai_report_agent)
    utils/llm_evolution/                         — 自演化子系统 (非 client 入口, gate + phase_b 依赖)
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_DIR = REPO_ROOT / "_archive" / "dead_code" / "2026-09-11"

ARCHIVED = [
    "utils/llm_finetune.py",
    "utils/local_llm.py",
    "utils/_legacy/__init__.py",
    "tests/unit/test_local_llm_unit.py",
]

# 已归档模块对应的可导入名 (禁止在生产树复活)
ARCHIVED_IMPORT_NAMES = {
    "utils.llm_finetune",
    "utils.local_llm",
    "utils._legacy",
}

# 现存 LLM 入口 (单一事实源)
LIVE_LLM_ENTRIES = [
    "utils/glm5_client.py",
    "utils/llm_gateway/__init__.py",
    "utils/llm_client.py",
    "utils/alpha/llm/__init__.py",
    "utils/alpha/llm_router.py",
    "utils/llm_evolution/__init__.py",
]

REQUIRED_BOUNDARY_PREFIXES = [
    "utils.glm5_decision_engine",
    "utils.glm5_client",
    "utils.llm_evolution",
    "utils.llm_gateway",
    "utils.llm_client",
    "utils.alpha.llm",
]

SKIP_DIRS = {
    ".git", "__pycache__", "qlib_env", ".venv", "node_modules", "10_第三方项目",
    "_win_machine_assets", ".codebuddy", ".mypy_cache", "backups", "_archive",
    "logs", "models", "reports", "unsloth_compiled_cache", ".codeartsdoer",
    "research", "qlib",
}


class TestLegacyStubRemoved:
    """utils/_legacy 空壳移除 + 防复活。"""

    def test_legacy_dir_gone(self):
        assert not (REPO_ROOT / "utils" / "_legacy").exists(), (
            "utils/_legacy/ 已于 2026-09-11 移除 (零引用空壳), 不得复活"
        )

    def test_no_production_import_of_archived_modules(self):
        """生产树中任何 .py 都不得 import 已归档模块 (AST 级判定, 不看注释)。"""
        offenders: list[str] = []
        for dp, _dirs, fn in os_walk(REPO_ROOT):
            for f in fn:
                if not f.endswith(".py"):
                    continue
                p = dp / f
                try:
                    tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    mods: list[str] = []
                    if isinstance(node, ast.Import):
                        mods = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                        mods = [node.module]
                    for m in mods:
                        for bad in ARCHIVED_IMPORT_NAMES:
                            if m == bad or m.startswith(bad + "."):
                                offenders.append(f"{p.relative_to(REPO_ROOT)} -> {m}")
        assert not offenders, f"已归档模块被重新 import: {offenders}"


class TestOrphanLlmArchived:
    """LLM 孤儿入口归档 + 可回滚 manifest。"""

    def test_orphan_files_gone(self):
        for rel in ("utils/llm_finetune.py", "utils/local_llm.py"):
            assert not (REPO_ROOT / rel).exists(), f"{rel} 已归档, 不得在生产树复活"

    def test_archive_manifest_covers_all(self):
        mf = ARCHIVE_DIR / "manifest.json"
        assert mf.exists(), f"缺少可回滚 manifest: {mf}"
        data = json.loads(mf.read_text(encoding="utf-8"))
        got = {e["path"] for e in data["entries"]}
        missing = sorted(set(ARCHIVED) - got)
        assert not missing, f"manifest 缺少条目: {missing}"

    def test_archived_blobs_still_readable(self):
        """归档内容可读且 sha1 与 manifest 一致 (回滚可用)。"""
        import hashlib

        data = json.loads((ARCHIVE_DIR / "manifest.json").read_text(encoding="utf-8"))
        for e in data["entries"]:
            blob = ARCHIVE_DIR / e["path"]
            assert blob.exists(), f"归档内容缺失: {blob}"
            assert hashlib.sha1(blob.read_bytes()).hexdigest() == e["sha1"]


class TestLlmEntryContract:
    """现存入口清单 + 边界门禁覆盖。"""

    def test_live_entries_present(self):
        missing = [p for p in LIVE_LLM_ENTRIES if not (REPO_ROOT / p).exists()]
        assert not missing, f"现存 LLM 入口缺失: {missing}"

    def test_boundary_gate_covers_all_live_entries(self):
        src = (REPO_ROOT / "scripts" / "check_llm_boundary.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        prefixes: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "LLM_MODULE_PREFIXES":
                        prefixes = [ast.unparse(e).strip('"\'') for e in node.value.elts]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "LLM_MODULE_PREFIXES" and isinstance(node.value, ast.Tuple):
                    prefixes = [ast.unparse(e).strip('"\'') for e in node.value.elts]
        assert prefixes, "check_llm_boundary.py 中找不到 LLM_MODULE_PREFIXES"
        missing = [p for p in REQUIRED_BOUNDARY_PREFIXES if p not in prefixes]
        assert not missing, f"边界门禁缺少 LLM 入口前缀 (盲区): {missing}"

    def test_archive_tool_no_longer_protects_legacy(self):
        """保护规则必须与事实同步: archive_dead_code.py 不得再写「严禁删除 utils/_legacy」。"""
        src = (REPO_ROOT / "archive_dead_code.py").read_text(encoding="utf-8")
        assert "严禁删除" not in src or "_legacy" not in src.split("严禁删除")[1][:60], (
            "archive_dead_code.py 仍在保护已删除的 utils/_legacy (规则与事实漂移)"
        )


class TestConfigDualSource:
    """config/ vs configs/ 双源处置 (2026-09-11 第二增量)。

    实测结论: 两份 portfolio.yaml 是**同名但 schema 完全不同**的两个文件
    (重叠叶子 = 0), 不是新旧版本关系:
      * ``config/portfolio.yaml`` (gitignored, 411 叶子) = positions/fallback_prices/
        options/hedge.allocation —— 持仓与对冲预算事实源;
      * ``configs/account_structure.yaml`` (gitignored, 116 叶子) = account_structure/assets/
        risk_parameters/risk_guard —— 账户结构与风控参数。
    因此 4 处硬编码主读 configs/ 版是**正确的** (它们要的段只在 configs/ 版存在)。
    真缺陷是 kill_switch 段被同名遮蔽 (ConfigManager "portfolio" 名字被 config/ 版
    抢占): get_kill_switch_config() 优先读 portfolio.kill_switch(缺失->{}) -> 全回退链空
    -> 落 positions.json meta.total_capital=5000000(v8.0 历史头) 而非权威 3000000,
    保证金熔断线被放大 1.67 倍。修复 = kill_switch 段迁入独立
    ``config/kill_switch.yaml``(非敏感治理配置, 经 ``!config/kill_switch.yaml`` 入版本库,
    单一读取口 ConfigManager.get_kill_switch_config 的回退名 "kill_switch";
    configs/ 版该段已移除), 钉死 total_margin:3000000。
    """

    def test_kill_switch_section_in_authoritative_config(self):
        import yaml

        # 修复后 kill_switch 段位于独立 config/kill_switch.yaml (非敏感治理配置, 入版本库)
        p = REPO_ROOT / "config" / "kill_switch.yaml"
        if not p.exists():  # 机器本地文件可能缺失 -> 跳过而非误判
            pytest.skip("config/kill_switch.yaml 为机器本地文件, 本机不存在")
        ks = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert isinstance(ks, dict) and ks, "config/kill_switch.yaml 缺少 kill_switch 段"
        for key in ("level_1", "level_2", "level_3"):
            assert key in ks, f"kill_switch 段缺少 {key}"
        # 权威口径: 证券 200w + 期货 100w (cairn/ROADMAP accounts 行)
        assert ks.get("total_margin") == 3_000_000, (
            f"total_margin 应为权威口径 3000000 (旧链曾落到 positions.json meta 的 "
            f"5000000 历史头, 使熔断线放大 1.67 倍), 实测 {ks.get('total_margin')}"
        )

    def test_legacy_copy_no_longer_holds_kill_switch(self):
        """防止双口径复活: configs/account_structure.yaml 不得再有 kill_switch 段。"""
        import yaml

        p = REPO_ROOT / "configs" / "account_structure.yaml"
        if not p.exists():  # 机器本地文件, 允许缺失
            pytest.skip("configs/account_structure.yaml 为机器本地文件, 本机不存在")
        cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert "kill_switch" not in cfg, "kill_switch 段已迁至 config/portfolio.yaml, configs/ 不得再持有 (双口径)"

    def test_two_schemas_are_not_versions_of_each_other(self):
        """固化双 schema 事实, 防止未来误合并 (重叠叶子应为 0)。"""
        import yaml

        a = yaml.safe_load((REPO_ROOT / "config" / "portfolio.yaml").read_text(encoding="utf-8"))
        b_path = REPO_ROOT / "configs" / "account_structure.yaml"
        if not b_path.exists():
            pytest.skip("configs/account_structure.yaml 为机器本地文件, 本机不存在")
        b = yaml.safe_load(b_path.read_text(encoding="utf-8"))
        assert "positions" in a and "account_structure" not in a
        assert "account_structure" in b and "positions" not in b

    def test_dead_config_copies_removed(self):
        """D1: 零生产消费的死副本已移除 (0 .py 消费方实测, 备份在 _archive)。"""
        for rel in ("configs/feature_flags.yaml", "configs/settings.yaml"):
            assert not (REPO_ROOT / rel).exists(), f"{rel} 是零消费死副本, 已于 2026-09-11 移除"
        for rel in ("configs/feature_flags.yaml", "configs/settings.yaml"):
            assert (ARCHIVE_DIR / rel).exists(), f"{rel} 的备份缺失 (_archive)"


def os_walk(root: Path):
    """os.walk 的 Path 版 (跳过第三方/缓存/归档目录)。"""
    import os

    for dp, _dn, fn in os.walk(root):
        _dn[:] = [d for d in _dn if d not in SKIP_DIRS]
        yield Path(dp), _dn, fn
