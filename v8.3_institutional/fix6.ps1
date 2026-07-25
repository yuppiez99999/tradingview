$script = @'
# -*- coding: utf-8 -*-
from pathlib import Path

p = Path(r"e:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional\daily_workflow.py")
text = p.read_text(encoding="utf-8")

old1 = '        report_path.write_text("\n".join(lines), encoding="utf-8")'
old2 = '        logger.info(f"报告已生成: {report_path}")'

idx1 = text.find(old1)
idx2 = text.find(old2, idx1) + len(old2)

if idx1 >= 0 and idx2 > idx1:
    new_block = """        # Report write with retry + fallback
        success = False
        for attempt in range(3):
            try:
                report_path.write_text("\\n".join(lines), encoding="utf-8")
                logger.info(f"报告已生成: {report_path}")
                success = True
                break
            except PermissionError:
                if attempt < 2:
                    import time
                    logger.warning(f"Report write permission denied, retry {attempt+1}/3...")
                    time.sleep(1)
                else:
                    logger.error(f"Report write failed ({attempt+1}/3): Permission denied")

        if not success:
            fallback_path = self.log_dir / report_path.name
            try:
                fallback_path.write_text("\\n".join(lines), encoding="utf-8")
                logger.warning(f"Report written to fallback: {fallback_path}")
            except Exception as e_fallback:
                logger.error(f"Fallback write also failed: {e_fallback}")"""
    
    text = text[:idx1] + new_block + text[idx2:]
    p.write_text(text, encoding="utf-8")
    print("Fix6 done")
else:
    print(f"ERROR: idx1={idx1}, idx2={idx2}")
'@

$pyPath = "e:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional\_fix6_inner.py"
[System.IO.File]::WriteAllText($pyPath, $script, (New-Object System.Text.UTF8Encoding $false))
& py -3 $pyPath
Remove-Item $pyPath -Force
