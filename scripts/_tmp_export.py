import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import _scan_system_quality as s

for d in ['utils', 'ai_decision', 'scripts']:
    target = ROOT / d
    files, viols = s.scan_directory(target)
    for v in viols:
        if v.category in ('assert_in_prod', 'sys_path_pollution'):
            print(f"{v.category}\t{v.file}:{v.line}\t{v.code}")
