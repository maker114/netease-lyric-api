# -*- coding: utf-8 -*-
"""cli.py - 命令行入口: 状态查询 / JSON / 监听 / 封面 / HTTP 服务 / 悬浮窗。

安装后可直接使用::

    netease-lyric status          # 打印当前播放与歌词状态
    netease-lyric json            # 输出完整状态 JSON
    netease-lyric watch           # 持续打印歌词行变化
    netease-lyric cover           # 打印当前歌曲封面路径(可 --out 另存)
    netease-lyric serve           # 启动本地 HTTP API (默认 127.0.0.1:23517)
    netease-lyric overlay         # 启动桌面悬浮歌词窗(可选 GUI)
"""
import argparse
import json
import shutil
import sys
import time

from ._version import API_NAME, __version__


def _fmt(ms):
    from .player import format_ms
    return format_ms(ms)


def _print_status(api) -> int:
    st = api.state()
    print(f"{API_NAME} v{__version__}")
    if not st["netease_running"]:
        print("✗ 未检测到网易云音乐客户端 (cloudmusic.exe) 在运行")
        return 1
    tr = st["track"]
    if not tr:
        print("… 网易云已运行, 正在识别当前歌曲")
        return 0
    print(f"♪ {tr['title']}")
    if tr.get("album"):
        print(f"  专辑 {tr['album']}")
    bits = []
    if tr.get("song_id"):
        bits.append(f"song_id={tr['song_id']}")
    if tr.get("source"):
        bits.append(f"来源={tr['source']}")
    if tr.get("duration_ms"):
        bits.append(f"时长={_fmt(tr['duration_ms'])}")
    if bits:
        print("  " + "  ".join(bits))
    if tr.get("cover_path"):
        print(f"  封面 {tr['cover_path']}")
    elif tr.get("cover_url"):
        print(f"  封面 {tr['cover_url']} (未缓存)")
    state = "⏸ 暂停" if st["paused"] else "▶ 播放中"
    progress = (f"  ({st['progress'] * 100:.0f}%)"
                if st.get("progress") is not None else "")
    print(f"  进度 {_fmt(st['position_ms'])} / {_fmt(st['duration_ms'])}{progress}   {state}")

    line = st["line"]
    if line:
        print(f"  当前 [{line['index']:>2}] {line['text']}")
        if line["translation"]:
            print(f"           {line['translation']}")
        if st["next_line"]:
            print(f"  下一句   {st['next_line']['text']}")
    elif st["lyric_loading"]:
        print("  歌词: 获取中…")
    elif not st["lyric_ready"]:
        print("  歌词: 暂无(纯音乐或未找到)")
    if st["syncing"]:
        print("  (正在定位播放进度…)")
    if st.get("error"):
        print(f"  ⚠ {st['error']}")
    return 0


def _cmd_watch(api) -> int:
    api.wait_for_ready(timeout=8.0)
    print("监听中, Ctrl+C 退出")
    last_song = None
    last_idx = None
    try:
        while True:
            st = api.state()
            tr = st["track"]
            line = st["line"]
            if tr and tr["title"] != last_song:
                last_song = tr["title"]
                last_idx = None
                print(f"\n▶ {tr['title']}  ({_fmt(tr['duration_ms'])})")
            if line and line["index"] != last_idx:
                last_idx = line["index"]
                text = line["text"]
                trans = line["translation"]
                print(f"[{time.strftime('%H:%M:%S')}] {_fmt(st['position_ms'])}  {text}"
                      + (f"  |  {trans}" if trans else ""))
            time.sleep(0.1)
    except KeyboardInterrupt:
        print()
        return 0


def _cmd_cover(args) -> int:
    """打印当前歌曲封面缓存路径; --out 时另存一份。"""
    from .player import NeteaseLyric
    api = NeteaseLyric()
    try:
        api.wait_for_ready(timeout=args.wait)
        path = api.ensure_cover()
        if not path:
            print("✗ 未能获取当前歌曲封面(未识别到歌曲、纯音乐或网络不可达)")
            return 1
        print(path)
        if args.out:
            shutil.copyfile(path, args.out)
            print(f"✓ 封面已保存到 {args.out}")
        return 0
    finally:
        api.stop()


def _cmd_serve(args) -> int:
    from .server import LyricApiServer
    srv = LyricApiServer(host=args.host, port=args.port,
                         lyric_offset_ms=args.offset)
    print(f"HTTP API 已启动: http://{args.host}:{args.port}")
    print("  GET /api/state | /api/now-playing | /api/line | /api/lyrics"
          " | /api/cover | /api/stream")
    print(f"  浏览器打开 http://{args.host}:{args.port}/ 可看实时歌词页")
    print("Ctrl+C 退出")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
    return 0


def _cmd_doctor(args) -> int:
    """环境自检: 判断当前电脑能否直接使用, 以及哪里需要处理。"""
    import sys as _sys
    print("netease-lyric doctor — 环境自检")
    print("=" * 52)
    fails = []

    def check(cond, text, advice=""):
        print(("✓ " if cond else "✗ ") + text
              + (f"\n    → {advice}" if (not cond and advice) else ""))
        if not cond:
            fails.append(text)
        return cond

    def info(text):
        print("· " + text)

    check(_sys.platform == "win32", f"操作系统: {_sys.platform} (需要 win32)",
          "本库仅支持 Windows")
    check(_sys.version_info >= (3, 8),
          f"Python: {_sys.version.split()[0]} (需要 >= 3.8)", "升级 Python")
    try:
        import numpy
        check(True, f"numpy: {numpy.__version__}")
    except Exception:
        check(False, "numpy: 未安装", "pip install numpy")
    try:
        import tkinter  # noqa: F401
        info("tkinter: 可用 (可选 GUI)")
    except Exception:
        info("tkinter: 不可用 (不影响纯接口调用, 只是无法使用悬浮窗 GUI)")
    try:
        import PIL  # noqa: F401
        info("Pillow: 可用 (悬浮窗可显示专辑封面)")
    except Exception:
        info("Pillow: 不可用 (悬浮窗不显示封面, 歌词/专辑/歌手文字不受影响)")

    from .clock import close_handle, find_ncm_window, get_window_title, open_process
    hwnd, pid = find_ncm_window()
    found = check(
        bool(hwnd),
        "网易云窗口: " + (
            f"pid={pid} 标题={get_window_title(hwnd)!r}" if hwnd else "未找到"),
        "先启动网易云音乐 PC 客户端并播放歌曲")
    if hwnd:
        try:
            h = open_process(pid)
            close_handle(h)
            check(True, "进程内存读取权限: OK")
        except Exception as e:
            check(False, f"进程内存读取权限: 失败 ({e})",
                  "让本程序与网易云使用相同权限运行(都管理员或都普通)")

    from .player import NeteaseLyric
    api = NeteaseLyric()
    try:
        ready = api.wait_for_ready(timeout=15)
        st = api.state()
        check(st["netease_running"],
              "后台同步: 网易云运行中" if st["netease_running"] else "后台同步: 未检测到网易云",
              "启动网易云音乐")
        check(st["clock_ready"],
              f"播放进度定位: {'OK' if st['clock_ready'] else '未成功'}"
              f" (position={st['position_ms']})",
              "确认正在播放; 若刚启动可稍等, 或调用 rediscover()")
        if st["clock_ready"]:
            check(ready, "状态就绪: OK" if ready else "状态就绪: 超时",
                  "检查网络后重试")
        if st["lyric_ready"]:
            info(f"歌词: 已加载 {st['line_count']} 行")
        elif st["lyric_loading"]:
            info("歌词: 加载中…")
        else:
            info("歌词: 暂无(可能是纯音乐, 或网络不可达)")
        if st.get("error"):
            info(f"最近错误: {st['error']}")

        tr = api.now_playing()
        if tr:
            try:
                from .lyrics import get_song_lyrics
                got = get_song_lyrics(tr.name, tr.artist)
                check(got is not None, f"歌词接口连通: {'OK' if got else '失败'}",
                      "检查网络/代理(需要访问 music.163.com)")
            except Exception as e:
                check(False, f"歌词接口连通: 异常 {e}", "检查网络/代理")
        else:
            info("歌词接口: 无当前歌曲, 跳过")
    finally:
        api.stop()

    print("=" * 52)
    if not fails:
        print("结论: 当前电脑环境可用 ✓ (无需额外配置)")
        return 0
    print(f"结论: 有 {len(fails)} 项需要处理, 见上方 ✗ 提示")
    return 2


def _cmd_overlay(args) -> int:
    from .overlay import run_overlay
    run_overlay(width=args.width, font_size=args.font_size, opacity=args.opacity,
                bottom=args.bottom, offset=args.offset, reset_pos=args.reset_pos,
                debug=args.debug)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="netease-lyric",
        description="网易云音乐实时播放状态 / 歌词 API (Windows)",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="command")

    p = sub.add_parser("status", help="打印当前播放与歌词状态")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.add_argument("--offset", type=int, default=0, help="歌词时间轴偏移(ms)")
    p.add_argument("--wait", type=float, default=8.0, help="等待就绪的最长秒数")

    p = sub.add_parser("json", help="输出完整状态 JSON")
    p.add_argument("--offset", type=int, default=0)

    p = sub.add_parser("watch", help="持续打印歌词行变化")
    p.add_argument("--offset", type=int, default=0)

    p = sub.add_parser("cover", help="打印或保存当前歌曲封面")
    p.add_argument("--out", metavar="PATH", help="把封面另存到指定路径")
    p.add_argument("--wait", type=float, default=8.0, help="等待就绪的最长秒数")

    p = sub.add_parser("serve", help="启动本地 HTTP API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=23517)
    p.add_argument("--offset", type=int, default=0)

    p = sub.add_parser("overlay", help="启动桌面悬浮歌词窗(可选 GUI)")
    p.add_argument("--width", type=int, default=1300)
    p.add_argument("--font-size", type=int, default=24)
    p.add_argument("--opacity", type=float, default=0.88)
    p.add_argument("--bottom", type=int, default=48, help="距屏幕底部像素")
    p.add_argument("--offset", type=int, default=60, help="歌词时间轴偏移(ms)")
    p.add_argument("--reset-pos", action="store_true", help="忽略上次窗口位置")
    p.add_argument("--debug", action="store_true")

    sub.add_parser("version", help="打印版本")
    sub.add_parser("doctor", help="环境自检(换电脑/排查问题先跑这个)")

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0
    if args.cmd == "version":
        print(__version__)
        return 0
    if args.cmd == "overlay":
        return _cmd_overlay(args)
    if args.cmd == "serve":
        return _cmd_serve(args)
    if args.cmd == "doctor":
        return _cmd_doctor(args)
    if args.cmd == "cover":
        return _cmd_cover(args)

    # 以下命令需要实例化 API
    from .player import NeteaseLyric
    api = NeteaseLyric(lyric_offset_ms=getattr(args, "offset", 0))
    try:
        if args.cmd == "json":
            api.wait_for_ready(timeout=8.0)
            print(json.dumps(api.state(), ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "status":
            if args.wait:
                api.wait_for_ready(timeout=args.wait)
            if args.json:
                print(json.dumps(api.state(), ensure_ascii=False, indent=2))
                return 0
            return _print_status(api)
        if args.cmd == "watch":
            return _cmd_watch(api)
    finally:
        api.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
