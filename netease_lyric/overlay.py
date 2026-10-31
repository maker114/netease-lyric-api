# -*- coding: utf-8 -*-
"""overlay.py - 可选 GUI: 桌面悬浮歌词窗(基于 :class:`NeteaseLyric` API)。

本模块是"接口层"的一个消费者: 只读取 ``api.state()``, 不包含任何内存/网络逻辑。
tkinter 只有导入本模块时才加载, 纯 API 调用不会依赖 GUI。

启动方式::

    python -m netease_lyric overlay
    # 或安装后: netease-lyric overlay / netease-lyric-overlay
"""
import argparse
import ctypes
import json
import logging
import os
import time

import tkinter as tk
import tkinter.font as tkfont

from ._version import __version__
from .player import NeteaseLyric, format_ms

LOG = logging.getLogger("netease_lyric.overlay")

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                       "NeteaseLyricApi")
CONFIG_FILE = os.path.join(APP_DIR, "window.json")
BG = "#0b0d12"

# ------------------------------------------------------------------ Win32
_u32 = ctypes.windll.user32
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _enable_dpi_awareness():
    """让 tkinter 使用物理像素坐标(避免高 DPI 屏窗口错位)。"""
    try:
        _u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        _u32.SetProcessDPIAware()
    except Exception:
        pass


def _hwnd_of(root):
    wid = root.winfo_id()
    return _u32.GetParent(wid) or wid


def _win_rect(hwnd):
    r = _RECT()
    if _u32.GetWindowRect(hwnd, ctypes.byref(r)):
        return r.left, r.top, r.right, r.bottom
    return 0, 0, 0, 0


def _set_win_pos(hwnd, x, y, w, h):
    _u32.SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST), int(x), int(y),
                      int(w), int(h), SWP_NOACTIVATE | SWP_SHOWWINDOW)


def _screen_size():
    return _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1)


# ------------------------------------------------------------------- GUI
class _OverlayApp:
    def __init__(self, width: int, font_size: int, opacity: float, bottom: int,
                 offset: int, reset_pos: bool):
        self.width = width
        self.font_size = font_size
        self.opacity = opacity
        self.bottom = bottom
        self.reset_pos = reset_pos

        self.api = NeteaseLyric(lyric_offset_ms=offset, auto_start=True)
        self._dragging = False
        self._drag_off = (0, 0)

        self._build_ui()
        self.root.after(100, self._tick)

    # -------------------------------------------------------------- build
    def _build_ui(self):
        _enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", self.opacity)
        self.root.configure(bg=BG)
        self.root.title("NeteaseLyricOverlay")

        w, h = self.width, 226
        sw, sh = _screen_size()
        x, y = max(0, (sw - w) // 2), max(0, sh - h - self.bottom)
        cfg = None
        if not self.reset_pos:
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if (cfg.get("v") == 2 and "x" in cfg and "y" in cfg
                        and 0 <= cfg["x"] <= sw - 200 and 0 <= cfg["y"] <= sh - 100):
                    x, y = cfg["x"], cfg["y"]
                else:
                    cfg = None
            except Exception:
                cfg = None
        LOG.debug("screen=%sx%s pos=%s,%s config=%s", sw, sh, x, y, cfg)

        self.root.geometry(f"{w}x{h}+0+0")
        self.root.update_idletasks()
        self.root.update()
        self._hwnd = _hwnd_of(self.root)
        _set_win_pos(self._hwnd, x, y, w, h)

        f_head = tkfont.Font(family="Microsoft YaHei UI", size=10)
        f_side = tkfont.Font(family="Microsoft YaHei UI",
                             size=max(9, self.font_size - 11))
        f_cur = tkfont.Font(family="Microsoft YaHei UI", size=self.font_size,
                            weight="bold")
        f_tr = tkfont.Font(family="Microsoft YaHei UI",
                           size=max(9, self.font_size - 9))

        pad = 20
        self.lbl_head = tk.Label(self.root, text="♪ 正在连接网易云…", font=f_head,
                                 fg="#7d8598", bg=BG, anchor="w")
        self.lbl_head.pack(fill="x", padx=pad, pady=(9, 0))
        self.lbl_prev = tk.Label(self.root, text=" ", font=f_side, fg="#6b7280", bg=BG)
        self.lbl_prev.pack(fill="x", padx=pad, pady=(3, 0))
        self.lbl_cur = tk.Label(self.root, text=" ", font=f_cur, fg="#ffffff", bg=BG,
                                wraplength=w - 80, justify="center")
        self.lbl_cur.pack(fill="x", padx=pad, pady=(2, 0))
        self.lbl_tr = tk.Label(self.root, text=" ", font=f_tr, fg="#8ecdf7", bg=BG,
                               wraplength=w - 80, justify="center")
        self.lbl_tr.pack(fill="x", padx=pad, pady=(0, 0))
        self.lbl_next = tk.Label(self.root, text=" ", font=f_side, fg="#6b7280", bg=BG)
        self.lbl_next.pack(fill="x", padx=pad, pady=(3, 6))

        self.bar_bg = tk.Frame(self.root, bg="#1c2230", height=3)
        self.bar_bg.pack(fill="x", side="bottom")
        self.bar = tk.Frame(self.bar_bg, bg="#5b8def", height=3, width=1)
        self.bar.place(x=0, y=0, relheight=1, relwidth=0)

        for wd in (self.root, self.lbl_head, self.lbl_prev, self.lbl_cur,
                   self.lbl_tr, self.lbl_next, self.bar_bg):
            wd.bind("<ButtonPress-1>", self._drag_start)
            wd.bind("<B1-Motion>", self._drag_move)
        self.root.bind("<Escape>", lambda e: self.quit())
        self.root.bind("<Button-3>", self._menu)

    # ------------------------------------------------------------- widget
    @staticmethod
    def _set(label, text: str):
        if label.cget("text") != text:
            label.configure(text=text)

    # ------------------------------------------------------------- events
    def _drag_start(self, e):
        l, t, _r, _b = _win_rect(self._hwnd)
        self._drag_off = (e.x_root - l, e.y_root - t)
        self._dragging = True

    def _drag_move(self, e):
        if not self._dragging:
            return
        l, t, r, b = _win_rect(self._hwnd)
        w, h = max(200, r - l), max(60, b - t)
        _set_win_pos(self._hwnd, e.x_root - self._drag_off[0],
                     e.y_root - self._drag_off[1], w, h)

    def _menu(self, e):
        m = tk.Menu(self.root, tearoff=0, bg=BG, fg="white",
                    activebackground="#2a3550", activeforeground="white")
        m.add_command(label="重新定位播放进度", command=self.api.rediscover)
        m.add_separator()
        m.add_command(label="退出", command=self.quit)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def save_pos(self):
        try:
            l, t, _r, _b = _win_rect(self._hwnd)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"v": 2, "x": l, "y": t}, f)
        except Exception:
            LOG.debug("save_pos failed", exc_info=True)

    def quit(self):
        try:
            self.save_pos()
            self.api.stop()
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass
            os._exit(0)

    # ------------------------------------------------------------- render
    def _tick(self):
        try:
            self._render(self.api.state())
        except Exception:
            LOG.exception("render failed")
        self.root.after(100, self._tick)

    def _render(self, st):
        running = st["netease_running"]
        track = st["track"]

        if not running:
            head = "♪ 等待网易云音乐启动…"
        else:
            head = "♪ " + (track["title"] if track else "正在识别歌曲…")
            if track and st["duration_ms"]:
                head += (f"    {format_ms(st['position_ms'])}"
                         f" / {format_ms(st['duration_ms'])}")
            if st["paused"]:
                head += "   ⏸ 已暂停"
            if st["syncing"]:
                head += "   · 同步进度中…"
            elif not st["clock_ready"]:
                head += "   · 等待播放定位…"
        self._set(self.lbl_head, head)

        if st.get("progress") is not None:
            self.bar.place_configure(relwidth=float(st["progress"]))

        line, prev, nxt = st["line"], st["prev_line"], st["next_line"]
        if not running or not track:
            self._set(self.lbl_prev, " ")
            self._set(self.lbl_cur, " " if not running else "正在识别歌曲…")
            self._set(self.lbl_tr, " ")
            self._set(self.lbl_next, " ")
            return
        if line:
            self._set(self.lbl_prev, prev["text"] if prev else " ")
            self._set(self.lbl_cur, line["text"])
            self._set(self.lbl_tr, line["translation"] or " ")
            self._set(self.lbl_next, nxt["text"] if nxt else " ")
        elif st["lyric_loading"]:
            self._set(self.lbl_prev, " ")
            self._set(self.lbl_cur, "正在获取歌词…")
            self._set(self.lbl_tr, " ")
            self._set(self.lbl_next, " ")
        elif st["lyric_ready"]:
            self._set(self.lbl_prev, " ")
            self._set(self.lbl_cur, "♪ 前奏 ♪")
            self._set(self.lbl_tr, " ")
            self._set(self.lbl_next, nxt["text"] if nxt else " ")
        else:
            self._set(self.lbl_prev, " ")
            self._set(self.lbl_cur, "纯音乐 / 暂无歌词")
            self._set(self.lbl_tr, " ")
            self._set(self.lbl_next, " ")

    def run(self):
        self.root.mainloop()


# ------------------------------------------------------------------ entry
def run_overlay(width: int = 1300, font_size: int = 24, opacity: float = 0.88,
                bottom: int = 48, offset: int = 60, reset_pos: bool = False,
                debug: bool = False) -> None:
    """启动悬浮歌词窗(阻塞, 关闭窗口后返回)。"""
    os.makedirs(APP_DIR, exist_ok=True)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            filename=os.path.join(APP_DIR, "overlay.log"),
            level=logging.DEBUG if debug else logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            encoding="utf-8",
        )
    LOG.info("overlay starting (v%s)", __version__)
    app = _OverlayApp(width=width, font_size=font_size, opacity=opacity,
                      bottom=bottom, offset=offset, reset_pos=reset_pos)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            app.api.stop()
        except Exception:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="netease-lyric-overlay",
        description="网易云音乐桌面悬浮歌词窗(可选 GUI)",
    )
    ap.add_argument("--width", type=int, default=1300)
    ap.add_argument("--font-size", type=int, default=24)
    ap.add_argument("--opacity", type=float, default=0.88)
    ap.add_argument("--bottom", type=int, default=48, help="距屏幕底部像素")
    ap.add_argument("--offset", type=int, default=60, help="歌词时间轴偏移(ms)")
    ap.add_argument("--reset-pos", action="store_true", help="忽略上次窗口位置")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    run_overlay(width=args.width, font_size=args.font_size, opacity=args.opacity,
                bottom=args.bottom, offset=args.offset, reset_pos=args.reset_pos,
                debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
