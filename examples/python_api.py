# -*- coding: utf-8 -*-
"""示例: 在 Python 里调用 netease_lyric 接口。

先确保网易云音乐 PC 客户端正在播放, 然后:

    python examples/python_api.py
"""
import time

from netease_lyric import NeteaseLyric, format_ms


def main():
    # 构造即开始后台同步; 退出 with 时自动停止
    with NeteaseLyric(lyric_offset_ms=0) as api:
        print("等待状态就绪…")
        if not api.wait_for_ready(timeout=10):
            print("未检测到网易云音乐(cloudmusic.exe)或状态未就绪")
            return

        song = api.now_playing()
        print(f"\n♪ {song.title}    ({format_ms(song.duration_ms)})")
        print(f"  song_id={song.song_id}  来源={song.source}")
        print(f"  歌手={'/'.join(song.artists or []) or song.artist or '-'}")
        print(f"  专辑={song.album or '(未知)'}")
        print(f"  封面={song.cover_url or '(无)'}")
        print(f"  封面缓存={song.cover_path or '(未缓存)'}")
        # 需要本地封面文件时: api.ensure_cover() 会懒下载并返回路径
        print(f"  进度={format_ms(api.position_ms())}  暂停={api.is_paused()}")

        line = api.current_line()
        if line:
            print(f"  当前歌词[{line.index}]: {line.text}")
            if line.translation:
                print(f"             {line.translation}")

        all_lines = api.lyrics()
        print(f"  歌词共 {len(all_lines)} 行")
        for ln in all_lines[:5]:
            print(f"    {format_ms(ln.start_ms)}  {ln.text}")

        # 事件回调示例: 打印 10 秒内发生的事件
        print("\n监听事件 10 秒 (Ctrl+C 退出)…")

        def on_event(ev):
            if ev["event"] in ("song_change", "line_change", "lyric_ready"):
                track = ev.get("track") or {}
                print(f"  [{ev['event']}] {track.get('title', '')} "
                      f"{(ev.get('line') or {}).get('text', '')}")

        api.add_listener(on_event)
        try:
            time.sleep(10)
        except KeyboardInterrupt:
            pass
        api.remove_listener(on_event)


if __name__ == "__main__":
    main()
