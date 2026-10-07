"""GUI 集成测试：不靠鼠标，直接驱动界面内部流程。

验证：参数收集 -> 生成命令行 -> 线程跑 cli.py -> 解析 JSON -> 刷新预览/日志/文本页。
运行: python tests/gui_e2e.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
import tkinter as tk
from tkinter import messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

CALLS = []


def patch_messageboxes():
    for name in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, name, (lambda n: (lambda *a, **k: CALLS.append((n, a[:1]))))(name))


def load_app():
    spec = importlib.util.spec_from_file_location("appmod", os.path.join(ROOT, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    patch_messageboxes()
    mod = load_app()
    root = tk.Tk()
    root.withdraw()
    app = mod.JianpuApp(root)

    ok = True
    mp3 = os.path.join(ROOT, "out", "test_song.mp3")
    if not os.path.exists(mp3):
        print("缺少测试音频，先运行 tests/make_test_audio.py")
        return 1
    app.var_audio.set(mp3)
    app.var_title.set("花又落")
    outdir = os.path.join(ROOT, "out", "gui_e2e")

    cmd = app._build_command()
    print("命令行:", " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
    assert "--input" in cmd and mp3 in cmd, "命令行缺少 --input"
    assert "--formats" in cmd, "命令行缺少 --formats"
    assert "auto" in cmd, "命令行缺少 key/meter/bpm"

    app.on_start()
    deadline = time.time() + 180
    while time.time() < deadline:
        root.update()
        if not app.busy:
            break
        time.sleep(0.05)
    for _ in range(40):
        root.update()
        time.sleep(0.03)

    pages = list(getattr(app, "pages", []) or [])
    print("结果页数:", len(pages))
    logtxt = app.log_box.get("1.0", "end").strip() if app.log_box else ""
    print("日志尾部:", (logtxt.splitlines() or [""])[-1][:120])
    if pages:
        print("第一页:", os.path.basename(pages[0]), "存在:", os.path.exists(pages[0]))
    txt = app.text_box.get("1.0", "end").strip() if app.text_box else ""
    table = app.table_box.get("1.0", "end").strip() if app.table_box else ""
    print("文本页字符数:", len(txt), " 指法表字符数:", len(table))
    print("messagebox 调用:", CALLS[:3])

    ok = bool(pages) and all(os.path.exists(p) for p in pages) and len(txt) > 50 and len(table) > 50

    # 预览缩放：适应宽度 -> 还原 1:1
    fit_ok = True
    if pages:
        app.page_canvas.configure(width=900, height=600)
        root.update()
        app.fit_page()
        f1 = app._fit_factor
        z1 = app.var_zoom.get()
        root.update()
        app.fit_page()
        f2 = app._fit_factor
        z2 = app.var_zoom.get()
        print("适应宽度: factor=%s (%s) -> 还原: factor=%s (%s)" % (f1, z1, f2, z2))
        fit_ok = f1 >= 1 and f2 == 1 and app.page_photo is not None
    ok = ok and fit_ok

    root.destroy()
    print("GUI 集成测试:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
