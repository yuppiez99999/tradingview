"""GNN 供应链因子 — 图数据源（边数据采集）

为 `supply_chain_graph.py` 提供真实、可用的图谱边数据来源，作为 W5.1 GNN 因子前置组件。

数据源（2026-08-03 对实际持仓实测）:
- 东财 push2 `stock/get` (f127 行业 / f130 概念) — 行业边 + 概念边 核心来源
- 东财 push2 `slist/get` (个股所属板块) — 板块归属反查
- 同花顺 `ths_hot_reason` — 当日强势股题材归因（产业链题材边）

不可用数据源（已实测，勿用）:
- `baidu_concept_blocks` — 被风控 (ResultCode=10003)，header 无关
- 东财 F10 `CompanySurvey`/`BusinessAnalysis` — 返回空结构（接口已变更）

设计原则:
1. 遵循系统数据源规范 — 健康监控 / TTL 缓存 / 单例 / fail-safe 降级
2. 输出与 `SupplyChainEdge` 数据结构对齐（relation_type=COMPETITOR 同行业 / PARTNER 同概念）
3. 限速 + 重试（实测东财 push2 偶发空响应需重试）
"""

from __future__ import annotations

import io
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# Windows 控制台 UTF-8 输出 (幂等 — 已包装则不重复, 避免多模块 import 冲突)
def _ensure_utf8_stream() -> None:
    """将 stdout/stderr 包装为 UTF-8; 已包装则跳过 (幂等)."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        # 已由本模块/兄弟模块包装 (UTF-8 编码) 则跳过
        if getattr(stream, "encoding", "").lower() == "utf-8":
            continue
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            continue
        try:
            setattr(sys, name, io.TextIOWrapper(buffer, encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            pass


_ensure_utf8_stream()

# 国内金融 API 不走代理（系统代理 127.0.0.1:7897 会拒绝转发）
_NO_PROXY_DOMAINS = ("eastmoney.com", "10jqka.com.cn", "baidu.com")
if os.environ.get("NO_PROXY"):
    os.environ["NO_PROXY"] = os.environ["NO_PROXY"] + "," + ",".join(_NO_PROXY_DOMAINS)
else:
    os.environ["NO_PROXY"] = ",".join(_NO_PROXY_DOMAINS)

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
)

_EM_HEADERS = {
    "User-Agent": _UA,
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}

_THS_HEADERS = {"User-Agent": _UA}

# 每只股票请求间隔（实测东财 push2 偶发空响应/连接重置，需限速）
_EM_INTERVAL = 0.6
# 东财 push2 实测间歇性 RemoteDisconnected，需较多重试 + 指数退避
_EM_RETRIES = 4

# 板块成分股本地缓存文件（减少东财请求，避免被限流）
_BOARD_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "cache", "graph_board_stocks.json")


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        v = float(val or 0)
        return v if v == v else default
    except (ValueError, TypeError):
        return default


def _market_of(code: str) -> str:
    """东财 secid 市场号: 1=沪 0=深/北."""
    s = str(code).strip()
    if s.startswith(("6", "5", "9")):
        return "1"
    if s.startswith(("4", "8")):
        return "0"  # 北交所用 0 也能返回部分，实际用 0
    return "0"


class GraphDataSource:
    """图数据源 — 为供应链图谱提供行业/概念/题材边数据.

    用法:
        ds = get_graph_data_source()
        info = ds.get_industry_relationship("688041")   # 行业归属
        concepts = ds.get_concept_blocks("688041")       # 概念板块
        boards = ds.get_stock_boards("688041")           # 所属板块(含代码)
        themes = ds.get_themes()                          # 当日题材归因
        edges = ds.build_concept_edges(["688041","300308"])  # 概念共享边
    """

    def __init__(self, cache_ttl: int = 3600):
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, tuple[float, Any]] = {}
        self.source_health: Dict[str, Dict[str, Any]] = {
            "eastmoney_push2": {"ok": False, "last_error": None, "last_success": None},
            "ths_hot_reason": {"ok": False, "last_error": None, "last_success": None},
        }
        self._session = requests.Session()
        self._session.headers.update(_EM_HEADERS)

    # ----------------------------------------------------------
    # 内部工具
    # ----------------------------------------------------------
    def _get(self, url: str, params: Dict[str, Any], headers: Optional[Dict[str, Any]] = None,
             source: str = "eastmoney_push2", timeout: int = 10) -> Any:
        """GET 请求带限速 + 重试 + 健康记录.

        实测东财 push2 `stock/get` 偶发 `RemoteDisconnected`（TCP 连接被重置），
        通过增加重试次数 + 更长退避间隔缓解。`slist/get` 与 `ths_hot_reason` 稳定。
        """
        sess_headers = headers or _EM_HEADERS
        last_exc = None
        max_attempts = _EM_RETRIES + 1
        for attempt in range(max_attempts):
            if attempt > 0:
                time.sleep(_EM_INTERVAL * (attempt + 1))  # 递增退避
            try:
                r = self._session.get(url, params=params, headers=sess_headers, timeout=timeout)
                r.raise_for_status()
                data = r.json()
                # 偶发空响应 → 重试
                if not data:
                    last_exc = ValueError("空响应")
                    continue
                self.source_health[source]["ok"] = True
                self.source_health[source]["last_success"] = datetime.now().isoformat()
                self.source_health[source]["last_error"] = None
                return data
            except requests.exceptions.ConnectionError as exc:
                # 连接重置: 关闭失效 Session, 下次用全新连接 (实测东财会拒绝复用被重置的连接)
                last_exc = exc
                logger.debug(f"{source} 连接重置(第{attempt + 1}次): {exc}, 重建Session")
                try:
                    self._session.close()
                except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                    pass
                self._session = requests.Session()
                self._session.headers.update(_EM_HEADERS)
                time.sleep(_EM_INTERVAL * (attempt + 2))
            except Exception as exc:  # 其他网络/解析异常, fail-safe  # noqa: BLE001
                last_exc = exc
                logger.debug(f"{source} 请求失败(第{attempt + 1}次): {exc}")
                time.sleep(_EM_INTERVAL * (attempt + 1))
        self.source_health[source]["ok"] = False
        self.source_health[source]["last_error"] = str(last_exc)
        logger.warning(f"{source} 请求最终失败: {last_exc}")
        return None

    def _cached(self, key: str, fetcher) -> Any:
        """TTL 缓存."""
        now = time.time()
        hit = self._cache.get(key)
        if hit and (now - hit[0]) < self.cache_ttl:
            return hit[1]
        val = fetcher()
        if val is not None:
            self._cache[key] = (now, val)
        return val

    # ----------------------------------------------------------
    # 东财 push2 — 行业 / 概念 / 板块
    # ----------------------------------------------------------
    def get_industry_relationship(self, code: str) -> Optional[Dict[str, Any]]:
        """获取个股行业归属与概念板块.

        数据源: 东财 push2 slist/get spt=3 (实测稳定, 返回所属行业+概念+地域+指数全板块).
        stock/get f127/f129 实测偶发 RemoteDisconnected 不稳定, 已弃用.

        Returns:
            {name, code, industry, industry_code, region, concepts(str)} 或 None
        """
        def _fetch() -> Optional[Dict[str, Any]]:
            boards = self.get_stock_boards(code)
            if not boards:
                return None
            industry = ""
            industry_code = ""
            concepts = []
            region = ""
            for b in boards:
                bname = b["name"]
                btype = b["category"]
                if btype == "industry" and not industry:
                    industry = bname
                    industry_code = b["code"]
                elif btype == "concept":
                    concepts.append(bname)
                elif btype == "region" and not region:
                    region = bname
            if not industry:
                return None
            return {
                "name": "",  # 个股名称 slist/get 不返回, 用板块反查时通过 f14 首元素近似; 留空不影响边构建
                "code": str(code),
                "industry": industry,
                "industry_code": industry_code,
                "region": region,
                "concepts": ",".join(concepts),
            }

        return self._cached(f"industry:{code}", _fetch)

    def get_concept_blocks(self, code: str) -> List[str]:
        """获取个股概念板块标签列表（来自 slist/get spt=3 概念板块）."""
        info = self.get_industry_relationship(code)
        if not info:
            return []
        concepts_str = info.get("concepts") or ""
        if not concepts_str:
            return []
        return [c.strip() for c in concepts_str.split(",") if c.strip()]

    def get_stock_boards(self, code: str) -> List[Dict[str, str]]:
        """获取个股所属全部板块（spt=3: 行业+概念+地域+指数，实测稳定）.

        Returns:
            [{code, name, category}] 其中 category ∈ {industry, concept, region, index, stock}
        """
        market = _market_of(code)
        url = "https://push2.eastmoney.com/api/qt/slist/get"
        params = {
            "secid": f"{market}.{code}",
            "spt": 3,
            "invt": 2,
            "fltt": 2,
            "fields": "f12,f13,f14,f102",
            "pi": 0,
            "po": 1,
            "pz": 50,
            "np": 1,
        }

        def _classify(name: str, code: str) -> str:
            """按命名规则分类板块类型（实测 spt=3 板块命名特征）."""
            code = str(code)
            name = str(name)
            # 个股 (代码为6位数字且非BK开头)
            if code.isdigit() and len(code) == 6:
                return "stock"
            if "概念" in name:
                return "concept"
            if "板块" in name:
                return "region"
            if code.startswith("BK"):
                return "industry"
            return "index"

        def _fetch() -> List[Dict[str, str]]:
            d = self._get(url, params)
            if not d:
                return []
            data = d.get("data") or {}
            diff = data.get("diff") or []
            blocks = []
            for item in diff or []:
                code = str(item.get("f12", ""))
                name = str(item.get("f14", ""))
                if not name or name == "None":
                    continue
                blocks.append({
                    "code": code,
                    "name": name,
                    "category": _classify(name, code),
                })
            return blocks

        return self._cached(f"boards:{code}", _fetch)

    # ----------------------------------------------------------
    # 同花顺 — 题材归因
    # ----------------------------------------------------------
    def get_themes(self, date: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取指定日期同花顺强势股题材归因.

        Returns:
            [{name, code, reason(题材), ...}] 或 []
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        url = (
            f"http://zx.10jqka.com.cn/event/api/getharden/"
            f"date/{date}/orderby/date/orderway/desc/charset/GBK/"
        )
        params = {}

        def _fetch() -> List[Dict[str, Any]]:
            d = self._get(url, params, headers=_THS_HEADERS, source="ths_hot_reason")
            if not d:
                return []
            if d.get("errocode", 0) != 0:
                logger.warning(f"同花顺热点错误: {d.get('errormsg','')}")
                return []
            return d.get("data") or []

        return self._cached(f"themes:{date}", _fetch)

    # ----------------------------------------------------------
    # 板块成分股 (扩大 universe 用)
    # ----------------------------------------------------------
    def fetch_board_stocks(self, board_code: str, limit: int = 100,
                           cache_ttl: int = 86400) -> List[Dict[str, str]]:
        """东财拉取板块成分股 (BK 板块), 带行业标签, 本地文件缓存.

        东财 push2 实测间歇性 RemoteDisconnected, 故用本地缓存减少请求,
        缓存 24h 内优先读取, 避免每次验证都触发东财 (降低被限流概率 + 加速).

        Args:
            board_code: 板块代码, 如 BK1036 (半导体)
            limit: 最多返回数量
            cache_ttl: 本地缓存有效期 (秒), 默认 24h

        Returns:
            [{code, name, industry}] 或 []
        """
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1",
            "pz": str(limit),
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": f"b:{board_code}",
            "fields": "f2,f3,f12,f14,f100",
        }
        cache_key = f"board:{board_code}:{limit}"

        # 1) 优先读本地文件缓存
        file_rows = self._load_board_cache(cache_key, cache_ttl)
        if file_rows:
            return file_rows

        # 2) 内存缓存
        def _fetch() -> List[Dict[str, str]]:
            d = self._get(url, params, source="eastmoney_push2")
            if not d:
                return []
            items = (d.get("data") or {}).get("diff") or []
            rows = []
            for it in items or []:
                code = str(it.get("f12", ""))
                name = str(it.get("f14", ""))
                if code:
                    rows.append({
                        "code": code,
                        "name": name,
                        "industry": str(it.get("f100", "") or ""),
                    })
            if rows:
                self._save_board_cache(cache_key, rows)  # 回写本地
            return rows

        return self._cached(f"board_stocks:{cache_key}", _fetch)

    def _load_board_cache(self, key: str, ttl: int) -> List[Dict[str, str]]:
        """读本地板块成分股缓存 (JSON)."""
        try:
            path = Path(_BOARD_CACHE_FILE)
            if not path.exists():
                return []
            if time.time() - path.stat().st_mtime > ttl:
                return []
            import json as _json
            data = _json.loads(path.read_text(encoding="utf-8"))
            rows = data.get(key, [])
            return rows if isinstance(rows, list) else []
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            return []

    def _save_board_cache(self, key: str, rows: List[Dict[str, str]]) -> None:
        """写本地板块成分股缓存 (JSON)."""
        try:
            import json as _json
            path = Path(_BOARD_CACHE_FILE)
            data = {}
            if path.exists():
                try:
                    data = _json.loads(path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                    data = {}
            data[key] = rows
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.debug(f"板块缓存写入失败: {exc}")

    # ----------------------------------------------------------
    # 东财 F10 主营构成 (供应商-客户边增强: 分产品/分行业标签)
    # ----------------------------------------------------------
    def fetch_main_business(self, code: str) -> Optional[Dict[str, Any]]:
        """东财 F10 主营构成 (zygcfx), 提取分产品/分行业标签.

        免费数据源无法直接获取「前五大客户/供应商名单」, 但主营构成
        (ITEM_NAME + MBI_RATIO 营收占比) 可提供更精细的产业链关联:
        - MAINOP_TYPE=1: 分产品构成 (如 "电子工艺装备")
        - MAINOP_TYPE=2: 分行业构成 (更精细的行业归属)

        Returns:
            {code, products: [产品名], industries: [行业名], review: 经营评述} 或 None
        """
        # 东财 secid: 沪 1.xxxxxx / 深 0.xxxxxx
        market = _market_of(code)
        secid = f"{market}.{code}"
        # 转东财 F10 代码 (SZ/SH 前缀)
        f10_code = f"SZ{code}" if market == "0" else f"SH{code}"
        url = (
            f"https://emweb.securities.eastmoney.com/PC_HSF10/"
            f"BusinessAnalysis/PageAjax?code={f10_code}"
        )
        params = {}

        def _fetch() -> Optional[Dict[str, Any]]:
            d = self._get(url, params, source="eastmoney_push2", timeout=15)
            if not d:
                return None
            # zygcfx 含多期 + 分产品/分行业, 需去重保留主要构成 (MBI_RATIO 排序)
            products = []
            industries = []
            product_sets: Dict[str, float] = {}
            industry_sets: Dict[str, float] = {}
            for block in d.get("zygcfx") or []:
                mtype = str(block.get("MAINOP_TYPE", ""))
                item = str(block.get("ITEM_NAME", "") or "")
                ratio = _safe_float(block.get("MBI_RATIO"))
                if not item or "其他" in item:
                    continue
                if mtype == "1":
                    product_sets[item] = max(product_sets.get(item, 0), ratio)
                elif mtype == "2":
                    industry_sets[item] = max(industry_sets.get(item, 0), ratio)
            # 按营收占比降序取主要构成 (最多 6 个)
            products = [k for k, _ in sorted(product_sets.items(), key=lambda kv: kv[1], reverse=True)][:6]
            industries = [k for k, _ in sorted(industry_sets.items(), key=lambda kv: kv[1], reverse=True)][:6]
            review = ""
            for j in d.get("jyps") or []:
                rev = j.get("BUSINESS_REVIEW", "")
                if rev:
                    review = rev
                    break
            if not products and not industries:
                return None
            return {
                "code": str(code),
                "products": products,
                "industries": industries,
                "review": review,
            }

        cache_key = f"mb:{code}"
        # 1) 优先读本地文件缓存 (避免 250 只批量请求东财 F10 被限流)
        #    主营构成缓存值是 dict (非 list), 直接用通用 JSON 读取
        file_rows = self._load_json_cache_value(cache_key, 86400)
        if file_rows:
            return file_rows

        # 2) 内存缓存 + 回写本地
        def _cached_fetch():
            result = _fetch()
            if result:
                self._save_board_cache(cache_key, result)
            return result

        return self._cached(f"main_business:{code}", _cached_fetch)

    def _load_json_cache_value(self, key: str, ttl: int) -> Any:
        """通用读本地 JSON 缓存值 (list 或 dict 均可).

        _load_board_cache 仅接受 list (板块成分股); 主营构成缓存为 dict,
        需独立读取避免类型不匹配导致缓存永久失效.
        """
        try:
            path = Path(_BOARD_CACHE_FILE)
            if not path.exists():
                return None
            if time.time() - path.stat().st_mtime > ttl:
                return None
            import json as _json
            data = _json.loads(path.read_text(encoding="utf-8"))
            return data.get(key)
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            return None

    # ----------------------------------------------------------
    # GNN 边构建 — 与 SupplyChainEdge 对齐
    # ----------------------------------------------------------
    def build_concept_edges(self, symbols: List[str], min_concept_share: int = 1) -> List[Dict[str, Any]]:
        """基于概念板块共享构建 PARTNER 边（同概念两两连边）.

        Returns:
            [{source, target, relation_type, strength, source_info}]
            与 SupplyChainEdge(source/target/relation_type/strength/source_info) 对齐.
        """
        symbol_concepts: Dict[str, set[str]] = {}
        for code in symbols:
            tags = self.get_concept_blocks(code)
            if tags:
                symbol_concepts[code] = set(tags)

        edges = []
        codes = list(symbol_concepts.keys())
        for i in range(len(codes)):
            for j in range(i + 1, len(codes)):
                a, b = codes[i], codes[j]
                shared = symbol_concepts[a] & symbol_concepts[b]
                if len(shared) >= min_concept_share:
                    strength = min(1.0, len(shared) / 5.0)  # 共享概念越多强度越高
                    edges.append({
                        "source": a,
                        "target": b,
                        "relation_type": "PARTNER",
                        "strength": round(strength, 3),
                        "source_info": "concept_shared:" + ",".join(sorted(shared)[:3]),
                    })
        return edges

    def build_industry_edges(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """基于同行业构建 COMPETITOR 边（同行业两两连边）.

        Returns:
            [{source, target, relation_type, strength, source_info}] 与 SupplyChainEdge 对齐.
        """
        symbol_industry: Dict[str, str] = {}
        for code in symbols:
            info = self.get_industry_relationship(code)
            if info and info.get("industry"):
                symbol_industry[code] = info["industry"]

        edges = []
        codes = list(symbol_industry.keys())
        for i in range(len(codes)):
            for j in range(i + 1, len(codes)):
                a, b = codes[i], codes[j]
                if symbol_industry[a] == symbol_industry[b]:
                    edges.append({
                        "source": a,
                        "target": b,
                        "relation_type": "COMPETITOR",
                        "strength": 0.7,
                        "source_info": f"industry_shared:{symbol_industry[a]}",
                    })
        return edges

    def build_thematic_edges(self, date: Optional[str] = None, min_shared: int = 1) -> List[Dict[str, Any]]:
        """基于当日题材共享构建 PARTNER 边（同题材两两连边）.

        Returns:
            [{source, target, relation_type, strength, source_info}]
        """
        themes = self.get_themes(date)
        theme_stocks: Dict[str, list[str]] = {}
        for row in themes:
            reason = str(row.get("reason") or "").replace(" ", "+")
            code = str(row.get("code") or "")
            if reason and code:
                for tag in reason.split("+"):
                    if tag:
                        theme_stocks.setdefault(tag, []).append(code)

        edges = []
        seen = set()
        for tag, stocks in theme_stocks.items():
            if len(stocks) < 2:
                continue
            for i in range(len(stocks)):
                for j in range(i + 1, len(stocks)):
                    pair = tuple(sorted((stocks[i], stocks[j])))
                    if pair in seen:
                        continue
                    seen.add(pair)
                    edges.append({
                        "source": pair[0],
                        "target": pair[1],
                        "relation_type": "PARTNER",
                        "strength": 0.5,
                        "source_info": f"theme:{tag}",
                    })
        return edges

    def build_main_business_edges(self, symbols: List[str], min_shared: int = 1) -> List[Dict[str, Any]]:
        """基于主营构成 (东财 F10) 构建 PARTNER 边 — 供应商-客户边的最佳免费近似.

        免费数据源无法获取真实「前五大客户/供应商名单」, 但主营构成 (分产品/分行业
        标签) 是产业链环节的直接定位: 主营产品/行业重叠的股票处于同一产业链环节,
        存在上下游供需关系, 建立强 PARTNER 边.

        Returns:
            [{source, target, relation_type, strength, source_info}]
        """
        symbol_tags: Dict[str, Dict[str, str]] = {}  # code -> {tag: type(product/industry)}
        for code in symbols:
            mb = self.fetch_main_business(code)
            if not mb:
                continue
            tags = {}
            for p in mb.get("products", []):
                tags[p] = "product"
            for i in mb.get("industries", []):
                tags[i] = "industry"
            if tags:
                symbol_tags[code] = tags

        edges = []
        seen = set()
        codes = list(symbol_tags.keys())
        for i in range(len(codes)):
            for j in range(i + 1, len(codes)):
                a, b = codes[i], codes[j]
                tags_a = symbol_tags[a]
                tags_b = symbol_tags[b]
                shared = set(tags_a.keys()) & set(tags_b.keys())
                if len(shared) < min_shared:
                    continue
                pair = tuple(sorted((a, b)))
                if pair in seen:
                    continue
                seen.add(pair)
                # 主营标签重叠越多, 产业链关联越强
                strength = min(1.0, 0.4 + 0.15 * len(shared))
                edges.append({
                    "source": pair[0],
                    "target": pair[1],
                    "relation_type": "PARTNER",
                    "strength": round(strength, 3),
                    "source_info": "main_business:" + ",".join(sorted(shared)[:3]),
                })
        return edges

    def build_graph_edges(self, symbols: List[str], include_themes: bool = True,
                          include_main_business: bool = True,
                          date: Optional[str] = None) -> List[Dict[str, Any]]:
        """一键构建 GNN 关系网全部边（行业 + 概念 + 题材 + 主营构成）.

        Args:
            include_main_business: 是否含主营构成边 (供应商-客户边免费近似)

        Returns:
            合并的边列表，可与 SupplyChainGraph.add_edge 对接.
        """
        edges = []
        edges += self.build_industry_edges(symbols)
        edges += self.build_concept_edges(symbols)
        if include_main_business:
            edges += self.build_main_business_edges(symbols)
        if include_themes:
            edges += self.build_thematic_edges(date)
        return edges


# ----------------------------------------------------------
# 单例
# ----------------------------------------------------------
_graph_data_source: Optional[GraphDataSource] = None


def get_graph_data_source() -> GraphDataSource:
    """获取图数据源单例."""
    global _graph_data_source
    if _graph_data_source is None:
        _graph_data_source = GraphDataSource()
    return _graph_data_source


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("=" * 70)
    logger.info("图数据源自测 — 东财 push2 行业/概念 + 同花顺题材")
    logger.info("=" * 70)
    ds = get_graph_data_source()

    test_codes = ["688041", "300308", "002371", "600276", "601088"]
    for c in test_codes:
        info = ds.get_industry_relationship(c)
        if info:
            logger.info(f"[行业] {info['name']}({c}) 行业={info['industry']} 地域={info['region']}")
            logger.info(f"        概念({len(ds.get_concept_blocks(c))}): {ds.get_concept_blocks(c)[:6]}")
        else:
            logger.error(f"[FAIL] {c} 无行业数据")
        boards = ds.get_stock_boards(c)
        if boards:
            logger.info(f"        板块({len(boards)}): {[(b['name'], b['code']) for b in boards[:4]]}")
        time.sleep(_EM_INTERVAL)

    logger.info("=" * 70)
    logger.info("边构建自测 (5只持仓)")
    logger.info("=" * 70)
    edges = ds.build_graph_edges(test_codes, include_themes=True)
    logger.info(f"构建边总数: {len(edges)}")
    for e in edges[:10]:
        logger.info(f"  {e['source']} --{e['relation_type']}--> {e['target']} (strength={e['strength']}, {e['source_info']})")
