"""cairn 文档导入 TDAM — Phase 0b 数据导入执行脚本.

将 export_cairn_for_tdam.py 导出的 cairn/ 文档 (wiki + chat_memory) 实际写入
TDAM MemoryCore, 打通 "cairn → TDAM" 单向同步链路。

用法:
    python scripts/tdam/import_cairn_to_tdam.py --dry-run        # 仅统计, 不写 TDAM
    python scripts/tdam/import_cairn_to_tdam.py                  # 全量导入
    python scripts/tdam/import_cairn_to_tdam.py --limit 5        # 限制导入条数 (验证用)
    python scripts/tdam/import_cairn_to_tdam.py --asset skill    # 仅导入 wiki 文档

关联文档: TDAM_cairn_对接方案.md Phase 0b / docs/计划同步对齐_20260807.md

设计原则:
  - 幂等: 同名 skill 重复导入由 TDAM 端去重 (create 语义), 可安全重跑
  - 断点续传: --offset 从指定位置续传, --limit 限制每次批量
  - 失败安全: 单条失败记录日志继续, 汇总统计成功/失败数
  - Feature Flag: 导入是显式操作, 绕过 USE_TDAM_MEMORY_ENHANCEMENT (与 test_client.py 一致)
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tdam_import")

# 项目根目录 (向上查找 utils/ marker)
def _find_project_root() -> Path:
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "utils").exists():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _find_project_root()
sys.path.insert(0, str(PROJECT_ROOT))

# 加载 .env (TDAM_TEAM_ID/TDAM_AGENT_ID/TDAM_BASE_URL 固化, 2026-08-07)
try:
    from utils.env_loader import load_dotenv  # noqa: E402

    load_dotenv(env_path=str(PROJECT_ROOT / ".env"))
except ImportError:
    pass

from scripts.tdam.export_cairn_for_tdam import export_cairn  # noqa: E402
from utils.tdam_client import TDAMClient, TDAMConfig  # noqa: E402


def build_session_id(doc: dict) -> str:
    """为 chat_memory 生成稳定 session_id (基于 source_path, 幂等)."""
    src = doc.get("source_path", "cairn")
    stem = Path(src).stem.replace("_", "-").replace(" ", "-")
    return f"cairn-{doc.get('id', stem)[:40]}"


def import_documents(client: TDAMClient, docs: list[dict], dry_run: bool) -> dict:
    """导入文档列表到 TDAM.

    Args:
        client: TDAMClient (已绕过 Feature Flag)
        docs: 文档列表 (export_cairn() 的 documents)
        dry_run: True 则仅打印, 不调用写接口

    Returns:
        {"total": N, "success": N, "failed": N, "errors": [..]}
    """
    stats = {"total": len(docs), "success": 0, "failed": 0, "errors": []}

    for i, doc in enumerate(docs):
        asset_type = doc.get("asset_type", "wiki")
        title = doc.get("title", "untitled")
        content = doc.get("content", "")
        summary = doc.get("summary", "")

        if not content:
            stats["failed"] += 1
            stats["errors"].append(f"[{i}] 空内容, 跳过: {title}")
            continue

        if dry_run:
            logger.info("[DRY-RUN][%s] %s", asset_type, title)
            stats["success"] += 1
            continue

        try:
            if asset_type == "chat_memory":
                # LOG 条目 → conversation
                result = client.add_conversation(
                    content=content,
                    title=title,
                    session_id=build_session_id(doc),
                )
            else:
                # 专题文档 → skill
                # TDAM skill name 必须与 content 内 frontmatter.name 一致, 否则
                # 40001 INVALID_FRONTMATTER。export_cairn_for_tdam._add_frontmatter 用
                # source_path 的文件名 stem 生成 frontmatter.name, 这里必须同源。
                src_stem = Path(doc.get("source_path", "")).stem or title
                slug = re.sub(r"[^a-z0-9-]", "-", src_stem.lower()).strip("-")
                if not slug or not slug[0].isalnum():
                    slug = f"cairn-skill-{abs(hash(title)) % 100000}"
                name = slug[:64]
                result = client.create_skill(
                    name=name,
                    content=content,
                    description=summary,
                )

            if result.get("success"):
                stats["success"] += 1
                if (i + 1) % 10 == 0 or i == len(docs) - 1:
                    logger.info("导入进度 %d/%d (%s, success=%d)", i + 1, len(docs), asset_type, stats["success"])
            else:
                stats["failed"] += 1
                stats["errors"].append(f"[{i}] 导入失败 ({asset_type}:{title}): {result.get('error', 'unknown')}")
                logger.warning("导入失败 (%s:%s): %s", asset_type, title, result.get("error"))
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            stats["failed"] += 1
            stats["errors"].append(f"[{i}] 异常 ({asset_type}:{title}): {e}")
            logger.error("导入异常 (%s:%s): %s", asset_type, title, e)

    return stats


def main() -> int:
    """主入口."""
    parser = argparse.ArgumentParser(description="导入 cairn/ 到 TDAM (Phase 0b)")
    parser.add_argument("--dry-run", action="store_true", help="仅统计预览, 不写 TDAM")
    parser.add_argument("--limit", type=int, default=0, help="限制导入条数 (0=全部)")
    parser.add_argument("--offset", type=int, default=0, help="跳过前 N 条")
    parser.add_argument("--asset", choices=["skill", "chat_memory"], default="", help="仅导入指定类型")
    parser.add_argument("--base-url", default=os.environ.get("TDAM_BASE_URL", "http://127.0.0.1:8420"), help="TDAM 服务地址")
    args = parser.parse_args()

    logger.info("=== cairn → TDAM 数据导入 (Phase 0b) ===")
    logger.info("模式: %s", "DRY-RUN (不写 TDAM)" if args.dry_run else "全量写入")

    # 1. 导出 cairn 文档
    logger.info("导出 cairn/ 文档...")
    export_data = export_cairn()
    if "error" in export_data:
        logger.error("导出失败: %s", export_data["error"])
        return 1

    docs = export_data.get("documents", [])
    logger.info("导出 %d 个文档 (chat_memory=%d, wiki=%d)",
                len(docs),
                export_data.get("stats", {}).get("chat_memory_count", 0),
                export_data.get("stats", {}).get("wiki_count", 0))

    # 2. 过滤类型 (cairn 导出中 wiki 文档的 asset_type 为 "wiki", 导入为 TDAM skill)
    if args.asset == "skill":
        docs = [d for d in docs if d.get("asset_type") in ("wiki", "skill")]
        logger.info("按类型过滤: skill(含 wiki) → %d 个", len(docs))
    elif args.asset:
        docs = [d for d in docs if d.get("asset_type") == args.asset]
        logger.info("按类型过滤: %s → %d 个", args.asset, len(docs))

    # 3. 应用 offset / limit
    if args.offset:
        docs = docs[args.offset:]
        logger.info("跳过前 %d 条, 剩余 %d", args.offset, len(docs))
    if args.limit:
        docs = docs[: args.limit]
        logger.info("限制 %d 条", args.limit)

    if not docs:
        logger.warning("无可导入文档")
        return 0

    # 4. 初始化 client (自动加载凭据) + 绕过 Feature Flag
    if not args.dry_run:
        client = TDAMClient(TDAMConfig(base_url=args.base_url))
        # 导入是显式操作, 绕过 USE_TDAM_MEMORY_ENHANCEMENT
        client._flag_checked = True  # type: ignore[attr-defined]
        client._flag_enabled = True  # type: ignore[attr-defined]

        # 健康检查
        if not client.health_check():
            logger.error("TDAM 健康检查失败, 无法导入: %s", args.base_url)
            return 1
        logger.info("TDAM 服务健康: %s", args.base_url)

        stats = import_documents(client, docs, dry_run=False)
    else:
        stats = import_documents(None, docs, dry_run=True)  # type: ignore[arg-type]

    # 5. 汇总
    logger.info("=== 导入汇总 ===")
    logger.info("总数: %d", stats["total"])
    logger.info("成功: %d", stats["success"])
    logger.info("失败: %d", stats["failed"])
    if stats["errors"]:
        logger.warning("失败明细 (%d):", len(stats["errors"]))
        for err in stats["errors"][:20]:
            logger.warning("  %s", err)
        if len(stats["errors"]) > 20:
            logger.warning("  ... (共 %d 条错误)", len(stats["errors"]))

    return 0 if stats["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
