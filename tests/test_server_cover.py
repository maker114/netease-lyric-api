# -*- coding: utf-8 -*-
"""HTTP API 离线测试: 用 stub api 启动真实 ThreadingHTTPServer, 验证 /api/cover 等。

不依赖 NeteaseLyric / 网易云客户端 / 网络: 服务只监听 127.0.0.1 随机端口。
"""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from netease_lyric import server

JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 64
TRACK = {
    "name": "Rather Be",
    "artist": "Clean Bandit/Jess Glynne",
    "artists": ["Clean Bandit", "Jess Glynne"],
    "title": "Rather Be - Clean Bandit/Jess Glynne",
    "song_id": 28095742,
    "duration_ms": 241057,
    "source": "queue",
    "album": "Rather Be",
    "album_id": 2726100,
    "cover_url": "https://p2.music.126.net/a.jpg",
    "cover_path": "",
}


class StubApi(object):
    """LyricApiServer 需要的 api 最小接口。"""

    def __init__(self, cover_path=None, ensure_cover_result=None):
        self._cover_path = cover_path
        self._ensure_cover_result = ensure_cover_result
        self.cover_path_calls = 0
        self.ensure_cover_calls = 0
        self.cover_path_args = []

    def state(self):
        track = dict(TRACK)
        track["cover_path"] = self._cover_path or ""
        return {
            "ok": True,
            "api_version": "1.1.0",
            "netease_running": True,
            "playing": True,
            "paused": False,
            "clock_ready": True,
            "syncing": False,
            "lyric_loading": False,
            "lyric_ready": True,
            "line_count": 1,
            "position_ms": 12345,
            "duration_ms": 241057,
            "progress": 0.0512,
            "track": track,
            "line": {"index": 0, "start_ms": 1000, "end_ms": 12000,
                     "text": "We're a thousand miles from comfort",
                     "translation": "我们相隔千里"},
            "prev_line": None,
            "next_line": None,
            "lyric_offset_ms": 0,
            "updated_at": 0.0,
            "error": None,
        }

    def is_netease_running(self):
        return True

    def lyrics(self):
        return []

    def cover_path(self):
        self.cover_path_calls += 1
        self.cover_path_args.append(None)
        return self._cover_path

    def ensure_cover(self):
        self.ensure_cover_calls += 1
        return self._ensure_cover_result


class ServerCoverTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="netease-lyric-server-test-")
        self.jpg = os.path.join(self._tmp, "cover.jpg")
        with open(self.jpg, "wb") as f:
            f.write(JPEG_BYTES)
        self._httpd = None
        self._thread = None

    def tearDown(self):
        if self._httpd is not None:
            self._httpd.running = False
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self.assertFalse(self._thread.is_alive())
            self._thread = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------- helpers
    def start_server(self, api):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), server._Handler)
        self._httpd.daemon_threads = True
        self._httpd.api = api
        self._httpd.running = True
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="netease-lyric-test-http",
                                        daemon=True)
        self._thread.start()
        host, port = self._httpd.server_address[:2]
        self.base = "http://%s:%d" % (host, port)

    def get(self, path, method="GET"):
        req = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    # --------------------------------------------------------------- tests
    def test_now_playing_contains_album_artists_and_cover_fields(self):
        self.start_server(StubApi())
        status, headers, body = self.get("/api/now-playing")
        self.assertEqual(status, 200)
        self.assertTrue(headers.get("Content-Type", "").startswith(
            "application/json"))
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["track"]["album"], "Rather Be")
        self.assertEqual(payload["track"]["album_id"], 2726100)
        self.assertEqual(payload["track"]["artists"],
                         ["Clean Bandit", "Jess Glynne"])
        self.assertEqual(payload["track"]["cover_url"],
                         "https://p2.music.126.net/a.jpg")
        self.assertIn("cover_path", payload["track"])
        self.assertEqual(payload["duration_ms"], 241057)

    def test_state_track_contains_meta_fields(self):
        self.start_server(StubApi())
        status, _headers, body = self.get("/api/state")
        self.assertEqual(status, 200)
        track = json.loads(body.decode("utf-8"))["track"]
        for key in ("album", "album_id", "artists", "cover_url", "cover_path"):
            self.assertIn(key, track)

    def test_cover_serves_cached_jpeg_with_cors(self):
        api = StubApi(cover_path=self.jpg)
        self.start_server(api)
        status, headers, body = self.get("/api/cover")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/jpeg")
        self.assertEqual(headers.get("Content-Length"), str(len(JPEG_BYTES)))
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(body, JPEG_BYTES)
        self.assertEqual(api.cover_path_calls, 1)
        # 已缓存时不应再触发懒下载
        self.assertEqual(api.ensure_cover_calls, 0)

    def test_cover_lazily_downloads_when_not_cached(self):
        api = StubApi(cover_path=None, ensure_cover_result=self.jpg)
        self.start_server(api)
        status, headers, body = self.get("/api/cover")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/jpeg")
        self.assertEqual(body, JPEG_BYTES)
        self.assertEqual(api.cover_path_calls, 1)
        self.assertEqual(api.ensure_cover_calls, 1)

    def test_cover_404_json_when_unavailable(self):
        api = StubApi(cover_path=None, ensure_cover_result=None)
        self.start_server(api)
        status, headers, body = self.get("/api/cover")
        self.assertEqual(status, 404)
        self.assertTrue(headers.get("Content-Type", "").startswith(
            "application/json"))
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        payload = json.loads(body.decode("utf-8"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "cover not available")

    def test_cover_404_when_ensure_returns_missing_file(self):
        missing = os.path.join(self._tmp, "never-written.jpg")
        api = StubApi(cover_path=None, ensure_cover_result=missing)
        self.start_server(api)
        status, _headers, body = self.get("/api/cover")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body.decode("utf-8"))["ok"], False)

    def test_cover_404_when_cached_file_is_empty(self):
        empty = os.path.join(self._tmp, "empty.jpg")
        with open(empty, "wb"):
            pass
        api = StubApi(cover_path=empty)
        self.start_server(api)
        status, headers, body = self.get("/api/cover")
        self.assertEqual(status, 404)
        self.assertTrue(headers.get("Content-Type", "").startswith(
            "application/json"))
        self.assertEqual(json.loads(body.decode("utf-8"))["error"],
                         "cover not available")

    def test_health_endpoint(self):
        self.start_server(StubApi())
        status, _headers, body = self.get("/api/health")
        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["netease_running"])

    def test_options_preflight_cors(self):
        self.start_server(StubApi())
        status, headers, body = self.get("/api/cover", method="OPTIONS")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        self.assertIn("GET", headers.get("Access-Control-Allow-Methods", ""))
        self.assertEqual(body, b"")

    def test_unknown_path_returns_404_json(self):
        self.start_server(StubApi())
        status, _headers, body = self.get("/api/nope")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body.decode("utf-8"))["error"], "not found")

    def test_server_stops_cleanly(self):
        self.start_server(StubApi())
        status, _headers, _body = self.get("/api/health")
        self.assertEqual(status, 200)
        self._httpd.running = False
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread.join(timeout=5)
        self.assertFalse(self._thread.is_alive())
        self._thread = None


if __name__ == "__main__":
    unittest.main()
