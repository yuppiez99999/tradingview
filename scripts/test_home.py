# -*- coding: utf-8 -*-
import os
import platform


def _get_real_home():
    if platform.system() != 'Windows':
        return os.path.expanduser('~')

    # 方法1: USERPROFILE 环境变量
    home = os.environ.get('USERPROFILE', '')
    print(f"Method 1 USERPROFILE: {home}, isdir={os.path.isdir(home)}, system32={'system32' in home.lower()}")
    if home and os.path.isdir(home) and 'system32' not in home.lower():
        return home

    # 方法2: HOMEDRIVE + HOMEPATH
    drive = os.environ.get('HOMEDRIVE', '')
    path = os.environ.get('HOMEPATH', '')
    home = drive + path
    print(f"Method 2 HOMEDRIVE+HOMEPATH: {home}, isdir={os.path.isdir(home)}")
    if drive and path and os.path.isdir(home):
        return home

    # 方法3: 从 APPDATA 反推
    appdata = os.environ.get('APPDATA', '')
    if appdata:
        home = os.path.dirname(os.path.dirname(appdata))
        print(f"Method 3 APPDATA: {home}, isdir={os.path.isdir(home)}")
        if os.path.isdir(home):
            return home

    # 方法4: LOCALAPPDATA 反推
    local_appdata = os.environ.get('LOCALAPPDATA', '')
    if local_appdata:
        home = os.path.dirname(os.path.dirname(local_appdata))
        print(f"Method 4 LOCALAPPDATA: {home}, isdir={os.path.isdir(home)}")
        if os.path.isdir(home):
            return home

    # 最终回退
    return os.path.expanduser('~')

print(f"Platform: {platform.system()}")
print(f"USERPROFILE: {os.environ.get('USERPROFILE', '')}")
print(f"HOMEDRIVE: {os.environ.get('HOMEDRIVE', '')}")
print(f"HOMEPATH: {os.environ.get('HOMEPATH', '')}")
print(f"APPDATA: {os.environ.get('APPDATA', '')}")
print(f"LOCALAPPDATA: {os.environ.get('LOCALAPPDATA', '')}")
print(f"Result: {_get_real_home()}")
