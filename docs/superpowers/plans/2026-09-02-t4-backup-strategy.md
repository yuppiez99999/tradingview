# T4 EOD 备份策略与恢复演练 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现每日 17:30 EOD 备份链（D 盘异盘 + 90 天滚动 + manifest 校验和 + 恢复验证），并将备份新鲜度接入 Health Score 数据维，兑现《v8.7 Production Edition 架构升级方案》T4。

**Architecture:** `utils/backup/eod_backup.py` 承载纯逻辑（备份收集/manifest SHA256/90 天滚动清理/verify/restore），`scripts/run_eod_backup.py` 为 CLI（backup/verify/restore 子命令），计划任务 17:30 交易日执行。manifest 含 `destination` 字段预留云端扩展（用户决策 2026-09-02：云端暂缓，v1 仅 D 盘）。备份缺失通过 `score_data` 可选参数接入 Health Score（评分 17:05 在备份 17:30 之前，故检查的是"最新备份距今 ≤4 天"，含节假日缓冲）。

**Tech Stack:** pytest（tmp_path fixture）、hashlib/pathlib/shutil、无第三方依赖。

**上游设计:** 方案 §六（RPO 1 天 / RTO 2 小时）+ §八（备份静默失败 → Health Score 数据维扣分）

**用户决策（2026-09-02）:** ①本地异盘 = D 盘（`D:\QuantBackup\28-quant\`）②云端暂缓——manifest 设计目的地抽象，后续接云端零改造。

**已核验的备份清单（2026-09-02）:**

| 项 | 源路径（相对项目根） | 现状 | 备份方式 |
| --- | --- | --- | --- |
| 配置目录 | `config/` | 实存 | 整目录递归 |
| 成交记录 | `reports/fills/` | 实存（~380KB） | 整目录递归（历史累积） |
| Health Score 历史 | `reports/health_score/` | 实存 | 整目录递归 |
| 账户状态 | `output/shadow_account/s12_shadow_state.json` | 实存 | 单文件 |
| 止损水位 | `reports/stop_loss_water_marks.json` | **尚不存在**（shadow 阶段正常） | 单文件，missing 容忍 |
| 降级审计 | `reports/degradation_log.jsonl` | 实存 | 单文件（全量追加式） |

**验收对照（方案 T4）:**
- 备份计划任务连续 7 日成功 → 计划任务注册后自然累积（LOG 注明起点）
- 云端关键文件可回拉 → 云端暂缓（用户决策），v1 以 D 盘回拉验证替代，云端扩展接口预留
- 1 次主机故障模拟恢复 ≤2h → Task 5 交付"关键状态回拉演练"（备份 → 模拟丢失 → 回拉 → verify 全绿），完整裸机演练记 LOG 为后续项

**目录结构（备份目的地）:**

```
D:\QuantBackup\28-quant\
  2026-09-02\
    manifest.json
    config\...
    fills\...
    health_score\...
    shadow_account\s12_shadow_state.json
    degradation\degradation_log.jsonl
```

**manifest 结构:**

```json
{
  "date": "2026-09-02",
  "created_at": "2026-09-02T17:30:01",
  "destination": "local-disk",
  "files": [{"path": "config/x.yaml", "sha256": "...", "bytes": 123}],
  "missing_sources": ["reports/stop_loss_water_marks.json"],
  "total_files": 12,
  "total_bytes": 45678
}
```

---

### Task 1: 备份核心模块（EodBackup 收集/manifest/滚动清理）

**Files:**
- Create: `utils/backup/__init__.py`
- Create: `utils/backup/eod_backup.py`
- Test: `tests/unit/test_eod_backup.py`

- [ ] **Step 1: 写失败测试**

```python
"""EOD 备份核心模块单测 (Production Edition T4, 2026-09-02)."""
from __future__ import annotations

import json
from pathlib import Path

from utils.backup.eod_backup import EodBackup


def _make_source(root: Path) -> None:
    """构造最小源结构: config/ + fills/ + health_score/ + shadow state + degradation_log."""
    (root / "config").mkdir(parents=True)
    (root / "config" / "main.yaml").write_text("a: 1\n", encoding="utf-8")
    (root / "config" / "sub").mkdir()
    (root / "config" / "sub" / "deep.yaml").write_text("b: 2\n", encoding="utf-8")
    (root / "reports" / "fills").mkdir(parents=True)
    (root / "reports" / "fills" / "fills_2026-09-01.jsonl").write_text('{"ts":1}\n', encoding="utf-8")
    (root / "reports" / "health_score").mkdir(parents=True)
    (root / "reports" / "health_score" / "health_score_2026-09-01.json").write_text("{}", encoding="utf-8")
    (root / "output" / "shadow_account").mkdir(parents=True)
    (root / "output" / "shadow_account" / "s12_shadow_state.json").write_text('{"nav":1.0}', encoding="utf-8")
    (root / "reports" / "degradation_log.jsonl").write_text('{"ts":"2026-09-01"}\n', encoding="utf-8")


class TestEodBackup:
    DATE = "2026-09-02"

    def test_run_creates_backup_and_manifest(self, tmp_path):
        src, dst = tmp_path / "proj", tmp_path / "bak"
        _make_source(src)
        bak = EodBackup(src, dst)
        manifest = bak.run(self.DATE)
        day_dir = dst / self.DATE
        assert (day_dir / "config" / "main.yaml").exists()
        assert (day_dir / "config" / "sub" / "deep.yaml").exists()  # 递归
        assert (day_dir / "fills" / "fills_2026-09-01.jsonl").exists()
        assert (day_dir / "health_score" / "health_score_2026-09-01.json").exists()
        assert (day_dir / "shadow_account" / "s12_shadow_state.json").exists()
        assert (day_dir / "degradation" / "degradation_log.jsonl").exists()
        assert (day_dir / "manifest.json").exists()
        assert manifest["date"] == self.DATE
        assert manifest["destination"] == "local-disk"
        assert manifest["total_files"] == 6
        assert len(manifest["files"]) == 6
        assert manifest["missing_sources"] == ["reports/stop_loss_water_marks.json"]

    def test_manifest_records_sha256(self, tmp_path):
        src, dst = tmp_path / "proj", tmp_path / "bak"
        _make_source(src)
        manifest = EodBackup(src, dst).run(self.DATE)
        by_path = {f["path"]: f for f in manifest["files"]}
        assert by_path["config/main.yaml"]["sha256"] == EodBackup._sha256(src / "config" / "main.yaml")
        assert by_path["config/main.yaml"]["bytes"] == 4

    def test_missing_stop_loss_tolerated(self, tmp_path):
        src, dst = tmp_path / "proj", tmp_path / "bak"
        _make_source(src)
        manifest = EodBackup(src, dst).run(self.DATE)
        assert "reports/stop_loss_water_marks.json" in manifest["missing_sources"]
        # 不算失败: 有 stop_loss 时移出 missing
        (src / "reports" / "stop_loss_water_marks.json").write_text("{}", encoding="utf-8")
        m2 = EodBackup(src, dst / "second").run(self.DATE)
        assert m2["missing_sources"] == []
        assert m2["total_files"] == 7
        assert (dst / "second" / self.DATE / "stop_loss" / "stop_loss_water_marks.json").exists()

    def test_rerun_same_date_overwrites(self, tmp_path):
        src, dst = tmp_path / "proj", tmp_path / "bak"
        _make_source(src)
        EodBackup(src, dst).run(self.DATE)
        (src / "config" / "main.yaml").write_text("a: 2\n", encoding="utf-8")
        manifest = EodBackup(src, dst).run(self.DATE)  # 幂等重跑
        assert manifest["total_files"] == 6
        assert (dst / self.DATE / "config" / "main.yaml").read_text(encoding="utf-8") == "a: 2\n"

    def test_cleanup_old_backups_keeps_recent(self, tmp_path):
        src, dst = tmp_path / "proj", tmp_path / "bak"
        _make_source(src)
        bak = EodBackup(src, dst)
        bak.run("2026-06-01")   # 123 天前 (>90)
        bak.run("2026-08-15")   # 18 天前 (<90)
        bak.run(self.DATE)
        removed = bak.cleanup_old(today="2026-09-02")
        assert removed == ["2026-06-01"]
        assert not (dst / "2026-06-01").exists()
        assert (dst / "2026-08-15").exists()
        assert (dst / self.DATE).exists()

    def test_cleanup_keeps_within_90_days(self, tmp_path):
        src, dst = tmp_path / "proj", tmp_path / "bak"
        _make_source(src)
        bak = EodBackup(src, dst)
        bak.run("2026-06-04")   # 恰 90 天
        removed = bak.cleanup_old(today="2026-09-02")
        assert removed == []
        assert (dst / "2026-06-04").exists()

    def test_non_date_dirs_untouched(self, tmp_path):
        src, dst = tmp_path / "proj", tmp_path / "bak"
        _make_source(src)
        (dst / "notes.txt").write_text("x", encoding="utf-8")
        bak = EodBackup(src, dst)
        bak.run("2026-01-01")
        removed = bak.cleanup_old(today="2026-09-02")
        assert removed == ["2026-01-01"]
        assert (dst / "notes.txt").exists()
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_eod_backup.py -v`
Expected: FAIL（ModuleNotFoundError: utils.backup）

- [ ] **Step 3: 写实现**

`utils/backup/__init__.py`:

```python
"""EOD 备份包 (Production Edition T4)."""
```

`utils/backup/eod_backup.py`:

```python
"""EOD 备份核心 (Production Edition T4, 2026-09-02).

每日 17:30 (EOD 链尾) 备份关键状态到本地异盘 D:\\QuantBackup\\28-quant\\:
  目录: config/ (配置) + reports/fills/ (成交) + reports/health_score/ (评分历史)
  文件: shadow 账户状态 + 止损水位 + degradation_log
manifest.json 记录每文件 SHA256 (供 verify/restore 校验), destination 字段
预留云端扩展 (2026-09-02 用户决策: 云端暂缓).
保留策略: 本地 90 天滚动 (cleanup_old).
"""
from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

RETENTION_DAYS = 90

# (源相对路径, 备份内相对路径) — 整目录递归
DIR_ITEMS: list[tuple[str, str]] = [
    ("config", "config"),
    ("reports/fills", "fills"),
    ("reports/health_score", "health_score"),
]

# (源相对路径, 备份内相对路径) — 单文件
FILE_ITEMS: list[tuple[str, str]] = [
    ("output/shadow_account/s12_shadow_state.json", "shadow_account/s12_shadow_state.json"),
    ("reports/stop_loss_water_marks.json", "stop_loss/stop_loss_water_marks.json"),
    ("reports/degradation_log.jsonl", "degradation/degradation_log.jsonl"),
]


class EodBackup:
    """EOD 备份执行器 (纯文件操作, 无网络)."""

    def __init__(self, project_root: Path, backup_root: Path):
        self.project_root = Path(project_root)
        self.backup_root = Path(backup_root)

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        h.update(path.read_bytes())
        return h.hexdigest()

    def run(self, date: str) -> dict:
        """执行当日备份, 返回 manifest dict. 幂等: 同日重跑覆盖."""
        day_dir = self.backup_root / date
        day_dir.mkdir(parents=True, exist_ok=True)
        files: list[dict] = []
        missing: list[str] = []
        for src_rel, dst_rel in DIR_ITEMS:
            src = self.project_root / src_rel
            if not src.is_dir():
                missing.append(src_rel + "/")
                continue
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(src).as_posix()
                    dst = day_dir / dst_rel / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst)
                    files.append({
                        "path": f"{dst_rel}/{rel}",
                        "sha256": self._sha256(f),
                        "bytes": f.stat().st_size,
                    })
        for src_rel, dst_rel in FILE_ITEMS:
            src = self.project_root / src_rel
            if not src.is_file():
                missing.append(src_rel)
                continue
            dst = day_dir / dst_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            files.append({
                "path": dst_rel,
                "sha256": self._sha256(src),
                "bytes": src.stat().st_size,
            })
        manifest = {
            "date": date,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "destination": "local-disk",
            "files": files,
            "missing_sources": missing,
            "total_files": len(files),
            "total_bytes": sum(f["bytes"] for f in files),
        }
        (day_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest

    def cleanup_old(self, today: str) -> list[str]:
        """删除 >RETENTION_DAYS 天的备份日期目录, 返回已删除目录名列表."""
        from datetime import timedelta
        today_dt = datetime.strptime(today, "%Y-%m-%d")
        cutoff = today_dt - timedelta(days=RETENTION_DAYS)
        removed: list[str] = []
        if not self.backup_root.is_dir():
            return removed
        for d in sorted(self.backup_root.iterdir()):
            if not d.is_dir():
                continue
            try:
                d_dt = datetime.strptime(d.name, "%Y-%m-%d")
            except ValueError:
                continue  # 非日期命名目录不动
            if d_dt < cutoff:
                shutil.rmtree(d)
                removed.append(d.name)
        return removed


import json  # noqa: E402  (manifest 序列化; 置底避免顶部 import 区拥挤)
```

注: `import json` 置底是为避免与计划文档排版冲突——实际写入时请把 `import json` 放到文件顶部 import 区（与 hashlib/shutil/datetime 并列），删除底部两行。**最终文件顶部 import 应为:**

```python
import hashlib
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
```

且 `cleanup_old` 内不再需要局部 `from datetime import timedelta`。

- [ ] **Step 4: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_eod_backup.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add utils/backup/__init__.py utils/backup/eod_backup.py tests/unit/test_eod_backup.py
git commit -m "feat(backup): EOD 备份核心 — manifest SHA256 + 90 天滚动清理"
```

---

### Task 2: 恢复验证（verify / restore）

**Files:**
- Modify: `utils/backup/eod_backup.py`（追加 verify_backup / restore_backup）
- Test: `tests/unit/test_eod_backup.py`（追加 TestVerifyRestore）

- [ ] **Step 1: 写失败测试**

```python
from utils.backup.eod_backup import verify_backup, restore_backup


class TestVerifyRestore:
    DATE = "2026-09-02"

    def _run_backup(self, tmp_path) -> Path:
        src, dst = tmp_path / "proj", tmp_path / "bak"
        _make_source(src)
        EodBackup(src, dst).run(self.DATE)
        return dst / self.DATE

    def test_verify_ok(self, tmp_path):
        day_dir = self._run_backup(tmp_path)
        r = verify_backup(day_dir)
        assert r["ok"] is True
        assert r["checked"] == 6
        assert r["mismatched"] == []
        assert r["missing"] == []

    def test_verify_detects_corruption(self, tmp_path):
        day_dir = self._run_backup(tmp_path)
        (day_dir / "config" / "main.yaml").write_text("tampered\n", encoding="utf-8")
        r = verify_backup(day_dir)
        assert r["ok"] is False
        assert "config/main.yaml" in r["mismatched"]

    def test_verify_detects_missing_file(self, tmp_path):
        day_dir = self._run_backup(tmp_path)
        (day_dir / "fills" / "fills_2026-09-01.jsonl").unlink()
        r = verify_backup(day_dir)
        assert r["ok"] is False
        assert "fills/fills_2026-09-01.jsonl" in r["missing"]

    def test_verify_no_manifest(self, tmp_path):
        r = verify_backup(tmp_path)
        assert r["ok"] is False
        assert "manifest.json 缺失" in r["missing"]

    def test_restore_roundtrip(self, tmp_path):
        day_dir = self._run_backup(tmp_path)
        target = tmp_path / "restored"
        r = restore_backup(day_dir, target)
        assert r["restored"] == 6
        assert (target / "config" / "main.yaml").read_text(encoding="utf-8") == "a: 1\n"
        assert (target / "config" / "sub" / "deep.yaml").exists()
        assert (target / "shadow_account" / "s12_shadow_state.json").read_text(encoding="utf-8") == '{"nav":1.0}'
        assert (target / "degradation" / "degradation_log.jsonl").exists()

    def test_restore_selective(self, tmp_path):
        day_dir = self._run_backup(tmp_path)
        target = tmp_path / "restored"
        r = restore_backup(day_dir, target, items=["config/main.yaml"])
        assert r["restored"] == 1
        assert (target / "config" / "main.yaml").exists()
        assert not (target / "fills").exists()
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_eod_backup.py::TestVerifyRestore -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 写实现（追加到 eod_backup.py 末尾，模块级函数）**

```python
def verify_backup(day_dir: Path) -> dict:
    """校验备份目录: manifest 存在性 + 逐文件 SHA256.

    返回 {"ok": bool, "checked": int, "mismatched": [path], "missing": [path]}.
    """
    day_dir = Path(day_dir)
    manifest_path = day_dir / "manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "checked": 0, "mismatched": [], "missing": ["manifest.json 缺失"]}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "checked": 0, "mismatched": [], "missing": ["manifest.json 解析失败"]}
    mismatched: list[str] = []
    missing: list[str] = []
    for f in manifest.get("files", []):
        p = day_dir / f["path"]
        if not p.is_file():
            missing.append(f["path"])
            continue
        if EodBackup._sha256(p) != f["sha256"]:
            mismatched.append(f["path"])
    return {
        "ok": not mismatched and not missing,
        "checked": len(manifest.get("files", [])),
        "mismatched": mismatched,
        "missing": missing,
    }


def restore_backup(day_dir: Path, target_root: Path, items: list[str] | None = None) -> dict:
    """按 manifest 回拉备份文件到 target_root (主机故障恢复用).

    items: 仅回拉指定相对路径列表; None = 全量. 返回 {"restored": n, "files": [...]}.
    """
    day_dir = Path(day_dir)
    target_root = Path(target_root)
    manifest = json.loads((day_dir / "manifest.json").read_text(encoding="utf-8"))
    restored: list[str] = []
    for f in manifest.get("files", []):
        rel = f["path"]
        if items is not None and rel not in items:
            continue
        src = day_dir / rel
        if not src.is_file():
            continue
        dst = target_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        restored.append(rel)
    return {"restored": len(restored), "files": restored}
```

- [ ] **Step 4: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_eod_backup.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add utils/backup/eod_backup.py tests/unit/test_eod_backup.py
git commit -m "feat(backup): 备份 verify (SHA256 校验) 与 restore (回拉) 模块"
```

---

### Task 3: CLI 入口（backup / verify / restore 子命令）

**Files:**
- Create: `scripts/run_eod_backup.py`

- [ ] **Step 1: 写 CLI 脚本**

```python
"""EOD 备份 CLI (Production Edition T4, 2026-09-02).

每交易日 17:30 (EOD 链尾) 由计划任务 EOD_Backup 调用.
目的地: D:\\QuantBackup\\28-quant\\ (本地异盘; 云端暂缓, manifest 预留扩展).

用法:
  python scripts/run_eod_backup.py backup [--date YYYY-MM-DD]   # 备份 (默认今日)
  python scripts/run_eod_backup.py verify [--date YYYY-MM-DD]   # 校验最新/指定日
  python scripts/run_eod_backup.py restore <target_dir> [--date YYYY-MM-DD] [--items a,b]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BACKUP_ROOT = Path(r"D:\QuantBackup\28-quant")
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.backup.eod_backup import EodBackup, restore_backup, verify_backup  # noqa: E402


def _resolve_date(date_arg: str | None) -> str:
    return date_arg or datetime.now().strftime("%Y-%m-%d")


def _find_day_dir(date: str) -> Path:
    day_dir = _BACKUP_ROOT / date
    if not day_dir.is_dir():
        # fallback: 最新备份目录
        if _BACKUP_ROOT.is_dir():
            dated = [d for d in _BACKUP_ROOT.iterdir() if d.is_dir() and len(d.name) == 10 and d.name[4] == "-"]
            if dated:
                day_dir = max(dated, key=lambda d: d.name)
    return day_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="EOD 备份 (D 盘异盘 + 90 天滚动)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_bak = sub.add_parser("backup", help="执行备份 (幂等, 同日重跑覆盖)")
    p_bak.add_argument("--date", default=None)
    p_ver = sub.add_parser("verify", help="校验备份完整性")
    p_ver.add_argument("--date", default=None)
    p_res = sub.add_parser("restore", help="回拉备份到目标目录 (主机故障恢复)")
    p_res.add_argument("target", help="回拉目标目录")
    p_res.add_argument("--date", default=None)
    p_res.add_argument("--items", default=None, help="仅回拉指定相对路径 (逗号分隔)")
    args = parser.parse_args()

    date = _resolve_date(getattr(args, "date", None))

    if args.cmd == "backup":
        bak = EodBackup(_PROJECT_ROOT, _BACKUP_ROOT)
        manifest = bak.run(date)
        removed = bak.cleanup_old(today=date)
        r = verify_backup(_BACKUP_ROOT / date)
        status = "OK" if r["ok"] else "VERIFY_FAIL"
        print(f"[OK] backup {date}: {manifest['total_files']} files "
              f"({manifest['total_bytes']} bytes) -> {_BACKUP_ROOT / date} [{status}]")
        if manifest["missing_sources"]:
            print(f"[WARN] 源缺失 (容忍): {manifest['missing_sources']}")
        if removed:
            print(f"[OK] cleanup {len(removed)} old backup(s): {removed}")
        if not r["ok"]:
            print(f"[FAIL] verify: mismatched={r['mismatched']} missing={r['missing']}")
            return 1
        return 0

    if args.cmd == "verify":
        day_dir = _find_day_dir(date)
        r = verify_backup(day_dir)
        print(f"[{'OK' if r['ok'] else 'FAIL'}] verify {day_dir.name}: "
              f"checked={r['checked']} mismatched={r['mismatched']} missing={r['missing']}")
        return 0 if r["ok"] else 1

    if args.cmd == "restore":
        day_dir = _find_day_dir(date)
        items = args.items.split(",") if args.items else None
        r = restore_backup(day_dir, Path(args.target), items=items)
        print(f"[OK] restore {day_dir.name} -> {args.target}: {r['restored']} files")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 真实运行 backup（首跑）**

Run（cwd 项目根）: `.venv\Scripts\python.exe scripts\run_eod_backup.py backup`
Expected: `[OK] backup 2026-09-02: N files (N bytes) -> D:\QuantBackup\28-quant\2026-09-02 [OK]`；可能含 `[WARN] 源缺失 (容忍): ['reports/stop_loss_water_marks.json']`

- [ ] **Step 3: 真实运行 verify**

Run: `.venv\Scripts\python.exe scripts\run_eod_backup.py verify`
Expected: `[OK] verify 2026-09-02: checked=N mismatched=[] missing=[]`

- [ ] **Step 4: ruff 检查**

Run: `.venv\Scripts\python.exe -m ruff check utils\backup\ scripts\run_eod_backup.py`
Expected: All checks passed

- [ ] **Step 5: Commit**

```bash
git add scripts/run_eod_backup.py
git commit -m "feat(backup): EOD 备份 CLI — backup/verify/restore 子命令"
```

---

### Task 4: Health Score 数据维集成备份新鲜度

**Files:**
- Modify: `utils/health/score_engine.py`（score_data 增加可选 backup_root 参数 + 备份过期扣分；compute_health_score 透传）
- Test: `tests/unit/test_health_score_engine.py`（TestScoreData 追加 2 用例）

- [ ] **Step 1: 写失败测试（追加到 TestScoreData 类内）**

```python
    def test_backup_stale_deducts(self, tmp_path):
        _write_degradation_log(tmp_path, [])
        backup_root = tmp_path / "bak"
        backup_root.mkdir()
        (backup_root / "2026-08-01").mkdir()  # 32 天前 > 4 天阈值
        d = score_data(tmp_path, self.DATE, backup_root=backup_root)
        assert d.score == 80.0  # 100 - 20
        assert d.detail["backup_stale"] is True

    def test_backup_fresh_no_deduction(self, tmp_path):
        _write_degradation_log(tmp_path, [])
        backup_root = tmp_path / "bak"
        backup_root.mkdir()
        (backup_root / "2026-09-01").mkdir()  # 1 天前
        d = score_data(tmp_path, self.DATE, backup_root=backup_root)
        assert d.score == 100.0
        assert d.detail["backup_stale"] is False

    def test_backup_root_none_skips_check(self, tmp_path):
        _write_degradation_log(tmp_path, [])
        d = score_data(tmp_path, self.DATE)  # 不传 backup_root: 向后兼容
        assert d.score == 100.0
        assert "backup_stale" not in d.detail
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_score_engine.py::TestScoreData -v`
Expected: FAIL（TypeError: score_data() got unexpected keyword 'backup_root'）

- [ ] **Step 3: 修改 score_engine.py**

1. 顶部 import 区追加:

```python
from datetime import date as _date, timedelta
```

（与现有 `from datetime import datetime` 合并为 `from datetime import date as _date, datetime, timedelta` 更佳）

2. `score_data` 整体替换为:

```python
def _backup_stale(backup_root: Path, today: str) -> bool:
    """最新备份目录距今 >4 天 (含周末+节假日缓冲) 视为过期.

    评分 (17:05) 在备份 (17:30) 之前, 故检查的是最新一次备份而非当日.
    """
    if not backup_root.is_dir():
        return True
    dated: list[str] = []
    for d in backup_root.iterdir():
        if d.is_dir() and len(d.name) == 10 and d.name[4] == "-":
            try:
                datetime.strptime(d.name, "%Y-%m-%d")
            except ValueError:
                continue
            dated.append(d.name)
    if not dated:
        return True
    latest = datetime.strptime(max(dated), "%Y-%m-%d").date()
    today_dt = datetime.strptime(today, "%Y-%m-%d").date()
    return (today_dt - latest).days > 4


def score_data(project_root: Path, date: str,
               backup_root: Path | None = None) -> DimensionScore:
    """数据维: 当日降级审计条目数 + 备份新鲜度 (可选).

    backup_root 提供时: 最新备份距今 >4 天 → 额外 -20 (下限 0).
    """
    path = project_root / "reports" / "degradation_log.jsonl"
    if not path.exists():
        return _degraded("data", "degradation_log.jsonl 不存在")
    n = 0
    scopes: set[str] = set()
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return _degraded("data", "degradation_log.jsonl 读取失败")
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if str(rec.get("ts", "")).startswith(date):
            n += 1
            scopes.add(str(rec.get("scope", "")))
    if n == 0:
        score = 100.0
    elif n <= 2:
        score = 80.0
    elif n <= 5:
        score = 60.0
    else:
        score = 40.0
    detail: dict = {"entries": n, "scopes": sorted(scopes)}
    if backup_root is not None:
        stale = _backup_stale(backup_root, date)
        detail["backup_stale"] = stale
        if stale:
            score = max(0.0, score - 20.0)
    return DimensionScore(
        score=score,
        weight=WEIGHTS["data"],
        degraded=False,
        detail=detail,
    )
```

3. `compute_health_score` 签名与 scorers 调整:

```python
def compute_health_score(project_root: Path, date: str,
                         backup_root: Path | None = None) -> dict:
    """五维聚合 → 评分报告 dict (落盘由 CLI 负责)."""
    scorers = {
        "model": score_model,
        "data": score_data,
        "trading": score_trading,
        "risk": score_risk,
        "capital": score_capital,
    }
    dims: dict[str, DimensionScore] = {}
    for name, fn in scorers.items():
        if name == "data":
            dims[name] = fn(project_root, date, backup_root=backup_root)
        else:
            dims[name] = fn(project_root, date)
    ...  # 其余不变
```

（保持原有 total/status/degraded_dimensions 逻辑不变。）

4. `scripts/compute_health_score.py` 的 main() 中调用处传入真实路径:

```python
_BACKUP_ROOT = Path(r"D:\QuantBackup\28-quant")
```

（模块顶部常量区），调用改为 `compute_health_score(_PROJECT_ROOT, date, backup_root=_BACKUP_ROOT)`。

- [ ] **Step 4: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_score_engine.py -v`
Expected: 44 passed（41 + 3 新增）；既有用例无回归（不传 backup_root 路径不变）

- [ ] **Step 5: 真实运行确认备份信号接入**

Run: `.venv\Scripts\python.exe scripts\compute_health_score.py --date 2026-09-01`
Expected: data 维 detail 含 `"backup_stale": true`（09-01 无备份），分数较之前 -20（40 → 20）

- [ ] **Step 6: Commit**

```bash
git add utils/health/score_engine.py scripts/compute_health_score.py tests/unit/test_health_score_engine.py
git commit -m "feat(health): 数据维集成备份新鲜度信号 (备份 >4 天未更新扣 20 分)"
```

---

### Task 5: 恢复演练 + 计划任务注册

**Files:**
- Create: `scripts/register_backup_task.ps1`（UTF-8 BOM）

- [ ] **Step 1: 主机故障模拟恢复演练（关键状态回拉 ≤2h 验收的组成部分）**

Run（cwd 项目根，PowerShell 顺序执行）:

```powershell
# 模拟主机故障后: 裸机新环境目录
$drill = "$env:TEMP\restore_drill_20260902"
Remove-Item $drill -Recurse -Force -ErrorAction SilentlyContinue
# 从 D 盘备份回拉关键状态
.venv\Scripts\python.exe scripts\run_eod_backup.py restore $drill --items "shadow_account/s12_shadow_state.json","config/main.yaml","degradation/degradation_log.jsonl"
# 回拉结果核对
Get-ChildItem $drill -Recurse -File | Select-Object FullName, Length
# 演练后清理
Remove-Item $drill -Recurse -Force
```

Expected: `[OK] restore 2026-09-02 -> ...: 3 files`；回拉文件存在且可读（config 文件名以实际备份清单为准，若 main.yaml 不在清单则从 manifest.json 中选 3 个真实存在的项）

- [ ] **Step 2: 写计划任务注册脚本（scripts/register_backup_task.ps1，需 UTF-8 BOM 编码）**

```powershell
# 注册 EOD_Backup 计划任务 (Production Edition T4)
# 17:30 交易日运行 (EOD 链尾: 16:30 shadow -> 17:05 评分 -> 17:10 报告 -> 17:30 备份)
# 用 schtasks 避开 CIM 层异常 (同 System_HealthScore 注册模式)
$exe = 'E:\各种PY程序\28-终极量化交易系统8.4\.venv\Scripts\python.exe'
$script = 'E:\各种PY程序\28-终极量化交易系统8.4\scripts\run_eod_backup.py'
$tr = '"' + $exe + '" "' + $script + '" backup'
schtasks /Create /TN "EOD_Backup" /TR $tr /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 17:30 /F
schtasks /Query /TN "EOD_Backup" /FO LIST
```

- [ ] **Step 3: 执行注册（主会话运行，需用户确认）**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "E:\各种PY程序\28-终极量化交易系统8.4\scripts\register_backup_task.ps1"`
Expected: 注册成功，下次运行今日 17:30

- [ ] **Step 4: Commit**

```bash
git add scripts/register_backup_task.ps1
git commit -m "feat(backup): EOD_Backup 计划任务注册脚本 (17:30 交易日)"
```

---

### Task 6: Runbook §4 激活 + LOG 登记

**Files:**
- Modify: `docs/runbooks/PRODUCTION_OPERATIONS_RUNBOOK.md`（§4 备份策略段、§1.1 ② 任务表）
- Modify: `cairn/LOG.md`（顶部追加条目）

- [ ] **Step 1: 更新 runbook §4**

将 §4 中 "**T4 交付前状态**" 段及其后人工应急条款替换为:

```markdown
- **T4 已交付（2026-09-02）**: 计划任务 EOD_Backup 每交易日 17:30 自动执行
  `scripts\run_eod_backup.py backup`（backup 后自动 verify + 90 天滚动清理）.
- **手动操作**:
  - 补跑: `.venv\Scripts\python.exe scripts\run_eod_backup.py backup`
  - 校验: `.venv\Scripts\python.exe scripts\run_eod_backup.py verify`
  - 恢复: `.venv\Scripts\python.exe scripts\run_eod_backup.py restore <目标目录> [--items a,b]`
- **静默失败防护**: 备份 >4 天未更新 → Health Score 数据维扣 20 分（detail.backup_stale）
  + §1.1 ② 调度健康检查 EOD_Backup 行.
- **云端备份**: 暂缓（2026-09-02 用户决策）; manifest destination 字段预留扩展, 接入时零改造.
```

同时 §1.1 ② 计划任务表中"备份任务（T4 交付后激活）"行改为:

```markdown
| EOD_Backup | 17:30 | 0 | 手动: `scripts\run_eod_backup.py backup`（备份后自动 verify） |
```

（删除"T4 未交付前此行 N/A"备注。）

- [ ] **Step 2: LOG.md 顶部追加条目**

```markdown
## 2026-09-02 · T4 EOD 备份链落地（D 盘异盘 + manifest 校验 + 恢复演练，提前于排期 11-01~12-10）

- **背景**: Production Edition 方案 T4（RPO 1 天 / RTO 2 小时）；用户决策：本地异盘 = D:\QuantBackup\28-quant\，云端暂缓（manifest destination 字段预留扩展）
- **交付**: ①`utils/backup/eod_backup.py`（EodBackup 收集/manifest SHA256/90 天滚动清理 + verify_backup/restore_backup）②`scripts/run_eod_backup.py` CLI（backup/verify/restore 子命令，backup 后自动 verify）③Health Score 数据维集成备份新鲜度（最新备份 >4 天扣 20 分，评分 17:05 在备份 17:30 前，检查最新而非当日）④计划任务 EOD_Backup 17:30 交易日（schtasks 模式，同 System_HealthScore）⑤runbook §4 激活
- **验证**: 单测 13 用例（备份 7 + verify/restore 6）+ Health Score 新增 3 用例（共 44）；真实首跑 backup→verify 全绿；恢复演练：关键状态回拉 3 文件至临时目录成功（主机故障模拟的回拉环节）
- **备份清单**: config/ 整目录 + reports/fills/ 整目录 + reports/health_score/ 整目录 + shadow state + degradation_log + stop_loss_water_marks（当前 missing 容忍，实盘后有值）
- **已知偏差**: ①schtasks 未带重试设置（CIM 层限制，同 System_HealthScore）②完整裸机演练（环境重建+计划任务重建+对账）记为后续季度演练项，本次交付回拉环节 ③"连续 7 日成功"自 09-02 起由 17:30 任务自然累积
- **指针**: `utils/backup/eod_backup.py`；`scripts/run_eod_backup.py`；`docs/superpowers/plans/2026-09-02-t4-backup-strategy.md`
```

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/PRODUCTION_OPERATIONS_RUNBOOK.md cairn/LOG.md docs/superpowers/plans/2026-09-02-t4-backup-strategy.md
git commit -m "docs(cairn): T4 备份链落地 LOG 登记 + runbook §4 激活 + 实施计划归档"
```

---

## Self-Review 记录

- **Spec 覆盖**: 方案 §6.1 备份策略（时点 17:30 ✅ Task 5 / 内容六项 ✅ Task 1 清单 / 目的地本地异盘 ✅ D 盘 / 保留 90 天 ✅ cleanup_old / 云端暂缓 ✅ 用户决策 + manifest 预留）；§6.2 恢复手册已由 T3 runbook 承载，本计划补 restore 工具（Task 2）；§八 备份静默失败 → 数据维扣分 ✅ Task 4；验收三条——连续 7 日（任务注册后累积，LOG 注明）、云端回拉（暂缓，D 盘回拉演练替代）、主机故障模拟 ≤2h（Task 5 回拉环节，完整演练记后续）✓
- **占位符扫描**: 无 TBD/TODO；Task 1 Step 3 的 import 置底问题已显式给出最终顶部 import 形态 ✓
- **类型一致性**: `EodBackup(project_root, backup_root).run(date) -> dict` 与 CLI 调用一致；`verify_backup(day_dir) -> dict{ok,checked,mismatched,missing}` 与 restore/CLI 消费一致；`score_data(project_root, date, backup_root=None)` 与 `compute_health_score(..., backup_root=None)` 透传一致 ✓
- **时序核对**: 评分 17:05 检查"最新备份 ≤4 天"而非"当日备份"（备份 17:30 晚于评分）——Task 4 的 `_backup_stale` 按 >4 天阈值实现，周五备份下周一评分为 3 天不误报 ✓
- **既有测试回归**: score_data 新参数默认 None，既有 8 个 TestScoreData 用例路径不变 ✓
