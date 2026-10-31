# -*- coding: utf-8 -*-
"""clock.py - 从网易云音乐(cloudmusic.exe)进程内存中读取实时播放进度。

原理(逆向所得, 网易云 PC 3.x):
  * 网易云不注册 Windows SMTC 媒体控件, 官方也没有提供播放进度接口;
  * 播放器在内存里维护一个"毫秒播放钟", 且存在多个同步副本(别名);
  * 本模块全内存扫描发现这些地址: 找 int32 值在 [1s, 60min] 范围内、
    且每经过 N 秒精确增加 N*1000 的整数, 再按"多个别名数值一致"聚类确认。
    切歌时该值归零。若某些版本用 double 秒存储, 会自动回退到 double 扫描。
  * 发现后只需 ReadProcessMemory 轮询几个地址, 开销极小; 地址失效时可自动重新发现。

跨机器兼容性:
  * 不依赖硬编码内存偏移 —— 每台电脑/每个版本都在运行时重新发现;
  * 窗口类名变化时按进程名 + 标题特征兜底查找;
  * 权限不足(网易云以管理员运行)时给出明确错误提示。

本模块只读目标进程内存, 不注入、不修改。
"""
import ctypes
import ctypes.wintypes as wt
import time

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MEM_COMMIT = 0x1000

READABLE_PROTECT = (0x02, 0x04, 0x08, 0x20, 0x40, 0x80)
CHUNK = 8 * 1024 * 1024

POS_MIN_MS = 1000
POS_MAX_MS = 60 * 60 * 1000

#: 这些窗口标题不是"歌名 - 歌手", 忽略
BAD_WINDOW_TITLES = {"桌面歌词", "迷你播放器", "网易云音乐", "", None}

_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


# ---------------------------------------------------------------- Win32 helpers
def _window_title(hwnd):
    buf = ctypes.create_unicode_buffer(1024)
    u32.GetWindowTextW(hwnd, buf, 1024)
    return buf.value or None


def _window_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(hwnd, buf, 256)
    return buf.value or ""


def _pid_of_window(hwnd):
    pid = wt.DWORD(0)
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _process_image_name(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wt.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value or ""
        return ""
    finally:
        k32.CloseHandle(h)


def _find_song_window_fallback():
    """按进程名 + 标题特征兜底查找网易云"歌名 - 歌手"窗口。

    用于不同版本窗口类名变化(不再是 OrpheusBrowserHost)的情况。
    """
    candidates = []

    def _cb(hwnd, _lparam):
        title = _window_title(hwnd)
        if not title or title in BAD_WINDOW_TITLES:
            return True
        pid = _pid_of_window(hwnd)
        if not pid:
            return True
        image = _process_image_name(pid)
        if not image.lower().endswith("cloudmusic.exe"):
            return True
        candidates.append((hwnd, pid, title))
        return True

    u32.EnumWindows(_WNDENUMPROC(_cb), 0)
    for hwnd, pid, title in candidates:
        if " - " in title:      # 最像"歌名 - 歌手"
            return hwnd, pid
    if candidates:
        return candidates[0][0], candidates[0][1]
    return None, None


def find_ncm_window():
    """返回 (hwnd, pid) - 网易云用来显示 '歌名 - 歌手' 的宿主窗口。

    优先用已知窗口类(OrpheusBrowserHost); 不同版本类名变化时自动兜底。
    """
    hwnd = u32.FindWindowW("OrpheusBrowserHost", None)
    if hwnd:
        return hwnd, _pid_of_window(hwnd)
    return _find_song_window_fallback()


def get_window_title(hwnd=None):
    if not hwnd:
        hwnd, _ = find_ncm_window()
    if not hwnd:
        return None
    return _window_title(hwnd)


def parse_title(title):
    """'歌名 - 歌手' -> (歌名, 歌手)"""
    if not title:
        return None, None
    if " - " in title:
        name, artist = title.rsplit(" - ", 1)
        return name.strip(), artist.strip()
    return title.strip(), ""


def open_process(pid):
    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        err = ctypes.get_last_error()
        if err == 5:  # ERROR_ACCESS_DENIED
            raise PermissionError(
                "读取网易云内存被拒绝(Windows 错误 5)。"
                "请让本程序与网易云音乐使用相同权限运行: "
                "如果网易云是以管理员身份启动的, 本程序也需要管理员权限; "
                "否则请都以普通权限运行。")
        raise OSError(f"OpenProcess 失败, Windows 错误码 {err}")
    return h


def close_handle(h):
    if h:
        k32.CloseHandle(h)


def regions(h):
    out, addr = [], 0
    mbi, sz = _MBI(), ctypes.sizeof(_MBI)
    while True:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi),
                                  ctypes.c_size_t(sz)):
            break
        base, size = mbi.BaseAddress or 0, mbi.RegionSize or 0
        kind = mbi.Protect & 0xFF
        if (mbi.State == MEM_COMMIT and (mbi.Protect & 0x100) == 0
                and kind in READABLE_PROTECT and 0 < size < 0x40000000):
            out.append((base, size))
        nxt = base + size
        if nxt <= addr:
            break
        addr = nxt
    return out


def read_bytes(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf,
                                 ctypes.c_size_t(size), ctypes.byref(got)):
        return None
    return buf.raw[: got.value]


def read_i32(h, addr):
    b = read_bytes(h, addr, 4)
    return int.from_bytes(b, "little", signed=True) if b and len(b) == 4 else None


def read_f64(h, addr):
    b = read_bytes(h, addr, 8)
    if not b or len(b) != 8 or np is None:
        return None
    return float(np.frombuffer(b, dtype="<f8")[0])


# --------------------------------------------------------------------- Clock
class PlaybackClock:
    """发现并持续读取网易云的实时播放位置(毫秒)。

    跨版本策略: 先按 int32 毫秒扫描; 找不到再回退到 double 秒扫描。
    完全不依赖硬编码偏移, 因此不同电脑/不同网易云版本都能自动适配。
    """

    def __init__(self):
        self.h = None
        self.pid = None
        self.aliases = []      # list[int] 播放钟地址
        self.mode = "i32"      # "i32"(毫秒) / "f64"(秒)
        self.duration_ms = 0
        self.last_pos = None
        self.last_read_at = 0.0
        self.last_error = None

    # -- lifecycle
    def available(self):
        return bool(find_ncm_window()[0])

    def attach(self):
        hwnd, pid = find_ncm_window()
        if not pid:
            return False
        if pid != self.pid or not self.h:
            close_handle(self.h)
            self.h = open_process(pid)
            self.pid = pid
            self.aliases = []
        return True

    def detach(self):
        close_handle(self.h)
        self.h = None
        self.pid = None
        self.aliases = []

    # -- scanning
    def _scan_chunks(self, want_f64=False):
        """返回 [[base, take, t, offsets_array, values_array], ...]"""
        lo, hi = (1.0, 3600.0) if want_f64 else (POS_MIN_MS, POS_MAX_MS)
        dtype = "<f8" if want_f64 else "<i4"
        step = 8 if want_f64 else 4
        chunks = []
        for base, size in regions(self.h):
            off = 0
            while off < size:
                take = min(CHUNK, size - off)
                data = read_bytes(self.h, base + off, take)
                if data and len(data) >= step:
                    t = time.time()
                    n = len(data) // step
                    arr = np.frombuffer(data[: n * step], dtype=dtype)
                    mask = (arr >= lo) & (arr <= hi) & np.isfinite(arr)
                    idx = np.nonzero(mask)[0]
                    if len(idx):
                        chunks.append([base + off, take, t, idx.copy(), arr[idx].copy()])
                off += take
        return chunks

    def _refine(self, chunks, want_f64=False, tol=None):
        step = 8 if want_f64 else 4
        scale = 1.0 if want_f64 else 1000.0
        tol = tol if tol is not None else (0.03 if want_f64 else 300.0)
        dtype = "<f8" if want_f64 else "<i4"
        out = []
        for c in chunks:
            base, take, t1, idx, vals = c
            data = read_bytes(self.h, base, take)
            if not data or len(data) < step:
                continue
            t2 = time.time()
            arr = np.frombuffer(data[: (len(data) // step) * step], dtype=dtype)
            vals2 = arr[idx]
            expect = vals + (t2 - t1) * scale
            mask = np.abs(vals2 - expect) <= tol
            if mask.any():
                out.append([base, take, t2, idx[mask].copy(), vals2[mask].copy()])
        return out

    @staticmethod
    def _explode(chunks, step=4):
        pts = []
        for base, take, t, idx, vals in chunks:
            for i, v in zip(idx, vals):
                pts.append((t, base + int(i) * step, float(v)))
        return pts

    def _discover_once(self, want_f64, limit_ms, verbose, log):
        """单次扫描尝试; 成功返回 (mode, aliases, pos_ms), 否则 None。"""
        if not self.attach():
            return None
        t_song = get_window_title()
        if verbose:
            log(f"discover attempt ({'f64' if want_f64 else 'i32'}), song={t_song!r}")
        chunks = self._scan_chunks(want_f64=want_f64)
        time.sleep(1.2)
        chunks = self._refine(chunks, want_f64=want_f64)
        time.sleep(0.8)
        chunks = self._refine(chunks, want_f64=want_f64,
                              tol=(0.02 if want_f64 else 250.0))
        pts = self._explode(chunks, step=8 if want_f64 else 4)
        if get_window_title() != t_song:
            if verbose:
                log("song changed during discovery, retry")
            return None
        # 统一换算成毫秒后聚类
        pts = [(t, addr, v * 1000.0 if want_f64 else v) for t, addr, v in pts]
        pts = [p for p in pts if 0 <= p[2] <= limit_ms]
        if not pts:
            if verbose:
                log("no candidates")
            return None
        pts.sort(key=lambda x: x[2])
        clusters, cur = [], [pts[0]]
        for p in pts[1:]:
            if p[2] - cur[-1][2] <= 120:
                cur.append(p)
            else:
                clusters.append(cur)
                cur = [p]
        clusters.append(cur)
        clusters = [c for c in clusters if len(c) >= 2]
        if not clusters:
            if verbose:
                log(f"candidates={len(pts)} but no alias cluster")
            return None
        clusters.sort(key=lambda c: len(c), reverse=True)
        best = clusters[0]
        pos = int(np.median([p[2] for p in best]))
        if self.duration_ms and pos > self.duration_ms + 10000:
            if verbose:
                log(f"cluster pos={pos}ms 超出时长, 重试")
            return None
        return ("f64" if want_f64 else "i32", [p[1] for p in best], pos)

    def discover(self, duration_ms=0, attempts=3, verbose=False, log=None):
        """全内存扫描发现播放钟。返回 True/False。

        先按 int32 毫秒扫描, 失败再回退到 double 秒 —— 以兼容不同网易云版本。
        """
        if np is None:
            raise RuntimeError("内存扫描需要 numpy, 请先 pip install numpy")
        log = log or (lambda msg: None)
        self.duration_ms = duration_ms or self.duration_ms
        limit = (self.duration_ms + 8000) if self.duration_ms else POS_MAX_MS
        self.last_error = None
        for want_f64 in (False, True):
            for _attempt in range(1, attempts + 1):
                try:
                    result = self._discover_once(want_f64, limit, verbose, log)
                except PermissionError as e:
                    self.last_error = str(e)
                    raise
                except Exception as e:  # 单次尝试失败不致命
                    self.last_error = f"scan failed: {e}"
                    if verbose:
                        log(self.last_error)
                    time.sleep(0.5)
                    continue
                if result is None:
                    continue
                mode, aliases, pos = result
                self.mode = mode
                self.aliases = aliases
                self.last_pos = pos
                self.last_read_at = time.time()
                if verbose:
                    log(f"aliases={len(self.aliases)} mode={mode} pos={pos}ms")
                return True
        return False

    # -- reading
    def read(self):
        """返回当前播放位置(ms), 失败返回 None。顺带剔除失效地址。"""
        if not self.h and not self.attach():
            return None
        vals, alive = [], []
        if self.mode == "f64":
            for a in self.aliases[:10]:
                v = read_f64(self.h, a)
                if v is not None and 0 <= v <= POS_MAX_MS / 1000.0:
                    vals.append(int(v * 1000.0))
                    alive.append(a)
        else:
            for a in self.aliases[:10]:
                v = read_i32(self.h, a)
                if v is not None and 0 <= v <= POS_MAX_MS:
                    vals.append(v)
                    alive.append(a)
        if not vals:
            return None
        if self.aliases and len(alive) < len(self.aliases):
            self.aliases = alive
        vals.sort()
        med = vals[len(vals) // 2]
        if len(vals) >= 3 and (vals[-1] - vals[0]) > 400:
            return None
        self.last_pos = med
        self.last_read_at = time.time()
        return med


if __name__ == "__main__":  # 简单自检
    clk = PlaybackClock()
    print("netease title:", get_window_title())
    t0 = time.time()
    ok = clk.discover(verbose=True, log=print)
    print(f"discover={ok} cost={time.time()-t0:.2f}s aliases={[hex(a) for a in clk.aliases]}")
    if ok:
        for _ in range(10):
            print(time.strftime("%H:%M:%S"), "pos =", clk.read(), "ms")
            time.sleep(0.8)
    clk.detach()
