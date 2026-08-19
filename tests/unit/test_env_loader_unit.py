"""env_loader 单元测试 — .env 文件加载全分支覆盖

覆盖:
  - env_path=None 自动查找: 项目根 / 当前目录 / 未找到返回 False
  - 显式 env_path: 文件不存在返回 False
  - KEY=VALUE 基本格式
  - 双引号 / 单引号包裹剥离
  - 单字符值 / 不匹配引号 不剥离
  - 空行 / 注释行 跳过
  - 缺少 = 分隔符 跳过
  - 空键 跳过
  - override=False 已存在不覆盖
  - override=True 已存在覆盖
  - 多键加载
  - OSError 返回 False
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import env_loader  # noqa: E402


class TestAutoDiscovery:
    """env_path=None 自动查找分支"""

    def test_auto_find_returns_false_when_no_env(self, tmp_path, monkeypatch):
        project_area = tmp_path / "proj"
        project_area.mkdir()
        cwd_area = tmp_path / "cwd"
        cwd_area.mkdir()
        monkeypatch.setattr(
            env_loader, "__file__", str(project_area / "sub" / "env_loader.py")
        )
        monkeypatch.chdir(cwd_area)
        assert env_loader.load_dotenv() is False

    def test_auto_find_in_project_root(self, tmp_path, monkeypatch):
        project_area = tmp_path / "proj"
        project_area.mkdir()
        (project_area / ".env").write_text("PROJ_KEY=proj_value\n", encoding="utf-8")
        cwd_area = tmp_path / "cwd"
        cwd_area.mkdir()
        monkeypatch.setattr(
            env_loader, "__file__", str(project_area / "sub" / "env_loader.py")
        )
        monkeypatch.chdir(cwd_area)
        monkeypatch.delenv("PROJ_KEY", raising=False)
        assert env_loader.load_dotenv() is True
        assert os.environ["PROJ_KEY"] == "proj_value"

    def test_auto_find_in_cwd(self, tmp_path, monkeypatch):
        project_area = tmp_path / "proj"
        project_area.mkdir()
        cwd_area = tmp_path / "cwd"
        cwd_area.mkdir()
        (cwd_area / ".env").write_text("CWD_KEY=cwd_value\n", encoding="utf-8")
        monkeypatch.setattr(
            env_loader, "__file__", str(project_area / "sub" / "env_loader.py")
        )
        monkeypatch.chdir(cwd_area)
        monkeypatch.delenv("CWD_KEY", raising=False)
        assert env_loader.load_dotenv() is True
        assert os.environ["CWD_KEY"] == "cwd_value"


class TestExplicitPath:
    """显式 env_path 分支"""

    def test_explicit_path_not_exist_returns_false(self, tmp_path):
        assert env_loader.load_dotenv(str(tmp_path / "nope.env")) is False

    def test_basic_key_value(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("BASIC_KEY=basic_value\n", encoding="utf-8")
        monkeypatch.delenv("BASIC_KEY", raising=False)
        assert env_loader.load_dotenv(str(env)) is True
        assert os.environ["BASIC_KEY"] == "basic_value"

    def test_multiple_keys(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("K1=v1\nK2=v2\nK3=v3\n", encoding="utf-8")
        for k in ("K1", "K2", "K3"):
            monkeypatch.delenv(k, raising=False)
        env_loader.load_dotenv(str(env))
        assert os.environ["K1"] == "v1"
        assert os.environ["K2"] == "v2"
        assert os.environ["K3"] == "v3"


class TestQuoteStripping:
    """引号包裹剥离"""

    def test_double_quotes_stripped(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text('DQ_KEY="quoted value"\n', encoding="utf-8")
        monkeypatch.delenv("DQ_KEY", raising=False)
        env_loader.load_dotenv(str(env))
        assert os.environ["DQ_KEY"] == "quoted value"

    def test_single_quotes_stripped(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("SQ_KEY='single quoted'\n", encoding="utf-8")
        monkeypatch.delenv("SQ_KEY", raising=False)
        env_loader.load_dotenv(str(env))
        assert os.environ["SQ_KEY"] == "single quoted"

    def test_single_char_value_not_quote_stripped(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text('SINGLE_Q="\n', encoding="utf-8")
        monkeypatch.delenv("SINGLE_Q", raising=False)
        env_loader.load_dotenv(str(env))
        assert os.environ["SINGLE_Q"] == '"'

    def test_mismatched_quotes_not_stripped(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("MISMATCH=\"value'\n", encoding="utf-8")
        monkeypatch.delenv("MISMATCH", raising=False)
        env_loader.load_dotenv(str(env))
        assert os.environ["MISMATCH"] == "\"value'"


class TestLineSkipping:
    """空行 / 注释 / 无等号 / 空键 跳过"""

    def test_empty_line_skipped(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("\n   \nEMPTY_LINE_KEY=val\n", encoding="utf-8")
        monkeypatch.delenv("EMPTY_LINE_KEY", raising=False)
        env_loader.load_dotenv(str(env))
        assert os.environ["EMPTY_LINE_KEY"] == "val"

    def test_comment_line_skipped(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("# this is a comment\nCOMMENT_KEY=val\n", encoding="utf-8")
        monkeypatch.delenv("COMMENT_KEY", raising=False)
        env_loader.load_dotenv(str(env))
        assert os.environ["COMMENT_KEY"] == "val"

    def test_line_without_equals_skipped(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("NO_EQUALS_HERE\nVALID_KEY=val\n", encoding="utf-8")
        monkeypatch.delenv("VALID_KEY", raising=False)
        env_loader.load_dotenv(str(env))
        assert os.environ["VALID_KEY"] == "val"

    def test_empty_key_skipped(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("=value_only\nEMPTY_KEY_KEY=val\n", encoding="utf-8")
        monkeypatch.delenv("EMPTY_KEY_KEY", raising=False)
        env_loader.load_dotenv(str(env))
        assert os.environ["EMPTY_KEY_KEY"] == "val"


class TestOverride:
    """override 标志"""

    def test_override_false_preserves_existing(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("OVERRIDE_KEY=new_value\n", encoding="utf-8")
        monkeypatch.setenv("OVERRIDE_KEY", "original_value")
        env_loader.load_dotenv(str(env), override=False)
        assert os.environ["OVERRIDE_KEY"] == "original_value"

    def test_override_true_overwrites_existing(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("OVERRIDE_KEY=new_value\n", encoding="utf-8")
        monkeypatch.setenv("OVERRIDE_KEY", "original_value")
        env_loader.load_dotenv(str(env), override=True)
        assert os.environ["OVERRIDE_KEY"] == "new_value"

    def test_override_false_sets_when_absent(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("NEW_KEY=brand_new\n", encoding="utf-8")
        monkeypatch.delenv("NEW_KEY", raising=False)
        env_loader.load_dotenv(str(env), override=False)
        assert os.environ["NEW_KEY"] == "brand_new"


class TestErrorHandling:
    """OSError 分支"""

    def test_open_oserror_returns_false(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("K=v\n", encoding="utf-8")
        with patch("pathlib.Path.open", side_effect=OSError("boom")):
            assert env_loader.load_dotenv(str(env)) is False
