# -*- coding: utf-8 -*-
"""MP3 扒简谱工具（88 键电子琴）—— tkinter 图形界面。

设计要点：
  * 只依赖标准库 + tkinter，保证用 pythonw.exe 秒启动（不 import numpy/scipy/PIL）。
  * 所有耗时工作交给 cli.py 子进程完成，界面用线程 + 队列 + after 轮询保持不卡。
  * 解析 cli.py 的 stdout JSON：取最后一行非空文本做 json.loads。

命令行自检（不真正开窗）：
    python app.py --selfcheck      -> 打印 SELFCHECK OK，退出码 0
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

APP_TITLE = "MP3 扒简谱工具（88键电子琴）"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLI_PATH = os.path.join(BASE_DIR, "cli.py")
ICON_PATH = os.path.join(BASE_DIR, "assets", "jianpu.ico")
UI_FONT = ("Microsoft YaHei UI", 10)
LOG_FONT = ("Microsoft YaHei UI", 9)

# 下拉选项（顺序与需求一致）
KEY_CHOICES = ["auto（自动检测）", "C", "#C", "D", "bE", "E", "F", "#F", "G", "bA", "A", "bB", "B"]
METER_CHOICES = ["auto", "2/4", "3/4", "4/4"]
RANGE_CHOICES = ["G3-E6", "C4-C6", "A3-A5", "C4-E6", "C3-C6"]
ENGINE_CHOICES = ["viterbi", "onset"]
TEMPO_CHOICES = ["slow", "fast", "none"]
AUDIO_TYPES = [
    ("音频文件", "*.mp3 *.wav *.m4a *.flac *.ogg *.aac"),
    ("MP3", "*.mp3"), ("WAV", "*.wav"), ("M4A", "*.m4a"),
    ("FLAC", "*.flac"), ("OGG", "*.ogg"), ("AAC", "*.aac"),
    ("所有文件", "*.*"),
]


def safe_print(text: str) -> None:
    """在 pythonw（无 stdout）环境下安全打印。"""
    try:
        if sys.stdout is not None:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 模块级纯函数：解析 CLI 输出（GUI 与自动化测试共用）
# --------------------------------------------------------------------------- #
def parse_result(line: str) -> dict:
    """解析 cli.py 打印的一行 JSON。

    入参可以是整段 stdout（内部会取最后一行非空文本），
    返回结构化 dict；解析失败时返回 ok=False 且带中文 message。
    """
    if line is None:
        line = ""
    text = line.replace("\ufeff", "").strip()
    last = ""
    for piece in text.splitlines():
        if piece.strip():
            last = piece.strip()
    if not last:
        return {"ok": False, "message": "没有收到命令行输出（cli.py 未返回任何内容）"}

    try:
        raw = json.loads(last)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": "无法解析命令行输出：%s" % exc, "raw": last}

    if not isinstance(raw, dict):
        return {"ok": False, "message": "命令行返回的不是 JSON 对象", "raw": last}

    files = raw.get("files")
    if not isinstance(files, dict):
        files = {}
    pngs = files.get("png")
    if isinstance(pngs, str):
        pngs = [pngs]
    elif not isinstance(pngs, list):
        pngs = []
    pngs = [str(p) for p in pngs if p]

    preview = raw.get("preview") or (pngs[0] if pngs else "")
    warnings = raw.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    warnings = [str(w) for w in warnings]

    return {
        "ok": bool(raw.get("ok")),
        "message": str(raw.get("message") or ""),
        "outdir": str(raw.get("outdir") or ""),
        "title": str(raw.get("title") or ""),
        "files": files,
        "png": pngs,
        "preview": str(preview or ""),
        "text": str(files.get("text") or ""),
        "table": str(files.get("table") or ""),
        "pdf": str(files.get("pdf") or ""),
        "midi": str(files.get("midi") or ""),
        "key": "" if raw.get("key") is None else str(raw.get("key")),
        "bpm": raw.get("bpm"),
        "meter": str(raw.get("meter") or ""),
        "measures": raw.get("measures"),
        "notes": raw.get("notes"),
        "duration": raw.get("duration"),
        "elapsed": raw.get("elapsed"),
        "key_confidence": raw.get("key_confidence"),
        "warnings": warnings,
    }


def find_python() -> str:
    """优先使用自带的 pythonw/python，保证与当前解释器一致。"""
    exe = sys.executable or ""
    cands = []
    if exe:
        base = os.path.basename(exe).lower()
        cands.append(os.path.join(os.path.dirname(exe), "pythonw.exe"))
        cands.append(exe)
    cands += [r"D:\PYTHON\pythonw.exe", r"D:\PYTHON\python.exe"]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return exe or "python"


def read_text_file(path: str) -> str:
    """读取文本文件（UTF-8 优先，失败退回 GBK）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        try:
            with open(path, "r", encoding="gbk", errors="replace") as f:
                return f.read()
        except Exception as exc:  # noqa: BLE001
            return "（无法读取文件：%s）\n%s" % (path, exc)


# --------------------------------------------------------------------------- #
# 主界面
# --------------------------------------------------------------------------- #
class JianpuApp:
    """扒谱工具主窗口。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1120x860")
        self.root.minsize(980, 640)
        self._apply_icon()

        # ---- 运行状态 ----
        self.log_box = None            # type: ignore[assignment]
        self.page_canvas = None        # type: ignore[assignment]
        self.page_inner = None         # type: ignore[assignment]
        self.page_image = None         # 保持 PhotoImage 引用，防止被回收
        self.pages = []                # 当前预览 PNG 列表
        self.page_index = 0
        self.page_photo = None
        self.page_base = None          # 1:1 原图（供缩放切换）
        self.page_size = (0, 0)        # 原始像素尺寸
        self._fit_factor = 1           # 当前缩放倍数（整数 subsample）
        self.text_box = None           # type: ignore[assignment]
        self.table_box = None          # type: ignore[assignment]
        self.text_source = ""          # 当前简谱文本文件路径
        self.outdir = ""               # 最近一次输出目录
        self.result = {}
        self.busy = False
        self.proc = None               # 当前子进程（用于「停止」）
        self.queue: "queue.Queue" = queue.Queue()
        self.last_command = []

        self.var_audio = tk.StringVar()
        self.var_title = tk.StringVar()
        self.var_key = tk.StringVar(value=KEY_CHOICES[0])
        self.var_meter = tk.StringVar(value="auto")
        self.var_tempo = tk.StringVar(value="auto")
        self.var_bpm = tk.StringVar(value="")
        self.var_range = tk.StringVar(value=RANGE_CHOICES[0])
        self.var_sens = tk.DoubleVar(value=0.5)
        self.var_engine = tk.StringVar(value=ENGINE_CHOICES[0])
        self.var_tempo_prefer = tk.StringVar(value=TEMPO_CHOICES[0])
        self.var_mpl = tk.IntVar(value=3)
        self.var_spp = tk.IntVar(value=4)
        self.var_maxsec = tk.IntVar(value=300)
        self.var_bass = tk.BooleanVar(value=True)
        self.var_fmt = {
            "png": tk.BooleanVar(value=True),
            "pdf": tk.BooleanVar(value=True),
            "txt": tk.BooleanVar(value=True),
            "midi": tk.BooleanVar(value=True),
        }
        self.var_status = tk.StringVar(value="就绪")
        self.var_sens_label = tk.StringVar(value="0.50")
        self.var_page = tk.StringVar(value="0/0")
        self.var_zoom = tk.StringVar(value="缩放：1:1（可点「适应宽度」）")

        self._build_style()
        self._build_menu()
        self._build_ui()

    # ---------------------------------------------------------------- 外观
    def _apply_icon(self) -> None:
        """窗口图标（有 ico 才设置，失败忽略）。"""
        try:
            if os.path.isfile(ICON_PATH):
                self.root.iconbitmap(ICON_PATH)
        except Exception:
            pass

    def _build_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            try:
                style.theme_use("clam")
            except Exception:
                pass
        style.configure(".", font=UI_FONT)
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Main.TButton", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Status.TLabel", foreground="#0a5")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="选择音频…", command=self.on_choose_audio)
        m_file.add_command(label="打开输出文件夹", command=self.on_open_outdir)
        m_file.add_separator()
        m_file.add_command(label="退出", command=self.root.destroy)
        menubar.add_cascade(label="文件", menu=m_file)
        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="关于", command=self.on_about)
        menubar.add_cascade(label="帮助", menu=m_help)
        self.root.config(menu=menubar)

    # ---------------------------------------------------------------- 布局
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=(10, 8, 10, 6))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        top = ttk.Frame(outer)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)

        self._build_file_row(top)
        self._build_params(top)

        # 按钮 + 进度条
        bar = ttk.Frame(outer)
        bar.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        bar.columnconfigure(4, weight=1)
        self.btn_run = ttk.Button(bar, text="开始扒谱", style="Main.TButton",
                                  command=self.on_start, width=14)
        self.btn_run.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(bar, text="打开输出文件夹", command=self.on_open_outdir).grid(row=0, column=1, padx=4)
        ttk.Button(bar, text="清空日志", command=self.on_clear_log).grid(row=0, column=2, padx=4)
        ttk.Button(bar, text="停止", command=self.on_cancel).grid(row=0, column=3, padx=(4, 10))
        self.progress = ttk.Progressbar(bar, mode="indeterminate")
        self.progress.grid(row=0, column=4, sticky="ew")
        ttk.Label(bar, textvariable=self.var_status, style="Status.TLabel",
                  width=12, anchor="e").grid(row=0, column=5, padx=(8, 0))

        # 下半部分：日志 + 标签页
        paned = ttk.Panedwindow(outer, orient="vertical")
        paned.grid(row=2, column=0, sticky="nsew", pady=(4, 0))

        log_frame = ttk.LabelFrame(paned, text="运行日志", padding=6)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_box = ScrolledText(log_frame, height=5, wrap="word",
                                    font=LOG_FONT, state="disabled")
        self.log_box.grid(row=0, column=0, sticky="nsew")
        paned.add(log_frame, weight=1)

        nb = ttk.Notebook(paned)
        paned.add(nb, weight=4)
        self._build_tab_preview(nb)
        self._build_tab_text(nb)
        self._build_tab_table(nb)

        self.log("欢迎使用 MP3 扒简谱工具。")
        self.log("请先选择音频文件，然后点击「开始扒谱」。")
        if not os.path.isfile(CLI_PATH):
            self.log("警告：未找到命令行程序 %s" % CLI_PATH)

    def _build_file_row(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent)
        row.grid(row=0, column=0, sticky="ew")
        row.columnconfigure(1, weight=1)

        ttk.Label(row, text="音频文件：").grid(row=0, column=0, sticky="w")
        self.entry_audio = ttk.Entry(row, textvariable=self.var_audio, font=UI_FONT)
        self.entry_audio.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(row, text="选择音频…", command=self.on_choose_audio).grid(row=0, column=2)

        ttk.Label(row, text="曲名：").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(row, textvariable=self.var_title, font=UI_FONT).grid(
            row=1, column=1, sticky="ew", padx=(0, 6), pady=(6, 0))
        ttk.Button(row, text="打开输出文件夹", command=self.on_open_outdir).grid(
            row=1, column=2, pady=(6, 0))

    def _build_params(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="扒谱参数", padding=(10, 6, 10, 8))
        box.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for c in (1, 3):
            box.columnconfigure(c, weight=1)

        def lab(text: str, r: int, c: int) -> None:
            ttk.Label(box, text=text, anchor="e").grid(row=r, column=c, sticky="e",
                                                       padx=(6, 6), pady=4)

        # ---- 左列 ----
        lab("调性：", 0, 0)
        ttk.Combobox(box, textvariable=self.var_key, values=KEY_CHOICES,
                     state="readonly", width=15).grid(row=0, column=1, sticky="ew", pady=4)

        lab("拍号：", 1, 0)
        ttk.Combobox(box, textvariable=self.var_meter, values=METER_CHOICES,
                     state="readonly", width=15).grid(row=1, column=1, sticky="ew", pady=4)

        lab("速度：", 2, 0)
        spd = ttk.Frame(box)
        spd.grid(row=2, column=1, sticky="ew", pady=4)
        spd.columnconfigure(1, weight=1)
        cmb_speed = ttk.Combobox(spd, textvariable=self.var_tempo,
                                 values=["auto"] + [str(v) for v in range(40, 241, 5)],
                                 state="readonly", width=8)
        cmb_speed.grid(row=0, column=0)
        cmb_speed.bind("<<ComboboxSelected>>", lambda _e: self._sync_bpm_state())
        self.entry_bpm = ttk.Entry(spd, textvariable=self.var_bpm, width=8)
        self.entry_bpm.grid(row=0, column=1, sticky="w", padx=(6, 0))
        ttk.Label(spd, text="BPM").grid(row=0, column=2, padx=(4, 0))

        lab("主旋律音域：", 3, 0)
        ttk.Combobox(box, textvariable=self.var_range, values=RANGE_CHOICES,
                     state="readonly", width=15).grid(row=3, column=1, sticky="ew", pady=4)

        lab("灵敏度：", 4, 0)
        sen = ttk.Frame(box)
        sen.grid(row=4, column=1, sticky="ew", pady=4)
        sen.columnconfigure(0, weight=1)
        ttk.Scale(sen, from_=0.0, to=1.0, orient="horizontal", variable=self.var_sens,
                  command=self._on_sens).grid(row=0, column=0, sticky="ew")
        ttk.Label(sen, textvariable=self.var_sens_label, width=5).grid(row=0, column=1,
                                                                      padx=(6, 0))

        # ---- 右列 ----
        lab("提取算法：", 0, 2)
        cmb_engine = ttk.Combobox(box, textvariable=self.var_engine, values=ENGINE_CHOICES,
                                  state="readonly", width=16)
        cmb_engine.grid(row=0, column=3, sticky="ew", pady=4)
        cmb_engine.bind("<<ComboboxSelected>>", lambda _e: self._engine_hint())

        lab("速度倾向：", 1, 2)
        ttk.Combobox(box, textvariable=self.var_tempo_prefer, values=TEMPO_CHOICES,
                     state="readonly", width=16).grid(row=1, column=3, sticky="ew", pady=4)

        lab("每行小节数：", 2, 2)
        ttk.Spinbox(box, from_=1, to=6, textvariable=self.var_mpl,
                    width=8).grid(row=2, column=3, sticky="w", pady=4)

        lab("每页行数：", 3, 2)
        ttk.Spinbox(box, from_=1, to=8, textvariable=self.var_spp,
                    width=8).grid(row=3, column=3, sticky="w", pady=4)

        lab("分析时长(秒)：", 4, 2)
        ttk.Spinbox(box, from_=30, to=600, increment=10, textvariable=self.var_maxsec,
                    width=8).grid(row=4, column=3, sticky="w", pady=4)

        # ---- 复选框区 ----
        opt = ttk.Frame(box)
        opt.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(opt, text="生成左手伴奏（取消则只出主旋律）",
                        variable=self.var_bass).grid(row=0, column=0, sticky="w", padx=(0, 18))
        ttk.Label(opt, text="输出格式：").grid(row=0, column=1, sticky="w")
        for i, (key, text) in enumerate([("png", "PNG 图片"), ("pdf", "PDF 乐谱"),
                                         ("txt", "简谱文本"), ("midi", "MIDI 音频")]):
            ttk.Checkbutton(opt, text=text, variable=self.var_fmt[key]).grid(
                row=0, column=2 + i, sticky="w", padx=(0, 12))
        ttk.Label(opt, text="（至少勾选一个）", foreground="#888").grid(
            row=0, column=6, sticky="w")

        self._sync_bpm_state()

    def _build_tab_preview(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="排版预览")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        bar = ttk.Frame(tab)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(bar, text="上一页", command=lambda: self.show_page(self.page_index - 1)).pack(side="left")
        ttk.Button(bar, text="下一页", command=lambda: self.show_page(self.page_index + 1)).pack(side="left", padx=4)
        ttk.Button(bar, text="适应宽度", command=self.fit_page).pack(side="left", padx=4)
        ttk.Label(bar, textvariable=self.var_page, width=10).pack(side="left", padx=8)
        ttk.Label(bar, textvariable=self.var_zoom, foreground="#888",
                  width=26).pack(side="left")

        wrap = ttk.Frame(tab)
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.page_canvas = tk.Canvas(wrap, background="#f4f4f4",
                                     highlightthickness=1, highlightbackground="#ccc")
        self.page_canvas.grid(row=0, column=0, sticky="nsew")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.page_canvas.yview)
        vs.grid(row=0, column=1, sticky="ns")
        hs = ttk.Scrollbar(wrap, orient="horizontal", command=self.page_canvas.xview)
        hs.grid(row=1, column=0, sticky="ew")
        self.page_canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)

        # 鼠标左键拖拽平移
        self.page_canvas.bind("<ButtonPress-1>", self._drag_start)
        self.page_canvas.bind("<B1-Motion>", self._drag_move)
        # 画布尺寸就绪后只维护滚动区域，不自动改缩放（避免预览缩得看不清）
        self.page_canvas.bind("<Configure>", self._on_canvas_configure)

    def _build_tab_text(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="简谱文本（可编辑）")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        bar = ttk.Frame(tab)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(bar, text="保存修改", command=self.on_save_text).pack(side="left")
        ttk.Button(bar, text="按修改重新排版", command=self.on_rerender).pack(side="left", padx=6)
        self.var_text_hint = tk.StringVar(value="（还没有简谱文本）")
        ttk.Label(bar, textvariable=self.var_text_hint, foreground="#888").pack(
            side="left", padx=10)

        self.text_box = ScrolledText(tab, wrap="none", font=("Consolas", 11), undo=True)
        self.text_box.grid(row=1, column=0, sticky="nsew")

    def _build_tab_table(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="88键指法表")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.table_box = ScrolledText(tab, wrap="none", font=("Consolas", 11), state="disabled")
        self.table_box.grid(row=0, column=0, sticky="nsew")

    # ---------------------------------------------------------------- 小工具
    def log(self, text: str) -> None:
        """向日志区追加一行（只读控件，临时开放写入）。"""
        if self.log_box is None:
            return
        try:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", text.rstrip() + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        except Exception:
            pass

    def set_status(self, text: str) -> None:
        self.var_status.set(text)

    def _on_sens(self, _value=None) -> None:
        try:
            v = round(float(self.var_sens.get()) / 0.05) * 0.05  # 吸附到 0.05 网格
            v = min(1.0, max(0.0, v))
            self.var_sens_label.set("%.2f" % v)
        except Exception:
            pass

    def _sync_bpm_state(self) -> None:
        """速度选 auto 时禁用 BPM 输入框。"""
        try:
            auto = str(self.var_tempo.get()).strip().lower() == "auto"
            self.entry_bpm.configure(state="disabled" if auto else "normal")
        except Exception:
            pass

    def _engine_hint(self) -> None:
        if self.var_engine.get() == "onset":
            self.log("提示：已切换为 onset（音头分段）算法，适合音符颗粒清晰的曲子。")
        else:
            self.log("提示：已切换为 viterbi（连续跟踪）算法。")

    def _drag_start(self, event) -> None:
        self.page_canvas.scan_mark(event.x, event.y)

    def _drag_move(self, event) -> None:
        self.page_canvas.scan_dragto(event.x, event.y, gain=1)

    # ---------------------------------------------------------------- 事件
    def on_choose_audio(self) -> None:
        path = filedialog.askopenfilename(title="选择音频文件", filetypes=AUDIO_TYPES)
        if not path:
            return
        self.var_audio.set(path)
        stem = os.path.splitext(os.path.basename(path))[0]
        self.var_title.set(stem)          # 自动填曲名，用户可改
        self.log("已选择音频：%s" % path)
        self.log("曲名自动填为「%s」，可直接修改。" % stem)
        self.set_status("已选择")

    def on_clear_log(self) -> None:
        try:
            self.log_box.configure(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.configure(state="disabled")
        except Exception:
            pass

    def on_open_outdir(self) -> None:
        path = self.outdir
        if not path or not os.path.isdir(path):
            audio = self.var_audio.get().strip()
            if audio and os.path.isdir(os.path.dirname(audio)):
                path = os.path.dirname(audio)
            else:
                messagebox.showinfo(APP_TITLE, "还没有输出目录，请先扒谱一次。")
                return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, "无法打开文件夹：\n%s\n%s" % (path, exc))

    def on_save_text(self) -> None:
        if not self.text_source or not os.path.isfile(self.text_source):
            messagebox.showinfo(APP_TITLE, "还没有可保存的简谱文本。")
            return
        try:
            data = self.text_box.get("1.0", "end-1c")
            with open(self.text_source, "w", encoding="utf-8", newline="\n") as f:
                f.write(data)
            self.log("已保存修改：%s" % self.text_source)
            self.set_status("已保存")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, "保存失败：\n%s" % exc)

    def on_about(self) -> None:
        messagebox.showinfo(
            "关于",
            "%s\n\n把音频转成 88 键电子琴简谱（右手旋律 + 左手伴奏）。\n"
            "界面：tkinter；扒谱内核：cli.py\n\n命令行程序：\n%s" % (APP_TITLE, CLI_PATH))

    def on_cancel(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.kill()
                self.log("已请求停止扒谱。")
            except Exception:
                pass
        else:
            self.log("当前没有正在运行的任务。")

    # ---------------------------------------------------------------- 参数
    def _collect_formats(self) -> str:
        keys = [k for k in ("png", "pdf", "txt", "midi") if self.var_fmt[k].get()]
        return ",".join(keys)

    def _build_command(self) -> list:
        """把界面参数拼成 cli.py 的命令行。"""
        audio = self.var_audio.get().strip()
        cmd = [find_python(), CLI_PATH,
               "--input", audio,
               "--title", self.var_title.get().strip() or
               os.path.splitext(os.path.basename(audio))[0],
               "--key", self.var_key.get().split("（")[0].strip(),
               "--meter", self.var_meter.get().strip(),
               "--melody-range", self.var_range.get().strip(),
               "--sensitivity", "%.2f" % self.var_sens.get(),
               "--engine", self.var_engine.get().strip(),
               "--tempo-prefer", self.var_tempo_prefer.get().strip(),
               "--measures-per-line", str(self.var_mpl.get()),
               "--systems-per-page", str(self.var_spp.get()),
               "--max-seconds", str(self.var_maxsec.get()),
               "--formats", self._collect_formats()]
        tempo = str(self.var_tempo.get()).strip()
        if tempo.lower() == "auto" or not tempo:
            cmd += ["--bpm", "auto"]
        else:
            cmd += ["--bpm", tempo]
        if not self.var_bass.get():
            cmd.append("--no-bass")
        if self.outdir:
            cmd += ["--outdir", self.outdir]
        return cmd

    # ---------------------------------------------------------------- 启动
    def on_start(self) -> None:
        if self.busy:
            messagebox.showinfo(APP_TITLE, "正在扒谱中，请稍候…")
            return
        audio = self.var_audio.get().strip()
        if not audio:
            messagebox.showerror(APP_TITLE, "请先选择音频文件。")
            return
        if not os.path.isfile(audio):
            messagebox.showerror(APP_TITLE, "音频文件不存在：\n%s" % audio)
            return
        if not os.path.isfile(CLI_PATH):
            messagebox.showerror(APP_TITLE, "找不到命令行程序 cli.py：\n%s" % CLI_PATH)
            return
        fmts = self._collect_formats()
        if not fmts:
            messagebox.showerror(APP_TITLE, "输出格式至少要勾选一个。")
            return

        # 输出目录：音频同目录下的「简谱输出」（也可以用上次的目录）
        self.outdir = os.path.join(os.path.dirname(audio), "简谱输出")
        self._run(self._build_command(), "扒谱")

    def on_rerender(self) -> None:
        """把编辑框内容写成文本，再用 --render-text 重新排版。"""
        if self.busy:
            messagebox.showinfo(APP_TITLE, "正在处理中，请稍候…")
            return
        if not self.text_source:
            messagebox.showerror(APP_TITLE, "还没有简谱文本，请先扒谱一次。")
            return
        try:
            data = self.text_box.get("1.0", "end-1c")
            if not data.strip():
                messagebox.showerror(APP_TITLE, "简谱文本是空的，无法排版。")
                return
            with open(self.text_source, "w", encoding="utf-8", newline="\n") as f:
                f.write(data)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, "写入文本失败：\n%s" % exc)
            return
        fmt = self._collect_formats() or "png"
        if "png" not in fmt:
            fmt = "png," + fmt      # 必须出 PNG 才能刷新预览
        cmd = [find_python(), CLI_PATH, "--render-text", self.text_source,
               "--formats", fmt, "--dpi", "150"]
        if self.outdir:
            cmd += ["--outdir", self.outdir]
        self.log("按修改重新排版：%s" % self.text_source)
        self._run(cmd, "重新排版")

    def _run(self, cmd: list, what: str) -> None:
        """统一的子进程启动入口（异步）。"""
        self.busy = True
        self.last_command = cmd
        self.btn_run.configure(state="disabled")
        self.set_status("%s中…" % what)
        self.progress.start(12)
        self.log("开始%s，命令参数：%s" % (what, self._pretty_cmd(cmd)))
        th = threading.Thread(target=self._worker, args=(cmd,), daemon=True)
        th.start()
        self.root.after(100, self._poll)

    @staticmethod
    def _pretty_cmd(cmd: list) -> str:
        """只展示关键参数，避免日志过长。"""
        keys = ("--input", "--title", "--key", "--meter", "--bpm", "--engine",
                "--tempo-prefer", "--formats")
        parts = []
        for i, item in enumerate(cmd):
            if item in keys and i + 1 < len(cmd):
                parts.append("%s=%s" % (item, cmd[i + 1]))
        return " ".join(parts) if parts else os.path.basename(cmd[1] if len(cmd) > 1 else "")

    def _worker(self, cmd: list) -> None:
        """后台线程：跑子进程，把结果放进队列。"""
        try:
            creation = 0
            if os.name == "nt":
                creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=BASE_DIR, creationflags=creation)
            out, err = self.proc.communicate()
            text = (out or b"").decode("utf-8", errors="replace")
            errtext = (err or b"").decode("utf-8", errors="replace")
            self.queue.put(("done", parse_result(text), self.proc.returncode, errtext))
        except FileNotFoundError as exc:
            self.queue.put(("error", "找不到 Python 或命令行程序：%s" % exc, 1, ""))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", "启动扒谱进程失败：%s" % exc, 1, ""))

    def _poll(self) -> None:
        """主线程定时取结果（不阻塞界面）。"""
        try:
            kind, payload, code, errtext = self.queue.get_nowait()
        except queue.Empty:
            if self.busy:
                self.root.after(100, self._poll)
            return
        self._finish(kind, payload, code, errtext)

    def _finish(self, kind, payload, code, errtext) -> None:
        self.busy = False
        self.proc = None
        try:
            self.progress.stop()
        except Exception:
            pass
        self.btn_run.configure(state="normal")
        self.set_status("就绪")

        if kind == "error":
            self.log("错误：%s" % payload)
            messagebox.showerror(APP_TITLE, str(payload))
            return

        result = payload
        if not result.get("ok"):
            reason = result.get("message") or "命令行执行失败（退出码 %s）" % code
            self.log("扒谱失败：%s" % reason)
            if errtext.strip():
                self.log("错误详情：%s" % errtext.strip()[-800:])
            messagebox.showerror(APP_TITLE, "扒谱失败：\n%s" % reason)
            return

        self._apply_result(result, rerender=self._is_rerender())
        messagebox.showinfo(APP_TITLE, self._summary(result))
        if result.get("warnings"):
            messagebox.showwarning(APP_TITLE,
                                   "提示信息：\n" + "\n".join("· " + w for w in result["warnings"]))

    def _is_rerender(self) -> bool:
        return "--render-text" in self.last_command

    # ---------------------------------------------------------------- 结果
    def _apply_result(self, result: dict, rerender: bool = False) -> None:
        """把 CLI 结果填进界面（日志 + 三个标签页）。"""
        self.result = result
        if result.get("outdir"):
            self.outdir = result["outdir"]
        if result.get("title") and not self.var_title.get().strip():
            self.var_title.set(result["title"])

        self.log("完成：%s" % (result.get("message") or "扒谱成功"))
        bits = []
        if result.get("key"):
            conf = result.get("key_confidence")
            bits.append("调性=%s%s" % (result["key"],
                                      ("（置信度 %.0f%%）" % (float(conf) * 100))
                                      if isinstance(conf, (int, float)) else ""))
        if result.get("bpm"):
            bits.append("速度=%.1f BPM" % float(result["bpm"]))
        if result.get("meter"):
            bits.append("拍号=%s" % result["meter"])
        if result.get("measures") is not None:
            bits.append("小节=%s" % result["measures"])
        if result.get("notes") is not None:
            bits.append("音符=%s" % result["notes"])
        if isinstance(result.get("duration"), (int, float)):
            bits.append("时长=%.1f 秒" % float(result["duration"]))
        if isinstance(result.get("elapsed"), (int, float)):
            bits.append("耗时=%.2f 秒" % float(result["elapsed"]))
        if bits:
            self.log("识别结果：" + "，".join(bits))
        if result.get("outdir"):
            self.log("输出目录：%s" % result["outdir"])
        for path in result.get("png", []):
            self.log("  图片：%s" % path)
        for key, label in (("pdf", "PDF"), ("text", "简谱文本"),
                           ("table", "指法表"), ("midi", "MIDI")):
            if result.get(key):
                self.log("  %s：%s" % (label, result[key]))
        for w in result.get("warnings", []):
            self.log("提示：%s" % w)

        # 预览页
        pngs = [p for p in result.get("png", []) if p and os.path.isfile(p)]
        if pngs:
            self.set_pages(pngs)
        elif result.get("preview") and os.path.isfile(result["preview"]):
            self.set_pages([result["preview"]])
        else:
            self.log("（本次没有生成 PNG 预览）")

        # 简谱文本
        if result.get("text") and os.path.isfile(result["text"]):
            if not rerender:      # 重新排版时保留用户正在编辑的内容
                self.text_source = result["text"]
                self.text_box.delete("1.0", "end")
                self.text_box.insert("1.0", read_text_file(result["text"]))
                self.var_text_hint.set(os.path.basename(result["text"]))
                self.log("已载入简谱文本，可编辑后点「按修改重新排版」。")
            else:
                self.text_source = result["text"]
                self.log("重新排版完成（文本保持不变）。")

        # 指法表
        if result.get("table") and os.path.isfile(result["table"]):
            self.table_box.configure(state="normal")
            self.table_box.delete("1.0", "end")
            self.table_box.insert("1.0", read_text_file(result["table"]))
            self.table_box.configure(state="disabled")
            self.log("已载入 88 键指法表。")

    @staticmethod
    def _summary(result: dict) -> str:
        parts = ["扒谱完成！"]
        if result.get("title"):
            parts.append("曲名：%s" % result["title"])
        if result.get("key"):
            parts.append("调性：%s" % result["key"])
        if result.get("bpm"):
            parts.append("速度：%.1f BPM" % float(result["bpm"]))
        if result.get("measures") is not None:
            parts.append("小节数：%s" % result["measures"])
        if result.get("outdir"):
            parts.append("输出目录：\n%s" % result["outdir"])
        return "\n".join(parts)

    # ---------------------------------------------------------------- 预览
    def set_pages(self, paths: list) -> None:
        """设置预览页列表（默认 1:1 显示，可滚动；需要缩小请看「适应宽度」按钮）。"""
        self.pages = list(paths)
        self.page_index = 0
        self.show_page(0)
        self.log("预览共 %d 页。" % len(self.pages))

    def _on_canvas_configure(self, _event=None) -> None:
        """画布尺寸变化时保持滚动区域正确（不自动改变缩放，避免预览变得太小）。"""
        if self.page_photo is not None:
            self.page_canvas.configure(
                scrollregion=(0, 0, self.page_photo.width(), self.page_photo.height()))

    def show_page(self, index: int) -> None:
        """显示第 index 页（从 0 开始），默认 1:1 原始像素。"""
        if not self.pages:
            self.var_page.set("0/0")
            return
        index = max(0, min(len(self.pages) - 1, index))
        self.page_index = index
        path = self.pages[index]
        self.var_page.set("%d/%d" % (index + 1, len(self.pages)))
        try:
            photo = tk.PhotoImage(file=path)   # tkinter 原生支持 PNG
        except Exception as exc:  # noqa: BLE001
            self.page_base = None
            self.page_photo = None
            self.page_canvas.delete("all")
            self.page_canvas.create_text(20, 20, anchor="nw",
                                         text="无法显示第 %d 页：\n%s\n%s"
                                              % (index + 1, path, exc))
            self.var_zoom.set("缩放：加载失败")
            self.log("预览加载失败：%s（%s）" % (path, exc))
            return
        self.page_base = photo               # 保底原图，供切换缩放
        self.page_photo = photo
        self.page_size = (photo.width(), photo.height())
        self._fit_factor = 1
        self.var_zoom.set("缩放 1:1（%d×%d 像素）" % self.page_size)
        self._draw_page()

    def _draw_page(self) -> None:
        if self.page_photo is None:
            return
        self.page_canvas.delete("all")
        self.page_canvas.create_image(0, 0, anchor="nw", image=self.page_photo)
        self.page_canvas.configure(scrollregion=(0, 0, self.page_photo.width(),
                                                 self.page_photo.height()))
        self.page_canvas.xview_moveto(0.0)
        self.page_canvas.yview_moveto(0.0)

    def fit_page(self) -> None:
        """适应宽度 / 还原 1:1 的切换。

        只按「宽度」适配（整数 subsample，不做插值），页面纵向可滚动，
        这样音符大小仍然看得清；再点一次回到 1:1 原图。
        """
        if self.page_base is None or not self.pages:
            return
        if self._fit_factor > 1:
            # 当前是缩小状态 -> 还原成原图
            self.page_photo = self.page_base.copy()
            self._fit_factor = 1
            self._draw_page()
            self.var_zoom.set("缩放 1:1（%d×%d 像素）" % self.page_size)
            self.log("预览已还原为 1:1。")
            return
        w = self.page_canvas.winfo_width()
        if w <= 20:
            w = 900                   # 画布还没排版完，按常见尺寸估算
        w -= 24                       # 留出滚动条与边距
        factor = max(1, -(-self.page_size[0] // max(1, w)))
        factor = min(8, factor)
        if factor == 1:
            self.var_zoom.set("缩放 1:1（页面宽度已适配）")
            self.log("页面宽度在 1:1 下已适配画布。")
            return
        self.page_photo = self.page_base.subsample(factor, factor)
        self._fit_factor = factor
        self._draw_page()
        self.var_zoom.set("缩放 1/%d（再点一次还原 1:1）" % factor)
        self.log("预览按宽度缩放：1/%d" % factor)

    # ---------------------------------------------------------------- 自检
    def log_selfcheck(self) -> None:
        self.log("自检：界面构建成功。")


def run_selfcheck() -> int:
    """--selfcheck：只做导入 + 构建界面 + 销毁，不开窗。"""
    try:
        root = tk.Tk()
    except Exception as exc:  # 没有显示环境时退化为 import 检查
        safe_print("SELFCHECK OK (no display: %s)" % exc)
        return 0
    try:
        root.withdraw()
        app = JianpuApp(root)
        app.log_selfcheck()
        root.update_idletasks()
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    safe_print("SELFCHECK OK")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selfcheck" in argv:
        return run_selfcheck()

    try:
        root = tk.Tk()
    except Exception as exc:  # noqa: BLE001
        safe_print("无法创建窗口：%s" % exc)
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "无法创建图形界面：\n%s" % exc,
                                             APP_TITLE, 0x10)
        except Exception:
            pass
        return 1
    JianpuApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
