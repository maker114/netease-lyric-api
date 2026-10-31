# -*- coding: utf-8 -*-
"""server.py - 可选: 本地 HTTP API 服务(基于 NeteaseLyric, 纯标准库实现)。

启动方式::

    python -m netease_lyric serve --port 23517
    # 或安装后: netease-lyric serve

端点:
    GET /                 内置实时歌词网页(可作 OBS 浏览器源)
    GET /api/state        完整状态快照
    GET /api/now-playing  当前歌曲 + 进度
    GET /api/line         当前歌词行 + 上/下一句
    GET /api/lyrics       当前歌曲完整歌词
    GET /api/stream       SSE 状态流(状态变化时推送)
    GET /api/health       健康检查
    GET /api/docs         端点说明
"""
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from ._version import __version__
from .player import NeteaseLyric

log = logging.getLogger("netease_lyric.server")

DEFAULT_PORT = 23517

DEMO_HTML = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>网易云实时歌词 API</title>
<style>
  html,body{margin:0;height:100%;background:#07080c;color:#fff;
    font-family:"Microsoft YaHei UI","PingFang SC",system-ui,sans-serif;}
  #wrap{height:100%;display:flex;flex-direction:column;justify-content:center;
    align-items:center;text-align:center;padding:0 4vw;box-sizing:border-box}
  #head{color:#8a93a6;font-size:15px;letter-spacing:.5px;margin-bottom:18px}
  #prev{color:#5f6779;font-size:20px;margin:6px 0;min-height:26px}
  #cur{color:#fff;font-size:clamp(26px,4vw,54px);font-weight:700;margin:10px 0;
    line-height:1.25;text-shadow:0 2px 24px rgba(0,0,0,.6)}
  #tr{color:#8ecdf7;font-size:clamp(15px,1.6vw,24px);margin:6px 0;min-height:24px}
  #next{color:#5f6779;font-size:20px;margin:8px 0;min-height:26px}
  #bar{width:min(900px,80vw);height:4px;background:#1a2030;border-radius:2px;
    margin-top:26px;overflow:hidden}
  #bar>i{display:block;height:100%;width:0;
    background:linear-gradient(90deg,#4f7cff,#7fb0ff);transition:width .2s linear}
  #err{color:#e06767;font-size:13px;margin-top:14px;min-height:18px}
</style></head>
<body><div id="wrap">
  <div id="head">♪ 连接中…</div>
  <div id="prev"></div>
  <div id="cur"></div>
  <div id="tr"></div>
  <div id="next"></div>
  <div id="bar"><i></i></div>
  <div id="err"></div>
</div>
<script>
const $ = (id) => document.getElementById(id);
const fmt = (ms) => ms==null ? "--:--"
  : String(Math.floor(ms/60000)).padStart(2,"0")+":"+String(Math.floor(ms/1000)%60).padStart(2,"0");
async function tick(){
  try{
    const r = await fetch("/api/state", {cache:"no-store"});
    const s = await r.json();
    $("err").textContent = s.error || "";
    if(!s.netease_running){
      $("head").textContent = "♪ 未检测到网易云音乐客户端";
      return;
    }
    const t = s.track;
    if(!t){ $("head").textContent = "♪ 正在识别歌曲…"; return; }
    let head = "♪ " + t.title;
    if(s.duration_ms) head += "   " + fmt(s.position_ms) + " / " + fmt(s.duration_ms);
    if(s.paused) head += "   ⏸ 已暂停";
    if(s.syncing) head += "   · 同步中…";
    $("head").textContent = head;
    const l = s.line, p = s.prev_line, n = s.next_line;
    $("prev").textContent = p ? p.text : "";
    $("cur").textContent  = l ? l.text
      : (s.lyric_loading ? "正在获取歌词…" : (s.lyric_ready ? "♪ 前奏 ♪" : "纯音乐 / 暂无歌词"));
    $("tr").textContent   = l ? (l.translation || "") : "";
    $("next").textContent = n ? n.text : "";
    $("bar").firstElementChild.style.width = ((s.progress||0)*100).toFixed(1)+"%";
  }catch(e){ $("err").textContent = "连接失败: " + e; }
}
tick(); setInterval(tick, 200);
</script></body></html>
"""

ENDPOINTS = {
    "GET /": "内置实时歌词网页 (可直接用作 OBS 浏览器源)",
    "GET /api/state": "完整状态快照 (JSON)",
    "GET /api/now-playing": "当前歌曲 + 进度",
    "GET /api/line": "当前歌词行 + 上/下一句",
    "GET /api/lyrics": "当前歌曲完整歌词 (含时间轴/翻译)",
    "GET /api/stream": "SSE 状态流 (text/event-stream)",
    "GET /api/health": "健康检查",
    "GET /api/docs": "本说明",
}


class _Handler(BaseHTTPRequestHandler):
    server_version = f"NeteaseLyricApi/{__version__}"
    protocol_version = "HTTP/1.1"

    # 静默默认日志, 交给 logging 模块
    def log_message(self, fmt, *args):
        log.debug("http: " + fmt, *args)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def _send(self, body: bytes, ctype: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, status: int = 200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", status)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if len(path) > 1:
            path = path.rstrip("/")
        api = self.server.api  # type: ignore[attr-defined]
        try:
            if path in ("/", "/index.html", "/demo"):
                return self._send(DEMO_HTML.encode("utf-8"),
                                  "text/html; charset=utf-8")
            if path == "/api/docs":
                return self._send_json({"ok": True, "api_version": __version__,
                                        "endpoints": ENDPOINTS})
            if path == "/api/health":
                return self._send_json({"ok": True, "api_version": __version__,
                                        "netease_running": api.is_netease_running()})
            if path == "/api/state":
                return self._send_json(api.state())
            if path == "/api/now-playing":
                st = api.state()
                return self._send_json({
                    "ok": st["ok"], "netease_running": st["netease_running"],
                    "playing": st["playing"], "paused": st["paused"],
                    "position_ms": st["position_ms"], "duration_ms": st["duration_ms"],
                    "progress": st["progress"], "track": st["track"],
                })
            if path == "/api/line":
                st = api.state()
                return self._send_json({
                    "ok": st["ok"], "track": st["track"],
                    "line": st["line"], "prev_line": st["prev_line"],
                    "next_line": st["next_line"], "position_ms": st["position_ms"],
                })
            if path == "/api/lyrics":
                st = api.state()
                return self._send_json({
                    "ok": st["ok"], "track": st["track"],
                    "lines": [ln.to_dict() for ln in api.lyrics()],
                })
            if path == "/api/stream":
                return self._stream(api)
            return self._send_json({"ok": False, "error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:  # noqa: BLE001
            log.exception("request failed")
            try:
                self._send_json({"ok": False, "error": str(e)}, 500)
            except Exception:
                self.close_connection = True

    def _stream(self, api: NeteaseLyric):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        last_sig = None
        last_ping = time.time()
        try:
            while getattr(self.server, "running", True):
                st = api.state()
                sig = (
                    st["track"]["song_id"] if st["track"] else None,
                    st["line"]["index"] if st["line"] else None,
                    st["paused"], round((st["position_ms"] or 0) / 500),
                    st["clock_ready"], st["lyric_ready"],
                )
                if sig != last_sig:
                    payload = json.dumps(st, ensure_ascii=False).encode("utf-8")
                    self.wfile.write(b"event: state\ndata: " + payload + b"\n\n")
                    self.wfile.flush()
                    last_sig = sig
                if time.time() - last_ping > 15:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = time.time()
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.close_connection = True


class _ThreadingServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    api = None
    running = True


class LyricApiServer:
    """把 NeteaseLyric 包装成本地 HTTP API。

    :param host: 监听地址, 默认 127.0.0.1 (仅本机)
    :param port: 监听端口, 默认 23517
    :param api: 可传入已有的 NeteaseLyric 实例复用
    """

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 api: NeteaseLyric = None, poll_interval: float = 0.1,
                 lyric_offset_ms: int = 0):
        self.host = host
        self.port = port
        self._owns_api = api is None
        self.api = api or NeteaseLyric(poll_interval=poll_interval,
                                       lyric_offset_ms=lyric_offset_ms)
        self._httpd = None
        self._thread = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _ensure(self):
        if self._httpd is None:
            self._httpd = _ThreadingServer((self.host, self.port), _Handler)
            self._httpd.api = self.api
            self._httpd.running = True

    def start(self) -> "LyricApiServer":
        """后台线程启动, 立即返回。"""
        self._ensure()
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="netease-lyric-http", daemon=True)
        self._thread.start()
        log.info("HTTP API: %s", self.url)
        return self

    def serve_forever(self) -> None:
        """前台阻塞运行(适合 CLI)。"""
        self._ensure()
        log.info("HTTP API: %s", self.url)
        self._httpd.serve_forever()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.running = False
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        if self._owns_api:
            self.api.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    srv = LyricApiServer()
    print(f"HTTP API: {srv.url}  (Ctrl+C 退出)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
