"""Ling-3.0-flash 舆情判断客户端（28 仓晨间舆情日报专用）

背景 (2026-09-07, 舆情链路统一改造):
  28 仓每日 08:00 晨间信息采集的《舆情综合日报》(nlp/sentiment_hub.py) 由纯规则引擎
  升级为 "Ling 主判 + 规则兜底"。本模块从 02_舆情与竞品监控/舆情监控/ling_flash_validate.py
  移植 ModelScope Ling-3.0-flash 调用范式 (few-shot + JSON), 差异:
    - 持仓组合由调用方动态传入 (config/positions.json 实际持仓), 示例标的名均在持仓内
    - token 读取链: 环境变量 -> 28仓/.env -> 02 舆情监控/.env (任一存在即可; 不打印不落盘)
    - 无 token / API 失败 -> judge_news 返回 None, 上层自动回退规则引擎 (观测路径 fail-open)

环境开关:
    SENTIMENT_LING_ENABLED=0       强制禁用 Ling (走规则引擎)
    SENTIMENT_LING_INTERVAL=0.6    相邻两次请求最小间隔 (秒, 缓解 ModelScope 免费层 RPM)
    SENTIMENT_LING_TIMEOUT=90      单次请求超时 (秒)
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

try:
    import requests
except Exception:  # pragma: no cover - 离线降级
    requests = None  # type: ignore[assignment]

_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parent.parent  # e:\各种PY程序\28-终极量化交易系统8.4
_02_ENV = (
    _PROJECT_ROOT.parent / "02_舆情与竞品监控" / "舆情监控" / ".env"
)
_28_ENV = _PROJECT_ROOT / ".env"

# ModelScope Ling-3.0-flash (openai 兼容 /v1/chat/completions)
try:
    from utils.safe_url import validate_url as _validate_url
except ImportError:  # pragma: no cover
    _validate_url = None

_LING_URL = "https://api-inference.modelscope.cn/v1/chat/completions"
if _validate_url is not None:
    _LING_URL = _validate_url(_LING_URL)
_LING_MODEL = "inclusionAI/Ling-3.0-flash"

_VALID_DIRECTIONS = {"利好", "利空", "中性", "混合"}

_lock = threading.Lock()
_last_call_ts: float = 0.0
_token: Optional[str] = None
_token_probed = False


def _load_env_file(path: Path) -> dict:
    env: dict = {}
    try:
        if path and path.is_file():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


def _resolve_token() -> Optional[str]:
    """token 解析: 环境变量 -> 28仓/.env -> 02 舆情/.env (任一首个非空)"""
    global _token, _token_probed
    if _token_probed:
        return _token
    _token = os.environ.get("MODELSCOPE_TOKEN") or ""
    if not _token:
        for fp in (_28_ENV, _02_ENV):
            val = _load_env_file(fp).get("MODELSCOPE_TOKEN", "")
            if val:
                _token = val
                break
    _token_probed = True
    return _token or None


def ling_enabled() -> bool:
    """是否启用 Ling (开关 + requests 可用 + token 存在)"""
    if os.environ.get("SENTIMENT_LING_ENABLED", "1") == "0":
        return False
    if requests is None:
        return False
    return bool(_resolve_token())


def ling_status() -> str:
    """供报告头标注的原因文本"""
    if os.environ.get("SENTIMENT_LING_ENABLED", "1") == "0":
        return "Ling 已由 SENTIMENT_LING_ENABLED=0 禁用"
    if requests is None:
        return "Ling 不可用 (requests 缺失)"
    if not _resolve_token():
        return "Ling 未启用 (缺少 MODELSCOPE_TOKEN, 自动回退规则引擎)"
    return "Ling-3.0-flash (ModelScope)"


def _try_load_json(blob: str) -> Optional[dict]:
    """尝试解析单个 JSON 片段, 容忍中文引号/全角标点; 失败返回 None"""
    try:
        return json.loads(blob)
    except Exception:
        pass
    cleaned = (
        blob.replace("\n", "")
        .replace("，", ",")
        .replace("：", ":")
        .replace("“", '"')
        .replace("”", '"')
    )
    try:
        return json.loads(cleaned)
    except Exception:
        return None


def parse_json_loose(text: str) -> Optional[dict]:
    """容忍模型输出附带 markdown 代码块/前后缀/中文引号.

    策略: 从文本中**最后一个** '{' 起解析 (模型最终 JSON 通常位于输出末尾,
    可规避其偶发逐步分析模式里前置的闲聊/回声干扰); 逐段向前回退到首个可解析块.
    """
    if not text:
        return None
    starts = [mm.start() for mm in re.finditer(r"\{", text)]
    for start in reversed(starts):
        obj = _try_load_json(text[start:])
        if isinstance(obj, dict):
            return obj
    return None


def _build_examples() -> list:
    """few-shot 示例: 新闻 -> JSON。标的名取自 positions.json 实际持仓"""
    return [
        (
            "新闻：央行宣布全面降准0.5个百分点，释放长期资金约1万亿元。",
            '{"direction":"利好","impacted":["上证5年期国债ETF","银行ETF华宝","证券ETF国泰"],"confidence":0.9,"brief":"降准释放流动性，利好利率债与金融"}',
        ),
        (
            "新闻：隔夜美股大跌，避险情绪升温，美债收益率快速下行。",
            '{"direction":"混合","impacted":["科创50ETF易方达","上证5年期国债ETF","黄金ETF华安"],"confidence":0.6,"brief":"权益承压但债黄金受益"}',
        ),
        (
            "新闻：某半导体大厂收到立案调查通知，涉嫌违规披露，股价或受冲击。",
            '{"direction":"利空","impacted":["半导体ETF国泰","海光信息","中科曙光"],"confidence":0.85,"brief":"立案调查压制科技股情绪"}',
        ),
    ]


def _request_with_retry(
    messages: list, timeout: int, temperature: float = 0.1
) -> Optional[str]:
    """ModelScope 免费层有 RPM 限流 (429) 与偶发服务端错误, 指数退避重试

    temperature 默认 0.1 (保留采样多样性, 使重试有机会用不同采样逃脱 verbose 模式);
    重试场景可显式提高温度打破确定性 verbose 死循环.
    """
    token = _resolve_token()
    if not token:
        return None
    payload = {
        "model": _LING_MODEL,
        "messages": messages,
        "temperature": temperature,
        # 留足空间: Ling 偶发进入英文逐步分析模式会狂吃 token, max_tokens 过小会被
        # 截断在 JSON 之前; 2048 给"分析完 + 输出 JSON"留足余量 (实测 verbose 约 1.5k token)
        "max_tokens": 2048,
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    max_tries = 3
    for attempt in range(max_tries):
        try:
            r = requests.post(  # type: ignore[union-attr]
                _LING_URL, headers=headers, json=payload, timeout=timeout
            )
            if r.status_code in (429, 500, 502, 503, 504):
                if attempt == max_tries - 1:
                    return None
                time.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code != 200:
                return None
            data = r.json()
            return (data.get("choices") or [{}])[0].get("message", {}).get("content")
        except Exception:
            if attempt == max_tries - 1:
                return None
            time.sleep(2.0 * (attempt + 1))
    return None


def judge_news(
    title: str, body: str, holdings_text: str
) -> Optional[dict]:
    """对单条新闻做方向判断 (Ling 主判)

    Args:
        title: 新闻标题
        body: 新闻正文/摘要 (截断由调用方做)
        holdings_text: 持仓标的名列表文本 (如 "科创50ETF易方达, 证券ETF国泰, ...")

    Returns:
        {"direction": "利好|利空|中性|混合", "impacted": [...], "confidence": float,
         "brief": str} | None (不可用/失败时返回 None 由上层回退)
    """
    if not ling_enabled():
        return None

    system = (
        "你是A股量化投研助手，持仓组合为：" + holdings_text +
        "。严格模仿示例：对每条新闻只输出一个JSON对象，禁止任何分析过程、编号或多余文字。"
        "JSON字段：direction(利好|利空|中性|混合,指对持仓组合整体净影响)、"
        "impacted(受影响持仓标的,最多3个,名字须在组合名单内)、"
        "confidence(0~1)、brief(15字内理由)。"
    )
    msgs: list = [{"role": "system", "content": system}]
    for ex_in, ex_out in _build_examples():
        msgs.append({"role": "user", "content": ex_in})
        msgs.append({"role": "assistant", "content": ex_out})

    head = (title or "").strip()
    snip = (body or "").strip()
    user_text = f"新闻：{head[:100]}。{snip[:220]}"
    msgs.append({"role": "user", "content": user_text})

    # 节流: 免费层 RPM 限制
    global _last_call_ts
    interval = float(os.environ.get("SENTIMENT_LING_INTERVAL", "0.6") or 0.6)
    timeout = int(float(os.environ.get("SENTIMENT_LING_TIMEOUT", "90") or 90))
    with _lock:
        wait = interval - (time.time() - _last_call_ts)
        if wait > 0:
            time.sleep(wait)
        _last_call_ts = time.time()

    raw = _request_with_retry(msgs, timeout=timeout, temperature=0.1)
    if not raw:
        return None
    obj = parse_json_loose(raw)
    if not obj:
        # Ling 偶发进入 verbose 英文分析模式未输出 JSON: 提高温度打破确定性死循环,
        # 并追加强制 JSON 续写指令 (同会话上下文仍在), 给一次补救机会
        force_msgs = msgs + [
            {
                "role": "user",
                "content": (
                    "停止分析。只输出一个JSON对象，不要任何其它文字、不要编号。"
                    '格式：{"direction":"利好|利空|中性|混合",'
                    '"impacted":["持仓名"],"confidence":0.0,"brief":"15字内理由"}。'
                ),
            }
        ]
        raw = _request_with_retry(force_msgs, timeout=timeout, temperature=0.5)
        obj = parse_json_loose(raw)
    if not obj:
        return None

    direction = str(obj.get("direction", "")).strip()
    if direction not in _VALID_DIRECTIONS:
        return None
    impacted = obj.get("impacted")
    if not isinstance(impacted, list):
        impacted = []
    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return {
        "direction": direction,
        "impacted": [str(x) for x in impacted][:3],
        "confidence": round(confidence, 2),
        "brief": str(obj.get("brief", ""))[:40],
    }
