"""cairn 文档导出为 TDAM 可导入格式 — Phase 0b 数据导入前置脚本.

将 cairn/ 目录的 Markdown 文档导出为 JSON 数组, 供 TDAM Cold-Start 导入。
分类导出:
  - LOG.md → chat_memory (L0 原始对话)
  - 专题文档 (*.md) → wiki (结构化知识页面)
  - ROADMAP.md → wiki (路线图)

用法:
    python scripts/tdam/export_cairn_for_tdam.py --output reports/tdam_cache/cairn_export.json
    python scripts/tdam/export_cairn_for_tdam.py --preview  # 仅预览, 不写文件

关联文档: TDAM_cairn_对接方案.md Phase 0b
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tdam_export")

# 项目根目录 (向上查找 utils/ marker)
def _find_project_root() -> Path:
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "utils").exists():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent

PROJECT_ROOT = _find_project_root()
CAIRN_DIR = PROJECT_ROOT / "cairn"
OUTPUT_DEFAULT = PROJECT_ROOT / "reports" / "tdam_cache" / "cairn_export.json"


def extract_title(content: str, fallback: str) -> str:
    """从 Markdown 内容提取第一个 H1 标题, 无则用 fallback."""
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)


def _add_frontmatter(title: str, content: str, source_stem: str = "") -> str:
    """为 wiki 文档添加 YAML frontmatter (TDAM skill 要求).

    TDAM /v3/skill/create 要求 content 以 '---\\n' 开头的 YAML frontmatter,
    且必须包含 'name' 字段 (slug 格式 ^[a-z0-9][a-z0-9-]*$) 和 'description' 字段,
    否则返回 42203 SKILL_FRONTMATTER_INVALID.

    - name: 用 source_stem (文件名) 作为 slug, 因为 name 不支持中文
    - description: 用中文标题/摘要
    """
    import re as _re
    title = title or "untitled"
    safe_title = title.replace('"', '\\"')[:100]
    # TDAM skill name 必须匹配 ^[a-z0-9][a-z0-9-]*$, 不能用中文
    # 优先用 source_stem (文件名), 因为文件名通常是英文 slug
    if source_stem:
        slug = _re.sub(r'[^a-z0-9-]', '-', source_stem.lower()).strip('-')
    else:
        slug = _re.sub(r'[^a-z0-9-]', '-', title.lower()).strip('-')
    if not slug or not slug[0].isalnum():
        slug = f"cairn-skill-{abs(hash(title)) % 100000}"
    slug = slug[:64]
    summary = extract_summary(content, max_chars=200)
    safe_summary = (summary or title).replace('"', '\\"').replace('\n', ' ')[:200]

    # 已有 frontmatter: 检查是否含 name 和 description 字段
    if content.startswith("---\n"):
        # 找到 frontmatter 结束位置
        end_idx = content.find("\n---\n", 4)
        if end_idx > 0:
            fm = content[4:end_idx]
            body = content[end_idx + 5:]
            # 检查缺失的字段并插入 (name 用 slug, description 用中文标题)
            insert_lines = []
            if "\nname:" not in fm and not fm.startswith("name:"):
                insert_lines.append(f'name: "{slug}"')
            if "\ndescription:" not in fm and not fm.startswith("description:"):
                insert_lines.append(f'description: "{safe_summary}"')
            if insert_lines:
                new_fm = "\n".join(insert_lines) + "\n" + fm
                # 去掉正文中的 H1 标题 (TDAM 校验 frontmatter.name != body.name 时会冲突)
                body = _re.sub(r'^#\s+.+\n', '', body, count=1, flags=_re.MULTILINE)
                return f'---\n{new_fm}\n---\n{body}'
            return content  # name 和 description 都已有, 无需修改

    # 无 frontmatter: 添加完整的 (name 用 slug, description 用中文标题)
    # 同时去掉正文中的 H1 标题 (TDAM 校验 frontmatter.name != body.name 时会冲突)
    body = _re.sub(r'^#\s+.+\n', '', content, count=1, flags=_re.MULTILINE)
    return f'---\nname: "{slug}"\ndescription: "{safe_summary}"\n---\n{body}'


def extract_title(content: str, fallback: str) -> str:
    """从 Markdown 内容提取第一个 H1 标题, 无则用 fallback."""
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def extract_summary(content: str, max_chars: int = 200) -> str:
    """提取摘要 (第一个非标题非空行的前 max_chars 字符)."""
    for line in content.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("---"):
            return line[:max_chars]
    return ""


def parse_log_entries(log_content: str) -> list[dict]:
    """解析 LOG.md 为条目列表 (按 ## 分割)."""
    entries = []
    # 按 ## 标题分割
    blocks = re.split(r"^## ", log_content, flags=re.MULTILINE)
    for block in blocks[1:]:  # 跳过头部
        lines = block.strip().split("\n")
        title = lines[0].strip() if lines else ""
        body = "\n".join(lines[1:]).strip()
        if title and body:
            entries.append({
                "title": title,
                "content": body,
                "summary": extract_summary(body),
                "doc_type": "log_entry",
            })
    return entries


def export_cairn() -> dict:
    """导出 cairn/ 目录为 TDAM 可导入格式.

    Returns:
        {
            "export_timestamp": "...",
            "source": "cairn/",
            "total_documents": N,
            "documents": [...],
        }
    """
    if not CAIRN_DIR.exists():
        logger.error("cairn/ 目录不存在: %s", CAIRN_DIR)
        return {"error": "cairn_dir_not_found", "documents": []}

    documents: list[dict] = []

    # 1. LOG.md → chat_memory
    log_path = CAIRN_DIR / "LOG.md"
    if log_path.exists():
        logger.info("解析 LOG.md...")
        log_content = log_path.read_text(encoding="utf-8")
        log_entries = parse_log_entries(log_content)
        for i, entry in enumerate(log_entries):
            documents.append({
                "id": f"cairn_log_{i:04d}",
                "title": entry["title"],
                "content": entry["content"],
                "summary": entry["summary"],
                "source_path": "cairn/LOG.md",
                "asset_type": "chat_memory",
                "layer": "L0",
                "timestamp": datetime.now().isoformat(),
            })
        logger.info("LOG.md: 导出 %d 条条目", len(log_entries))

    # 2. 专题文档 → wiki
    md_files = sorted(CAIRN_DIR.glob("*.md"))
    md_files = [f for f in md_files if f.name != "LOG.md"]
    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        title = extract_title(content, md_file.stem)
        # TDAM skill 要求 YAML frontmatter, 否则返回 42203 SKILL_FRONTMATTER_INVALID
        content = _add_frontmatter(title, content, source_stem=md_file.stem)
        documents.append({
            "id": f"cairn_wiki_{md_file.stem}",
            "title": title,
            "content": content,
            "summary": extract_summary(content),
            "source_path": f"cairn/{md_file.name}",
            "asset_type": "wiki",
            "layer": "L2",
            "timestamp": datetime.now().isoformat(),
        })
    logger.info("专题文档: 导出 %d 个", len(md_files))

    # 3. Reference/ 子目录
    ref_dir = CAIRN_DIR / "Reference"
    if ref_dir.exists():
        ref_files = sorted(ref_dir.glob("*.md"))
        for md_file in ref_files:
            content = md_file.read_text(encoding="utf-8")
            title = extract_title(content, md_file.stem)
            content = _add_frontmatter(title, content, source_stem=md_file.stem)
            documents.append({
                "id": f"cairn_ref_{md_file.stem}",
                "title": extract_title(content, md_file.stem),
                "content": content,
                "summary": extract_summary(content),
                "source_path": f"cairn/Reference/{md_file.name}",
                "asset_type": "wiki",
                "layer": "L1",
                "timestamp": datetime.now().isoformat(),
            })
        logger.info("Reference/: 导出 %d 个", len(ref_files))

    export_data = {
        "export_timestamp": datetime.now().isoformat(),
        "source": "cairn/",
        "total_documents": len(documents),
        "documents": documents,
        "stats": {
            "chat_memory_count": sum(1 for d in documents if d["asset_type"] == "chat_memory"),
            "wiki_count": sum(1 for d in documents if d["asset_type"] == "wiki"),
        },
    }

    return export_data


def main() -> None:
    """主入口."""
    parser = argparse.ArgumentParser(description="导出 cairn/ 为 TDAM 可导入格式")
    parser.add_argument(
        "--output", "-o",
        default=str(OUTPUT_DEFAULT),
        help=f"输出文件路径 (默认: {OUTPUT_DEFAULT})",
    )
    parser.add_argument(
        "--preview", action="store_true",
        help="仅预览统计, 不写文件",
    )
    args = parser.parse_args()

    logger.info("开始导出 cairn/ 目录...")
    export_data = export_cairn()

    if "error" in export_data:
        logger.error("导出失败: %s", export_data["error"])
        return

    stats = export_data.get("stats", {})
    logger.info("=== 导出统计 ===")
    logger.info("总文档数: %d", export_data["total_documents"])
    logger.info("Chat Memory (L0): %d", stats.get("chat_memory_count", 0))
    logger.info("Wiki (L1/L2): %d", stats.get("wiki_count", 0))

    if args.preview:
        logger.info("(--preview 模式, 不写文件)")
        # 打印前 5 个文档标题
        for doc in export_data["documents"][:5]:
            logger.info("  [%s] %s", doc["asset_type"], doc["title"])
        if export_data["total_documents"] > 5:
            logger.info("  ... (共 %d 个)", export_data["total_documents"])
        return

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(export_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("已写入: %s (%.1f KB)", output_path, output_path.stat().st_size / 1024)


if __name__ == "__main__":
    main()
