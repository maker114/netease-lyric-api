# -*- coding: utf-8 -*-
"""Track 新字段 / 序列化, 以及 NeteaseLyric 无歌曲时封面接口的离线行为测试。

不启动后台线程、不读进程内存、不访问网络。
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import netease_lyric.player as player
from netease_lyric.player import NeteaseLyric, Track


class TrackTests(unittest.TestCase):
    def test_new_fields_defaults(self):
        trk = Track(name="Rather Be", artist="Clean Bandit/Jess Glynne")
        self.assertEqual(trk.song_id, None)
        self.assertEqual(trk.duration_ms, 0)
        self.assertEqual(trk.source, "")
        self.assertEqual(trk.album, "")
        self.assertIsNone(trk.album_id)
        self.assertEqual(trk.artists, [])
        self.assertEqual(trk.cover_url, "")
        self.assertEqual(trk.cover_path, "")
        self.assertEqual(trk.title, "Rather Be - Clean Bandit/Jess Glynne")

    def test_title_without_artist(self):
        self.assertEqual(Track(name="Solo", artist="").title, "Solo")

    def test_artists_default_factory_not_shared(self):
        a = Track(name="a", artist="")
        b = Track(name="b", artist="")
        a.artists.append("X")
        self.assertEqual(b.artists, [])

    def test_to_dict_contains_new_fields_and_is_json_serializable(self):
        trk = Track(name="Rather Be", artist="Clean Bandit/Jess Glynne",
                    song_id=28095742, duration_ms=241057, source="queue",
                    album="Rather Be", album_id=2726100,
                    artists=["Clean Bandit", "Jess Glynne"],
                    cover_url="https://p2.music.126.net/a.jpg",
                    cover_path="C:\\cache\\covers\\28095742.jpg")
        data = trk.to_dict()
        self.assertEqual(data["album"], "Rather Be")
        self.assertEqual(data["album_id"], 2726100)
        self.assertEqual(data["artists"], ["Clean Bandit", "Jess Glynne"])
        self.assertEqual(data["cover_url"], "https://p2.music.126.net/a.jpg")
        self.assertEqual(data["cover_path"], "C:\\cache\\covers\\28095742.jpg")
        self.assertEqual(data["title"], "Rather Be - Clean Bandit/Jess Glynne")
        self.assertEqual(
            set(data),
            {"name", "artist", "artists", "title", "song_id", "duration_ms",
             "source", "album", "album_id", "cover_url", "cover_path"})

        text = json.dumps(data, ensure_ascii=False)
        self.assertEqual(json.loads(text)["album"], "Rather Be")

    def test_to_dict_copies_artists_list(self):
        trk = Track(name="X", artist="A", artists=["A"])
        data = trk.to_dict()
        data["artists"].append("B")
        self.assertEqual(trk.artists, ["A"])


class NeteaseLyricWithoutTrackTests(unittest.TestCase):
    """auto_start=False: 无歌曲时封面接口必须直接返回 None, 不触发扫描/网络。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="netease-lyric-player-test-")
        self.api = NeteaseLyric(auto_start=False)

    def tearDown(self):
        self.api.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _guard(self):
        """一旦触发内存扫描或封面下载就让测试失败。"""
        return (
            mock.patch.object(player, "find_ncm_window",
                              side_effect=AssertionError("memory scan")),
            mock.patch.object(player, "_download_cover",
                              side_effect=AssertionError("network download")),
        )

    def test_auto_start_false_does_not_start_thread(self):
        self.assertIsNone(self.api._thread)
        self.assertIsNone(self.api.now_playing())

    def test_no_song_cover_methods_return_none_without_scan_or_network(self):
        g1, g2 = self._guard()
        with g1, g2:
            self.assertIsNone(self.api.cover_path())
            self.assertIsNone(self.api.ensure_cover())
            self.assertIsNone(self.api.now_playing())

    def test_cover_path_none_when_cached_file_missing(self):
        self.api._track = Track(name="X", artist="Y", song_id=1,
                                cover_url="https://x/a.jpg",
                                cover_path=os.path.join(self._tmp, "gone.jpg"))
        g1, g2 = self._guard()
        with g1, g2:
            self.assertIsNone(self.api.cover_path())

    def test_zero_byte_cover_is_treated_as_missing_and_redownloaded(self):
        """0 字节缓存视为损坏: cover_path 返回 None, ensure_cover 会重下。"""
        empty = os.path.join(self._tmp, "empty.jpg")
        with open(empty, "wb"):
            pass
        self.api._track = Track(name="X", artist="Y", song_id=1,
                                cover_url="https://x/a.jpg", cover_path=empty)
        with mock.patch.object(player, "_download_cover", return_value="") as fake, \
                mock.patch.object(player, "find_ncm_window",
                                  side_effect=AssertionError("memory scan")):
            self.assertIsNone(self.api.cover_path())
            self.assertIsNone(self.api.ensure_cover())
        fake.assert_called_once_with(1, "https://x/a.jpg")

    def test_cover_path_returns_existing_file_without_network(self):
        path = os.path.join(self._tmp, "cover.jpg")
        with open(path, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0")
        self.api._track = Track(name="X", artist="Y", song_id=1,
                                cover_url="https://x/a.jpg", cover_path=path)
        g1, g2 = self._guard()
        with g1, g2:
            self.assertEqual(self.api.cover_path(), path)
            self.assertEqual(self.api.ensure_cover(), path)

    def test_ensure_cover_returns_none_without_url(self):
        self.api._track = Track(name="X", artist="Y", song_id=1,
                                cover_url="", cover_path="")
        g1, g2 = self._guard()
        with g1, g2:
            self.assertIsNone(self.api.ensure_cover())

    def test_ensure_cover_downloads_lazily_and_updates_track(self):
        path = os.path.join(self._tmp, "cover.jpg")
        with open(path, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0")
        self.api._track = Track(name="X", artist="Y", song_id=28095742,
                                cover_url="https://x/a.jpg", cover_path="")
        with mock.patch.object(player, "_download_cover",
                               return_value=path) as fake, \
                mock.patch.object(player, "find_ncm_window",
                                  side_effect=AssertionError("memory scan")):
            got = self.api.ensure_cover()
        self.assertEqual(got, path)
        fake.assert_called_once_with(28095742, "https://x/a.jpg")
        self.assertEqual(self.api._track.cover_path, path)
        self.assertEqual(self.api.cover_path(), path)

    def test_ensure_cover_returns_none_when_download_fails(self):
        self.api._track = Track(name="X", artist="Y", song_id=1,
                                cover_url="https://x/a.jpg", cover_path="")
        with mock.patch.object(player, "_download_cover", return_value=""), \
                mock.patch.object(player, "find_ncm_window",
                                  side_effect=AssertionError("memory scan")):
            self.assertIsNone(self.api.ensure_cover())

    def test_state_without_track_is_json_serializable(self):
        st = self.api.state()
        self.assertIsNone(st["track"])
        self.assertFalse(st["netease_running"])
        json.dumps(st, ensure_ascii=False)

    def test_state_track_dict_contains_meta_fields(self):
        self.api._track = Track(
            name="Rather Be", artist="Clean Bandit/Jess Glynne",
            song_id=28095742, duration_ms=241057, source="queue",
            album="Rather Be", album_id=2726100,
            artists=["Clean Bandit", "Jess Glynne"],
            cover_url="https://x/a.jpg", cover_path="C:\\c\\1.jpg")
        st = self.api.state()
        track = st["track"]
        self.assertEqual(track["album"], "Rather Be")
        self.assertEqual(track["album_id"], 2726100)
        self.assertEqual(track["artists"], ["Clean Bandit", "Jess Glynne"])
        self.assertEqual(track["cover_url"], "https://x/a.jpg")
        self.assertEqual(track["cover_path"], "C:\\c\\1.jpg")
        self.assertEqual(st["duration_ms"], 241057)
        json.dumps(st, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
