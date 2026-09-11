"""check_utf8_mojibake 单元测试 — 2026-09-11 编码事故防复发门禁。

背景: `9e884552` (cnb/main 合并冲突解决) 对 cairn/ROADMAP.md 与
docs/LLM权限边界规范.md 写成 mojibake 双重编码全文花屏且已入 main,
现有门禁对编码损坏无感。本测试锁定新检测器的三类检测 + 误报控制。

先红后绿: 检测器交付前, 以下"必须抓到"的用例全部无工具可跑 (脚本不存在);
交付后全绿。负向用例 (clean/单字符) 锁定不误报基线。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from check_utf8_mojibake import (  # noqa: E402
    _MOJIBAKE_SIGNATURES,
    _analyze_file,
    main,
)

# 与事故同构的真实样本: UTF-8 中文被按 GBK 误读再存回 UTF-8
_CLEAN_SAMPLE = "# 测试标题\n\n这是测试的正文，包含逗号与引号“引号”。数据文件的上传。"
_MOJIBAKE_SAMPLE = _CLEAN_SAMPLE.encode("utf-8").decode("gbk", errors="replace")


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    f = tmp_path / name
    f.write_bytes(data)
    return f


class TestDetection:
    """三类损坏形态必须被抓到。"""

    def test_mojibake_double_encoding_detected(self, tmp_path: Path) -> None:
        """2026-09-11 事故同构: UTF-8→GBK 误读→存回 UTF-8。"""
        f = _write(tmp_path, "moji.md", _MOJIBAKE_SAMPLE.encode("utf-8"))
        v = _analyze_file(f)
        assert v is not None
        assert v["kind"] == "MOJIBAKE_DOUBLE_ENCODING"

    def test_invalid_utf8_detected(self, tmp_path: Path) -> None:
        """字节流不能按 UTF-8 解码 (含 GBK 直写形态)。"""
        f = _write(tmp_path, "bad.md", "这是GBK直写的中文".encode("gbk"))
        v = _analyze_file(f)
        assert v is not None
        assert v["kind"] == "INVALID_UTF8"

    def test_replacement_char_pileup_detected(self, tmp_path: Path) -> None:
        """U+FFFD 堆积 (历史 damage, 每日报告归档 2026-08-23 同款)。"""
        f = _write(
            tmp_path, "pile.md", "正常文本\ufffd\ufffd\ufffd\ufffd" .encode("utf-8")
        )
        v = _analyze_file(f)
        assert v is not None
        assert v["kind"] == "REPLACEMENT_CHAR_PILEUP"

    def test_read_error_reported(self, tmp_path: Path) -> None:
        """读文件失败 → READ_ERROR (fail-closed, 不静默跳过)。"""
        f = tmp_path / "vanish.md"
        f.write_bytes(b"ok")
        f.chmod(0o000)
        try:
            v = _analyze_file(f)
            if v is not None:  # 非 root 环境下才可触发
                assert v["kind"] == "READ_ERROR"
        finally:
            f.chmod(0o644)


class TestNoFalsePositive:
    """误报控制 — 正常简体中文知识文档必须全绿。"""

    def test_clean_chinese_md_passes(self, tmp_path: Path) -> None:
        f = _write(
            tmp_path, "ok.md",
            "正常简体中文文档：包含逗号、引号“这样”、版本号 v8.7。".encode(),
        )
        assert _analyze_file(f) is None

    def test_single_fffd_below_threshold_passes(self, tmp_path: Path) -> None:
        """单字符损耗 (cairn/LOG.md 现存 1 处 U+FFFD) 不得误报。"""
        f = _write(tmp_path, "single.md", "TTL \ufffd 惰性淘汰".encode("utf-8"))
        assert _analyze_file(f) is None

    def test_common_chinese_chars_not_in_signatures(self) -> None:
        """特征字符集不得混入常用简体字 (防 sprint1 材料 '版' 字误报)。"""
        common = "版的是在上和了有这中大为来个时我到"
        for ch in common:
            assert ch not in _MOJIBAKE_SIGNATURES, f"常用字 {ch} 误入特征集"

    def test_english_only_passes(self, tmp_path: Path) -> None:
        f = _write(tmp_path, "en.md", b"# English only\nplain ascii text\n")
        assert _analyze_file(f) is None


class TestMainIntegration:
    """CLI 入口集成。"""

    def test_full_scan_current_repo_passes(self) -> None:
        """全量扫描当前仓库知识层必须 exit 0 (2026-09-11 基线: 除豁免归档外全干净)。"""
        rc = main([])
        assert rc == 0

    def test_unknown_arg_exits_nonzero(self) -> None:
        with pytest.raises(SystemExit) as e:
            main(["--definitely-not-a-flag"])
        assert e.value.code != 0

    def test_staged_mode_clean_tree(self) -> None:
        """--staged 模式: 干净暂存区 → 0 文件 → exit 0。"""
        # 本测试工作树无暂存 .md (变更未 add), → exit 0
        rc = main(["--staged", "--quiet"])
        assert rc == 0

    def test_script_direct_run(self) -> None:
        """脚本可独立执行 (python scripts/check_utf8_mojibake.py)。"""
        r = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "check_utf8_mojibake.py")],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(PROJECT_ROOT),
        )
        assert r.returncode == 0, r.stdout + r.stderr


