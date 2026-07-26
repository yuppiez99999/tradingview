# -*- coding: utf-8 -*-
from pathlib import Path
import time

p = Path('models/pipeline_factor_signals')
for f in sorted(p.glob('*.json'), reverse=True)[:5]:
    mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(f.stat().st_mtime))
    print(f'{f.name}: mtime={mtime}, size={f.stat().st_size}')
