from pathlib import Path

path = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\update_qlib_signals.py")
text = path.read_text(encoding="utf-8")

if 'import re\n' not in text and 'import re\r\n' not in text:
    text = text.replace('import json\n', 'import json\nimport re\n', 1)
    path.write_text(text, encoding="utf-8")
    print("patched update_qlib_signals.py: added import re")
else:
    print("update_qlib_signals.py already has import re")
