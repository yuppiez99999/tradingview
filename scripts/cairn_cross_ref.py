"""
cairn 知识层自动交叉引用生成器 (GH+-2)

为 cairn/ 目录下所有 .md 文档生成底部"相关文档"交叉引用区块。
基于关键词 + TF-IDF 相似度匹配，纯本地计算，无外部依赖。

用法:
    python scripts/cairn_cross_ref.py           # 生成所有文档的交叉引用
    python scripts/cairn_cross_ref.py --check   # 仅检查，不写入
    python scripts/cairn_cross_ref.py --top 5   # 每篇文档关联数量 (默认 5)
"""

import argparse
import math
import re
from collections import Counter
from pathlib import Path

CAIRN_DIR = Path(__file__).resolve().parent.parent / "cairn"
SECTION_MARKER = "<!-- AUTO-GENERATED: 相关文档 -->"
EXCLUDE_FILES = {
    "LOG.md", "Cited.md", "TODO_from_ROADMAP.md",
    "KNOWLEDGE_DIGEST.md", "ROADMAP.md",
    "SYSTEM_QUALITY_SCAN_20260803.md",
}
EXCLUDE_DIRS = {"Reference", ".codeartsdoer"}


def extract_title_and_keywords(text: str, filename: str) -> tuple[str, list[str]]:
    """从 Markdown 提取标题和关键词 (H1/H2 标题 + 代码块外的专有名词)。"""
    # 移除代码块
    clean = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    clean = re.sub(r"`[^`]+`", "", clean)

    # 提取标题
    h1_match = re.search(r"^#\s+(.+)$", clean, re.MULTILINE)
    title = h1_match.group(1).strip() if h1_match else Path(filename).stem

    # 提取 H2 标题作为关键词
    h2_titles = re.findall(r"^##\s+(.+)$", clean, re.MULTILINE)

    # 提取专有名词 (2+ 个中文字符组成的词组, 或英文驼峰/下划线标识符)
    cn_phrases = re.findall(r"[\u4e00-\u9fff]{2,}(?:[\u4e00-\u9fff0-9]{1,})?", clean)
    en_terms = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", clean)

    # 统计词频
    cn_counter = Counter(w for w in cn_phrases if len(w) >= 2)
    en_counter = Counter(w.lower() for w in en_terms if len(w) >= 4)

    # 组合关键词: H2标题 + 高频中文词 + 高频英文术语
    keywords = []
    for h2 in h2_titles[:5]:
        keywords.append(h2.strip())
    for word, _ in cn_counter.most_common(15):
        keywords.append(word)
    for word, _ in en_counter.most_common(10):
        keywords.append(word)

    return title, keywords


def tokenize(text: str) -> list[str]:
    """简单分词: 中文按 2-gram, 英文按单词。"""
    tokens = []
    # 移除代码块
    clean = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    clean = re.sub(r"`[^`]+`", "", clean)

    # 英文单词
    en_words = re.findall(r"\b[a-z][a-z0-9_]{2,}\b", clean.lower())
    tokens.extend(en_words)

    # 中文 2-gram (跳过常见停用字)
    cn_chars = re.findall(r"[\u4e00-\u9fff]", clean)
    stop = set("的了是在和有我不这就也都还于与及等中将对从被使到要可如或而其一个上下前后")
    for i in range(len(cn_chars) - 1):
        gram = cn_chars[i] + cn_chars[i + 1]
        if cn_chars[i] not in stop and cn_chars[i + 1] not in stop:
            tokens.append(gram)

    return tokens


def compute_tfidf(docs: dict[str, list[str]]) -> dict[str, dict[str, float]]:
    """计算 TF-IDF 向量。"""
    # DF: 每个 token 出现在多少文档中
    df = Counter()
    for tokens in docs.values():
        df.update(set(tokens))

    n_docs = len(docs)
    idf = {tok: math.log(n_docs / (1 + freq)) for tok, freq in df.items()}

    # TF-IDF 向量
    vectors = {}
    for name, tokens in docs.items():
        tf = Counter(tokens)
        total = len(tokens) if tokens else 1
        vec = {tok: (count / total) * idf.get(tok, 0) for tok, count in tf.items()}
        # 归一化
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            vec = {k: v / norm for k, v in vec.items()}
        vectors[name] = vec

    return vectors


def cosine_similarity(v1: dict[str, float], v2: dict[str, float]) -> float:
    """余弦相似度。"""
    common = set(v1.keys()) & set(v2.keys())
    return sum(v1[k] * v2[k] for k in common)


def find_related(
    vectors: dict[str, dict[str, float]],
    titles: dict[str, str],
    target: str,
    top_n: int = 5,
) -> list[tuple[str, str, float]]:
    """找出最相关的文档。"""
    scores = []
    for name, vec in vectors.items():
        if name == target:
            continue
        sim = cosine_similarity(vectors[target], vec)
        if sim > 0.02:  # 阈值过滤
            scores.append((name, titles.get(name, name), sim))
    scores.sort(key=lambda x: -x[2])
    return scores[:top_n]


def strip_existing_section(text: str) -> str:
    """移除已有的自动生成区块。"""
    pattern = rf"\n*{re.escape(SECTION_MARKER)}.*"
    return re.sub(pattern, "", text, flags=re.DOTALL).rstrip() + "\n"


def build_section(related: list[tuple[str, str, float]]) -> str:
    """构建相关文档区块。"""
    lines = [
        "",
        SECTION_MARKER,
        "## 相关文档",
        "",
    ]
    if not related:
        lines.append("_暂无显著相关的文档_")
    else:
        for filename, title, score in related:
            lines.append(f"- [{title}]({filename}) (相似度 {score:.0%})")
    lines.append("")
    lines.append("<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="cairn 知识层交叉引用生成器")
    parser.add_argument("--check", action="store_true", help="仅检查，不写入")
    parser.add_argument("--top", type=int, default=5, help="每篇关联文档数")
    args = parser.parse_args()

    # 收集文档
    docs_text = {}
    for md_file in sorted(CAIRN_DIR.glob("*.md")):
        if md_file.name in EXCLUDE_FILES:
            continue
        if any(part in EXCLUDE_DIRS for part in md_file.parts):
            continue
        docs_text[md_file.name] = md_file.read_text(encoding="utf-8")

    print(f"共读取 {len(docs_text)} 篇文档")

    # 提取标题和分词
    titles = {}
    docs_tokens = {}
    for name, text in docs_text.items():
        title, _ = extract_title_and_keywords(text, name)
        titles[name] = title
        docs_tokens[name] = tokenize(text)

    # 计算 TF-IDF
    vectors = compute_tfidf(docs_tokens)

    # 为每篇文档生成相关引用
    updated = 0
    skipped = 0
    for name, text in docs_text.items():
        related = find_related(vectors, titles, name, top_n=args.top)
        old_text = strip_existing_section(text)
        new_text = old_text.rstrip() + "\n" + build_section(related)

        if new_text == text:
            skipped += 1
            continue

        if args.check:
            print(f"  [DRY-RUN] {name}: {len(related)} 篇相关")
            for _fn, t, s in related:
                print(f"    - {t} ({s:.1%})")
        else:
            (CAIRN_DIR / name).write_text(new_text, encoding="utf-8")
            updated += 1
            print(f"  [UPDATE] {name}: {len(related)} 篇相关文档")

    if args.check:
        print(f"\n检查完成: {len(docs_text)} 篇文档, {skipped} 篇无需更新")
    else:
        print(f"\n完成: 更新 {updated} 篇, 跳过 {skipped} 篇 (共 {len(docs_text)} 篇)")


if __name__ == "__main__":
    main()
