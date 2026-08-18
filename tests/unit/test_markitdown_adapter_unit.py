"""test_markitdown_adapter_unit.py — MarkItDown 文档转换适配器单元测试

覆盖要点:
    - SUPPORTED_EXTENSIONS 常量
    - MarkItDownAdapter 单例 get_instance
    - _find_python310 (subprocess mock: 找到/找不到)
    - _check_installed (已安装/未安装/无 Python 3.10+)
    - _ensure_installed (已安装跳过/自动安装成功/安装失败)
    - convert_to_markdown (文件不存在/不支持格式/不可用/转换成功/转换失败/超时)
    - convert_url (不可用/成功/失败/超时)
    - batch_convert
    - get_status
    - 便捷函数 get_adapter / convert_to_markdown / convert_url
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import utils.markitdown_adapter as mod
from utils.markitdown_adapter import MarkItDownAdapter, SUPPORTED_EXTENSIONS


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """每个测试前重置单例"""
    monkeypatch.setattr(MarkItDownAdapter, "_instance", None)
    monkeypatch.setattr(mod, "_default_adapter", None)


# ============================================================
# 常量
# ============================================================


class TestConstants:
    @pytest.mark.unit
    def test_supported_extensions_includes_pdf(self):
        assert ".pdf" in SUPPORTED_EXTENSIONS

    @pytest.mark.unit
    def test_supported_extensions_includes_docx(self):
        assert ".docx" in SUPPORTED_EXTENSIONS

    @pytest.mark.unit
    def test_supported_extensions_includes_xlsx(self):
        assert ".xlsx" in SUPPORTED_EXTENSIONS


# ============================================================
# 单例
# ============================================================


class TestSingleton:
    @pytest.mark.unit
    def test_get_instance_returns_same(self):
        a = MarkItDownAdapter.get_instance()
        b = MarkItDownAdapter.get_instance()
        assert a is b

    @pytest.mark.unit
    def test_get_adapter_returns_same(self):
        a = mod.get_adapter()
        b = mod.get_adapter()
        assert a is b


# ============================================================
# _find_python310
# ============================================================


class TestFindPython310:
    @pytest.mark.unit
    def test_finds_version(self):
        adapter = MarkItDownAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Python 3.12.0"
        with patch("subprocess.run", return_value=mock_result):
            version = adapter._find_python310()
        assert version is not None

    @pytest.mark.unit
    def test_no_python_found(self):
        adapter = MarkItDownAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("subprocess.run", return_value=mock_result):
            version = adapter._find_python310()
        assert version is None

    @pytest.mark.unit
    def test_filenotfound_returns_none(self):
        adapter = MarkItDownAdapter()
        with patch("subprocess.run", side_effect=FileNotFoundError("no py launcher")):
            version = adapter._find_python310()
        assert version is None


# ============================================================
# _check_installed
# ============================================================


class TestCheckInstalled:
    @pytest.mark.unit
    def test_already_checked_returns_cached(self):
        adapter = MarkItDownAdapter()
        adapter._installed = True
        # 不应调用 subprocess
        with patch("subprocess.run", side_effect=AssertionError("should not call")):
            assert adapter._check_installed() is True

    @pytest.mark.unit
    def test_no_python_returns_false(self):
        adapter = MarkItDownAdapter()
        with patch.object(adapter, "_find_python310", return_value=None):
            assert adapter._check_installed() is False
        assert adapter._installed is False

    @pytest.mark.unit
    def test_installed_true(self):
        adapter = MarkItDownAdapter()
        adapter._py_version = "3.12"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "markitdown 0.1.0"
        with patch("subprocess.run", return_value=mock_result):
            assert adapter._check_installed() is True
        assert adapter._installed is True

    @pytest.mark.unit
    def test_markitdown_not_installed(self):
        adapter = MarkItDownAdapter()
        adapter._py_version = "3.12"
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("subprocess.run", return_value=mock_result):
            assert adapter._check_installed() is False
        assert adapter._installed is False


# ============================================================
# _ensure_installed
# ============================================================


class TestEnsureInstalled:
    @pytest.mark.unit
    def test_already_installed(self):
        adapter = MarkItDownAdapter()
        adapter._installed = True
        adapter._py_version = "3.12"
        with patch("subprocess.run", side_effect=AssertionError("should not call")):
            assert adapter._ensure_installed() is True

    @pytest.mark.unit
    def test_install_success(self):
        adapter = MarkItDownAdapter()
        adapter._py_version = "3.12"
        adapter._installed = False
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            assert adapter._ensure_installed() is True
        assert adapter._installed is True

    @pytest.mark.unit
    def test_install_failure(self):
        adapter = MarkItDownAdapter()
        adapter._py_version = "3.12"
        adapter._installed = False
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "pip install failed"
        with patch("subprocess.run", return_value=mock_result):
            assert adapter._ensure_installed() is False

    @pytest.mark.unit
    def test_no_python_returns_false(self):
        adapter = MarkItDownAdapter()
        adapter._py_version = None
        adapter._installed = False
        assert adapter._ensure_installed() is False


# ============================================================
# convert_to_markdown
# ============================================================


class TestConvertToMarkdown:
    @pytest.mark.unit
    def test_file_not_exists(self, tmp_path):
        adapter = MarkItDownAdapter()
        result = adapter.convert_to_markdown(str(tmp_path / "nonexistent.pdf"))
        assert result == ""

    @pytest.mark.unit
    def test_unsupported_extension(self, tmp_path):
        adapter = MarkItDownAdapter()
        f = tmp_path / "test.xyz"
        f.write_text("dummy")
        result = adapter.convert_to_markdown(str(f))
        assert result == ""

    @pytest.mark.unit
    def test_markitdown_unavailable(self, tmp_path):
        adapter = MarkItDownAdapter()
        f = tmp_path / "test.pdf"
        f.write_text("dummy")
        with patch.object(adapter, "_ensure_installed", return_value=False):
            result = adapter.convert_to_markdown(str(f))
        assert "文档转换不可用" in result

    @pytest.mark.unit
    def test_convert_success(self, tmp_path):
        adapter = MarkItDownAdapter()
        adapter._py_version = "3.12"
        adapter._installed = True
        f = tmp_path / "test.pdf"
        f.write_text("dummy")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "# Markdown content"
        with patch("subprocess.run", return_value=mock_result):
            result = adapter.convert_to_markdown(str(f))
        assert result == "# Markdown content"

    @pytest.mark.unit
    def test_convert_failure(self, tmp_path):
        adapter = MarkItDownAdapter()
        adapter._py_version = "3.12"
        adapter._installed = True
        f = tmp_path / "test.pdf"
        f.write_text("dummy")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"
        with patch("subprocess.run", return_value=mock_result):
            result = adapter.convert_to_markdown(str(f))
        assert result == ""

    @pytest.mark.unit
    def test_convert_timeout(self, tmp_path):
        import subprocess
        adapter = MarkItDownAdapter()
        adapter._py_version = "3.12"
        adapter._installed = True
        f = tmp_path / "test.pdf"
        f.write_text("dummy")
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="markitdown", timeout=120)):
            result = adapter.convert_to_markdown(str(f))
        assert result == ""


# ============================================================
# convert_url
# ============================================================


class TestConvertUrl:
    @pytest.mark.unit
    def test_unavailable(self):
        adapter = MarkItDownAdapter()
        with patch.object(adapter, "_ensure_installed", return_value=False):
            result = adapter.convert_url("https://example.com")
        assert "网页转换不可用" in result

    @pytest.mark.unit
    def test_success(self):
        adapter = MarkItDownAdapter()
        adapter._py_version = "3.12"
        adapter._installed = True
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "# Web content"
        with patch("subprocess.run", return_value=mock_result):
            result = adapter.convert_url("https://example.com")
        assert result == "# Web content"

    @pytest.mark.unit
    def test_failure(self):
        adapter = MarkItDownAdapter()
        adapter._py_version = "3.12"
        adapter._installed = True
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"
        with patch("subprocess.run", return_value=mock_result):
            result = adapter.convert_url("https://example.com")
        assert result == ""

    @pytest.mark.unit
    def test_timeout(self):
        import subprocess
        adapter = MarkItDownAdapter()
        adapter._py_version = "3.12"
        adapter._installed = True
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="markitdown", timeout=120)):
            result = adapter.convert_url("https://example.com")
        assert result == ""


# ============================================================
# batch_convert
# ============================================================


class TestBatchConvert:
    @pytest.mark.unit
    def test_batch(self, tmp_path):
        adapter = MarkItDownAdapter()
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.write_text("a")
        f2.write_text("b")
        with patch.object(adapter, "convert_to_markdown", side_effect=["md_a", "md_b"]):
            result = adapter.batch_convert([str(f1), str(f2)])
        assert result[str(f1)] == "md_a"
        assert result[str(f2)] == "md_b"

    @pytest.mark.unit
    def test_batch_skips_empty(self, tmp_path):
        adapter = MarkItDownAdapter()
        f1 = tmp_path / "a.pdf"
        f1.write_text("a")
        with patch.object(adapter, "convert_to_markdown", side_effect=["", "md_b"]):
            result = adapter.batch_convert([str(f1), "nonexistent"])
        assert str(f1) not in result


# ============================================================
# get_status
# ============================================================


class TestGetStatus:
    @pytest.mark.unit
    def test_status_structure(self):
        adapter = MarkItDownAdapter()
        adapter._py_version = "3.12"
        adapter._installed = True
        with patch.object(adapter, "_ensure_installed", return_value=True):
            status = adapter.get_status()
        assert "available" in status
        assert "python_version" in status
        assert "markitdown_installed" in status
        assert "supported_formats" in status
        assert status["available"] is True
        assert status["python_version"] == "3.12"