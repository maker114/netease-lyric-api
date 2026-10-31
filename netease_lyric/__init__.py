# -*- coding: utf-8 -*-
"""netease_lyric - 网易云音乐实时播放状态 / 歌词 API(Windows)。

这是一个可被任意 Python 项目安装并调用的库:

    pip install netease-lyric-api

    from netease_lyric import NeteaseLyric

    with NeteaseLyric() as api:
        api.wait_for_ready()
        print(api.now_playing().title)      # 当前歌曲
        print(api.position_ms())            # 实时播放进度(ms)
        print(api.current_line().text)      # 当前歌词行

注意:
  * 仅支持 Windows(需要读取网易云音乐 PC 客户端 cloudmusic.exe 的内存);
  * GUI(悬浮歌词窗)是可选模块, 只有显式调用 ``python -m netease_lyric overlay``
    或入口 ``netease-lyric-overlay`` 时才会加载 tkinter, 纯接口调用不会依赖 GUI。
"""
import sys

from ._version import API_NAME, DISPLAY_NAME, __version__

__all__ = [
    "NeteaseLyric",
    "Track",
    "LyricLine",
    "format_ms",
    "get_song_lyrics",
    "API_NAME",
    "DISPLAY_NAME",
    "__version__",
]

_WINDOWS_ONLY = (
    "netease_lyric 仅支持 Windows: 播放进度需要读取网易云音乐 PC 客户端"
    "(cloudmusic.exe) 的进程内存。"
)


def __getattr__(name):
    """懒加载公开 API, 避免 import 包时就加载 ctypes/numpy/tkinter。"""
    if name in __all__ and name != "__version__":
        if sys.platform != "win32":
            raise RuntimeError(_WINDOWS_ONLY)
        if name == "get_song_lyrics":
            from .lyrics import get_song_lyrics
            return get_song_lyrics
        from . import player
        return getattr(player, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
