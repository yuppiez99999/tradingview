"""P1-2 配置缺失静默降级闭环单测

覆盖: degradation_audit 审计 (追加/去重/读取/fail-safe)、
ConfigManager strict 模式、daily_trade_executor 风控降级审计、
stop_loss_monitor 规则缺失审计。
"""

import json
from pathlib import Path

import pytest

import utils.degradation_audit as da
from utils.config_manager import (
    ConfigNotFoundError,
    get_config,
)
from utils.degradation_audit import (
    pending_degradations,
    record_degradation,
    reset_dedupe,
)


@pytest.fixture(autouse=True)
def _isolated_audit_log(tmp_path, monkeypatch):
    """每个测试用独立审计文件 + 清空去重标记"""
    log_file = tmp_path / "degradation_log.jsonl"
    monkeypatch.setattr(da, "LOG_FILE", log_file)
    reset_dedupe()
    yield log_file
    reset_dedupe()


class TestDegradationAudit:
    def test_record_writes_jsonl(self, _isolated_audit_log):
        ok = record_degradation(
            scope="m1", key="cfg.yaml", default="d", reason="r"
        )
        assert ok is True
        events = pending_degradations()
        assert len(events) == 1
        e = events[0]
        assert e["scope"] == "m1"
        assert e["key"] == "cfg.yaml"
        assert e["default"] == "d"
        assert e["reason"] == "r"
        assert "ts" in e

    def test_dedupe_same_scope_key(self, _isolated_audit_log):
        assert record_degradation(scope="m", key="k") is True
        assert record_degradation(scope="m", key="k") is False  # 去重
        assert len(pending_degradations()) == 1

    def test_no_dedupe_different_key(self, _isolated_audit_log):
        record_degradation(scope="m", key="k1")
        record_degradation(scope="m", key="k2")
        assert len(pending_degradations()) == 2

    def test_dedupe_disabled(self, _isolated_audit_log):
        record_degradation(scope="m", key="k")
        assert record_degradation(scope="m", key="k", dedupe=False) is True
        assert len(pending_degradations()) == 2

    def test_pending_empty_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(da, "LOG_FILE", tmp_path / "nonexistent.jsonl")
        assert pending_degradations() == []

    def test_record_fail_safe_on_os_error(self, tmp_path, monkeypatch):
        """审计写失败 (父路径是文件) 不抛异常"""
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("x")
        monkeypatch.setattr(da, "LOG_FILE", blocker / "sub.jsonl")
        assert record_degradation(scope="m", key="k") is False

    def test_thread_safe_append(self, _isolated_audit_log):
        import threading

        threads = [
            threading.Thread(
                target=record_degradation, args=(f"scope{i}", f"key{i}")
            )
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(pending_degradations()) == 20


class TestConfigManagerStrict:
    def test_strict_missing_config_raises(self, monkeypatch):
        from utils.config_manager import clear_config_cache

        clear_config_cache()
        with pytest.raises(ConfigNotFoundError, match="strict"):
            get_config("__no_such_config_p12__", strict=True)
        clear_config_cache()

    def test_non_strict_missing_returns_default(self):
        cfg = get_config("__no_such_config_p12__", default={"f": 1})
        assert cfg == {"f": 1}

    def test_missing_config_records_audit(self, monkeypatch):
        from utils.config_manager import clear_config_cache

        clear_config_cache()
        get_config("__no_such_config_p12__")
        events = pending_degradations()
        assert any(
            e["scope"] == "config_manager" and "__no_such_config_p12__" in e["key"]
            for e in events
        )
        clear_config_cache()

    def test_empty_config_records_audit(self, tmp_path, monkeypatch):
        """文件存在但为空也记审计 (需重置单例让 QUANT_CONFIG_DIR 生效)"""
        from utils.config_manager import ConfigManager, clear_config_cache

        monkeypatch.setenv("QUANT_CONFIG_DIR", str(tmp_path))
        (tmp_path / "p12_empty.yaml").write_text("")
        orig_instance = ConfigManager._instance
        ConfigManager._instance = None  # 重建单例以拾取 env 搜索路径
        try:
            cfg = get_config("p12_empty.yaml")
        finally:
            ConfigManager._instance = orig_instance
            clear_config_cache()
        assert cfg == {}
        events = pending_degradations()
        assert any(e["reason"].startswith("配置存在但解析为空") for e in events)

    def test_valid_config_no_audit(self, monkeypatch):
        """正常加载不记审计 (不产生噪音)"""
        from utils.config_manager import clear_config_cache

        clear_config_cache()
        get_config("portfolio")  # 真实存在的配置
        events = [
            e
            for e in pending_degradations()
            if e["key"] == "portfolio"
        ]
        assert events == []
        clear_config_cache()


class TestTradeExecutorRiskDegradation:
    """daily_trade_executor 风控配置降级闭环

    2026-09-02 巡检 P1-4: configs/trade_execution.yaml 已落盘, 缺失场景改为
    子进程内 patch get_config 模拟 (不再依赖"文件实际不存在"这一历史事实)。
    """

    # 子进程内屏蔽 trade_execution 配置的注入代码 (模拟文件缺失)
    _PATCH_CODE = (
        "import utils.config_manager as cm\n"
        "_orig = cm.get_config\n"
        "cm.get_config = (lambda name, default=None, strict=False:\n"
        "    {} if name == 'trade_execution'\n"
        "    else _orig(name, default, strict))\n"
    )

    def test_risk_degradation_recorded(self):
        """trade_execution 配置不可用时审计落盘 (子进程隔离 + patch 模拟缺失)"""
        import os
        import subprocess
        import sys

        env = {k: v for k, v in os.environ.items()
               if k not in ("QUANT_CONFIG_DIR", "QUANT_STRICT_CONFIG")}
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                self._PATCH_CODE
                + "import daily_trade_executor; "
                "from utils.degradation_audit import pending_degradations; "
                "import json,sys; "
                "json.dump(pending_degradations(), sys.stdout)",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",  # 显式解码: text=True 默认按本地 GBK, 子进程中文输出会崩 reader 线程
            errors="replace",
            env=env,
        )
        assert r.returncode == 0, r.stderr
        events = json.loads(r.stdout)
        assert any(
            e["scope"] == "daily_trade_executor"
            and "trade_execution.yaml" in e["key"]
            for e in events
        )

    def test_strict_config_env_hard_fails(self):
        """QUANT_STRICT_CONFIG=1 且配置不可用时 import 即硬失败 (patch 模拟缺失)"""
        import os
        import subprocess
        import sys

        env = {**os.environ, "QUANT_STRICT_CONFIG": "1"}
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                self._PATCH_CODE + "import daily_trade_executor",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",  # 显式解码: text=True 默认按本地 GBK, 子进程中文输出会崩 reader 线程
            errors="replace",
            env=env,
        )
        assert r.returncode != 0
        assert "QUANT_STRICT_CONFIG" in r.stderr

    def test_config_present_no_new_degradation(self):
        """P1-4 验收: configs/trade_execution.yaml 存在时 import 不新增降级事件

        pending_degradations() 返回全部历史落盘条目, 故用日志行数前后对比
        (import 前后 degradation_log.jsonl 行数不变 = 无新增)。
        """
        import os
        import subprocess
        import sys
        from pathlib import Path

        log_path = Path(__file__).resolve().parent.parent.parent / "reports" / "degradation_log.jsonl"

        def _count_lines() -> int:
            if not log_path.exists():
                return 0
            with open(log_path, encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())

        before = _count_lines()
        env = {k: v for k, v in os.environ.items()
               if k not in ("QUANT_CONFIG_DIR", "QUANT_STRICT_CONFIG")}
        r = subprocess.run(
            [sys.executable, "-c", "import daily_trade_executor"],
            capture_output=True,
            text=True,
            encoding="utf-8",  # 显式解码: text=True 默认按本地 GBK, 子进程中文输出会崩 reader 线程
            errors="replace",
            env=env,
        )
        assert r.returncode == 0, r.stderr
        after = _count_lines()
        assert after == before, (
            f"配置已落盘 (P1-4), import daily_trade_executor 不应新增降级事件: "
            f"日志行数 {before} -> {after}"
        )

    def test_defaults_still_applied(self):
        """降级时硬编码默认值兜底 (fail-safe 行为不变)"""
        import daily_trade_executor as dte

        assert dte.DAILY_AMOUNT_LIMIT == 200000
        assert dte.DAILY_LOSS_STOP_PCT == 0.03
        assert dte.PORTFOLIO_DRAWDOWN_STOP_PCT == 0.05


class TestStrayOutputDetection:
    """check_stray_output_dirs 项目外泄漏检测 (2026-09-01 泄漏事件防护)"""

    def _setup_stray(self, monkeypatch, tmp_path, recent: bool):
        """构造模拟目录结构: tmp_path 当项目根父目录"""
        import utils.degradation_audit as da_mod

        fake_root = tmp_path / "proj"
        fake_root.mkdir()
        stray = tmp_path / "reports"
        stray.mkdir()
        import time

        f = stray / "leaked.md"
        f.write_text("x")
        mtime = time.time() if recent else time.time() - 30 * 86400
        import os

        os.utime(f, (mtime, mtime))
        monkeypatch.setattr(da_mod, "_PROJECT_ROOT", fake_root)
        return str(stray)

    def test_recent_stray_dir_detected(self, monkeypatch, tmp_path):
        import utils.degradation_audit as da_mod

        stray = self._setup_stray(monkeypatch, tmp_path, recent=True)
        result = da_mod.check_stray_output_dirs()
        assert stray in result
        events = pending_degradations()
        assert any(e["scope"] == "degradation_audit" and e["key"] == stray for e in events)

    def test_old_stray_dir_not_reported(self, monkeypatch, tmp_path):
        """历史遗留且 30 天无写入 → 不刷屏"""
        import utils.degradation_audit as da_mod

        stray = self._setup_stray(monkeypatch, tmp_path, recent=False)
        assert stray not in da_mod.check_stray_output_dirs()
        assert pending_degradations() == []

    def test_no_stray_dirs(self, monkeypatch, tmp_path):
        import utils.degradation_audit as da_mod

        fake_root = tmp_path / "proj"
        fake_root.mkdir()
        monkeypatch.setattr(da_mod, "_PROJECT_ROOT", fake_root)
        assert da_mod.check_stray_output_dirs() == []

    def test_empty_stray_dir_not_reported(self, monkeypatch, tmp_path):
        import utils.degradation_audit as da_mod

        fake_root = tmp_path / "proj"
        fake_root.mkdir()
        (tmp_path / "reports").mkdir()  # 空目录
        monkeypatch.setattr(da_mod, "_PROJECT_ROOT", fake_root)
        assert da_mod.check_stray_output_dirs() == []


class TestStopLossRulesDegradation:
    """stop_loss_monitor 规则缺失闭环"""

    def test_missing_rules_file_records_audit(self, _isolated_audit_log):
        from stop_loss_monitor import StopLossMonitor

        monitor = StopLossMonitor.__new__(StopLossMonitor)
        rules = monitor._load_rules("Z:/no/such/file.yaml")
        assert rules == {}
        events = pending_degradations()
        assert any(
            e["scope"] == "stop_loss_monitor"
            and e["reason"] == "止损规则文件不存在"
            for e in events
        )

    def test_none_rules_file_records_audit(self, _isolated_audit_log):
        from stop_loss_monitor import StopLossMonitor

        monitor = StopLossMonitor.__new__(StopLossMonitor)
        rules = monitor._load_rules(None)
        assert rules == {}
        events = pending_degradations()
        assert any(e["scope"] == "stop_loss_monitor" for e in events)

    def test_valid_rules_no_audit(self, _isolated_audit_log):
        from stop_loss_monitor import StopLossMonitor

        rules_file = Path("config/stop_loss_rules_auto.yaml")
        if not rules_file.exists():  # 环境差异保护
            pytest.skip("config/stop_loss_rules_auto.yaml 不存在")
        monitor = StopLossMonitor.__new__(StopLossMonitor)
        rules = monitor._load_rules(str(rules_file))
        assert len(rules) > 0
        events = [
            e for e in pending_degradations() if e["scope"] == "stop_loss_monitor"
        ]
        assert events == []
