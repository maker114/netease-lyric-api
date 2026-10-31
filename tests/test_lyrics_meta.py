# -*- coding: utf-8 -*-
"""数据层离线单元测试: 歌曲详情规范化 / 缓存与 fallback 合并 / 封面缓存 / 端到端聚合。

全部测试都 mock 掉网络与本地播放队列, 不依赖网易云客户端、不访问 music.163.com。
"""
import copy
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from netease_lyric import lyrics

JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 64
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

SONG_ID = 28095742
SONG_NAME = "Rather Be"
SONG_ARTISTS = ["Clean Bandit", "Jess Glynne"]
ALBUM_ID = 2726100
ALBUM_NAME = "Rather Be"
COVER_URL = "https://p2.music.126.net/gnLaaIBB6RPBP16mtt8-Mw==/2534374303468372.jpg"
DURATION = 241057


def song_detail_payload():
    """构造一份最小可用的 /api/song/detail 模拟响应(深拷贝, 防测试间污染)。"""
    return {
        "songs": [{
            "id": SONG_ID,
            "name": SONG_NAME,
            "artists": [{"name": "Clean Bandit"}, {"name": "Jess Glynne"}],
            "album": {"id": ALBUM_ID, "name": ALBUM_NAME,
                      "picUrl": "http://p2.music.126.net/a.jpg"},
            "duration": DURATION,
        }]
    }


def lyric_payload():
    return {
        "lrc": {"lyric": ("[00:01.00]We're a thousand miles from comfort\n"
                          "[00:12.50]But as long as you are with me\n")},
        "tlyric": {"lyric": "[00:01.00]我们相隔千里\n"},
    }


class _FakeResponse(object):
    """伪装 urllib.request.urlopen 返回的上下文管理器。"""

    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _TempCacheCase(unittest.TestCase):
    """把 lyrics 的缓存目录/播放队列全局状态隔离到临时目录。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="netease-lyric-test-")
        self._old = {
            "CACHE_DIR": lyrics.CACHE_DIR,
            "COVER_DIR": lyrics.COVER_DIR,
            "PLAYING_LIST": lyrics.PLAYING_LIST,
            "_detail_index": lyrics._detail_index,
            "_song_index": lyrics._song_index,
            "_queue_map": lyrics._queue_map,
            "_queue_mtime": lyrics._queue_mtime,
        }
        lyrics.CACHE_DIR = self._tmp
        lyrics.COVER_DIR = os.path.join(self._tmp, "covers")
        lyrics.PLAYING_LIST = os.path.join(self._tmp, "playingList")
        # 模块级缓存必须重置, 否则会读到真实用户缓存 / 上一个测试的数据
        lyrics._detail_index = None
        lyrics._song_index = None
        lyrics._queue_map = []
        lyrics._queue_mtime = 0

    def tearDown(self):
        lyrics.CACHE_DIR = self._old["CACHE_DIR"]
        lyrics.COVER_DIR = self._old["COVER_DIR"]
        lyrics.PLAYING_LIST = self._old["PLAYING_LIST"]
        lyrics._detail_index = self._old["_detail_index"]
        lyrics._song_index = self._old["_song_index"]
        lyrics._queue_map = self._old["_queue_map"]
        lyrics._queue_mtime = self._old["_queue_mtime"]
        shutil.rmtree(self._tmp, ignore_errors=True)


# ------------------------------------------------------------------ helpers
class NormHelperTests(unittest.TestCase):
    def test_as_int(self):
        self.assertEqual(lyrics._as_int("42"), 42)
        self.assertEqual(lyrics._as_int(42), 42)
        self.assertIsNone(lyrics._as_int(None))
        self.assertIsNone(lyrics._as_int("abc"))

    def test_norm_url_upgrades_http_only(self):
        self.assertEqual(lyrics._norm_url("http://x/y.jpg"), "https://x/y.jpg")
        self.assertEqual(lyrics._norm_url("https://x/y.jpg"), "https://x/y.jpg")
        self.assertEqual(lyrics._norm_url("  https://x/y.jpg  "), "https://x/y.jpg")
        self.assertEqual(lyrics._norm_url(None), "")
        self.assertEqual(lyrics._norm_url(""), "")

    def test_norm_artists_dedup_strip_and_accepts_strings(self):
        raw = [{"name": "Clean Bandit"}, {"name": "Jess Glynne"},
               {"name": "Clean Bandit"}, {"name": "   "}, "Plain Artist", {}]
        self.assertEqual(lyrics._norm_artists(raw),
                         ["Clean Bandit", "Jess Glynne", "Plain Artist"])
        self.assertEqual(lyrics._norm_artists(None), [])
        self.assertEqual(lyrics._norm_artists([]), [])


# --------------------------------------------------------- fetch_song_detail
class FetchSongDetailTests(_TempCacheCase):
    def test_normalizes_fields_and_upgrades_http_url(self):
        with mock.patch.object(lyrics, "_api",
                               return_value=song_detail_payload()) as fake:
            detail = lyrics.fetch_song_detail(SONG_ID)

        self.assertIsNotNone(detail)
        self.assertEqual(detail["id"], SONG_ID)
        self.assertEqual(detail["name"], SONG_NAME)
        self.assertEqual(detail["artists"], SONG_ARTISTS)
        self.assertEqual(detail["artist"], "Clean Bandit/Jess Glynne")
        self.assertEqual(detail["album"], ALBUM_NAME)
        self.assertEqual(detail["album_id"], ALBUM_ID)
        # 老数据 http 图片地址统一升级为 https
        self.assertEqual(detail["cover_url"], "https://p2.music.126.net/a.jpg")
        self.assertEqual(detail["duration"], DURATION)

        url = fake.call_args.args[0]
        self.assertIn("/api/song/detail", url)
        self.assertIn("ids=[28095742]", url)
        self.assertEqual(fake.call_args.kwargs.get("timeout"), 8)

    def test_uses_blur_pic_url_fallback_and_cleans_artists(self):
        payload = {"songs": [{
            "id": 7, "name": " X ", "duration": "abc",
            "artists": [{"name": "A"}, {"name": "A"}, {"name": ""}],
            "album": {"blurPicUrl": "http://x/z.jpg"},
        }]}
        with mock.patch.object(lyrics, "_api", return_value=payload):
            detail = lyrics.fetch_song_detail(7)
        self.assertEqual(detail["name"], "X")
        self.assertEqual(detail["artists"], ["A"])
        self.assertEqual(detail["artist"], "A")
        self.assertEqual(detail["album"], "")
        self.assertIsNone(detail["album_id"])
        self.assertEqual(detail["cover_url"], "https://x/z.jpg")
        self.assertEqual(detail["duration"], 0)

    def test_api_failure_returns_none(self):
        with mock.patch.object(lyrics, "_api", side_effect=OSError("offline")):
            self.assertIsNone(lyrics.fetch_song_detail(SONG_ID))

    def test_empty_songs_returns_none(self):
        with mock.patch.object(lyrics, "_api", return_value={"songs": []}):
            self.assertIsNone(lyrics.fetch_song_detail(SONG_ID))
        with mock.patch.object(lyrics, "_api", return_value={}):
            self.assertIsNone(lyrics.fetch_song_detail(SONG_ID))

    def test_invalid_song_id_does_not_hit_network(self):
        with mock.patch.object(lyrics, "_api",
                               side_effect=AssertionError("must not call api")):
            for bad in (None, 0, "", "abc"):
                self.assertIsNone(lyrics.fetch_song_detail(bad))


# ----------------------------------------------------------- get_song_detail
class GetSongDetailTests(_TempCacheCase):
    def test_fetch_then_cache_hit_skips_network(self):
        with mock.patch.object(lyrics, "_api",
                               return_value=song_detail_payload()):
            first = lyrics.get_song_detail(SONG_ID)
        self.assertEqual(first["album"], ALBUM_NAME)
        self.assertEqual(first["cover_url"],
                         "https://p2.music.126.net/a.jpg")

        cache_file = os.path.join(self._tmp, "detailcache.json")
        self.assertTrue(os.path.isfile(cache_file))
        with open(cache_file, "r", encoding="utf-8") as f:
            cached = json.load(f)
        self.assertIn(str(SONG_ID), cached)
        self.assertEqual(cached[str(SONG_ID)]["album"], ALBUM_NAME)

        # 模拟新进程: 模块级缓存清空, 只剩磁盘缓存 -> 不应再请求网络
        lyrics._detail_index = None
        with mock.patch.object(lyrics, "_api",
                               side_effect=AssertionError("cached, no fetch")):
            second = lyrics.get_song_detail(SONG_ID)
        self.assertEqual(second["album"], ALBUM_NAME)
        self.assertEqual(second["artists"], SONG_ARTISTS)
        self.assertEqual(second["duration"], DURATION)

    def test_api_failure_uses_fallback_and_normalizes(self):
        fallback = {
            "id": SONG_ID, "name": "Queue Name", "artists": ["A", "B"],
            "artist": "A/B", "album": "Fallback Album", "album_id": 9,
            "cover_url": "http://p1.music.126.net/f.jpg", "duration": 123456,
        }
        with mock.patch.object(lyrics, "_api", side_effect=OSError("offline")):
            detail = lyrics.get_song_detail(SONG_ID, fallback=fallback)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["album"], "Fallback Album")
        self.assertEqual(detail["album_id"], 9)
        self.assertEqual(detail["artists"], ["A", "B"])
        self.assertEqual(detail["artist"], "A/B")
        self.assertEqual(detail["cover_url"], "https://p1.music.126.net/f.jpg")
        self.assertEqual(detail["duration"], 123456)

    def test_detail_from_api_wins_over_fallback(self):
        fallback = {"id": SONG_ID, "name": "Queue Name",
                    "album": "Fallback Album", "cover_url": "http://f/x.jpg"}
        with mock.patch.object(lyrics, "_api",
                               return_value=song_detail_payload()):
            detail = lyrics.get_song_detail(SONG_ID, fallback=fallback)
        self.assertEqual(detail["name"], SONG_NAME)
        self.assertEqual(detail["album"], ALBUM_NAME)
        self.assertEqual(detail["cover_url"],
                         "https://p2.music.126.net/a.jpg")

    def test_partial_api_fills_missing_fields_from_fallback(self):
        fallback = {"id": 42, "name": "Base", "artists": ["Base Artist"],
                    "album": "Base Album", "cover_url": "http://base/c.jpg",
                    "duration": 1000}
        payload = {"songs": [{
            "id": 42, "name": "Live Version",
            "artists": [{"name": "Live Artist"}],
            "album": {},
        }]}
        with mock.patch.object(lyrics, "_api", return_value=payload):
            detail = lyrics.get_song_detail(42, fallback=fallback)
        self.assertEqual(detail["name"], "Live Version")
        self.assertEqual(detail["artists"], ["Live Artist"])
        self.assertEqual(detail["artist"], "Live Artist")
        self.assertEqual(detail["album"], "Base Album")
        self.assertEqual(detail["cover_url"], "https://base/c.jpg")
        self.assertEqual(detail["duration"], 1000)

    def test_incomplete_cache_entry_triggers_refetch(self):
        # 缓存里只有 artist, 没有 album/cover -> 应重新请求并更新缓存
        with open(os.path.join(self._tmp, "detailcache.json"), "w",
                  encoding="utf-8") as f:
            json.dump({str(SONG_ID): {"id": SONG_ID, "name": "old",
                                      "artists": ["Old"], "album": "",
                                      "cover_url": ""}}, f)
        with mock.patch.object(lyrics, "_api",
                               return_value=song_detail_payload()) as fake:
            detail = lyrics.get_song_detail(SONG_ID)
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(detail["album"], ALBUM_NAME)
        with open(os.path.join(self._tmp, "detailcache.json"), "r",
                  encoding="utf-8") as f:
            self.assertEqual(json.load(f)[str(SONG_ID)]["album"], ALBUM_NAME)

    def test_nothing_usable_returns_none(self):
        with mock.patch.object(lyrics, "_api", side_effect=OSError("offline")):
            self.assertIsNone(lyrics.get_song_detail(SONG_ID))
            self.assertIsNone(lyrics.get_song_detail(SONG_ID, fallback={"id":
                                                                        SONG_ID}))
            self.assertIsNone(lyrics.get_song_detail(None))


# --------------------------------------------------------------- ensure_cover
class EnsureCoverTests(_TempCacheCase):
    def _cover_path(self, song_id=SONG_ID):
        return os.path.join(lyrics.COVER_DIR, "%d.jpg" % song_id)

    def test_first_download_writes_file(self):
        with mock.patch.object(lyrics.urllib.request, "urlopen",
                               return_value=_FakeResponse(JPEG_BYTES)) as fake:
            path = lyrics.ensure_cover(SONG_ID, "http://p2.music.126.net/a.jpg")
        self.assertEqual(path, self._cover_path())
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), JPEG_BYTES)

        req = fake.call_args.args[0]
        self.assertEqual(
            req.full_url,
            "https://p2.music.126.net/a.jpg?param=500y500")
        self.assertEqual(req.headers.get("Referer"), "https://music.163.com/")
        self.assertEqual(fake.call_args.kwargs.get("timeout"), 10)

    def test_second_call_hits_cache_without_download(self):
        with mock.patch.object(lyrics.urllib.request, "urlopen",
                               return_value=_FakeResponse(JPEG_BYTES)):
            first = lyrics.ensure_cover(SONG_ID, COVER_URL)
        self.assertTrue(os.path.isfile(first))
        with mock.patch.object(lyrics.urllib.request, "urlopen",
                               side_effect=AssertionError("cached, no download")):
            second = lyrics.ensure_cover(SONG_ID, COVER_URL)
        self.assertEqual(second, first)

    def test_download_failure_returns_empty_and_writes_nothing(self):
        with mock.patch.object(lyrics.urllib.request, "urlopen",
                               side_effect=OSError("offline")):
            self.assertEqual(lyrics.ensure_cover(SONG_ID, COVER_URL), "")
        self.assertFalse(os.path.exists(self._cover_path()))

    def test_empty_response_body_returns_empty(self):
        with mock.patch.object(lyrics.urllib.request, "urlopen",
                               return_value=_FakeResponse(b"")):
            self.assertEqual(lyrics.ensure_cover(SONG_ID, COVER_URL), "")
        self.assertFalse(os.path.exists(self._cover_path()))

    def test_zero_byte_cached_file_is_redownloaded(self):
        os.makedirs(lyrics.COVER_DIR, exist_ok=True)
        path = self._cover_path(7)
        with open(path, "wb"):
            pass
        with mock.patch.object(lyrics.urllib.request, "urlopen",
                               return_value=_FakeResponse(PNG_BYTES)):
            got = lyrics.ensure_cover(7, "https://x/y.jpg")
        self.assertEqual(got, path)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), PNG_BYTES)

    def test_url_with_existing_query_uses_ampersand(self):
        with mock.patch.object(lyrics.urllib.request, "urlopen",
                               return_value=_FakeResponse(JPEG_BYTES)) as fake:
            lyrics.ensure_cover(9, "https://x/y.jpg?thumb=1", size=120)
        self.assertEqual(fake.call_args.args[0].full_url,
                         "https://x/y.jpg?thumb=1&param=120y120")

    def test_invalid_arguments_do_not_download(self):
        with mock.patch.object(lyrics.urllib.request, "urlopen",
                               side_effect=AssertionError("must not download")):
            self.assertEqual(lyrics.ensure_cover(None, COVER_URL), "")
            self.assertEqual(lyrics.ensure_cover("abc", COVER_URL), "")
            self.assertEqual(lyrics.ensure_cover(SONG_ID, ""), "")
            self.assertEqual(lyrics.ensure_cover(SONG_ID, None), "")


# ------------------------------------------------------------- get_song_lyrics
class GetSongLyricsTests(_TempCacheCase):
    def _write_playing_list(self):
        row = {"track": {
            "id": SONG_ID, "name": SONG_NAME, "duration": DURATION,
            "artists": [{"name": "Clean Bandit"}, {"name": "Jess Glynne"}],
            "album": {"id": ALBUM_ID, "name": ALBUM_NAME,
                      "picUrl": "http://p4.music.126.net/q.jpg"},
        }}
        with open(lyrics.PLAYING_LIST, "w", encoding="utf-8") as f:
            json.dump({"list": [row]}, f)

    @staticmethod
    def _api_router(qr_detail=None, qr_lyric=None):
        def fake_api(url, timeout=12):
            if "/api/song/detail" in url:
                return qr_detail if qr_detail is not None else song_detail_payload()
            if "/api/song/lyric" in url:
                return qr_lyric if qr_lyric is not None else lyric_payload()
            raise AssertionError("unexpected api url: %s" % url)
        return fake_api

    def test_queue_hit_returns_full_metadata_and_cover(self):
        self._write_playing_list()
        with mock.patch.object(lyrics, "_api",
                               side_effect=self._api_router()), \
                mock.patch.object(lyrics.urllib.request, "urlopen",
                                  return_value=_FakeResponse(JPEG_BYTES)):
            info = lyrics.get_song_lyrics(SONG_NAME,
                                          "Clean Bandit/Jess Glynne")

        self.assertIsNotNone(info)
        self.assertEqual(info["id"], SONG_ID)
        self.assertEqual(info["source"], "queue")
        self.assertEqual(info["duration"], DURATION)
        self.assertEqual(info["artists"], SONG_ARTISTS)
        self.assertEqual(info["artist"], "Clean Bandit/Jess Glynne")
        self.assertEqual(info["album"], ALBUM_NAME)
        self.assertEqual(info["album_id"], ALBUM_ID)
        self.assertEqual(info["cover_url"],
                         "https://p2.music.126.net/a.jpg")
        self.assertEqual(os.path.normcase(info["cover_path"]),
                         os.path.normcase(os.path.join(lyrics.COVER_DIR,
                                                       "28095742.jpg")))
        self.assertTrue(os.path.isfile(info["cover_path"]))
        with open(info["cover_path"], "rb") as f:
            self.assertEqual(f.read()[:3], b"\xff\xd8\xff")

        self.assertEqual(len(info["lines"]), 2)
        self.assertEqual(info["lines"][0][0], 1000)
        self.assertEqual(info["lines"][0][1],
                         "We're a thousand miles from comfort")
        self.assertEqual(info["lines"][0][2], "我们相隔千里")
        self.assertEqual(info["lines"][1][2], "")
        # 歌词落盘缓存
        self.assertTrue(os.path.isfile(os.path.join(self._tmp,
                                                    "28095742.json")))

    def test_search_fallback_returns_metadata(self):
        # 没有本地播放队列 -> 走搜索接口
        self.assertFalse(os.path.exists(lyrics.PLAYING_LIST))
        search_payload = {"result": {"songs": [{
            "id": SONG_ID, "name": SONG_NAME, "duration": DURATION,
            "artists": [{"name": "Clean Bandit"}, {"name": "Jess Glynne"}],
            "album": {"id": ALBUM_ID, "name": ALBUM_NAME,
                      "picUrl": "http://p3.music.126.net/s.jpg"},
        }]}}
        seen = []

        def fake_api(url, timeout=12):
            seen.append(url)
            if "/api/search/get" in url:
                return search_payload
            if "/api/song/detail" in url:
                return song_detail_payload()
            if "/api/song/lyric" in url:
                return lyric_payload()
            raise AssertionError("unexpected api url: %s" % url)

        with mock.patch.object(lyrics, "_api", side_effect=fake_api), \
                mock.patch.object(lyrics.urllib.request, "urlopen",
                                  return_value=_FakeResponse(JPEG_BYTES)):
            info = lyrics.get_song_lyrics(SONG_NAME,
                                          "Clean Bandit/Jess Glynne")

        self.assertIsNotNone(info)
        self.assertEqual(info["source"], "search")
        self.assertEqual(info["album"], ALBUM_NAME)
        self.assertEqual(info["album_id"], ALBUM_ID)
        self.assertTrue(any("/api/search/get" in u for u in seen))
        self.assertTrue(os.path.isfile(os.path.join(self._tmp,
                                                    "songcache.json")))

    def test_cover_download_failure_is_tolerated(self):
        self._write_playing_list()
        with mock.patch.object(lyrics, "_api",
                               side_effect=self._api_router()), \
                mock.patch.object(lyrics.urllib.request, "urlopen",
                                  side_effect=OSError("offline")):
            info = lyrics.get_song_lyrics(SONG_NAME,
                                          "Clean Bandit/Jess Glynne")
        self.assertIsNotNone(info)
        self.assertEqual(info["album"], ALBUM_NAME)
        self.assertEqual(info["cover_path"], "")
        self.assertEqual(len(info["lines"]), 2)

    def test_no_match_returns_none_without_network(self):
        with mock.patch.object(lyrics, "_resolve_song", return_value=None), \
                mock.patch.object(lyrics.urllib.request, "urlopen",
                                  side_effect=AssertionError("no network")):
            self.assertIsNone(lyrics.get_song_lyrics("Nope", "Nobody"))


if __name__ == "__main__":
    unittest.main()
