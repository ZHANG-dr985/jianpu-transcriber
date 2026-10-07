"""在桌面创建「MP3扒简谱」快捷方式。

中文文件名由 Python 处理（避免 PowerShell 命令行编码问题）。
运行: D:\\PYTHON\\python.exe tools\\make_shortcut.py
"""
from __future__ import annotations

import os
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINK_NAME = "MP3扒简谱.lnk"          # 桌面上的名字
TMP_NAME = "MP3_jianpu_tmp.lnk"      # 先建 ASCII 名再改名


def desktop_dir() -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, text=True)
        p = out.stdout.strip()
        if p and os.path.isdir(p):
            return p
    except Exception:
        pass
    return os.path.join(os.environ.get("USERPROFILE", r"C:\Users\PC"), "Desktop")


def find_pythonw() -> str:
    cand = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.exists(cand):
        return cand
    return sys.executable


def main() -> int:
    desktop = desktop_dir()
    os.makedirs(desktop, exist_ok=True)
    pyw = find_pythonw()
    app = os.path.join(PROJ, "app.py")
    icon = os.path.join(PROJ, "assets", "jianpu.ico")
    if not os.path.exists(app):
        print("找不到 app.py：", app)
        return 1
    if not os.path.exists(icon):
        icon = pyw

    tmp = os.path.join(desktop, TMP_NAME)
    if os.path.exists(tmp):
        os.remove(tmp)
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{tmp}'); "
        f"$sc.TargetPath = '{pyw}'; "
        f"$sc.Arguments = '\"{app}\"'; "
        f"$sc.WorkingDirectory = '{PROJ}'; "
        f"$sc.IconLocation = '{icon}'; "
        "$sc.Description = 'MP3 to Jianpu score'; "
        "$sc.WindowStyle = 1; "
        "$sc.Save(); "
        "Write-Output 'saved'"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(tmp):
        print("创建快捷方式失败：")
        print(r.stdout)
        print(r.stderr)
        return 1

    final = os.path.join(desktop, LINK_NAME)
    if os.path.exists(final):
        os.remove(final)
    os.replace(tmp, final)

    # 校验
    r2 = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"$ws = New-Object -ComObject WScript.Shell; "
         f"$s = $ws.CreateShortcut('{final}'); "
         "Write-Output $s.TargetPath; Write-Output $s.Arguments; "
         "Write-Output $s.WorkingDirectory"],
        capture_output=True, text=True)
    print("桌面快捷方式:", final)
    print("存在:", os.path.exists(final), " 大小:", os.path.getsize(final), "字节")
    print(r2.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
