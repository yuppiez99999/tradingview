# -*- coding: utf-8 -*-
"""
量化策略系统 v5.10 安装脚本

功能：
- 自动安装依赖包
- 创建必要的目录
- 初始化配置文件
- 验证系统安装
"""

import sys
import os
import subprocess
import json
from pathlib import Path

# 添加当前路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

from utils.logger import get_logger

# 日志配置
logger = get_logger('setup')


class SetupManager:
    """安装管理器"""
    
    def __init__(self):
        self.current_dir = current_dir
        self.requirements_file = os.path.join(current_dir, "requirements.txt")
        self.setup_complete = False
        
        # 需要创建的目录
        self.required_dirs = [
            "deploy",
            "deploy/config",
            "deploy/logs",
            "deploy/data",
            "deploy/reports",
            "deploy/scripts",
            "models",
            "models/ml_models",
            "models/cache",
            "tests",
            "tests/fixtures",
            "docs",
            "docs/api",
            "docs/guides",
            "docs/examples",
            "docs/assets",
            "scripts",
            "backup",
            "templates",
            "utils"
        ]
    
    def _check_python_version(self):
        """检查Python版本"""
        logger.info("检查Python版本...")
        
        if sys.version_info < (3, 8):
            error_msg = f"Python版本过低，需要3.8+，当前版本: {sys.version}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        
        logger.info(f"Python版本检查通过: {sys.version}")
    
    def _install_dependencies(self):
        """安装依赖包"""
        logger.info("安装依赖包...")
        
        if not os.path.exists(self.requirements_file):
            logger.warning("requirements.txt文件不存在")
            return
        
        try:
            # 升级pip
            subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], 
                         check=True, capture_output=True)
            
            # 安装依赖
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", self.requirements_file],
                         check=True, capture_output=True)
            
            logger.info("依赖包安装完成")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"依赖安装失败: {e}")
            raise RuntimeError(f"依赖安装失败: {e}")
    
    def _create_directories(self):
        """创建必要的目录"""
        logger.info("创建系统目录...")
        
        for dir_path in self.required_dirs:
            full_path = os.path.join(self.current_dir, dir_path)
            os.makedirs(full_path, exist_ok=True)
            logger.debug(f"创建目录: {full_path}")
        
        logger.info("目录创建完成")
    
    def _initialize_config(self):
        """初始化配置文件"""
        logger.info("初始化配置文件...")
        
        # 创建默认配置
        config = {
            "system": {
                "version": "v5.10",
                "installed_at": str(Path(self.current_dir).stat().st_ctime),
                "python_version": sys.version
            },
            "paths": {
                "root": self.current_dir,
                "logs": os.path.join(self.current_dir, "deploy", "logs"),
                "data": os.path.join(self.current_dir, "deploy", "data"),
                "reports": os.path.join(self.current_dir, "deploy", "reports"),
                "models": os.path.join(self.current_dir, "models")
            }
        }
        
        # 保存配置
        config_file = os.path.join(self.current_dir, "setup_info.json")
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        logger.info("配置文件初始化完成")
    
    def _verify_installation(self):
        """验证安装"""
        logger.info("验证系统安装...")
        
        try:
            # 导入核心模块
            import pandas as pd
            import numpy as np
            import matplotlib.pyplot as plt
            import sklearn
            
            # 检查必要的文件
            required_files = [
                "quantitative_strategy_system.py",
                "config.py",
                "utils/logger.py",
                "utils/data_provider.py",
                "utils/risk_metrics.py"
            ]
            
            missing_files = []
            for file in required_files:
                if not os.path.exists(file):
                    missing_files.append(file)
            
            if missing_files:
                error_msg = f"缺少必要文件: {', '.join(missing_files)}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            
            logger.info("系统验证通过")
            return True
            
        except ImportError as e:
            logger.error(f"模块导入失败: {e}")
            return False
    
    def _create_scripts(self):
        """创建启动脚本"""
        logger.info("创建启动脚本...")
        
        # 创建启动脚本
        start_script = """#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
        
        start_script += f"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    try:
        from start_system import main
        main()
    except Exception as e:
        print(f"启动失败: {{e}}")
        sys.exit(1)
"""
        
        # 写入启动脚本
        start_file = os.path.join(self.current_dir, "start.py")
        with open(start_file, 'w', encoding='utf-8') as f:
            f.write(start_script)
        
        # 创建监控脚本
        monitor_script = """#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
        
        monitor_script += f"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    try:
        from monitor_dashboard import main
        main()
    except Exception as e:
        print(f"监控失败: {{e}}")
        sys.exit(1)
"""
        
        # 写入监控脚本
        monitor_file = os.path.join(self.current_dir, "monitor.py")
        with open(monitor_file, 'w', encoding='utf-8') as f:
            f.write(monitor_script)
        
        logger.info("启动脚本创建完成")
    
    def _post_install_info(self):
        """安装后信息"""
        logger.info("生成安装后信息...")
        
        info = {
            "安装成功": True,
            "安装时间": str(Path(self.current_dir).stat().st_ctime),
            "系统版本": "v5.10",
            "Python版本": sys.version,
            "建议操作": [
                "1. 运行系统检查: python quick_check.py",
                "2. 启动系统: python start.py",
                "3. 监控系统: python monitor.py",
                "4. 查看文档: README.md, USER_GUIDE.md"
            ],
            "联系支持": {
                "技术支持": "quant_support@example.com",
                "紧急联系": "emergency@example.com"
            }
        }
        
        # 保存安装信息
        info_file = os.path.join(self.current_dir, "install_info.json")
        with open(info_file, 'w', encoding='utf-8') as f:
            json.dump(info, f, indent=2, ensure_ascii=False)
        
        logger.info("安装信息生成完成")
    
    def install(self):
        """执行安装"""
        logger.info("开始安装量化策略系统 v5.10...")
        
        try:
            # 检查Python版本
            self._check_python_version()
            
            # 安装依赖
            self._install_dependencies()
            
            # 创建目录
            self._create_directories()
            
            # 初始化配置
            self._initialize_config()
            
            # 创建启动脚本
            self._create_scripts()
            
            # 验证安装
            if not self._verify_installation():
                logger.error("系统验证失败")
                return False
            
            # 安装后信息
            self._post_install_info()
            
            self.setup_complete = True
            logger.info("系统安装完成")
            
            # 打印安装结果
            self._print_install_result()
            
            return True
            
        except Exception as e:
            logger.error(f"安装失败: {e}")
            return False
    
    def _print_install_result(self):
        """打印安装结果"""
        print("\n" + "="*60)
        print("量化策略系统 v5.10 安装完成")
        print("="*60)
        
        if self.setup_complete:
            print("✓ 安装成功")
            print(f"系统版本: v5.10")
            print(f"Python版本: {sys.version}")
            print(f"安装目录: {self.current_dir}")
            
            print("\n后续步骤:")
            print("1. 运行系统检查: python quick_check.py")
            print("2. 启动系统: python start.py")
            print("3. 监控系统: python monitor.py")
            print("4. 查看文档: README.md")
            
            print("\n联系支持:")
            print("技术支持: quant_support@example.com")
            print("紧急联系: emergency@example.com")
        else:
            print("✗ 安装失败")
            print("请检查错误信息并重新安装")
        
        print("\n" + "="*60)


def main():
    """主函数"""
    # 创建安装管理器
    setup_manager = SetupManager()
    
    # 执行安装
    success = setup_manager.install()
    
    if success:
        print("✓ 安装成功")
        sys.exit(0)
    else:
        print("✗ 安装失败")
        sys.exit(1)


if __name__ == "__main__":
    main()