# -*- coding: utf-8 -*-
"""交付验收：一次性检查目标全部达成。

运行: D:\\PYTHON\\python.exe tests\\acceptance.py
检查项：
  1) 关键文件齐全（程序、内核、文档、启动器、图标）
  2) 桌面快捷方式存在、指向正确、图标正确
  3) 简谱格式与参考图一致（黄金样板逐项像素比对）
  4) 合成音频端到端：MP3 -> 简谱，调性/速度/小节数/文件产出
  5) 界面集成：界面内部流程 -> 内核 -> JSON -> 预览
  6) 自检套件 26 项
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
LNK = os.path.join(os.environ.get("USERPROFILE", r"C:\Users\PC"),
                   "Desktop", "MP3\u626c\u7b80\u8c31.lnk")  # MP3扒简谱.lnk
LNK = os.path.join(os.environ.get("USERPROFILE", r"C:\Users\PC"),
                   "Desktop", "MP3\u6252\u7b80\u8c31.lnk")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("   " + detail) if detail else ""))


def section(t):
    print("\n" + t)


def main() -> int:
    print("=" * 74)
    print("交付验收  MP3 -> 简谱 工具")
    print("=" * 74)

    section("[1] 关键文件")
    need = [
        ("app.py", "图形界面"),
        ("cli.py", "命令行后端"),
        ("README.md", "使用说明"),
        ("\u542f\u52a8\u6252\u8c31\u5de5\u5177.bat", "启动脚本"),
        (os.path.join("assets", "jianpu.ico"), "图标"),
        (os.path.join("jianpu", "model.py"), "数据模型"),
        (os.path.join("jianpu", "audio.py"), "音频分析"),
        (os.path.join("jianpu", "transcribe.py"), "扒谱主流程"),
        (os.path.join("jianpu", "fingering.py"), "指法"),
        (os.path.join("jianpu", "render.py"), "排版渲染"),
        (os.path.join("jianpu", "export.py"), "导出"),
        (os.path.join("ref", "reference.jpg"), "参考谱"),
        (os.path.join("tests", "selftest.py"), "自检"),
        (os.path.join("tests", "gui_e2e.py"), "界面集成测试"),
        (os.path.join("tests", "golden.py"), "黄金样板"),
    ]
    for rel, desc in need:
        p = os.path.join(ROOT, rel)
        check(f"{desc}  {rel}", os.path.exists(p),
              f"{os.path.getsize(p)} B" if os.path.exists(p) else "缺失")

    section("[2] 桌面快捷方式")
    check("\u684c\u9762\u5feb\u6377\u65b9\u5f0f\u5b58\u5728", os.path.exists(LNK),
          LNK if os.path.exists(LNK) else "未找到")
    if os.path.exists(LNK):
        ps = ("$ws=New-Object -ComObject WScript.Shell;"
              f"$s=$ws.CreateShortcut('{LNK}');"
              "Write-Output $s.TargetPath; Write-Output $s.Arguments;"
              "Write-Output $s.WorkingDirectory; Write-Output $s.IconLocation")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True)
        lines = [x.strip() for x in r.stdout.splitlines() if x.strip()]
        tgt = lines[0] if lines else ""
        args = lines[1].strip('"') if len(lines) > 1 else ""
        wd = lines[2] if len(lines) > 2 else ""
        icon = lines[3] if len(lines) > 3 else ""
        check("目标解释器存在", os.path.exists(tgt), tgt)
        check("启动 app.py", os.path.exists(args) and args.endswith("app.py"), args)
        check("工作目录为项目目录", os.path.isdir(wd) and os.path.samefile(wd, ROOT), wd)
        check("图标文件存在", os.path.exists(icon.split(",")[0]), icon)

    section("[3] 版式与参考图一致（黄金样板比对）")
    r = subprocess.run([PY, os.path.join(HERE, "selftest.py")], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    layout = [l for l in r.stdout.splitlines() if "[1]" in l or "[2]" in l or
              l.strip().startswith("[PASS]") or l.strip().startswith("[FAIL]")]
    seg = r.stdout.split("[3]")[0]
    for l in seg.splitlines():
        if l.strip().startswith("[PASS]") or l.strip().startswith("[FAIL]"):
            check(l.split("]", 1)[1].strip(), "[PASS]" in l)

    section("[4] 端到端：合成 MP3 -> 简谱")
    mp3 = os.path.join(ROOT, "out", "test_song.mp3")
    if not os.path.exists(mp3):
        subprocess.run([PY, os.path.join(HERE, "make_test_audio.py")], cwd=ROOT, check=True)
    outdir = os.path.join(ROOT, "out", "acceptance")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([PY, os.path.join(ROOT, "cli.py"), "--input", mp3,
                        "--outdir", outdir, "--title", "\u82b1\u53c8\u843d",
                        "--formats", "png,pdf,txt,midi"],
                       cwd=ROOT, capture_output=True, env=env)
    lines = [l for l in p.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
    res = json.loads(lines[-1]) if lines else {}
    check("内核返回 ok=true", res.get("ok") is True, res.get("message", ""))
    check("调性识别 C", res.get("key") == "C", str(res.get("key")))
    check("速度识别≈72", abs(float(res.get("bpm") or 0) - 72) < 8, str(res.get("bpm")))
    check("小节数 12", res.get("measures") == 12, str(res.get("measures")))
    files = res.get("files") or {}
    for k, label in (("png", "PNG 简谱图"), ("pdf", "PDF"), ("text", "可编辑文本"),
                     ("table", "88键对照表"), ("midi", "MIDI")):
        v = files.get(k)
        ok = bool(v) and (all(os.path.exists(x) for x in v) if isinstance(v, list)
                          else os.path.exists(v))
        check("产出 " + label, ok, str(v)[:70])

    section("[5] 界面集成（不开鼠标，驱动界面内部流程）")
    g = subprocess.run([PY, os.path.join(HERE, "gui_e2e.py")], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = "GUI \u96c6\u6210\u6d4b\u8bd5: PASS" in g.stdout
    check("界面 -> 内核 -> 预览/文本 全链路", ok,
          [l for l in g.stdout.splitlines() if "结果页数" in l or "字符数" in l][:1].__str__())

    section("[6] 自检套件")
    tail = [l for l in r.stdout.splitlines() if "\u5171 " in l and "\u9879" in l]
    check("自检全部通过", r.returncode == 0, tail[-1].strip() if tail else r.stdout[-100:])

    failed = [n for n, o, _ in RESULTS if not o]
    print("\n" + "=" * 74)
    print(f"验收项 {len(RESULTS)}，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    for n in failed:
        print("   失败: " + n)
    print("结论: " + ("全部达成 \u2714" if not failed else "仍有未达成项 \u2718"))
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
