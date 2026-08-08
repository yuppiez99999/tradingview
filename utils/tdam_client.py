"""TDAM (TencentDB Agent Memory) REST 客户端 — Phase 0a/1' 核心组件.

将 TDAM MemoryCore 的 REST /v3/* 接口封装为 Python 客户端, 提供记忆检索能力。
作为 cairn/ 记忆体系的**只读增强层**, 不替代 cairn 写入路径。

设计原则:
  1. 失败安全 (fail-safe): TDAM 不可用时降级为空结果, 不阻塞交易决策
  2. 盘后离线: 仅盘后调用生成缓存, 盘中决策只读本地文件 (不实时调 TDAM)
  3. 超时+熔断: 防止网络故障拖垮调用方 (3 次失败后熔断 60s)
  4. Feature Flag: USE_TDAM_MEMORY_ENHANCEMENT (默认 False) 控制启用
  5. 单向同步: cairn → TDAM, 不反向 (cairn 保持唯一权威写入路径)

硬约束对齐:
  - HC: 交易核心链路不依赖外部服务 → fail-safe 降级, TDAM 挂了回退读缓存
  - HC: 盘中卸载大模型 → TDAM 通过任务计划程序时段化隔离 (09:00 停 / 15:30 启)
  - HC: Windows 实盘机本地部署, 端口 127.0.0.1:8420, 数据不出网

真实端点 (2026-08-06 通过源码核查 E:\\TDAM\\MemoryCore\\src\\gateway\\ 确认):
  - POST /v3/skill/search         (BM25 检索 skill 资产, 支持中文)
  - POST /v3/skill/list            (列出 skill)
  - POST /v3/skill/create          (创建 skill, Phase 0b 数据导入用)
  - POST /v3/conversation/search   (检索对话记忆, L0-L3 蒸馏层)
  - POST /v3/conversation/add      (追加对话, Phase 0b 数据导入用)
  - POST /v3/atomic/search         (检索原子记忆)
  - POST /v3/knowledge/list        (列出 knowledge 资产)
  - GET  /health                   (健康检查, 无鉴权)

鉴权: TDAI_GATEWAY_API_KEY 环境变量非空时, 所有非 /health 接口需
       Authorization: Bearer <key> header。为空时禁用鉴权 (本地默认)。

用法 (Phase 1' 盘后作业):
    from utils.tdam_client import TDAMClient, TDAMConfig

    client = TDAMClient(TDAMConfig(base_url="http://127.0.0.1:8420"))
    result = client.search_memory(query="气象因子引擎接口", asset_type="skill")
    if result.success:
        for item in result.items:
            print(item.get("title"), item.get("content", "")[:200])

    # 健康检查 (Phase 0a 验证用)
    ok = client.health_check()

关联文档: TDAM_cairn_对接方案.md (v3 Windows 部署版)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("tdam_client")

# ============================================================
# 常量
# ============================================================
DEFAULT_BASE_URL = "http://127.0.0.1:8420"  # Windows 本地部署端口
DEFAULT_TIMEOUT = 10  # 秒 (本地调用, 10s 足够)
DEFAULT_MAX_RETRIES = 2
CIRCUIT_BREAKER_THRESHOLD = 3  # 连续失败 3 次后熔断
CIRCUIT_BREAKER_RESET_SECONDS = 60  # 熔断 60s 后尝试半开
CACHE_DIR = Path("reports/tdam_cache")

# 默认 user_id (运行时从 .admin-credentials.json 加载, 此处仅兜底)
DEFAULT_USER_ID = ""
DEFAULT_TEAM_ID = "default"
DEFAULT_SERVICE_ID = "default"

# admin 凭据文件 (init-admin 后生成, 含 user_key + user_id)
ADMIN_CREDS_FILE = Path("E:/tdam-data/memory/.admin-credentials.json")

# ============================================================
# REST 端点 (基于源码核查, 非 README 推测)
# ============================================================
ENDPOINT_HEALTH = "/health"

# 资产类型 → 搜索端点映射
# skill/conversation/atomic 走 search 接口; knowledge 走 list 接口 (无 search)
ASSET_SEARCH_ENDPOINT: dict[str, str] = {
    "skill": "/v3/skill/search",
    "conversation": "/v3/conversation/search",
    "atomic": "/v3/atomic/search",
    # 兼容旧别名
    "chat_memory": "/v3/conversation/search",
    "wiki": "/v3/skill/search",  # cairn 专题文档导入为 skill, 走 skill/search
}

# 资产类型 → 列表端点映射
ASSET_LIST_ENDPOINT: dict[str, str] = {
    "skill": "/v3/skill/list",
    "knowledge": "/v3/knowledge/list",
}

# 写入端点 (Phase 0b 数据导入用)
ENDPOINT_SKILL_CREATE = "/v3/skill/create"
ENDPOINT_CONVERSATION_ADD = "/v3/conversation/add"


# ============================================================
# 异常体系
# ============================================================
class TDAMError(Exception):
    """TDAM 客户端基础异常."""


class TDAMConnectionError(TDAMError):
    """TDAM 连接异常 (网络不可达/超时)."""


class TDAMCircuitOpenError(TDAMError):
    """熔断器开启异常 (连续失败过多, 暂时不可用)."""

    def __init__(self, message: str, reset_in_seconds: float = 0.0) -> None:
        super().__init__(message)
        self.reset_in_seconds = reset_in_seconds


class TDAMAPIError(TDAMError):
    """TDAM API 返回错误 (非 2xx 状态码)."""

    def __init__(self, message: str, status_code: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


# ============================================================
# 熔断器状态
# ============================================================
class CircuitState(Enum):
    """熔断器三态."""

    CLOSED = "closed"  # 正常 (允许请求)
    OPEN = "open"  # 熔断 (拒绝请求)
    HALF_OPEN = "half_open"  # 半开 (允许试探性请求)


@dataclass
class CircuitBreaker:
    """简易熔断器 — 连续失败 N 次后开启, 等待 T 秒后半开试探.

    线程安全: 使用 GIL 保证的简单实现 (适用于盘后单线程作业)。
    如需多线程, 需加 Lock。

    Attributes:
        threshold: 连续失败次数阈值
        reset_seconds: 熔断后等待时间 (秒)
        failure_count: 当前连续失败计数
        state: 当前状态
        last_failure_time: 上次失败时间戳
    """

    threshold: int = CIRCUIT_BREAKER_THRESHOLD
    reset_seconds: int = CIRCUIT_BREAKER_RESET_SECONDS
    failure_count: int = 0
    state: CircuitState = CircuitState.CLOSED
    last_failure_time: float = 0.0

    def can_execute(self) -> bool:
        """检查是否允许执行请求.

        Returns:
            True 表示允许 (CLOSED 或 HALF_OPEN), False 表示熔断中 (OPEN)
        """
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # 检查是否已过冷却期
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.reset_seconds:
                self.state = CircuitState.HALF_OPEN
                logger.info(
                    "[tdam_circuit] 熔断器半开 (冷却 %ds 已过), 允许试探性请求",
                    self.reset_seconds,
                )
                return True
            return False

        # HALF_OPEN: 允许一次试探
        return True

    def record_success(self) -> None:
        """记录一次成功请求 (重置失败计数)."""
        if self.state != CircuitState.CLOSED:
            logger.info("[tdam_circuit] 熔断器恢复 (CLOSED)")
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """记录一次失败请求 (增加计数, 可能触发熔断)."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            # 半开态失败 → 重新熔断
            self.state = CircuitState.OPEN
            logger.warning(
                "[tdam_circuit] 半开态试探失败, 重新熔断 (OPEN, 冷却 %ds)",
                self.reset_seconds,
            )
        elif self.failure_count >= self.threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "[tdam_circuit] 连续失败 %d 次 (>= %d), 熔断开启 (OPEN, 冷却 %ds)",
                self.failure_count,
                self.threshold,
                self.reset_seconds,
            )

    def reset_in_seconds(self) -> float:
        """返回距离熔断器恢复的剩余秒数 (OPEN 态)."""
        if self.state != CircuitState.OPEN:
            return 0.0
        elapsed = time.time() - self.last_failure_time
        remaining = self.reset_seconds - elapsed
        return max(0.0, remaining)


# ============================================================
# 数据类
# ============================================================
@dataclass
class TDAMConfig:
    """TDAM 客户端配置.

    Attributes:
        base_url: TDAM 服务地址 (默认 http://127.0.0.1:8420, Windows 本地部署)
        timeout: 单次请求超时 (秒)
        max_retries: 最大重试次数 (不含首次)
        offline: 强制离线模式 (不发起任何网络请求, 仅读缓存)
        cache_dir: 本地缓存目录 (Phase 1' 盘后缓存)
        api_key: TDAM Gateway 鉴权 key (对应 TDAI_GATEWAY_API_KEY 环境变量;
                 为空时禁用鉴权, 与服务端行为对齐)
        user_id: 默认 user_id (TDAM /v3 严格模式要求, 默认 "system")
        team_id: 默认 team_id (默认 "default")
    """

    base_url: str = DEFAULT_BASE_URL
    timeout: int = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    offline: bool = False
    cache_dir: Path = CACHE_DIR
    user_key: str = ""  # sk-mem-xxx, /v3/* 路由必需 (从 .admin-credentials.json 加载)
    service_id: str = DEFAULT_SERVICE_ID  # x-tdai-service-id header 值
    user_id: str = DEFAULT_USER_ID  # usr-xxx, body 参数 (从 .admin-credentials.json 加载)
    team_id: str = DEFAULT_TEAM_ID
    agent_id: str = "default"  # agent 标识 (conversation/add 必需)
    admin_creds_file: Path = ADMIN_CREDS_FILE

    def __post_init__(self) -> None:
        # 1. 优先用显式传入的 user_key / user_id
        # 2. 从环境变量读取 (TDAM_USER_KEY / TDAM_USER_ID)
        if not self.user_key:
            self.user_key = os.environ.get("TDAM_USER_KEY", "")
        if not self.user_id:
            self.user_id = os.environ.get("TDAM_USER_ID", "")
        # 3. 从 .admin-credentials.json 读取 (init-admin 后生成)
        if (not self.user_key or not self.user_id) and self.admin_creds_file.exists():
            try:
                import json as _json
                creds = _json.loads(self.admin_creds_file.read_text(encoding="utf-8"))
                if not self.user_key:
                    self.user_key = creds.get("user_key", "")
                if not self.user_id:
                    self.user_id = creds.get("user_id", "")
            except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
                logger.warning("[tdam_client] 读取 admin 凭据失败: %s — %s", self.admin_creds_file, e)
        # 4. team_id / agent_id 支持环境变量覆盖 (TDAM_TEAM_ID / TDAM_AGENT_ID)
        #    TDAM 的 team/agent 用系统生成的带前缀 id (如 team-xxx / agt-xxx),
        #    需与 v3-meta 创建的实体一致, 否则 skill 写入会报 team/agent not found。
        if self.team_id == DEFAULT_TEAM_ID:
            self.team_id = os.environ.get("TDAM_TEAM_ID", self.team_id)
        if self.agent_id == "default":
            self.agent_id = os.environ.get("TDAM_AGENT_ID", self.agent_id)


@dataclass
class TDAMSearchResult:
    """TDAM 记忆检索结果.

    Attributes:
        success: 是否成功 (False 表示降级/失败)
        items: 检索到的记忆条目列表 (每条为 dict, 含 title/content/score 等)
        total: 总匹配数
        latency_ms: 响应延迟 (毫秒)
        error: 错误信息 (success=False 时)
        degraded: 是否降级 (TDAM 不可用但未报错, 返回空结果)
    """

    success: bool = False
    items: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    latency_ms: float = 0.0
    error: str = ""
    degraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (用于序列化缓存)."""
        return {
            "success": self.success,
            "items": self.items,
            "total": self.total,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "degraded": self.degraded,
        }


# ============================================================
# 核心客户端
# ============================================================
class TDAMClient:
    """TDAM REST 客户端 — 封装记忆检索接口.

    用法:
        client = TDAMClient(TDAMConfig(base_url="http://127.0.0.1:8420"))
        result = client.search_memory(query="气象因子", asset_type="skill")
        if result.success:
            ...

    失败安全:
        - Feature Flag 关闭 → 返回 degraded 空结果
        - 熔断器开启 → 返回 degraded 空结果
        - 网络超时/连接失败 → 重试后仍失败则返回 degraded 空结果
        - offline 模式 → 不发请求, 直接返回 degraded 空结果
    """

    def __init__(self, config: TDAMConfig | None = None) -> None:
        self.config = config or TDAMConfig()
        self._session = self._create_session()
        self._circuit = CircuitBreaker()

        # 延迟导入 FeatureFlags (避免循环依赖)
        self._flag_checked = False
        self._flag_enabled = False

    # ============================================================
    # Session 管理
    # ============================================================
    def _create_session(self) -> requests.Session:
        """创建 HTTP Session (复用 http_session 模块的代理禁用策略)."""
        session = requests.Session()
        session.trust_env = False  # 不读取系统代理 (数据不出网)
        session.proxies = {"http": None, "https": None}
        session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        # 鉴权 header (user_key 非空时启用, /v3/* 路由必需)
        if self.config.user_key:
            session.headers.update({
                "Authorization": f"Bearer {self.config.user_key}",
                "x-tdai-service-id": self.config.service_id,
            })
        return session

    def _check_flag(self) -> bool:
        """检查 Feature Flag 是否启用 (延迟导入, 首次调用后缓存)."""
        if self._flag_checked:
            return self._flag_enabled

        try:
            from utils.infra.feature_flags import is_enabled
            self._flag_enabled = is_enabled("USE_TDAM_MEMORY_ENHANCEMENT")
        except (ImportError, AttributeError, RuntimeError) as e:
            logger.warning("[tdam_client] Feature Flag 检查失败, 视为未启用: %s", e)
            self._flag_enabled = False

        self._flag_checked = True
        if not self._flag_enabled:
            logger.debug("[tdam_client] USE_TDAM_MEMORY_ENHANCEMENT=False, TDAM 未启用")
        return self._flag_enabled

    # ============================================================
    # 响应解析 helper
    # ============================================================
    @staticmethod
    def _extract_items(result: Any, asset_type: str = "") -> list[dict[str, Any]]:
        """从 TDAM 响应中提取 items 列表 (适配不同端点的响应结构).

        TDAM 标准响应: {"code":0, "message":"ok", "data":{"items":[...], "total":N}}
        conversation/search 特殊: {"code":0, "data":{"messages":[...]}}
        """
        if isinstance(result, list):
            return result
        if not isinstance(result, dict):
            return []

        data = result.get("data")
        if isinstance(data, dict):
            # conversation/search 返回 data.messages
            if asset_type in ("conversation", "chat_memory"):
                return data.get("messages") or data.get("items") or []
            # 标准结构: data.items / data.list / data.results
            return data.get("items") or data.get("list") or data.get("results") or []

        # 兜底: items 直接在顶层
        return result.get("items") or result.get("results") or []

    @staticmethod
    def _extract_total(result: Any) -> int:
        """从 TDAM 响应中提取 total 计数."""
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, dict):
                return int(data.get("total", 0))
            return int(result.get("total", 0))
        return 0

    # ============================================================
    # 公共 API
    # ============================================================
    def health_check(self) -> bool:
        """健康检查 — 验证 TDAM 服务是否可达.

        Phase 0a 验证用: 确认服务部署成功 + REST 接口可用。

        Returns:
            True 表示服务健康, False 表示不可达
        """
        if self.config.offline:
            return False

        try:
            resp = self._session.get(
                f"{self.config.base_url}{ENDPOINT_HEALTH}",
                timeout=5,
            )
            return resp.status_code == 200
        except (requests.Timeout, requests.ConnectionError, requests.RequestException) as e:
            logger.debug("[tdam_client] 健康检查失败: %s", e)
            return False

    def list_skills(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """列出 TDAM 中的 skill 资产 — Phase 0b 数据导入验证用.

        Args:
            limit: 返回条目数上限
            offset: 偏移量 (分页用)

        Returns:
            {"items": [...], "total": N} 或 {"error": "...", "items": []}
        """
        return self._list_assets("skill", limit=limit, offset=offset)

    def list_knowledge(self, limit: int = 50) -> dict[str, Any]:
        """列出 TDAM 中的 knowledge 资产."""
        return self._list_assets("knowledge", limit=limit)

    def _list_assets(
        self,
        asset_type: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """通用列表接口."""
        if not self._check_flag():
            return {"error": "feature_flag_disabled", "items": [], "total": 0}

        if self.config.offline:
            return {"error": "offline_mode", "items": [], "total": 0}

        endpoint = ASSET_LIST_ENDPOINT.get(asset_type)
        if not endpoint:
            return {"error": f"unsupported_asset_type:{asset_type}", "items": [], "total": 0}

        body: dict[str, Any] = {"limit": limit, "team_id": self.config.team_id}
        if asset_type == "skill":
            body["offset"] = offset

        result = self._call_with_circuit_breaker(
            method="POST",
            endpoint=endpoint,
            json_body=body,
        )
        if result is None:
            return {"error": "circuit_open_or_request_failed", "items": [], "total": 0}

        items = self._extract_items(result, asset_type)
        total = self._extract_total(result)
        return {"items": items, "total": total, "raw": result}

    def search_memory(
        self,
        query: str,
        asset_type: str = "skill",
        top_k: int = 5,
        score_threshold: float = 0.0,
    ) -> TDAMSearchResult:
        """检索 TDAM 记忆 — Phase 1' 盘后作业核心接口.

        Args:
            query: 检索查询 (如 "气象因子引擎接口" / "GNN 供应链 Gate1 结论")
                   支持中文, 走 BM25 全文检索。
            asset_type: 资产类型
                - "skill" (默认, cairn 专题文档导入为 skill)
                - "conversation" / "chat_memory" (对话记忆, L0-L3 蒸馏层)
                - "atomic" (原子记忆)
                - "wiki" (别名, 实际走 skill/search)
            top_k: 返回条目数上限
            score_threshold: 相关性分数阈值 (0.0 表示不过滤)

        Returns:
            TDAMSearchResult — success=True 时 items 含检索结果,
            success=False 时 degraded=True 表示降级 (TDAM 不可用但未报错)
        """
        # 1. Feature Flag 检查
        if not self._check_flag():
            return TDAMSearchResult(
                success=False, degraded=True,
                error="feature_flag_disabled",
            )

        # 2. 离线模式
        if self.config.offline:
            return TDAMSearchResult(
                success=False, degraded=True,
                error="offline_mode",
            )

        # 3. 端点解析
        endpoint = ASSET_SEARCH_ENDPOINT.get(asset_type)
        if not endpoint:
            return TDAMSearchResult(
                success=False, degraded=True,
                error=f"unsupported_asset_type:{asset_type}",
            )

        # 4. 熔断器检查
        if not self._circuit.can_execute():
            remaining = self._circuit.reset_in_seconds()
            logger.warning(
                "[tdam_client] 熔断中, 跳过检索 (剩余 %.0fs): query=%s",
                remaining, query[:50],
            )
            return TDAMSearchResult(
                success=False, degraded=True,
                error=f"circuit_open (reset in {remaining:.0f}s)",
            )

        # 5. 构造请求 body (POST + JSON)
        # conversation/chat_memory/atomic 走 L0-L3 严格 isolation, 必须带 agent_id,
        # 否则 /v3/conversation/search 在严格模式下查不到 (返回空 messages)。
        json_body: dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "user_id": self.config.user_id,
            "team_id": self.config.team_id,
        }
        if asset_type in ("conversation", "chat_memory", "atomic"):
            json_body["agent_id"] = self.config.agent_id
        if score_threshold > 0.0:
            json_body["score_threshold"] = score_threshold

        # 6. 调用 REST API
        start = time.perf_counter()
        result = self._call_with_circuit_breaker(
            method="POST",
            endpoint=endpoint,
            json_body=json_body,
        )
        latency_ms = (time.perf_counter() - start) * 1000

        # 7. 处理结果
        if result is None:
            return TDAMSearchResult(
                success=False, degraded=True,
                error="request_failed_after_retries",
                latency_ms=latency_ms,
            )

        # TDAM 标准响应: {"code":0, "data":{"items":[...], "total":N}}
        # conversation/search 特殊: {"code":0, "data":{"messages":[...]}}
        items = self._extract_items(result, asset_type)
        total = len(items)

        logger.info(
            "[tdam_client] 检索成功: asset=%s, query=%s, items=%d, latency=%.0fms",
            asset_type, query[:30], total, latency_ms,
        )

        return TDAMSearchResult(
            success=True,
            items=items,
            total=total,
            latency_ms=latency_ms,
        )

    def create_skill(
        self,
        name: str,
        content: str,
        description: str = "",
    ) -> dict[str, Any]:
        """创建 skill 资产 — Phase 0b 数据导入用.

        Args:
            name: skill 名称 (1-64 字符, 超长截断)
            content: skill 内容 (Markdown 文本, 非空)
            description: skill 描述 (放入 metadata, schema 无 description 字段)

        Returns:
            {"success": True, "id": "..."} 或 {"success": False, "error": "..."}
        """
        if not self._check_flag():
            return {"success": False, "error": "feature_flag_disabled"}

        if self.config.offline:
            return {"success": False, "error": "offline_mode"}

        # name 限 64 字符 (schema: z.string().min(1).max(64))
        name = name[:64]

        json_body: dict[str, Any] = {
            "name": name,
            "content": content,
            "user_id": self.config.user_id,
            "team_id": self.config.team_id,
            "agent_id": self.config.agent_id,
        }
        # description 不在 schema 里, 放到 metadata
        if description:
            json_body["metadata"] = {"description": description[:500]}

        result = self._call_with_circuit_breaker(
            method="POST",
            endpoint=ENDPOINT_SKILL_CREATE,
            json_body=json_body,
        )
        if result is None:
            return {"success": False, "error": "request_failed"}

        # 响应可能为 {data: {id: ...}} 或 {id: ...}
        skill_id = ""
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, dict):
                skill_id = data.get("id") or data.get("skill_id") or ""
            else:
                skill_id = result.get("id") or result.get("skill_id") or ""
        return {"success": True, "id": skill_id, "raw": result}

    def add_conversation(
        self,
        content: str,
        title: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        """追加对话记忆 — Phase 0b 数据导入用 (cairn LOG.md → conversation).

        TDAM schema (skill-schemas.ts:221):
          session_id: string (必需, 不能含 |)
          user_id: string (必需)
          team_id: string (必需)
          agent_id: string (必需)
          messages: array (必需, 1-500 元素, 每元素 {role, content})

        Args:
            content: 对话内容 (放入 messages[0].content)
            title: 对话标题 (放入 metadata, schema 无 title 字段)
            session_id: 会话 ID (空则自动生成, 不能含 |)

        Returns:
            {"success": True, "raw": ...} 或 {"success": False, "error": "..."}
        """
        if not self._check_flag():
            return {"success": False, "error": "feature_flag_disabled"}

        if self.config.offline:
            return {"success": False, "error": "offline_mode"}

        # session_id 必需, 不能含 |
        if not session_id:
            from datetime import datetime
            session_id = f"cairn-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        session_id = session_id.replace("|", "-")

        json_body: dict[str, Any] = {
            "session_id": session_id,
            "user_id": self.config.user_id,
            "team_id": self.config.team_id,
            "agent_id": self.config.agent_id,
            "messages": [
                {"role": "user", "content": content},
            ],
        }

        result = self._call_with_circuit_breaker(
            method="POST",
            endpoint=ENDPOINT_CONVERSATION_ADD,
            json_body=json_body,
        )
        if result is None:
            return {"success": False, "error": "request_failed"}

        return {"success": True, "raw": result}

    # ============================================================
    # 内部: 带熔断器的请求
    # ============================================================
    def _call_with_circuit_breaker(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """带熔断器+重试的 REST 调用.

        Returns:
            响应 JSON 字典, None 表示失败 (已重试仍失败)
        """
        url = f"{self.config.base_url}{endpoint}"
        last_exception: Exception | None = None

        for attempt in range(self.config.max_retries + 1):
            try:
                if method == "GET":
                    resp = self._session.get(url, params=params, timeout=self.config.timeout)
                elif method == "POST":
                    resp = self._session.post(url, json=json_body, timeout=self.config.timeout)
                else:
                    raise TDAMError(f"不支持的 HTTP 方法: {method}")

                # 检查状态码
                if resp.status_code >= 400:
                    raise TDAMAPIError(
                        f"TDAM API 错误: {resp.status_code}",
                        status_code=resp.status_code,
                        body=resp.text[:500],
                    )

                # 检查业务错误码 (TDAM 在 HTTP 200 的 body.code 返回业务错误,
                # 如 42203 SKILL_FRONTMATTER_INVALID / 50001 agent_not_found)。
                # 若不检查, 会造成"假成功"——调用方误以为写入成功, 实际数据未持久化。
                try:
                    body_json = resp.json()
                except ValueError:
                    raise TDAMAPIError(
                        f"TDAM 响应非 JSON: {resp.status_code}",
                        status_code=resp.status_code,
                        body=resp.text[:500],
                    )
                biz_code = body_json.get("code", 0) if isinstance(body_json, dict) else 0
                if biz_code not in (0, "0", None):
                    raise TDAMAPIError(
                        f"TDAM 业务错误: {biz_code}",
                        status_code=resp.status_code,
                        body=json.dumps(body_json, ensure_ascii=False)[:500],
                    )

                self._circuit.record_success()
                return body_json

            except (requests.Timeout, requests.ConnectionError) as e:
                last_exception = e
                logger.debug(
                    "[tdam_client] 连接失败 (attempt %d/%d): %s",
                    attempt + 1, self.config.max_retries + 1, e,
                )

            except TDAMAPIError as e:
                last_exception = e
                logger.warning(
                    "[tdam_client] API 错误 %d (attempt %d/%d): %s",
                    e.status_code, attempt + 1, self.config.max_retries + 1,
                    e.body[:100],
                )
                # 4xx 错误不重试 (除了 429 限流)
                if 400 <= e.status_code < 500 and e.status_code != 429:
                    break

            except (requests.RequestException, ValueError, TypeError) as e:
                last_exception = e
                logger.debug(
                    "[tdam_client] 请求异常 (attempt %d/%d): %s",
                    attempt + 1, self.config.max_retries + 1, e,
                )

            # 重试前等待 (指数退避)
            if attempt < self.config.max_retries:
                wait = 0.5 * (2 ** attempt)
                time.sleep(wait)

        # 全部重试失败
        self._circuit.record_failure()
        if last_exception:
            logger.warning(
                "[tdam_client] 请求失败 (%d 次重试后): %s — %s",
                self.config.max_retries,
                type(last_exception).__name__,
                last_exception,
            )
        return None

    # ============================================================
    # 缓存读写 (Phase 1' 盘后缓存)
    # ============================================================
    def save_cache(
        self,
        query: str,
        result: TDAMSearchResult,
        date_str: str = "",
    ) -> Path:
        """将检索结果保存为本地缓存文件 (Phase 1' 盘后作业用).

        缓存路径: reports/tdam_cache/{date}_{query_hash}.json

        Args:
            query: 原始查询
            result: 检索结果
            date_str: 日期字符串 (空则用今天)

        Returns:
            缓存文件路径
        """
        from datetime import datetime
        import hashlib
        date = date_str or datetime.now().strftime("%Y-%m-%d")
        query_hash = hashlib.md5(query.encode("utf-8")).hexdigest()[:8]  # nosec B324 — 非安全用途, 仅作查询缓存键

        cache_dir = self.config.cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{date}_{query_hash}.json"

        cache_data = {
            "query": query,
            "date": date,
            "timestamp": datetime.now().isoformat(),
            "result": result.to_dict(),
        }

        cache_path.write_text(
            json.dumps(cache_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug("[tdam_client] 缓存已保存: %s", cache_path)
        return cache_path

    @staticmethod
    def load_cache(cache_path: Path) -> dict[str, Any] | None:
        """读取本地缓存文件 (Phase 1' 盘中决策用).

        Args:
            cache_path: 缓存文件路径

        Returns:
            缓存数据字典, None 表示文件不存在或损坏
        """
        if not cache_path.exists():
            return None
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            logger.warning("[tdam_client] 缓存读取失败: %s — %s", cache_path, e)
            return None


# ============================================================
# 模块级快捷函数
# ============================================================
_default_client: TDAMClient | None = None


def get_default_client() -> TDAMClient:
    """获取默认 TDAMClient 单例 (延迟初始化)."""
    global _default_client
    if _default_client is None:
        _default_client = TDAMClient()
    return _default_client


def search_memory(query: str, asset_type: str = "skill", top_k: int = 5) -> TDAMSearchResult:
    """快捷函数: 使用默认客户端检索记忆."""
    return get_default_client().search_memory(query, asset_type, top_k)


__all__ = [
    "TDAMConfig",
    "TDAMSearchResult",
    "TDAMClient",
    "CircuitBreaker",
    "CircuitState",
    "TDAMError",
    "TDAMConnectionError",
    "TDAMCircuitOpenError",
    "TDAMAPIError",
    "get_default_client",
    "search_memory",
    "ASSET_SEARCH_ENDPOINT",
    "ASSET_LIST_ENDPOINT",
]
