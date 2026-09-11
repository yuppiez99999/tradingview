"""
CLI 模型切换器 单元测试
======================

测试 W.C.2 EchoBird 多 CLI 模型切换:
- list_profiles / get_profile
- current (从 .env 推断)
- switch (dry_run + 实际切换 + 备份)
- IDE 配置同步 (best-effort)
- CLI 入口 (main)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.cli_model_switcher import (  # noqa: E402
    _read_env,
    _sync_ide_config,
    _update_env_file,
    backup_env,
    current,
    get_profile,
    list_profiles,
    main,
    switch,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def tmp_env():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8"
    ) as f:
        f.write("# test .env\n")
        f.write("DEEPSEEK_API_KEY=secret_key\n")
        f.write("DEEPSEEK_MODEL=deepseek-chat\n")
        f.write("DEEPSEEK_BASE_URL=https://api.deepseek.com\n")
        f.write("OLLAMA_MODEL=qwen2.5:3b\n")
        path = Path(f.name)
    yield path
    try:
        path.unlink(missing_ok=True)
    except (ValueError, TypeError, OSError):
        pass


@pytest.fixture
def tmp_project(tmp_path):
    """临时项目目录 (含 configs/cli_profiles + .env)"""
    profiles_dir = tmp_path / "configs" / "cli_profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "deepseek.yaml").write_text(
        "name: deepseek\ndisplay_name: 'DeepSeek'\ndescription: 'test'\n"
        "env_vars:\n  DEEPSEEK_MODEL: deepseek-chat\n  OLLAMA_MODEL: qwen2.5:3b\n"
        "fallback_chain: [deepseek, ollama]\n"
        "ide_configs: {}\n",
        encoding="utf-8",
    )
    (profiles_dir / "glm.yaml").write_text(
        "name: glm\ndisplay_name: 'GLM'\ndescription: 'test'\n"
        "env_vars:\n  DEEPSEEK_MODEL: glm-5.2\n"
        "fallback_chain: [glm, deepseek]\n"
        "ide_configs: {}\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "# test\nDEEPSEEK_MODEL=deepseek-chat\nDEEPSEEK_API_KEY=secret\n",
        encoding="utf-8",
    )
    return tmp_path


# ============================================================
# 1. Profile 加载
# ============================================================


class TestProfileLoading:
    def test_list_profiles(self):
        profiles = list_profiles()
        assert len(profiles) >= 3
        names = [p.name for p in profiles]
        assert "deepseek" in names
        assert "glm" in names
        assert "ollama" in names

    def test_get_profile(self):
        p = get_profile("deepseek")
        assert p.name == "deepseek"
        assert p.display_name
        assert p.env_vars

    def test_get_profile_nonexistent(self):
        with pytest.raises(ValueError, match="不存在"):
            get_profile("nonexistent")

    def test_profile_has_fallback_chain(self):
        p = get_profile("deepseek")
        assert len(p.fallback_chain) >= 2
        assert p.fallback_chain[0] == "deepseek"


# ============================================================
# 2. .env 读写
# ============================================================


class TestEnvReadWrite:
    def test_read_env(self, tmp_env):
        env = _read_env(tmp_env)
        assert env["DEEPSEEK_API_KEY"] == "secret_key"
        assert env["DEEPSEEK_MODEL"] == "deepseek-chat"
        assert env["OLLAMA_MODEL"] == "qwen2.5:3b"

    def test_read_env_nonexistent(self):
        result = _read_env(Path("/nonexistent/path/.env"))
        assert result == {}

    def test_update_env_file(self, tmp_env):
        updated = _update_env_file({"DEEPSEEK_MODEL": "glm-5.2"}, tmp_env)
        assert updated is True
        env = _read_env(tmp_env)
        assert env["DEEPSEEK_MODEL"] == "glm-5.2"
        assert env["DEEPSEEK_API_KEY"] == "secret_key"

    def test_update_env_file_new_key(self, tmp_env):
        _update_env_file({"NEW_VAR": "new_value"}, tmp_env)
        env = _read_env(tmp_env)
        assert env["NEW_VAR"] == "new_value"

    def test_backup_env(self, tmp_env):
        backup = backup_env(tmp_env)
        assert backup is not None
        assert backup.exists()
        assert ".env.bak." in backup.name
        backup.unlink(missing_ok=True)

    def test_backup_env_nonexistent(self):
        result = backup_env(Path("/nonexistent/.env"))
        assert result is None


# ============================================================
# 3. current / switch
# ============================================================


class TestCurrentAndSwitch:
    def test_current_matches(self, tmp_env):
        # hermetic 修复 (2026-09-09): 此前依赖仓库根 .env 真实存在且 DEEPSEEK_MODEL
        # 与某 profile 匹配 (.env 属 gitignore 敏感文件, clone 后必然缺失)。
        # 改为 patch _ENV_FILE 到临时 .env, 语义不变、环境无关。
        with patch("scripts.cli_model_switcher._ENV_FILE", tmp_env):
            cur = current()
        assert cur is not None
        assert cur.name in ("deepseek", "glm", "ollama")

    def test_switch_dry_run(self):
        result = switch("glm", dry_run=True)
        assert result.dry_run is True
        assert result.profile.name == "glm"
        assert result.env_updated is False

    def test_switch_with_backup(self, tmp_env):
        with patch("scripts.cli_model_switcher._ENV_FILE", tmp_env):
            result = switch("glm", backup=True)
        assert result.env_updated is True
        assert result.env_backup_path is not None
        env = _read_env(tmp_env)
        assert env["DEEPSEEK_MODEL"] == "glm-5.2"

    def test_switch_no_backup(self, tmp_env):
        with patch("scripts.cli_model_switcher._ENV_FILE", tmp_env):
            result = switch("ollama", backup=False)
        assert result.env_backup_path is None
        env = _read_env(tmp_env)
        assert env["OLLAMA_MODEL"] == "qwen2.5:3b"

    def test_switch_preserves_api_key(self, tmp_env):
        original_env = _read_env(tmp_env)
        with patch("scripts.cli_model_switcher._ENV_FILE", tmp_env):
            switch("glm", backup=False)
        new_env = _read_env(tmp_env)
        assert new_env["DEEPSEEK_API_KEY"] == original_env["DEEPSEEK_API_KEY"]

    def test_switch_nonexistent_profile(self):
        with pytest.raises(ValueError, match="不存在"):
            switch("nonexistent")


# ============================================================
# 4. IDE 配置同步
# ============================================================


class TestIDESync:
    def test_sync_ide_json(self, tmp_path):
        ide_dir = tmp_path / ".codebuddy"
        ide_dir.mkdir()
        settings = ide_dir / "settings.local.json"
        settings.write_text(json.dumps({"existing": True}), encoding="utf-8")

        ok = _sync_ide_config(
            ".codebuddy",
            {
                "type": "json",
                "model_field": "model",
                "model_value": "glm-5.2",
            },
            project_root=tmp_path,
        )
        assert ok is True
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["model"] == "glm-5.2"
        assert data["existing"] is True

    def test_sync_ide_nonexistent_dir(self, tmp_path):
        ok = _sync_ide_config(".nonexistent", {}, project_root=tmp_path)
        assert ok is False

    def test_sync_ide_no_settings_file(self, tmp_path):
        (tmp_path / ".codebuddy").mkdir()
        ok = _sync_ide_config(
            ".codebuddy",
            {
                "type": "json",
                "model_field": "model",
                "model_value": "glm-5.2",
            },
            project_root=tmp_path,
        )
        assert ok is False


# ============================================================
# 5. CLI 入口
# ============================================================


class TestCLIEntry:
    def test_main_list(self, capsys):
        ret = main(["list"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "deepseek" in captured.out
        assert "glm" in captured.out

    def test_main_current(self, capsys):
        ret = main(["current"])
        assert ret == 0

    def test_main_switch_dry_run(self, capsys):
        ret = main(["switch", "glm", "--dry-run"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "[dry-run]" in captured.out
        assert "glm" in captured.out

    def test_main_switch_nonexistent(self):
        with pytest.raises(ValueError):
            main(["switch", "nonexistent"])
