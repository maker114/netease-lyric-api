# -*- coding: utf-8 -*-
"""lyrics.py - 歌词数据层: 按歌名/歌手匹配网易云 song id, 取 LRC + 翻译, 带本地缓存。

匹配顺序:
  1. 网易云本地播放队列(webdata/file/playingList) - 最准, 和正在播放的版本一致;
  2. 网易云搜索接口 - 兜底。
歌词来源: music.163.com 歌词接口 (lrc + tlyric)。
"""
import json
import os
import re
import urllib.parse
import urllib.request

from ._version import DISPLAY_NAME

CACHE_DIR = os.environ.get("NETEASE_LYRIC_CACHE_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    DISPLAY_NAME, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

PLAYING_LIST = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "Netease", "CloudMusic", "webdata", "file", "playingList")

_HEADERS = {
    "Referer": "https://music.163.com/",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
}

TS_RE = re.compile(r"\[(\d+):(\d+)(?:[.:](\d+))?\]")
META_RE = re.compile(
    r"^\s*(作词|作曲|编曲|制作人|制作|出品|发行|监制|混音|母带|录音|和声|吉他|贝斯|鼓|"
    r"键盘|弦乐|OP|SP|词|曲)\s*[:：]")

_song_index = None
_queue_mtime = 0
_queue_map = []   # [(name_lower, artist_lower, id, duration_ms)]


# ------------------------------------------------------------------ helpers
def _api(url):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8"))


def _load_index():
    global _song_index
    if _song_index is None:
        p = os.path.join(CACHE_DIR, "songcache.json")
        try:
            with open(p, "r", encoding="utf-8") as f:
                _song_index = json.load(f)
        except Exception:
            _song_index = {}
    return _song_index


def _save_index():
    p = os.path.join(CACHE_DIR, "songcache.json")
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_song_index, f, ensure_ascii=False)
    except Exception:
        pass


def _load_queue():
    """读取网易云本地播放队列, 建立 歌名|歌手 -> song_id 映射。"""
    global _queue_mtime, _queue_map
    try:
        mt = os.path.getmtime(PLAYING_LIST)
    except OSError:
        return
    if mt == _queue_mtime:
        return
    try:
        with open(PLAYING_LIST, "rb") as f:
            data = json.loads(f.read().decode("utf-8", errors="replace"))
        rows = []
        for it in data.get("list") or []:
            tr = it.get("track") or {}
            name = (tr.get("name") or "").strip()
            artists = "/".join(a.get("name", "") for a in (tr.get("artists") or [])).lower()
            if name and tr.get("id"):
                rows.append((name.lower(), artists, int(tr["id"]),
                             int(tr.get("duration") or 0)))
        _queue_map = rows
        _queue_mtime = mt
    except Exception:
        pass


def lookup_queue(name, artist):
    """在本地播放队列里查找 (song_id, duration_ms)。"""
    _load_queue()
    nl = (name or "").strip().lower()
    al = (artist or "").strip().lower()
    for qn, qa, sid, dur in _queue_map:
        if qn == nl and (not al or al in qa or qa in al):
            return sid, dur
    for qn, qa, sid, dur in _queue_map:
        if qn == nl:
            return sid, dur
    return None


# ------------------------------------------------------------------- lyric
def _parse_lrc(text):
    out = {}
    if not text:
        return out
    for raw in text.splitlines():
        times = TS_RE.findall(raw)
        if not times:
            continue
        txt = TS_RE.sub("", raw).strip()
        for mm, ss, ff in times:
            ms = int(mm) * 60000 + int(ss) * 1000
            if ff:
                ms += int((ff + "000")[:3])
            out.setdefault(ms, txt)
    return out


def parse_lyrics(lrc_text, tlyric_text=""):
    """把 LRC 文本解析为 [(time_ms, 原文, 翻译)] 列表。"""
    main = _parse_lrc(lrc_text)
    trans = _parse_lrc(tlyric_text)
    lines = []
    for t, txt in sorted(main.items()):
        if not txt:
            continue
        if t < 1500 and META_RE.match(txt):
            continue
        lines.append((t, txt, trans.get(t, "")))
    return lines


def fetch_lyrics(song_id):
    """按 song id 拉取并解析歌词。"""
    try:
        data = _api(f"https://music.163.com/api/song/lyric?os=pc&id={song_id}"
                    f"&lv=1&kv=1&tv=-1")
    except Exception:
        return []
    lrc = (data.get("lrc") or {}).get("lyric") or ""
    tlrc = (data.get("tlyric") or {}).get("lyric") or ""
    return parse_lyrics(lrc, tlrc)


def search_song(name, artist):
    """搜索接口: 返回 (song_id, duration_ms) 或 None。"""
    idx = _load_index()
    key = f"{name}|{artist}".lower()
    if key in idx:
        return idx[key]["id"], idx[key]["duration"]
    q = urllib.parse.quote(f"{name} {artist}".strip())
    try:
        data = _api(f"https://music.163.com/api/search/get?s={q}&type=1&limit=10&offset=0")
    except Exception:
        return None
    songs = (data.get("result") or {}).get("songs") or []
    if not songs:
        return None
    best = None
    for s in songs:
        sname = (s.get("name") or "").strip().lower()
        artists = "/".join(a.get("name", "") for a in (s.get("artists") or [])).lower()
        if sname == name.strip().lower() and (not artist or artist.strip().lower() in artists):
            best = s
            break
    if best is None:
        best = songs[0]
    res = (best["id"], best.get("duration") or 0)
    idx[key] = {"id": res[0], "duration": res[1]}
    _save_index()
    return res


def clean_credit_lines(lines, name, artist):
    """去掉歌词开头的歌手名/歌名等非歌词行。"""
    al = (artist or "").strip().lower()
    nl = (name or "").strip().lower()
    out = []
    for t, txt, tr in lines:
        low = txt.strip().lower()
        if t < 10000 and (low == al or low == nl or
                          (al and low == al.rsplit("/", 1)[-1].strip())):
            continue
        out.append((t, txt, tr))
    return out


def get_song_lyrics(name, artist):
    """一站式: 匹配 song id + 取歌词。

    返回 dict:
        {id, duration, lines: [(t_ms, text, translation)], source, name, artist}
    或 None。
    """
    hit = lookup_queue(name, artist)
    source = "queue"
    if not hit:
        hit = search_song(name, artist)
        source = "search"
    if not hit:
        return None
    song_id, duration = hit
    cache_file = os.path.join(CACHE_DIR, f"{song_id}.json")
    lines = None
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                lines = [tuple(x) for x in json.load(f)]
        except Exception:
            lines = None
    if lines is None:
        lines = fetch_lyrics(song_id)
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(lines, f, ensure_ascii=False)
        except Exception:
            pass
    lines = clean_credit_lines(lines, name, artist)
    return {"id": song_id, "duration": duration, "lines": lines,
            "source": source, "name": name, "artist": artist}


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "Far From Any Road - The Handsome Family"
    name, artist = (q.rsplit(" - ", 1) if " - " in q else (q, ""))
    info = get_song_lyrics(name.strip(), artist.strip())
    print(info and (info["id"], info["duration"], len(info["lines"]), info["source"]))
    if info:
        for t, s, tr in info["lines"][:8]:
            print(f"{t/1000:7.2f}s | {s} | {tr}")
