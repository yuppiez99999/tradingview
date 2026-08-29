"""ECL flag 回归测试 — CTX-A3 T1.

验证三 flag 注册正确 + 现有 flag 不破坏.
"""

from __future__ import annotations

from utils.infra.feature_flags import FeatureFlags


class TestEclFlags:
    """三 flag 注册验证."""

    def test_use_ecl_event_log_registered(self) -> None:
        flags = FeatureFlags.get_instance()
        flag_def = flags.get_flag_def("USE_ECL_EVENT_LOG")
        assert flag_def is not None
        assert flag_def.get("default") is False
        assert flag_def.get("requires_dual_sign") is True

    def test_use_ecl_experience_registered(self) -> None:
        flags = FeatureFlags.get_instance()
        flag_def = flags.get_flag_def("USE_ECL_EXPERIENCE")
        assert flag_def is not None
        assert flag_def.get("default") is False

    def test_use_ecl_retrieval_registered(self) -> None:
        flags = FeatureFlags.get_instance()
        flag_def = flags.get_flag_def("USE_ECL_RETRIEVAL")
        assert flag_def is not None
        assert flag_def.get("default") is False

    def test_all_ecl_flags_default_false(self) -> None:
        flags = FeatureFlags.get_instance()
        assert flags.is_enabled("USE_ECL_EVENT_LOG") is False
        assert flags.is_enabled("USE_ECL_EXPERIENCE") is False
        assert flags.is_enabled("USE_ECL_RETRIEVAL") is False

    def test_ecl_flags_in_list_flags(self) -> None:
        flags = FeatureFlags.get_instance()
        all_flags = flags.list_flags()
        names = {f["name"] for f in all_flags}
        assert "USE_ECL_EVENT_LOG" in names
        assert "USE_ECL_EXPERIENCE" in names
        assert "USE_ECL_RETRIEVAL" in names

    def test_existing_flags_not_broken(self) -> None:
        """现有 flag 不被破坏."""
        flags = FeatureFlags.get_instance()
        all_flags = flags.list_flags()
        assert len(all_flags) > 3
        for f in all_flags:
            assert "name" in f
            assert "current_value" in f
