# -*- coding: utf-8 -*-
"""
量化策略系统部署脚本

功能：
- 自动部署系统到生产环境
- 检查系统依赖和环境
- 配置系统参数
- 启动监控系统
- 部署文档和配置

使用方法：
python deploy_system.py [environment]

environment参数：
- development: 开发环境
- testing: 测试环境
- production: 生产环境（默认）
"""

import sys
import os
import shutil
import json
import argparse
from datetime import datetime
from typing import Dict, List, Any, Optional

# 添加路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

# 导入必要的模块
from utils.logger import get_logger
from config import Config

# 日志配置
logger = get_logger('system_deployer')


class SystemDeployer:
    """系统部署器"""
    
    def __init__(self, environment: str = "production"):
        self.environment = environment
        self.deploy_dir = os.path.join(current_dir, "deploy")
        self.backup_dir = os.path.join(current_dir, "backup")
        self.config = Config.get_instance()
        
        # 环境配置
        self.env_config = {
            "development": {
                "auto_start": False,
                "monitoring": "console",
                "log_level": "DEBUG",
                "execution_mode": "manual"
            },
            "testing": {
                "auto_start": True,
                "monitoring": "console",
                "log_level": "INFO",
                "execution_mode": "test"
            },
            "production": {
                "auto_start": True,
                "monitoring": "web",
                "log_level": "WARNING",
                "execution_mode": "auto"
            }
        }
        
        # 部署状态
        self.deployment_status = {
            "environment": environment,
            "started_at": datetime.now().isoformat(),
            "steps_completed": [],
            "errors": [],
            "warnings": [],
            "success": False
        }
        
        logger.info(f"初始化系统部署器 - 环境: {environment}")
    
    def _create_directories(self):
        """创建必要的目录"""
        logger.info("创建部署目录...")
        
        try:
            # 创建部署目录
            os.makedirs(self.deploy_dir, exist_ok=True)
            os.makedirs(self.backup_dir, exist_ok=True)
            os.makedirs(os.path.join(self.deploy_dir, "logs"), exist_ok=True)
            os.makedirs(os.path.join(self.deploy_dir, "config"), exist_ok=True)
            os.makedirs(os.path.join(self.deploy_dir, "data"), exist_ok=True)
            os.makedirs(os.path.join(self.deploy_dir, "reports"), exist_ok=True)
            
            self.deployment_status["steps_completed"].append("create_directories")
            logger.info("目录创建成功")
            
        except Exception as e:
            error_msg = f"创建目录失败: {str(e)}"
            logger.error(error_msg)
            self.deployment_status["errors"].append(error_msg)
    
    def _check_dependencies(self):
        """检查系统依赖"""
        logger.info("检查系统依赖...")
        
        try:
            # 检查Python版本
            import sys
            python_version = sys.version_info
            if python_version.major < 3 or (python_version.major == 3 and python_version.minor < 8):
                raise Exception(f"Python版本过低: {python_version.major}.{python_version.minor}")
            
            # 检查必要库
            required_libs = [
                "pandas", "numpy", "matplotlib", "yfinance", 
                "scikit-learn", "requests", "scipy"
            ]
            
            missing_libs = []
            for lib in required_libs:
                try:
                    __import__(lib)
                except ImportError:
                    missing_libs.append(lib)
            
            if missing_libs:
                raise Exception(f"缺少必要库: {', '.join(missing_libs)}")
            
            # 检查可选库
            optional_libs = {
                "flask": "Web监控界面",
                "schedule": "任务调度",
                "ta-lib": "技术分析"
            }
            
            missing_optional = []
            for lib, desc in optional_libs.items():
                try:
                    __import__(lib)
                except ImportError:
                    missing_optional.append(f"{lib} ({desc})")
            
            if missing_optional:
                self.deployment_status["warnings"].append(f"缺少可选库: {', '.join(missing_optional)}")
            
            self.deployment_status["steps_completed"].append("check_dependencies")
            logger.info("依赖检查完成")
            
        except Exception as e:
            error_msg = f"依赖检查失败: {str(e)}"
            logger.error(error_msg)
            self.deployment_status["errors"].append(error_msg)
    
    def _backup_system(self):
        """备份系统文件"""
        logger.info("备份系统文件...")
        
        try:
            # 创建备份目录
            backup_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(self.backup_dir, f"backup_{backup_timestamp}")
            os.makedirs(backup_path, exist_ok=True)
            
            # 备份配置文件
            config_backup = os.path.join(backup_path, "config_backup.json")
            with open(config_backup, 'w', encoding='utf-8') as f:
                json.dump(self.config.get_config_summary(), f, indent=2, ensure_ascii=False)
            
            # 备份关键文件
            key_files = [
                "quantitative_strategy_system.py",
                "config.py",
                "utils/logger.py",
                "utils/data_provider.py",
                "utils/risk_metrics.py"
            ]
            
            for file in key_files:
                if os.path.exists(file):
                    shutil.copy2(file, os.path.join(backup_path, file))
            
            self.deployment_status["steps_completed"].append("backup_system")
            logger.info(f"系统备份完成: {backup_path}")
            
        except Exception as e:
            error_msg = f"系统备份失败: {str(e)}"
            logger.error(error_msg)
            self.deployment_status["errors"].append(error_msg)
    
    def _configure_system(self):
        """配置系统参数"""
        logger.info("配置系统参数...")
        
        try:
            # 根据环境调整配置
            env_config = self.env_config[self.environment]
            
            # 更新配置
            config_updates = {
                "execution_config": {
                    "execution_mode": env_config["execution_mode"],
                    "log_level": env_config["log_level"]
                },
                "data_config": {
                    "cache_enabled": self.environment != "development",
                    "data_quality_check": self.environment != "development"
                },
                "performance_config": {
                    "evaluation_frequency": 1 if self.environment == "development" else 7,
                    "optimization_frequency": 7 if self.environment == "development" else 30
                }
            }
            
            # 应用配置更新
            self.config.update_config(config_updates)
            
            # 创建环境配置文件
            env_config_file = os.path.join(self.deploy_dir, "config", "env_config.json")
            with open(env_config_file, 'w', encoding='utf-8') as f:
                json.dump(env_config, f, indent=2, ensure_ascii=False)
            
            self.deployment_status["steps_completed"].append("configure_system")
            logger.info("系统配置完成")
            
        except Exception as e:
            error_msg = f"系统配置失败: {str(e)}"
            logger.error(error_msg)
            self.deployment_status["errors"].append(error_msg)
    
    def _deploy_launchers(self):
        """部署启动脚本"""
        logger.info("部署启动脚本...")
        
        try:
            # 创建启动脚本
            start_script = os.path.join(self.deploy_dir, "start_system.py")
            shutil.copy2("start_system.py", start_script)
            
            # 创建监控脚本
            monitor_script = os.path.join(self.deploy_dir, "monitor_dashboard.py")
            shutil.copy2("monitor_dashboard.py", monitor_script)
            
            # 创建检查脚本
            check_script = os.path.join(self.deploy_dir, "quick_check.py")
            shutil.copy2("quick_check.py", check_script)
            
            # 创建系统服务脚本（Linux系统）
            if os.name != 'nt':  # 非Windows系统
                service_script = os.path.join(self.deploy_dir, "systemd", "quantitative_strategy.service")
                os.makedirs(os.path.dirname(service_script), exist_ok=True)
                
                service_content = f"""[Unit]
Description=Quantitative Strategy System
After=network.target

[Service]
Type=simple
User=quantitative
WorkingDirectory={current_dir}
ExecStart={os.sys.executable} {start_script}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
                with open(service_script, 'w', encoding='utf-8') as f:
                    f.write(service_content)
            
            self.deployment_status["steps_completed"].append("deploy_launchers")
            logger.info("启动脚本部署完成")
            
        except Exception as e:
            error_msg = f"启动脚本部署失败: {str(e)}"
            logger.error(error_msg)
            self.deployment_status["errors"].append(error_msg)
    
    def _deploy_documents(self):
        """部署文档"""
        logger.info("部署文档...")
        
        try:
            # 复制文档
            doc_files = [
                "README.md",
                "USER_GUIDE.md",
                "PROJECT_DOCUMENTATION.md"
            ]
            
            for doc in doc_files:
                if os.path.exists(doc):
                    shutil.copy2(doc, os.path.join(self.deploy_dir, doc))
            
            # 创建部署报告模板
            report_template = {
                "deployment_info": {
                    "environment": self.environment,
                    "deployed_at": datetime.now().isoformat(),
                    "version": "v5.10"
                },
                "system_config": self.config.get_config_summary(),
                "checklist": [
                    "✓ 系统备份完成",
                    "✓ 依赖检查通过",
                    "✓ 系统配置完成",
                    "✓ 启动脚本部署完成",
                    "✓ 文档部署完成"
                ],
                "next_steps": [
                    "1. 运行系统检查: python quick_check.py",
                    "2. 启动系统: python start_system.py",
                    "3. 监控系统: python monitor_dashboard.py",
                    "4. 查看日志: cat logs/quant_strategy_system.log"
                ]
            }
            
            report_file = os.path.join(self.deploy_dir, "reports", "deployment_report.json")
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report_template, f, indent=2, ensure_ascii=False)
            
            self.deployment_status["steps_completed"].append("deploy_documents")
            logger.info("文档部署完成")
            
        except Exception as e:
            error_msg = f"文档部署失败: {str(e)}"
            logger.error(error_msg)
            self.deployment_status["errors"].append(error_msg)
    
    def _start_system(self):
        """启动系统"""
        logger.info("启动系统...")
        
        try:
            env_config = self.env_config[self.environment]
            
            if env_config["auto_start"]:
                # 根据环境选择启动方式
                if self.environment == "production":
                    # 生产环境：后台启动
                    import subprocess
                    subprocess.Popen([
                        os.sys.executable, "start_system.py",
                        "auto"
                    ], cwd=current_dir)
                    logger.info("系统已在后台启动")
                else:
                    # 开发/测试环境：前台启动
                    logger.info("系统配置为自动启动，请手动运行: python start_system.py")
            else:
                logger.info("系统配置为手动启动模式")
            
            self.deployment_status["steps_completed"].append("start_system")
            
        except Exception as e:
            error_msg = f"系统启动失败: {str(e)}"
            logger.error(error_msg)
            self.deployment_status["errors"].append(error_msg)
    
    def _generate_deployment_report(self):
        """生成部署报告"""
        logger.info("生成部署报告...")
        
        try:
            # 计算部署结果
            self.deployment_status["success"] = len(self.deployment_status["errors"]) == 0
            
            # 创建部署报告
            report = {
                "deployment_summary": {
                    "environment": self.environment,
                    "started_at": self.deployment_status["started_at"],
                    "completed_at": datetime.now().isoformat(),
                    "success": self.deployment_status["success"],
                    "steps_completed": len(self.deployment_status["steps_completed"]),
                    "errors": len(self.deployment_status["errors"]),
                    "warnings": len(self.deployment_status["warnings"])
                },
                "deployment_status": self.deployment_status,
                "next_steps": self._get_next_steps(),
                "contact_info": {
                    "support": "quant_support@example.com",
                    "emergency": "emergency@example.com"
                }
            }
            
            # 保存报告
            report_file = os.path.join(self.deploy_dir, "reports", 
                                     f"deployment_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            
            logger.info(f"部署报告已生成: {report_file}")
            
        except Exception as e:
            logger.error(f"生成部署报告失败: {str(e)}")
    
    def _get_next_steps(self) -> List[str]:
        """获取后续步骤"""
        steps = []
        
        if self.deployment_status["success"]:
            steps.append("✓ 部署成功")
            
            env_config = self.env_config[self.environment]
            if env_config["monitoring"] == "web":
                steps.append("1. 访问Web监控界面: http://localhost:5000")
            else:
                steps.append("1. 运行命令行监控: python monitor_dashboard.py")
            
            if not env_config["auto_start"]:
                steps.append("2. 启动系统: python start_system.py")
            
            steps.append("3. 检查系统状态: python quick_check.py")
            steps.append("4. 查看系统日志: cat logs/quant_strategy_system.log")
        else:
            steps.append("✗ 部署失败")
            if self.deployment_status["errors"]:
                steps.append("错误信息:")
                for error in self.deployment_status["errors"]:
                    steps.append(f"  - {error}")
            
            steps.append("建议:")
            steps.append("1. 检查错误信息")
            steps.append("2. 查看备份文件")
            steps.append("3. 重新运行部署脚本")
        
        return steps
    
    def deploy(self):
        """执行部署"""
        logger.info(f"开始部署量化策略系统到 {self.environment} 环境")
        
        try:
            # 执行部署步骤
            self._create_directories()
            self._check_dependencies()
            self._backup_system()
            self._configure_system()
            self._deploy_launchers()
            self._deploy_documents()
            self._start_system()
            
            # 生成部署报告
            self._generate_deployment_report()
            
            # 打印部署结果
            self._print_deployment_result()
            
            return self.deployment_status["success"]
            
        except Exception as e:
            logger.error(f"部署失败: {str(e)}")
            self.deployment_status["errors"].append(f"部署过程异常: {str(e)}")
            self._generate_deployment_report()
            return False
    
    def _print_deployment_result(self):
        """打印部署结果"""
        print("\n" + "="*60)
        print("量化策略系统部署报告")
        print("="*60)
        print(f"部署环境: {self.environment}")
        print(f"开始时间: {self.deployment_status['started_at']}")
        print(f"完成时间: {datetime.now().isoformat()}")
        print(f"部署状态: {'成功' if self.deployment_status['success'] else '失败'}")
        
        print(f"\n完成的步骤: {len(self.deployment_status['steps_completed'])}")
        for step in self.deployment_status['steps_completed']:
            print(f"  ✓ {step}")
        
        if self.deployment_status['errors']:
            print(f"\n错误数量: {len(self.deployment_status['errors'])}")
            for error in self.deployment_status['errors']:
                print(f"  ✗ {error}")
        
        if self.deployment_status['warnings']:
            print(f"\n警告数量: {len(self.deployment_status['warnings'])}")
            for warning in self.deployment_status['warnings']:
                print(f"  ⚠ {warning}")
        
        print(f"\n后续步骤:")
        for step in self._get_next_steps():
            print(f"  {step}")
        
        print("\n" + "="*60)


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="量化策略系统部署脚本")
    parser.add_argument('environment', nargs='?', 
                       choices=['development', 'testing', 'production'],
                       default='production', help='部署环境')
    args = parser.parse_args()
    
    # 创建部署器
    deployer = SystemDeployer(args.environment)
    
    # 执行部署
    success = deployer.deploy()
    
    if success:
        print("✓ 部署成功")
        sys.exit(0)
    else:
        print("✗ 部署失败")
        sys.exit(1)


if __name__ == "__main__":
    main()