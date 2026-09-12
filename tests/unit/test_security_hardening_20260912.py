"""安全加固回归 — 2026-09-12 (bandit MEDIUM+ 首方代码清零)

覆盖本次修复的 11 项 bandit 发现对应的实质防护行为:
- B301 反序列化: ms_strategy load_model_safe SHA256 校验 / research 缓存路径越界拒绝
- B608 SQL: checkpointer 显式字面量 SQL + 参数绑定 (无表名拼接)
- B314 XML: safe_xml 拒绝 DTD (实体扩展), defusedxml 优先
- B310 URL: safe_url 拒绝 file:// 等非 HTTP(S) scheme
- B615 HF 下载: FinetuneConfig.base_model_revision 透传 revision
"""

from __future__ import annotations

import sys
from pathlib import Path

import importlib.util

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.safe_url import validate_url
from utils.safe_xml import (
    ParseError,
    is_defusedxml_available,
    safe_xml_fromstring,
    safe_xml_parse,
)


def _is_rejection(exc: BaseException) -> bool:
    """判定"解析器拒绝"语义 —— 归一化两种后端.

    defusedxml 抛 `EntitiesForbidden` / `DTDForbidden` (继承 DefusedXmlException,
    与 ParseError 无继承关系); stdlib 兜底抛 `ParseError`。调用方关心的是
    "被拒绝" 而非具体异常类, 故此处统一判定, 避免测试把后端选择当契约。
    """
    return "Forbidden" in type(exc).__name__ or isinstance(exc, ParseError)


class TestSafeXml:
    """B314 — XML 外部实体/DTD 防护。"""

    def test_normal_xml_parses(self):
        root = safe_xml_fromstring("<a><b>1</b></a>")
        assert root.tag == "a"
        assert root.find("b").text == "1"

    def test_bytes_input(self):
        assert safe_xml_fromstring(b"<r/>").tag == "r"

    def test_dtd_bomb_rejected(self):
        """billion-laughs 载荷必须被拒绝 (禁止 DTD), 而非爆内存。"""
        payload = (
            '<?xml version="1.0"?>'
            "<!DOCTYPE lolz [<!ENTITY lol \"lol\">"
            '<!ENTITY lol2 "&lol;&lol;&lol;&lol;">'
            '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;">]>'
            "<lolz>&lol3;</lolz>"
        )
        with pytest.raises(Exception) as ei:
            safe_xml_fromstring(payload)
        # defusedxml 抛 EntitiesForbidden / stdlib 抛 ParseError — 两者都属"拒绝"
        assert _is_rejection(ei.value)

    def test_malformed_raises_parse_error(self):
        with pytest.raises(ParseError):
            safe_xml_fromstring("<a><b></a>")

    def test_xxe_external_entity_rejected(self):
        """外部实体 (XXE, SYSTEM "file://...") 必须在实体声明阶段即被拒绝。

        这是 B314 的实质攻击面: 若兜底解析器未真正禁用实体, 本地文件内容会被
        回填进文档树。
        """
        payload = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            "<r>&xxe;</r>"
        )
        with pytest.raises(Exception) as ei:
            safe_xml_fromstring(payload)
        assert _is_rejection(ei.value)

    def test_parse_file_hardened(self, tmp_path):
        """文件路径入口同样走加固解析器 (safe_xml_parse)。"""
        good = tmp_path / "good.xml"
        good.write_text("<ts><t/></ts>", encoding="utf-8")
        assert safe_xml_parse(good).getroot().tag == "ts"

        bomb = tmp_path / "bomb.xml"
        bomb.write_text(
            '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "a">]><l>&a;</l>',
            encoding="utf-8",
        )
        with pytest.raises(Exception) as ei:
            safe_xml_parse(bomb)
        assert _is_rejection(ei.value)

    def test_fallback_is_not_plain_stlib_parser(self):
        """regression: 兜底解析器不得退化为未加固的 stdlib 默认解析器。

        历史缺陷 (2026-09-12, 本 PR 自检发现): 初版据「CPython 3.8+ 支持
        XMLParser(forbid_dtd=True)」实现, 但该参数**不存在**, TypeError 被吞掉后
        静默回退到默认 XMLParser, billion-laughs 载荷可展开。此处对 **stdlib
        兜底路径本身**做断言 (不依赖环境是否装了 defusedxml), 防止再次静默退化。
        """
        from utils import safe_xml as sx

        parser = sx._HardenedXMLParser()
        # 必须暴露 pyexpat 底层解析器 (拿不到 = 无法在其上装 DTD/实体拒绝 handler)
        assert hasattr(parser, "_parser")
        # DTD/实体拒绝 handler 必须已挂载 —— 未挂载时 expat 默认返回 None
        assert parser._parser.StartDoctypeDeclHandler is not None
        assert parser._parser.EntityDeclHandler is not None
        # 直接驱动兜底解析器 (绕过 defusedxml 分支), 验证实质拒绝
        with pytest.raises(ParseError):
            parser.feed('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY x "x">]><l>&x;</l>')

    def test_backend_reported(self):
        assert isinstance(is_defusedxml_available(), bool)


class TestSafeUrl:
    """B310 — urlopen scheme 白名单。"""

    @pytest.mark.parametrize("url", ["http://localhost:11434/x", "https://example.com/y"])
    def test_http_schemes_allowed(self, url):
        assert validate_url(url) == url

    @pytest.mark.parametrize(
        "url",
        ["file:///etc/passwd", "ftp://host/x", "data:text/plain,hi", "gopher://h/1"],
    )
    def test_non_http_schemes_rejected(self, url):
        with pytest.raises(ValueError, match="非白名单协议"):
            validate_url(url)

    def test_empty_url_rejected(self):
        with pytest.raises(ValueError):
            validate_url("")

    def test_custom_whitelist(self):
        assert validate_url("ftp://h/x", allowed_schemes=frozenset({"ftp"})) == "ftp://h/x"


class TestCheckpointerSql:
    """B608 — SQL 语句不得含字符串拼接的表名。"""

    def test_no_fstring_sql_in_source(self):
        src = (
            _PROJECT_ROOT / "quant_modules" / "ai_hedge_fund" / "graph" / "checkpointer.py"
        ).read_text(encoding="utf-8")
        assert 'f"DELETE FROM {table}' not in src
        assert "DELETE FROM writes WHERE thread_id = ?" in src
        assert "DELETE FROM checkpoints WHERE thread_id = ?" in src

    @pytest.mark.skipif(
        importlib.util.find_spec("langgraph") is None,
        reason="langgraph 未安装 (沙箱无该栈), 仅源码级断言生效",
    )
    def test_clear_checkpoint_deletes_only_target_thread(self, tmp_path):
        import sqlite3

        from quant_modules.ai_hedge_fund.graph import checkpointer as cp

        data_dir = tmp_path
        cp._db_path(data_dir, "AAPL")  # 触发目录创建
        db = cp._db_path(data_dir, "AAPL")
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE IF NOT EXISTS writes (thread_id TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS checkpoints (thread_id TEXT)")
        conn.execute("INSERT INTO writes VALUES ('keep')")
        conn.execute("INSERT INTO checkpoints VALUES ('keep')")
        conn.commit()
        conn.close()

        cp.clear_checkpoint(data_dir, "AAPL", "2026-09-12")

        conn = sqlite3.connect(str(db))
        rows = conn.execute("SELECT thread_id FROM writes").fetchall()
        conn.close()
        assert rows == [("keep",)]


class TestResearchCacheGuard:
    """B301 — pickle 缓存加载前置校验 (路径越界 / 大小异常拒绝)。"""

    def _make(self):
        import importlib.util

        path = _PROJECT_ROOT / "research" / "backtest_current_portfolio.py"
        spec = importlib.util.spec_from_file_location("_rcp_security_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_path_outside_cache_rejected(self, tmp_path, caplog):
        mod = self._make()
        obj = object.__new__(mod.PortfolioBacktester)
        outside = tmp_path / "evil.pkl"
        outside.write_bytes(b"x")
        assert obj._try_load_cache(outside, 3) is None

    def test_empty_cache_rejected(self, tmp_path):
        mod = self._make()
        obj = object.__new__(mod.PortfolioBacktester)
        empty = mod.CACHE_DIR / "_security_test_empty.pkl"
        try:
            mod.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            empty.write_bytes(b"")
            assert obj._try_load_cache(empty, 3) is None
        finally:
            empty.unlink(missing_ok=True)


@pytest.mark.skipif(
    importlib.util.find_spec("torch") is None,
    reason="torch 未安装 (沙箱无 ML 栈), 仅源码级断言生效",
)
class TestHfRevisionPinning:
    """B615 — HF 下载必须可固定 revision。"""

    def test_config_has_revision_field(self):
        from lgb_trainer.llm_finetune.finetune_sentiment_model import FinetuneConfig

        cfg = FinetuneConfig()
        assert hasattr(cfg, "base_model_revision")
        assert cfg.base_model_revision is None  # 默认不固定, 生产应显式指定

    def test_config_accepts_revision(self):
        from lgb_trainer.llm_finetune.finetune_sentiment_model import FinetuneConfig

        cfg = FinetuneConfig(base_model_revision="v1.0.0")
        assert cfg.base_model_revision == "v1.0.0"

    def test_revision_passed_to_from_pretrained(self):
        src = (
            _PROJECT_ROOT
            / "lgb_trainer"
            / "llm_finetune"
            / "finetune_sentiment_model.py"
        ).read_text(encoding="utf-8")
        assert src.count("revision=config.base_model_revision") == 2
        assert src.count("revision=self.config.base_model_revision") == 2


class TestPickleSafeLoaderNosec:
    """B301 — 云训练模型加载入口保留 SHA256 校验语义。"""

    @pytest.mark.parametrize(
        "rel",
        ["ms_strategy/cloud_train/backtest.py", "ms_strategy/cloud_train/simple_backtest.py"],
    )
    def test_load_model_safe_verifies_sha256(self, rel, tmp_path):
        import hashlib
        import importlib.util
        import pickle

        path = _PROJECT_ROOT / rel
        spec = importlib.util.spec_from_file_location(f"_bt_{path.stem}_{path.parent.name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        model_file = tmp_path / "m.pkl"
        model_file.write_bytes(pickle.dumps({"w": 1}))
        good = hashlib.sha256(model_file.read_bytes()).hexdigest()

        assert mod.load_model_safe(str(model_file), expected_sha256=good) == {"w": 1}

        with pytest.raises(RuntimeError, match="完整性校验失败"):
            mod.load_model_safe(str(model_file), expected_sha256="0" * 64)
