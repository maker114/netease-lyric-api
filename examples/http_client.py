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
        tr = st["track"]
        print("正在播放:", tr["title"])
        print("歌手:", "/".join(tr.get("artists") or []) or tr.get("artist") or "-")
        print("专辑:", tr.get("album") or "-")
        print("封面:", tr.get("cover_url") or "(无)")
        print("进度:", st["position_ms"], "/", st["duration_ms"], "ms")
    if st.get("line"):
        print("当前歌词:", st["line"]["text"], "|", st["line"]["translation"])

    # 完整歌词
    ly = get("/api/lyrics")
    print("歌词行数:", len(ly["lines"]))
    for ln in ly["lines"][:5]:
        print(f"  {ln['start_ms']/1000:7.2f}s  {ln['text']}")

    # 封面图: /api/cover 返回图片 bytes; 未缓存时可能失败, 不影响上面的输出
    try:
        with urllib.request.urlopen(BASE + "/api/cover", timeout=10) as r:
            data = r.read()
        with open("cover.jpg", "wb") as f:
            f.write(data)
        print("封面已保存: cover.jpg")
    except Exception as e:
        print("封面未缓存或获取失败:", e)


if __name__ == "__main__":
    main()
