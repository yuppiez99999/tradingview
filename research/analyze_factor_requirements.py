import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
zoo = (
    PROJECT_ROOT
    / "research"
    / "references"
    / "Vibe-Trading"
    / "agent"
    / "src"
    / "factors"
    / "zoo"
)

for zoo_name in ["alpha101", "fundamental"]:
    zoo_dir = zoo / zoo_name
    files = [f for f in zoo_dir.glob("*.py") if not f.name.startswith("_")]
    print(f"\n=== {zoo_name} ({len(files)} files) ===")
    col_counts = {}
    requires_sector = 0
    requires_fund = 0
    fund_details = []
    for py in sorted(files):
        src = py.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
            for stmt in tree.body:
                if isinstance(stmt, ast.Assign):
                    for t in stmt.targets:
                        if isinstance(t, ast.Name) and t.id == "__alpha_meta__":
                            meta = ast.literal_eval(stmt.value)
                            cols = tuple(sorted(meta.get("columns_required", [])))
                            col_counts[cols] = col_counts.get(cols, 0) + 1
                            if meta.get("requires_sector"):
                                requires_sector += 1
                            extras = meta.get("extras_required", [])
                            if extras:
                                requires_fund += 1
                                fund_details.append((py.stem, extras))
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            print(f"  ERROR {py.name}: {e}")
    print("需要的数据列组合:")
    for cols, cnt in sorted(col_counts.items(), key=lambda x: -x[1]):
        print(f"  {cnt:3d} 个需要: {list(cols)}")
    print(f"需要 sector: {requires_sector}")
    print(f"需要 extras/fundamental: {requires_fund}")
    if fund_details:
        print("基本面数据详情:")
        for name, extras in fund_details:
            print(f"  {name}: {extras}")
