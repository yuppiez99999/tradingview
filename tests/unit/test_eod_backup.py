"""EOD 备份核心模块单测 (Production Edition T4, 2026-09-02)."""
from __future__ import annotations

from pathlib import Path

from utils.backup.eod_backup import EodBackup, restore_backup, verify_backup


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
        # 注: Windows write_text 默认 newline 转换 (\n -> \r\n), 故与源文件实际大小比较
        assert by_path["config/main.yaml"]["bytes"] == (src / "config" / "main.yaml").stat().st_size

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
        dst.mkdir(parents=True, exist_ok=True)  # write_text 前需先建目录
        (dst / "notes.txt").write_text("x", encoding="utf-8")
        bak = EodBackup(src, dst)
        bak.run("2026-01-01")
        removed = bak.cleanup_old(today="2026-09-02")
        assert removed == ["2026-01-01"]
        assert (dst / "notes.txt").exists()


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
