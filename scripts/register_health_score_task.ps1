# 注册 System_HealthScore 计划任务 (Production Edition T2)
# 17:05 交易日运行, 先于 17:10 状态报告; 用 schtasks 避开 CIM 层异常
$exe = 'E:\各种PY程序\28-终极量化交易系统8.4\.venv\Scripts\python.exe'
$script = 'E:\各种PY程序\28-终极量化交易系统8.4\scripts\compute_health_score.py'
$tr = '"' + $exe + '" "' + $script + '"'
schtasks /Create /TN "System_HealthScore" /TR $tr /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 17:05 /F
schtasks /Query /TN "System_HealthScore" /FO LIST
