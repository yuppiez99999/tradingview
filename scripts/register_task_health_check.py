#!/usr/bin/env python3
"""注册 V84_TaskHealthCheck 计划任务 (每日 09:05 任务健康检查告警).

来源: 2026-09-01 P0-1 后续 — 晨报任务静默失败 6 天无告警。
用 pywin32 COM (Schedule.Service) 注册, 规避 schtasks XML UTF-16/中文路径坑
与 PowerShell CIM 模块异常 (两者均在 2026-09-01 P0 批次修复中踩过)。

用法:
    .venv\\Scripts\\python.exe scripts\\register_task_health_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

TASK_NAME = "V84_TaskHealthCheck"
PYTHON_EXE = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
SCRIPT_ARG = "scripts\\check_scheduled_tasks_health.py"


def main() -> int:
    import win32com.client  # noqa: PLC0415

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    service = win32com.client.Dispatch("Schedule.Service")
    service.Connect()
    root = service.GetFolder("\\")

    # 已存在则删除重建 (幂等)
    try:
        root.DeleteTask(TASK_NAME, 0)
        print(f"[register] 已删除旧任务 {TASK_NAME}")
    except Exception:  # pywin32 COM 错误类型未装时不可静态导入, 任务不存在时忽略
        pass

    # XML 直接经 COM BSTR 传入 (无 schtasks /create 读文件的 UTF-16 编码坑;
    # Task Scheduler XML schema: https://learn.microsoft.com/windows/win32/taskschd/task-scheduler-schema)
    def _xml_escape(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    task_xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>v84 计划任务健康检查: FAILED/STALE 判定 + 钉钉/飞书告警 + 快照落盘 (2026-09-01 代码质量扫描 P0-1 防复发)</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-09-02T09:05:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
    <RestartOnFailure>
      <Interval>PT5M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{_xml_escape(PYTHON_EXE)}</Command>
      <Arguments>{_xml_escape(SCRIPT_ARG)}</Arguments>
      <WorkingDirectory>{_xml_escape(str(PROJECT_ROOT))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""

    # 6=TASK_CREATE_OR_UPDATE, 3=TASK_LOGON_INTERACTIVE_TOKEN (当前用户交互登录)
    root.RegisterTask(TASK_NAME, task_xml, 6, None, None, 3, None)
    print(f"[register] ✅ {TASK_NAME} 注册成功: 每日 09:05, {PYTHON_EXE} {SCRIPT_ARG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
