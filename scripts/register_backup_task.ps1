# 注册 EOD_Backup 计划任务 (Production Edition T4)
# 17:30 交易日运行 (EOD 链尾: 16:30 shadow -> 17:05 评分 -> 17:10 报告 -> 17:30 备份)
# 用 schtasks 避开 CIM 层异常 (同 System_HealthScore 注册模式)
$exe = 'E:\各种PY程序\28-终极量化交易系统8.4\.venv\Scripts\python.exe'
$script = 'E:\各种PY程序\28-终极量化交易系统8.4\scripts\run_eod_backup.py'
$tr = '"' + $exe + '" "' + $script + '" backup'
schtasks /Create /TN "EOD_Backup" /TR $tr /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 17:30 /F
schtasks /Query /TN "EOD_Backup" /FO LIST
