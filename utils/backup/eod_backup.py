"""EOD 备份核心 (Production Edition T4, 2026-09-02).

每日 17:30 (EOD 链尾) 备份关键状态到本地 backups/28-quant/
(2026-09-05 起工程内自包含, 原 D:\\QuantBackup 历史已并入):
  目录: config/ (配置) + reports/fills/ (成交) + reports/health_score/ (评分历史)
  文件: shadow 账户状态 + 止损水位 + degradation_log
manifest.json 记录每文件 SHA256 (供 verify/restore 校验), destination 字段
预留云端扩展 (2026-09-02 用户决策: 云端暂缓).
保留策略: 本地 90 天滚动 (cleanup_old).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta
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
