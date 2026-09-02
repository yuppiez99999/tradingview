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
