# -*- coding: utf-8 -*-
"""lyrics.py - 歌曲详情/歌词数据层: 按歌名/歌手匹配网易云 song id, 取歌曲详情、
LRC + 翻译与专辑封面, 全部带本地缓存。

匹配顺序:
  1. 网易云本地播放队列(webdata/file/playingList) - 最准, 和正在播放的版本一致;
  2. 网易云搜索接口 - 兜底。
歌曲详情来源: music.163.com song detail 接口 (专辑名 / 歌手 / 封面地址)。
歌词来源: music.163.com 歌词接口 (lrc + tlyric)。
封面来源: 专辑封面 URL, 下载后缓存到本地 covers/ 目录。
"""
import json
import os
import re
import threading
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

#: 封面缓存目录与默认请求尺寸(像素)
COVER_DIR = os.path.join(CACHE_DIR, "covers")
COVER_SIZE = 500

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
_index_lock = threading.Lock()

_queue_mtime = 0
_queue_map = []   # [{"name_lc", "artists_lc", "id", "duration", "artists", ...}]

_detail_index = None
_detail_lock = threading.Lock()
_cover_lock = threading.Lock()


# ------------------------------------------------------------------ helpers
def _api(url, timeout: float = 12):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _norm_url(url):
    """统一为 https 地址(网易云老数据里有 http 图片地址)。"""
    url = (url or "").strip()
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url


def _norm_artists(raw):
    """[{"name": "A"}, ...] 或 ["A"] -> ["A", ...](去空去重, 保持顺序)。"""
    names = []
    for item in raw or []:
        if isinstance(item, dict):
            name = item.get("name")
        else:
            name = item
        name = (name or "").strip() if isinstance(name, str) else ""
        if name and name not in names:
            names.append(name)
    return names


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
    """读取网易云本地播放队列, 建立 歌名|歌手 -> 歌曲信息 映射。"""
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
            song_id = _as_int(tr.get("id"))
            if not name or not song_id:
                continue
            artists = _norm_artists(tr.get("artists"))
            album = tr.get("album") or {}
            rows.append({
                "name_lc": name.lower(),
                "artists_lc": "/".join(artists).lower(),
                "id": song_id,
                "duration": _as_int(tr.get("duration")) or 0,
                "artists": artists,
                "artist": "/".join(artists),
                "album": (album.get("name") or "").strip(),
                "album_id": _as_int(album.get("id")),
                "cover_url": _norm_url(album.get("picUrl")
                                       or album.get("blurPicUrl") or ""),
            })
        _queue_map = rows
        _queue_mtime = mt
    except Exception:
        pass


def _match_queue(name_lc, artist_lc):
    for row in _queue_map:
        if row["name_lc"] == name_lc and (
                not artist_lc or artist_lc in row["artists_lc"]
                or row["artists_lc"] in artist_lc):
            return row
    for row in _queue_map:
        if row["name_lc"] == name_lc:
            return row
    return None


def lookup_queue_detail(name, artist):
    """在本地播放队列查找, 返回包含专辑/封面等信息的 dict; 未找到返回 None。"""
    _load_queue()
    row = _match_queue((name or "").strip().lower(),
                       (artist or "").strip().lower())
    if not row:
        return None
    return {
        "id": row["id"],
        "duration": row["duration"],
        "source": "queue",
        "name": name,
        "artists": list(row["artists"]),
        "artist": row["artist"],
        "album": row["album"],
        "album_id": row["album_id"],
        "cover_url": row["cover_url"],
    }


def lookup_queue(name, artist):
    """在本地播放队列里查找 (song_id, duration_ms)。"""
    hit = lookup_queue_detail(name, artist)
    return (hit["id"], hit["duration"]) if hit else None


# --------------------------------------------------------------- song detail
def fetch_song_detail(song_id):
    """按 song id 请求网易云 song detail 接口。

    返回规范化的 dict(失败返回 None):
        {id, name, artists: [str], artist: str, album: str,
         album_id: int|None, cover_url: str, duration: int}
    """
    song_id = _as_int(song_id)
    if not song_id:
        return None
    try:
        data = _api("https://music.163.com/api/song/detail?ids=[%d]" % song_id,
                    timeout=8)
    except Exception:
        return None
    songs = data.get("songs") or []
    if not songs:
        return None
    song = songs[0] or {}
    album = song.get("album") or {}
    artists = _norm_artists(song.get("artists"))
    return {
        "id": _as_int(song.get("id")) or song_id,
        "name": (song.get("name") or "").strip(),
        "artists": artists,
        "artist": "/".join(artists),
        "album": (album.get("name") or "").strip(),
        "album_id": _as_int(album.get("id")),
        "cover_url": _norm_url(album.get("picUrl")
                               or album.get("blurPicUrl") or ""),
        "duration": _as_int(song.get("duration")) or 0,
    }


def _detail_cache_path():
    return os.path.join(CACHE_DIR, "detailcache.json")


def _load_detail_cache():
    global _detail_index
    if _detail_index is None:
        try:
            with open(_detail_cache_path(), "r", encoding="utf-8") as f:
                _detail_index = json.load(f)
        except Exception:
            _detail_index = {}
        if not isinstance(_detail_index, dict):
            _detail_index = {}
    return _detail_index


def _save_detail_cache():
    try:
        path = _detail_cache_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_detail_index, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def _merge_detail(base, detail):
    """合并两个详情 dict, detail 优先, base 补全缺失字段。"""
    base = base if isinstance(base, dict) else {}
    detail = detail if isinstance(detail, dict) else {}

    def pick(key, empty):
        value = detail.get(key)
        if value is not None and value != empty:
            return value
        value = base.get(key)
        if value is not None and value != empty:
            return value
        return empty

    artists = list(pick("artists", []) or [])
    return {
        "id": _as_int(pick("id", None)),
        "name": (pick("name", "") or "").strip(),
        "artists": artists,
        "artist": "/".join(artists),
        "album": (pick("album", "") or "").strip(),
        "album_id": _as_int(pick("album_id", None)),
        "cover_url": _norm_url(pick("cover_url", "") or ""),
        "duration": _as_int(detail.get("duration")
                            or base.get("duration") or 0) or 0,
    }


def get_song_detail(song_id, fallback=None):
    """带本地缓存的歌曲详情; 接口失败时用 fallback(播放队列/搜索结果)补全。

    :param song_id: 网易云 song id
    :param fallback: 可选的已有信息 dict(通常来自播放队列或搜索)
    :return: 规范化详情 dict; 完全拿不到有效信息时返回 None
    """
    song_id = _as_int(song_id)
    if not song_id:
        return None
    with _detail_lock:
        cached = _load_detail_cache().get(str(song_id))
    fetched = None
    if not isinstance(cached, dict) or not (
            cached.get("album") or cached.get("cover_url")):
        fetched = fetch_song_detail(song_id)
    detail = _merge_detail(fallback, fetched if fetched else cached)
    if not detail.get("id"):
        detail["id"] = song_id
    if not (detail.get("album") or detail.get("cover_url")
            or detail.get("artists") or detail.get("name")):
        return None
    if fetched:
        with _detail_lock:
            _load_detail_cache()[str(song_id)] = detail
            _save_detail_cache()
    return detail


# ------------------------------------------------------------------- cover
def _file_ok(path):
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def ensure_cover(song_id, cover_url, size=COVER_SIZE):
    """下载并缓存专辑封面, 返回本地文件路径; 失败返回 ""。

    已缓存(非空文件)时直接返回, 不重复下载。文件统一存为
    ``COVER_DIR/{song_id}.jpg``(网易云封面实际是 JPEG)。
    """
    song_id = _as_int(song_id)
    cover_url = _norm_url(cover_url)
    if not song_id or not cover_url:
        return ""
    path = os.path.join(COVER_DIR, "%d.jpg" % song_id)
    with _cover_lock:
        if _file_ok(path):
            return path
        if "?" in cover_url:
            url = cover_url + "&param=%dy%d" % (int(size), int(size))
        else:
            url = cover_url + "?param=%dy%d" % (int(size), int(size))
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
            if not data:
                return ""
            os.makedirs(COVER_DIR, exist_ok=True)
            tmp = path + ".%d.tmp" % os.getpid()
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            return path
        except Exception:
            return ""


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


# ------------------------------------------------------------------ search
def search_song_detail(name, artist):
    """搜索接口: 返回包含专辑等信息的 dict; 未找到返回 None。"""
    with _index_lock:
        idx = _load_index()
        key = f"{name}|{artist}".lower()
        cached = idx.get(key)
    if cached and _as_int(cached.get("id")):
        return {
            "id": _as_int(cached.get("id")),
            "duration": _as_int(cached.get("duration")) or 0,
            "source": "search",
            "name": name,
            "artists": list(cached.get("artists") or []),
            "artist": cached.get("artist") or "",
            "album": cached.get("album") or "",
            "album_id": _as_int(cached.get("album_id")),
            "cover_url": _norm_url(cached.get("cover_url") or ""),
        }
    q = urllib.parse.quote(f"{name} {artist}".strip())
    try:
        data = _api(f"https://music.163.com/api/search/get?s={q}"
                    f"&type=1&limit=10&offset=0")
    except Exception:
        return None
    songs = (data.get("result") or {}).get("songs") or []
    if not songs:
        return None
    best = None
    for s in songs:
        sname = (s.get("name") or "").strip().lower()
        artists = "/".join(a.get("name", "") for a in
                           (s.get("artists") or [])).lower()
        if sname == name.strip().lower() and (
                not artist or artist.strip().lower() in artists):
            best = s
            break
    if best is None:
        best = songs[0]
    album = best.get("album") or {}
    artists = _norm_artists(best.get("artists"))
    entry = {
        "id": _as_int(best.get("id")),
        "duration": _as_int(best.get("duration")) or 0,
        "source": "search",
        "name": name,
        "artists": artists,
        "artist": "/".join(artists),
        "album": (album.get("name") or "").strip(),
        "album_id": _as_int(album.get("id")),
        "cover_url": _norm_url(album.get("picUrl")
                               or album.get("blurPicUrl") or ""),
    }
    if not entry["id"]:
        return None
    with _index_lock:
        idx = _load_index()
        idx[key] = {k: entry[k] for k in
                    ("id", "duration", "artists", "artist", "album",
                     "album_id", "cover_url")}
        _save_index()
    return entry


def search_song(name, artist):
    """搜索接口: 返回 (song_id, duration_ms) 或 None。"""
    hit = search_song_detail(name, artist)
    return (hit["id"], hit["duration"]) if hit else None


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


def _resolve_song(name, artist):
    """按 播放队列 -> 搜索接口 的顺序匹配歌曲, 返回含专辑/封面的 dict。"""
    hit = lookup_queue_detail(name, artist)
    if hit:
        return hit
    return search_song_detail(name, artist)


def get_song_lyrics(name, artist):
    """一站式: 匹配 song id + 取歌曲详情/封面 + 取歌词。

    返回 dict:
        {id, duration, lines: [(t_ms, text, translation)], source, name, artist,
         artists: [str], album, album_id, cover_url, cover_path}
    或 None。
    """
    hit = _resolve_song(name, artist)
    if not hit:
        return None
    song_id = hit["id"]
    duration = int(hit.get("duration") or 0)
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

    detail = get_song_detail(song_id, fallback=hit) or hit
    cover_url = detail.get("cover_url") or hit.get("cover_url") or ""
    cover_path = ensure_cover(song_id, cover_url)
    if not duration:
        duration = int(detail.get("duration") or 0)

    lines = clean_credit_lines(lines, name, artist)
    return {"id": song_id, "duration": duration, "lines": lines,
            "source": hit.get("source") or "", "name": name, "artist": artist,
            "artists": list(detail.get("artists") or hit.get("artists") or []),
            "album": detail.get("album") or "",
            "album_id": detail.get("album_id"),
            "cover_url": cover_url,
            "cover_path": cover_path}


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "Far From Any Road - The Handsome Family"
    name, artist = (q.rsplit(" - ", 1) if " - " in q else (q, ""))
    info = get_song_lyrics(name.strip(), artist.strip())
    print(info and (info["id"], info["duration"], len(info["lines"]),
                    info["source"]))
    if info:
        print("artists:", info["artists"])
        print("album:", info["album"], "album_id:", info["album_id"])
        print("cover_url:", info["cover_url"])
        print("cover_path:", info["cover_path"])
        for t, s, tr in info["lines"][:8]:
            print(f"{t/1000:7.2f}s | {s} | {tr}")
