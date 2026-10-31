# -*- coding: utf-8 -*-
"""示例: 通过本地 HTTP API 读取状态(先运行 `netease-lyric serve`)。

    python examples/http_client.py
"""
import json
import urllib.request

BASE = "http://127.0.0.1:23517"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    health = get("/api/health")
    print("health:", health)
    if not health.get("netease_running"):
        print("网易云未运行")
        return

    st = get("/api/state")
    if st.get("track"):
        print("正在播放:", st["track"]["title"])
        print("进度:", st["position_ms"], "/", st["duration_ms"], "ms")
    if st.get("line"):
        print("当前歌词:", st["line"]["text"], "|", st["line"]["translation"])

    # 完整歌词
    ly = get("/api/lyrics")
    print("歌词行数:", len(ly["lines"]))
    for ln in ly["lines"][:5]:
        print(f"  {ln['start_ms']/1000:7.2f}s  {ln['text']}")


if __name__ == "__main__":
    main()
