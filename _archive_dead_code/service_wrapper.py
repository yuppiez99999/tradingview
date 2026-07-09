#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows 服务化运行入口
======================
功能：
  - 将 auto_hedge_executor 包装为可后台常驻进程
  - 进程崩溃自动重启
  - 日志轮转
  - 优雅启停

用法：
  python service_wrapper.py start   # 后台启动
  python service_wrapper.py stop    # 停止
  python service_wrapper.py run     # 前台运行（调试）
  python service_wrapper.py install # 安装为 Windows 服务（需管理员）
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# 服务 PID 文件
PID_FILE = BASE_DIR / "service.pid"
# 主程序
MAIN_SCRIPT = BASE_DIR / "auto_hedge_executor.py"
# Python 解释器
PYTHON = sys.executable


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                LOG_DIR / f"service_{datetime.now():%Y%m%d}.log",
                encoding="utf-8",
            ),
        ],
    )
    return logging.getLogger("ServiceWrapper")


logger = setup_logging()


class ServiceController:
    """服务控制器：启动/停止/重启/守护"""

    def __init__(self):
        self.process: Optional[subprocess.Popen] = None

    def _write_pid(self, pid: int):
        PID_FILE.write_text(str(pid), encoding="utf-8")

    def _read_pid(self) -> Optional[int]:
        if PID_FILE.exists():
            try:
                return int(PID_FILE.read_text(encoding="utf-8").strip())
            except Exception:
                return None
        return None

    def _is_running(self, pid: Optional[int] = None) -> bool:
        pid = pid or self._read_pid()
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def start(self, run_once: bool = False, scenario: str = "normal") -> bool:
        if self._is_running():
            logger.warning(f"服务已在运行，PID={self._read_pid()}")
            return False

        cmd = [PYTHON, str(MAIN_SCRIPT)]
        if run_once:
            cmd += ["--once", "--scenario", scenario]
        else:
            cmd += ["--mode", "live"]

        logger.info(f"启动服务: {' '.join(cmd)}")
        self.process = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        self._write_pid(self.process.pid)
        logger.info(f"服务已启动，PID={self.process.pid}")
        return True

    def stop(self, timeout: int = 30) -> bool:
        pid = self._read_pid()
        if not pid or not self._is_running(pid):
            logger.info("服务未运行")
            if PID_FILE.exists():
                PID_FILE.unlink()
            return True

        logger.info(f"停止服务 PID={pid}")
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGTERM)
        except Exception:
            pass

        # 等待进程结束
        for _ in range(timeout):
            if not self._is_running(pid):
                logger.info("服务已停止")
                if PID_FILE.exists():
                    PID_FILE.unlink()
                return True
            time.sleep(1)

        # 强制终止
        try:
            os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
        except Exception:
            pass

        logger.warning("服务已强制终止")
        if PID_FILE.exists():
            PID_FILE.unlink()
        return True

    def run_forever(self, run_once: bool = False, scenario: str = "normal", restart_delay: int = 5):
        """前台运行，带守护重启"""
        logger.info("服务启动（前台守护模式）")
        while True:
            try:
                if not self.start(run_once=run_once, scenario=scenario):
                    time.sleep(restart_delay)
                    continue

                return_code = self.process.wait()
                logger.warning(f"服务进程退出，code={return_code}，{restart_delay}秒后重启...")
                time.sleep(restart_delay)
            except KeyboardInterrupt:
                logger.info("收到中断信号，停止服务...")
                self.stop()
                break
            except Exception as e:
                logger.error(f"服务异常: {e}")
                time.sleep(restart_delay)


def main():
    parser = argparse.ArgumentParser(description="Windows 服务化运行入口")
    parser.add_argument("action", choices=["start", "stop", "restart", "run", "install", "uninstall"])
    parser.add_argument("--once", action="store_true", help="单次执行后退出")
    parser.add_argument("--scenario", default="normal", help="单次执行场景")
    args = parser.parse_args()

    controller = ServiceController()

    if args.action == "start":
        controller.start(run_once=args.once, scenario=args.scenario)
    elif args.action == "stop":
        controller.stop()
    elif args.action == "restart":
        controller.stop()
        time.sleep(2)
        controller.start(run_once=args.once, scenario=args.scenario)
    elif args.action == "run":
        controller.run_forever(run_once=args.once, scenario=args.scenario)
    elif args.action == "install":
        install_windows_service()
    elif args.action == "uninstall":
        uninstall_windows_service()


def install_windows_service():
    """安装为 Windows 服务（使用 nssm 或 sc.exe）"""
    service_name = "AutoHedgeExecutor"
    display_name = "Auto Hedge Executor"

    # 检查 nssm
    nssm_path = BASE_DIR / "tools" / "nssm.exe"
    if nssm_path.exists():
        logger.info("使用 nssm 安装服务...")
        subprocess.run([
            str(nssm_path), "install", service_name,
            PYTHON, str(BASE_DIR / "service_wrapper.py"), "run"
        ], check=True)
        subprocess.run([
            str(nssm_path), "set", service_name, "DisplayName", display_name
        ], check=True)
        subprocess.run([
            str(nssm_path), "set", service_name, "Start", "SERVICE_AUTO_START"
        ], check=True)
        logger.info(f"服务 {service_name} 安装成功")
        return

    # 回退到 sc.exe
    logger.info("使用 sc.exe 安装服务...")
    subprocess.run([
        "sc.exe", "create", service_name,
        "binPath=", f'"{PYTHON}" "{BASE_DIR / "service_wrapper.py"}" run',
        "start=", "auto",
        "DisplayName=", display_name,
    ], check=True)
    logger.info(f"服务 {service_name} 安装成功")


def uninstall_windows_service():
    """卸载 Windows 服务"""
    service_name = "AutoHedgeExecutor"
    subprocess.run(["sc.exe", "delete", service_name], check=True)
    logger.info(f"服务 {service_name} 已卸载")


if __name__ == "__main__":
    main()
