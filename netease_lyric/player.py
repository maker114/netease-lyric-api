# -*- coding: utf-8 -*-
"""player.py - 核心公共 API: 网易云播放状态 + 实时歌词状态(线程安全)。

对外的唯一核心对象是 :class:`NeteaseLyric`: 后台线程持续同步状态,
调用方在任何时刻都可以线程安全地查询 当前歌曲 / 播放进度 / 当前歌词行。

典型用法::

    from netease_lyric import NeteaseLyric

    with NeteaseLyric() as api:              # 自动开始后台同步
        api.wait_for_ready(timeout=10)
        print(api.now_playing().title)
        print(api.position_ms())
        print(api.current_line().text)

也可以注册事件回调(回调在后台轮询线程中触发)::

    api.add_listener(lambda ev: print(ev["event"], ev.get("line", {}).get("text")))
"""
from __future__ import annotations

import bisect
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ._version import __version__
from .clock import PlaybackClock, find_ncm_window, get_window_title, parse_title
from .lyrics import ensure_cover as _download_cover, get_song_lyrics

log = logging.getLogger("netease_lyric")

#: 这些标题不是歌曲名, 忽略
BAD_TITLES = {"桌面歌词", "迷你播放器", "网易云音乐", "", None}

DEFAULT_POLL_INTERVAL = 0.10
DISCOVERY_RETRY_SECONDS = 3.0


def format_ms(ms: Optional[float]) -> str:
    """毫秒 -> 'mm:ss'"""
    if ms is None:
        return "--:--"
    total = max(0, int(ms // 1000))
    return f"{total // 60:02d}:{total % 60:02d}"


def _cover_ok(path: Optional[str]) -> bool:
    """封面缓存文件是否可用(存在且非空, 0 字节视为损坏)。"""
    if not path:
        return False
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


@dataclass
class Track:
    """当前播放的歌曲信息。"""
    name: str
    artist: str
    song_id: Optional[int] = None
    duration_ms: int = 0
    source: str = ""        # queue / search / ""
    album: str = ""
    album_id: Optional[int] = None
    artists: List[str] = field(default_factory=list)  # 网易云标准歌手名列表
    cover_url: str = ""     # 封面图地址(https)
    cover_path: str = ""    # 本地已缓存的封面文件路径(未缓存为 "")

    @property
    def title(self) -> str:
        return f"{self.name} - {self.artist}" if self.artist else self.name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "artist": self.artist,
            "artists": list(self.artists),
            "title": self.title,
            "song_id": self.song_id,
            "duration_ms": self.duration_ms,
            "source": self.source,
            "album": self.album,
            "album_id": self.album_id,
            "cover_url": self.cover_url,
            "cover_path": self.cover_path,
        }


@dataclass
class LyricLine:
    """一行歌词(带时间轴与翻译)。"""
    index: int
    start_ms: int
    end_ms: int
    text: str
    translation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "translation": self.translation,
        }


class NeteaseLyric:
    """网易云播放状态 + 实时歌词 API。

    :param poll_interval: 后台轮询间隔(秒), 默认 0.1
    :param lyric_offset_ms: 歌词时间轴偏移(毫秒), 正数让歌词更早显示
    :param auto_start: 是否在构造时自动开始后台同步(默认 True)
    """

    def __init__(self, poll_interval: float = DEFAULT_POLL_INTERVAL,
                 lyric_offset_ms: int = 0,
                 auto_start: bool = True):
        self._poll_interval = max(0.02, float(poll_interval))
        self._lyric_offset_ms = int(lyric_offset_ms)

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._clock = PlaybackClock()

        # --- 状态(均在 self._lock 保护下读写)
        self._netease_running = False
        self._track: Optional[Track] = None
        self._lines: List[LyricLine] = []
        self._line_idx = -2
        self._title_now: Optional[str] = None
        self._had_title = False

        self._pos_ms: Optional[float] = None
        self._pos_ts = 0.0
        self._paused = False
        self._last_raw: Optional[int] = None
        self._last_raw_change = 0.0
        self._song_started_at = 0.0

        self._lyric_loading = False
        self._song_gen = 0
        self._discovering = False
        self._discover_pending = False
        self._discover_tried_at = 0.0
        self._revalidate_at = 0.0
        self._last_error: Optional[str] = None

        self._listeners: List[Callable[[Dict[str, Any]], None]] = []

        if auto_start:
            self.start()

    # ------------------------------------------------------------ lifecycle
    def start(self) -> "NeteaseLyric":
        """开始后台同步(幂等)。"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="netease-lyric",
                                            daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        """停止后台同步并释放句柄。"""
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._thread = None
        try:
            self._clock.detach()
        except Exception:
            pass

    close = stop

    def __enter__(self) -> "NeteaseLyric":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------- public API
    @property
    def version(self) -> str:
        return __version__

    def is_netease_running(self) -> bool:
        """网易云客户端是否在运行。"""
        with self._lock:
            return self._netease_running

    def now_playing(self) -> Optional[Track]:
        """当前歌曲(Track); 未识别到返回 None。"""
        with self._lock:
            return self._track

    def position_ms(self) -> Optional[int]:
        """当前播放位置(毫秒, 已做短时插值); 未知返回 None。"""
        st = self.state()
        return st["position_ms"]

    def duration_ms(self) -> int:
        with self._lock:
            return int(self._track.duration_ms) if self._track else 0

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def current_line(self) -> Optional[LyricLine]:
        """当前正在唱的歌词行; 无歌词/前奏/未就绪返回 None。"""
        with self._lock:
            idx = self._line_idx
            if 0 <= idx < len(self._lines):
                return self._lines[idx]
            return None

    def lyrics(self) -> List[LyricLine]:
        """当前歌曲的完整歌词(带时间轴与翻译)。"""
        with self._lock:
            return list(self._lines)

    def cover_path(self) -> Optional[str]:
        """当前歌曲已缓存的封面本地文件路径; 未缓存/无歌曲返回 None。"""
        with self._lock:
            trk = self._track
            path = trk.cover_path if trk else ""
        return path if _cover_ok(path) else None

    def ensure_cover(self) -> Optional[str]:
        """确保当前歌曲封面已缓存, 返回本地文件路径; 失败返回 None。

        播放歌曲信息里已有封面缓存时直接返回; 否则按 song_id + cover_url
        懒下载(可阻塞数秒, 不会持有内部锁)。下载期间切歌则丢弃结果。
        """
        with self._lock:
            trk = self._track
            if trk is None:
                return None
            song_id = trk.song_id
            cover_url = trk.cover_url
            path = trk.cover_path if _cover_ok(trk.cover_path) else ""
            gen = self._song_gen
        if path:
            return path
        if not song_id or not cover_url:
            return None
        try:
            path = _download_cover(song_id, cover_url)
        except Exception:  # 下载失败不影响主流程
            log.exception("cover fetch failed")
            return None
        if not path:
            return None
        with self._lock:
            trk = self._track
            if trk is not None and self._song_gen == gen and trk.song_id == song_id:
                trk.cover_path = path
            else:
                # 下载期间切歌了, 这张封面不属于当前歌曲
                return None
        return path

    def state(self) -> Dict[str, Any]:
        """完整状态快照(dict, 可直接 JSON 序列化), 见 README 的字段表。"""
        now = time.time()
        with self._lock:
            track = self._track
            lines = self._lines
            idx = self._line_idx
            paused = self._paused
            pos = self._pos_ms
            pos_ts = self._pos_ts
            dur = int(track.duration_ms) if track else 0

            # 播放中时对位置做 <=0.5s 的插值, 让 5Hz 轮询的调用方看到平滑进度
            if pos is not None and not paused and pos_ts:
                pos = pos + max(0.0, min(0.5, now - pos_ts)) * 1000.0

            line = lines[idx] if 0 <= idx < len(lines) else None
            prev_line = lines[idx - 1] if 1 <= idx < len(lines) else None
            next_line = lines[idx + 1] if 0 <= idx + 1 < len(lines) else None

            return {
                "ok": True,
                "api_version": __version__,
                "netease_running": self._netease_running,
                "playing": bool(self._netease_running and track is not None),
                "paused": paused,
                "clock_ready": bool(self._clock.aliases),
                "syncing": bool(self._discovering),
                "lyric_loading": bool(self._lyric_loading),
                "lyric_ready": bool(lines),
                "line_count": len(lines),
                "position_ms": int(pos) if pos is not None else None,
                "duration_ms": dur,
                "progress": (round(min(1.0, max(0.0, pos / dur)), 4)
                             if pos is not None and dur else None),
                "track": track.to_dict() if track else None,
                "line": line.to_dict() if line else None,
                "prev_line": prev_line.to_dict() if prev_line else None,
                "next_line": next_line.to_dict() if next_line else None,
                "lyric_offset_ms": self._lyric_offset_ms,
                "updated_at": now,
                "error": self._last_error,
            }

    # ------------------------------------------------------------- listeners
    def add_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """注册事件回调。回调在后台轮询线程中触发, 不要做耗时操作。

        事件类型:
            song_change          {"track": {...}}
            lyric_ready          {"track": {...}, "line_count": n}
            lyric_none           {"track": {...}}
            line_change          {"line": {...}, "position_ms": int}
            clock_discovered     {}
            clock_discovery_failed {}
            netease_available    {}
            netease_unavailable  {}
        """
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _emit(self, event: Dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners)
        event.setdefault("at", time.time())
        for cb in listeners:
            try:
                cb(event)
            except Exception:  # 回调异常不影响主循环
                log.exception("listener failed")

    # -------------------------------------------------------------- controls
    def rediscover(self) -> None:
        """强制重新定位内存播放钟(右健菜单/换版本后可用)。"""
        with self._lock:
            self._clock.aliases = []
            self._pos_ms = None
            self._pos_ts = 0.0
            self._last_raw = None
        self._start_discovery(force=True)

    def wait_for_ready(self, timeout: float = 10.0) -> bool:
        """阻塞等待首次状态就绪(已识别歌曲, 且歌词已加载或播放钟已定位)。"""
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            with self._lock:
                if (self._netease_running and self._track is not None
                        and not self._lyric_loading
                        and (self._clock.aliases or self._lines)):
                    return True
            time.sleep(0.1)
        return False

    def wait_until_playing(self, timeout: float = 10.0) -> bool:
        """阻塞等待"正在播放"(有进度且未暂停)。"""
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            with self._lock:
                if self._pos_ms is not None and not self._paused:
                    return True
            time.sleep(0.1)
        return False

    # ---------------------------------------------------------------- loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._tick()
            except Exception:
                log.exception("tick failed")
                with self._lock:
                    self._last_error = "tick failed (see log)"
            self._stop.wait(max(0.01, self._poll_interval - (time.time() - t0)))

    def _set_unavailable(self) -> None:
        with self._lock:
            was = self._netease_running
            self._netease_running = False
            self._track = None
            self._lines = []
            self._line_idx = -2
            self._pos_ms = None
            self._pos_ts = 0.0
            self._paused = False
            self._title_now = None
            self._had_title = False
            self._lyric_loading = False
        if was:
            self._emit({"event": "netease_unavailable"})
        try:
            self._clock.detach()
        except Exception:
            pass

    def _tick(self) -> None:
        now = time.time()
        hwnd, _pid = find_ncm_window()
        if not hwnd:
            self._set_unavailable()
            return

        with self._lock:
            was_running = self._netease_running
            self._netease_running = True
        if not was_running:
            self._emit({"event": "netease_available"})

        if not self._clock.h:
            try:
                self._clock.attach()
            except OSError:
                return

        title = get_window_title(hwnd)
        if title and title not in BAD_TITLES and title != self._title_now:
            self._on_song_change(title)

        # 切歌后校验: 新歌进度应接近 0; 不对就重新发现
        if self._revalidate_at and now >= self._revalidate_at and not self._discovering:
            self._revalidate_at = 0.0
            p = self._clock.read() if self._clock.aliases else None
            if p is None or p > 25000:
                self.rediscover()

        # 没有有效时钟时周期性重试发现
        if (not self._clock.aliases and not self._discovering
                and now - self._discover_tried_at > DISCOVERY_RETRY_SECONDS):
            self._start_discovery()

        self._update_position(now)
        self._update_line(now)

    def _on_song_change(self, title: str) -> None:
        name, artist = parse_title(title)
        if not name:
            return
        is_initial = not self._had_title
        with self._lock:
            self._had_title = True
            self._title_now = title
            self._track = Track(name=name, artist=artist)
            self._lines = []
            self._line_idx = -2
            self._lyric_loading = True
            self._pos_ms = None
            self._pos_ts = 0.0
            self._last_raw = None
            self._paused = False
            self._song_started_at = time.time()
            self._song_gen += 1
            gen = self._song_gen
            # 只有"真的切歌"才做归零校验; 程序启动时歌曲可能已播到一半
            self._revalidate_at = 0.0 if is_initial else time.time() + 2.0
            track = self._track
            need_discover = not self._clock.aliases
        self._emit({"event": "song_change", "track": track.to_dict()})
        threading.Thread(target=self._fetch_lyrics, args=(gen, name, artist),
                         name="netease-lyric-fetch", daemon=True).start()
        if need_discover:
            self._start_discovery()

    # ------------------------------------------------------------ lyric fetch
    def _fetch_lyrics(self, gen: int, name: str, artist: str) -> None:
        info = None
        err = None
        try:
            info = get_song_lyrics(name, artist)
        except Exception as e:  # 网络/接口异常
            log.exception("lyrics fetch failed")
            err = f"lyrics fetch failed: {e}"
        with self._lock:
            if gen != self._song_gen:   # 期间又切歌了, 丢弃
                return
            self._lyric_loading = False
            if err:
                self._last_error = err
            if info:
                raw = info.get("lines") or []
                dur = int(info.get("duration") or 0)
                lines: List[LyricLine] = []
                for i, (t, txt, tr) in enumerate(raw):
                    end = raw[i + 1][0] if i + 1 < len(raw) else (dur or t + 6000)
                    lines.append(LyricLine(i, int(t), int(end), txt, tr or ""))
                self._lines = lines
                self._line_idx = -2
                trk = self._track
                if trk:
                    trk.song_id = info.get("id")
                    trk.duration_ms = dur
                    trk.source = info.get("source") or ""
                    trk.artists = list(info.get("artists") or [])
                    trk.album = info.get("album") or ""
                    trk.album_id = info.get("album_id")
                    trk.cover_url = info.get("cover_url") or ""
                    trk.cover_path = info.get("cover_path") or ""
                    if not trk.artist:
                        trk.artist = (info.get("artist")
                                      or "/".join(info.get("artists") or []))
                self._last_error = None
                track = self._track.to_dict() if self._track else None
                count = len(lines)
            else:
                track = self._track.to_dict() if self._track else None
                count = 0
        if info:
            self._emit({"event": "lyric_ready", "track": track, "line_count": count})
        else:
            self._emit({"event": "lyric_none", "track": track})

    # -------------------------------------------------------------- position
    def _update_position(self, now: float) -> None:
        raw = None
        with self._lock:
            has_clock = bool(self._clock.aliases)
        if has_clock:
            raw = self._clock.read()

        with self._lock:
            if raw is not None:
                if self._last_raw is None or raw != self._last_raw:
                    self._last_raw_change = now
                self._last_raw = raw
                self._paused = (now - self._last_raw_change) > 1.5
                self._pos_ms = float(raw)
                self._pos_ts = now
                return
            # 内存读不到: 切歌/启动后 15 秒内用墙钟估算进度, 之后冻结等待重新定位
            if self._song_started_at and (now - self._song_started_at) < 15.0:
                self._pos_ms = (now - self._song_started_at) * 1000.0
                self._pos_ts = now

    def _line_index(self, pos: Optional[float]) -> int:
        with self._lock:
            lines = self._lines
            offset = self._lyric_offset_ms
        if not lines or pos is None:
            return -1
        times = [ln.start_ms for ln in lines]
        return min(bisect.bisect_right(times, pos + offset) - 1, len(times) - 1)

    def _update_line(self, now: float) -> None:
        with self._lock:
            pos = self._pos_ms
            if pos is not None and not self._paused and self._pos_ts:
                pos = pos + max(0.0, min(0.5, now - self._pos_ts)) * 1000.0
        idx = self._line_index(pos)
        with self._lock:
            changed = idx != self._line_idx
            self._line_idx = idx
            if changed and 0 <= idx < len(self._lines):
                event = {
                    "event": "line_change",
                    "line": self._lines[idx].to_dict(),
                    "position_ms": int(pos) if pos is not None else None,
                    "track": self._track.to_dict() if self._track else None,
                }
            else:
                event = None
        if event:
            self._emit(event)

    # ------------------------------------------------------------ discovery
    def _start_discovery(self, force: bool = False) -> None:
        with self._lock:
            if self._discovering:
                if force:
                    self._discover_pending = True
                return
            self._discovering = True
            self._discover_tried_at = time.time()
            dur = int(self._track.duration_ms) if self._track else 0
        threading.Thread(target=self._do_discovery, args=(dur,),
                         name="netease-lyric-discovery", daemon=True).start()

    def _do_discovery(self, dur: int) -> None:
        ok = False
        try:
            ok = self._clock.discover(duration_ms=dur,
                                      verbose=log.isEnabledFor(logging.DEBUG),
                                      log=log.debug)
        except Exception as e:
            log.exception("discovery failed")
            with self._lock:
                self._last_error = f"discovery failed: {e}"
        with self._lock:
            self._discovering = False
            pending = self._discover_pending
            self._discover_pending = False
            if ok:
                self._pos_ms = float(self._clock.last_pos or 0)
                self._pos_ts = time.time()
                self._last_raw = None
        self._emit({"event": "clock_discovered"} if ok
                   else {"event": "clock_discovery_failed"})
        if pending:
            self._start_discovery(force=True)
