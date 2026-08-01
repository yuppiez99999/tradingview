"""AnySearch 实时搜索数据连接器 (v7.5 适配版)

集成 AnySearch 搜索引擎，提供：
- 金融新闻搜索
- 个股公告查询
- 宏观经济数据搜索
- 实时舆情监控

作为 iFinD 的补充数据源，在 iFinD 不可用时自动 fallback
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

logger = logging.getLogger("v75.anysearch")

ANYSEARCH_SKILL_DIR = r"e:\各种PY程序\.agents\skills\anysearch"


class AnySearchConnector:
    """AnySearch 实时搜索连接器 (v7.5 独立版)"""

    name = "anysearch"
    priority = 6
    available = False
    _connected = False

    def __init__(self):
        self._check_available()

    def _check_available(self):
        cli_path = os.path.join(ANYSEARCH_SKILL_DIR, "scripts", "anysearch_cli.py")
        self.available = os.path.exists(cli_path)
        if not self.available:
            logger.warning(f"AnySearch CLI 未找到: {cli_path}")

    def connect(self) -> bool:
        if self.available:
            try:
                result = subprocess.run(
                    [sys.executable, os.path.join(ANYSEARCH_SKILL_DIR, "scripts", "anysearch_cli.py"), "doc"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self._connected = result.returncode == 0
                if self._connected:
                    logger.info("AnySearch 连接器已连接")
            except Exception as e:
                logger.warning(f"AnySearch 连接失败: {e}")
                self._connected = False
        return self._connected

    def search(
        self,
        query: str,
        domain: str | None = None,
        sub_domain: str | None = None,
        sub_domain_params: str | None = None,
        max_results: int = 10,
    ) -> list[dict] | None:
        return self._search_impl(query, domain, sub_domain, sub_domain_params, max_results)

    def get_finance_news(
        self, cn_code: str | None = None, period: str = "1d", max_results: int = 5
    ) -> list[dict] | None:
        if cn_code:
            params = f"type=announcement,cn_code={cn_code},period={period}"
            return self.search(f"{cn_code} 公告", "finance", "finance.news", params, max_results)
        else:
            params = f"type=flash,period={period},news_src=sina"
            return self.search("今日财经头条", "finance", "finance.news", params, max_results)

    def get_macro_data(self, indicator_type: str, period: str = "1y") -> list[dict] | None:
        params = f"type={indicator_type},period={period}"
        return self.search(f"{indicator_type}", "finance", "finance.macro", params, 5)

    def get_stock_quote(self, cn_code: str) -> dict[str, float] | None:
        symbol = cn_code.replace(".SZ", "").replace(".SH", "")
        params = f"type=stock,symbol=,cn_code={cn_code},period=7d"
        results = self.search(symbol, "finance", "finance.quote", params, 1)
        if results and len(results) > 0:
            data = results[0]
            if "close" in data or "open" in data:
                numeric_fields = [
                    "amount",
                    "change",
                    "circ_mv",
                    "close",
                    "high",
                    "low",
                    "open",
                    "pb",
                    "pct_chg",
                    "pe",
                    "pe_ttm",
                    "pre_close",
                    "ps",
                    "ps_ttm",
                    "total_mv",
                    "turnover_rate",
                    "vol",
                ]
                result = {}
                for k, v in data.items():
                    if k in numeric_fields:
                        try:
                            result[k] = float(v)
                        except (ValueError, TypeError):
                            result[k] = v
                    else:
                        result[k] = v
                return result
        return None

    def _search_impl(
        self,
        query: str,
        domain: str | None = None,
        sub_domain: str | None = None,
        sub_domain_params: str | None = None,
        max_results: int = 10,
    ) -> list[dict] | None:
        cli_path = os.path.join(ANYSEARCH_SKILL_DIR, "scripts", "anysearch_cli.py")

        cmd = [sys.executable, cli_path, "search", query, "--max_results", str(max_results)]

        if domain:
            cmd.extend(["--domain", domain])
        if sub_domain:
            cmd.extend(["--sub_domain", sub_domain])
        if sub_domain_params:
            cmd.extend(["--sdp", sub_domain_params])

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)

        if result.returncode != 0:
            logger.warning(f"AnySearch 搜索失败: {result.stderr}")
            return None

        parsed = self._parse_search_result(result.stdout)
        if parsed and len(parsed) > 0 and "title" not in parsed[0]:
            for i, item in enumerate(parsed):
                if "url" in item:
                    parsed[i]["title"] = (
                        item["url"].split("/")[-1].replace(".html", "").replace(".htm", "").replace("-", " ").title()
                    )
        return parsed

    def _parse_search_result(self, output: str) -> list[dict]:
        results = []
        lines = output.strip().split("\n")
        current_result = {}

        for line in lines:
            line = line.strip()
            if line.startswith("##"):
                if current_result:
                    results.append(current_result)
                    current_result = {}
            elif line.startswith("###"):
                if current_result:
                    results.append(current_result)
                title = line[4:].strip()
                current_result = {"title": title}
            elif line.startswith("- **URL**:"):
                current_result["url"] = line.replace("- **URL**:", "").strip()
            elif line.startswith("- **") and ":" in line:
                key_end = line.find("**:")
                if key_end > 0:
                    key = line[3:key_end].strip().lower()
                    value = line[key_end + 3 :].strip()
                    current_result[key] = value
            elif line.startswith("- ") and len(line) > 2:
                content = line[2:].strip()
                if content.startswith("{"):
                    try:
                        data = json.loads(content)
                        if isinstance(data, dict):
                            current_result.update(data)
                        else:
                            current_result["content"] = content
                    except (json.JSONDecodeError, ValueError):
                        current_result["content"] = content
                else:
                    existing = current_result.get("content", "")
                    current_result["content"] = (existing + "\n" + content).strip()
            elif line and current_result and "title" in current_result:
                content = current_result.get("content", "")
                current_result["content"] = (content + "\n" + line).strip()

        if current_result:
            results.append(current_result)

        return results
