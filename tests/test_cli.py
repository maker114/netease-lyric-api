# -*- coding: utf-8 -*-
"""CLI 离线测试: status 输出含专辑/封面, cover 子命令成功/失败返回码。

用假 api 替换 NeteaseLyric, 不启动后台同步、不读内存、不联网。
"""
import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

import netease_lyric.player as player
from netease_lyric import cli


def make_state(track=True, cover_path="C:\\cache\\covers\\28095742.jpg",
               cover_url="https://p2.music.126.net/a.jpg"):
    tr = None
    if track:
        tr = {
            "name": "Rather Be",
            "artist": "Clean Bandit/Jess Glynne",
            "artists": ["Clean Bandit", "Jess Glynne"],
            "title": "Rather Be - Clean Bandit/Jess Glynne",
            "song_id": 28095742,
            "duration_ms": 241057,
            "source": "queue",
            "album": "Rather Be",
            "album_id": 2726100,
            "cover_url": cover_url,
            "cover_path": cover_path,
        }
    return {
        "ok": True, "api_version": "1.1.0", "netease_running": True,
        "playing": bool(track), "paused": False, "clock_ready": True,
        "syncing": False, "lyric_loading": False, "lyric_ready": True,
        "line_count": 1, "position_ms": 12345, "duration_ms": 241057,
        "progress": 0.05, "track": tr,
        "line": {"index": 0, "start_ms": 1000, "end_ms": 12000,
                 "text": "We're a thousand miles from comfort",
                 "translation": "我们相隔千里"},
        "prev_line": None,
        "next_line": {"index": 1, "start_ms": 12500, "end_ms": 30000,
                      "text": "But as long as you are with me",
                      "translation": ""},
        "lyric_offset_ms": 0, "updated_at": 0.0, "error": None,
    }


class FakeApi(object):
    def __init__(self, state=None, cover=None):
        self._state = state if state is not None else make_state()
        self._cover = cover
        self.stopped = False
        self.waited = []

    def state(self):
        return self._state

    def wait_for_ready(self, timeout=10.0):
        self.waited.append(timeout)
        return True

    def ensure_cover(self):
        return self._cover

    def stop(self):
        self.stopped = True


def capture(fn, *args, **kwargs):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(*args, **kwargs)
    return rc, buf.getvalue()


class PrintStatusTests(unittest.TestCase):
    def test_status_prints_album_and_cover_path(self):
        state = make_state()
        rc, out = capture(cli._print_status, SimpleNamespace(
            state=lambda: state))
        self.assertEqual(rc, 0)
        self.assertIn("♪ Rather Be - Clean Bandit/Jess Glynne", out)
        self.assertIn("专辑 Rather Be", out)
        self.assertIn("封面 C:\\cache\\covers\\28095742.jpg", out)
        self.assertIn("song_id=28095742", out)
        self.assertIn("来源=queue", out)
        self.assertIn("We're a thousand miles from comfort", out)
        self.assertNotIn("未检测到", out)

    def test_status_without_cover_path_shows_unCached_url(self):
        state = make_state(cover_path="")
        rc, out = capture(cli._print_status, SimpleNamespace(
            state=lambda: state))
        self.assertEqual(rc, 0)
        self.assertIn("封面 https://p2.music.126.net/a.jpg (未缓存)", out)

    def test_status_without_track(self):
        state = make_state(track=False)
        rc, out = capture(cli._print_status, SimpleNamespace(
            state=lambda: state))
        self.assertEqual(rc, 0)
        self.assertIn("正在识别当前歌曲", out)

    def test_status_when_netease_not_running_returns_1(self):
        state = make_state()
        state["netease_running"] = False
        rc, out = capture(cli._print_status, SimpleNamespace(
            state=lambda: state))
        self.assertEqual(rc, 1)
        self.assertIn("未检测到网易云音乐客户端", out)


class CmdCoverTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="netease-lyric-cli-test-")
        self.jpg = os.path.join(self._tmp, "cover.jpg")
        with open(self.jpg, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, fake_api, out=None):
        args = SimpleNamespace(out=out, wait=0.2)
        with mock.patch.object(player, "NeteaseLyric",
                               return_value=fake_api):
            return capture(cli._cmd_cover, args)

    def test_cover_success_prints_path_and_returns_0(self):
        api = FakeApi(cover=self.jpg)
        rc, out = self._run(api)
        self.assertEqual(rc, 0)
        self.assertIn(self.jpg, out)
        self.assertTrue(api.stopped)
        self.assertEqual(api.waited, [0.2])

    def test_cover_out_copies_file(self):
        api = FakeApi(cover=self.jpg)
        dest = os.path.join(self._tmp, "saved.jpg")
        rc, out = self._run(api, out=dest)
        self.assertEqual(rc, 0)
        self.assertIn("封面已保存到", out)
        self.assertTrue(os.path.isfile(dest))
        with open(dest, "rb") as f:
            self.assertEqual(f.read()[:3], b"\xff\xd8\xff")

    def test_cover_failure_returns_1(self):
        api = FakeApi(cover=None)
        rc, out = self._run(api)
        self.assertEqual(rc, 1)
        self.assertIn("未能获取当前歌曲封面", out)
        self.assertTrue(api.stopped)

    def test_main_cover_dispatches_and_returns_0(self):
        api = FakeApi(cover=self.jpg)
        with mock.patch.object(player, "NeteaseLyric", return_value=api):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["cover"])
        self.assertEqual(rc, 0)
        self.assertIn(self.jpg, buf.getvalue())
        self.assertTrue(api.stopped)

    def test_main_version_is_offline(self):
        rc, out = capture(cli.main, ["version"])
        self.assertEqual(rc, 0)
        self.assertTrue(out.strip())


if __name__ == "__main__":
    unittest.main()
