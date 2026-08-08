import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import _scan_system_quality as s

for d in ['utils', 'ai_decision']:
    target = ROOT / d
    files, viols = s.scan_directory(target)
    print(f"==== {d} assert_in_prod ====")
    for v in viols:
        if v.category == 'assert_in_prod':
            print(f"{v.file}:{v.line}\t{v.code}")
