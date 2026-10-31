# netease-lyric-api

网易云音乐 **实时播放状态 / 歌词** 的 Python 接口库（Windows）。  
可以被任意 Python 项目 `pip install` 后直接调用；附带一个**可选**的桌面悬浮歌词窗（GUI）。

- ✅ 实时播放进度（毫秒级）
- ✅ 当前歌曲 / 歌手 / 时长 / 歌单来源
- ✅ 当前歌词行 + 上一句 + 下一句 + 中文翻译
- ✅ Python API / 命令行 / 本地 HTTP API（JSON + SSE）三种调用方式
- ✅ GUI 悬浮窗是可选模块，纯接口调用不依赖 tkinter

原理：只读 `cloudmusic.exe` 进程内存，定位播放器内部的"毫秒播放钟"来获取实时进度；
歌名取自客户端窗口标题，歌词来自网易云歌词接口。**不修改网易云、不需要登录、不注入 DLL。**

![GUI 预览](docs/preview.png)

> 平台要求：**Windows 10/11** + 网易云音乐 PC 客户端；Python 3.8+；依赖 `numpy`。

---

## 安装

```bash
# 1) 从源码目录安装（推荐开发/本机使用）
pip install .

# 2) 或先构建 wheel，再拷到其他电脑安装
python -m pip wheel . --no-deps --no-build-isolation -w dist
pip install dist\netease_lyric_api-1.0.0-py3-none-any.whl
```

安装后会得到两个命令：`netease-lyric`（接口/CLI）和 `netease-lyric-overlay`（可选 GUI）。
如果不想安装，也可以在本目录直接 `python -m netease_lyric ...` 使用。

---

## 跨电脑兼容性（换电脑要重新配置吗？）

**通常不需要**。所有和机器相关的东西都是运行时自动发现的：

| 项目 | 处理方式 |
|---|---|
| 网易云进程 PID / 内存地址 | 运行时按窗口/进程查找；内存地址每次启动重新扫描，**没有硬编码偏移** |
| 播放钟格式(int32 ms / double s) | 先扫 int32，失败自动回退 double，兼容不同版本 |
| 窗口类名变化 | 优先 `OrpheusBrowserHost`，找不到则按 `cloudmusic.exe` 进程名 + 标题特征兜底 |
| 歌曲 ID / 歌词 | 本地播放队列优先，缺失则走搜索接口，均无需配置 |
| GUI 窗口位置 | 存在**本机**的 `%LOCALAPPDATA%\NeteaseLyricApi\window.json`，拷项目到别的电脑不会带过去 |
| 依赖 | wheel 声明了 `numpy`，`pip install` 自动装 |

换到新电脑后，建议先跑一次环境自检：

```bash
netease-lyric doctor
```

它会逐项检查：操作系统 / Python 版本 / numpy / tkinter(可选 GUI) / 网易云窗口 /
内存读取权限 / 播放进度定位 / 歌词接口连通性，并给出针对性提示。

少数需要手动处理的情况：

- **权限不一致**：如果网易云是"以管理员身份运行"，调用方程序也必须管理员权限
  （反之都普通权限即可）。`doctor` 会直接报"进程内存读取权限: 失败"。
- **网易云大版本改动**：若播放钟结构被彻底改掉，会自动重试并失败；先 `rediscover()`，
  仍不行就需要更新适配（`doctor` 会显示"播放进度定位: 未成功"）。
- **歌词接口不通**：需要能访问 `music.163.com`（代理/防火墙），否则只有进度没有歌词。
- **纯音乐/无版权歌词**：显示"暂无歌词"，不是故障。

---

## Python API（核心）

```python
from netease_lyric import NeteaseLyric

with NeteaseLyric() as api:          # 构造即开始后台同步
    if api.wait_for_ready(timeout=10):
        print(api.now_playing().title)     # "歌名 - 歌手"
        print(api.position_ms())           # 实时进度(ms)
        line = api.current_line()
        if line:
            print(line.text, "|", line.translation)
```

### 类参考

```python
NeteaseLyric(poll_interval=0.1, lyric_offset_ms=0, auto_start=True)
```

| 方法 / 属性 | 说明 |
|---|---|
| `start()` / `stop()` / `close()` | 启动/停止后台同步（`with` 退出时自动 stop） |
| `is_netease_running()` | 网易云客户端是否在运行 |
| `now_playing()` | 当前歌曲 `Track`，未识别返回 `None` |
| `position_ms()` | 实时播放进度（毫秒，已做 ≤0.5s 插值），未知返回 `None` |
| `duration_ms()` | 当前歌曲时长（毫秒） |
| `is_paused()` | 是否暂停 |
| `current_line()` | 当前歌词行 `LyricLine`，前奏/无歌词返回 `None` |
| `lyrics()` | 完整歌词 `list[LyricLine]`（带时间轴和翻译） |
| `state()` | 完整状态快照 `dict`（字段见下表） |
| `add_listener(fn)` / `remove_listener(fn)` | 事件回调（在后台轮询线程触发） |
| `wait_for_ready(timeout=10)` | 阻塞等待首次就绪（识别到歌 + 歌词/时钟） |
| `wait_until_playing(timeout=10)` | 阻塞等待正在播放 |
| `rediscover()` | 强制重新定位播放钟（换版本/异常时可用） |
| `version` | API 版本 |

`Track` 字段：`name, artist, title, song_id, duration_ms, source, album`  
`LyricLine` 字段：`index, start_ms, end_ms, text, translation`

### `state()` 返回字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | bool | 服务正常 |
| `api_version` | str | 版本 |
| `netease_running` | bool | 网易云是否运行 |
| `playing` | bool | 是否识别到歌曲 |
| `paused` | bool | 是否暂停 |
| `clock_ready` | bool | 播放钟是否已定位 |
| `syncing` | bool | 是否正在重新定位播放钟 |
| `lyric_loading` | bool | 歌词是否正在加载 |
| `lyric_ready` | bool | 是否已加载到歌词 |
| `line_count` | int | 歌词行数 |
| `position_ms` | int/null | 当前进度(ms) |
| `duration_ms` | int | 歌曲时长(ms) |
| `progress` | float/null | 0~1 进度 |
| `track` | object/null | 歌曲信息（同 `Track`） |
| `line` | object/null | 当前歌词行 |
| `prev_line` / `next_line` | object/null | 上一句 / 下一句 |
| `lyric_offset_ms` | int | 歌词偏移 |
| `updated_at` | float | 更新时刻(epoch 秒) |
| `error` | str/null | 最近错误 |

### 事件

```python
api.add_listener(lambda ev: print(ev))

# event 取值:
#   song_change           {"track": {...}}
#   lyric_ready           {"track": {...}, "line_count": n}
#   lyric_none            {"track": {...}}
#   line_change           {"line": {...}, "position_ms": int}
#   clock_discovered      {}
#   clock_discovery_failed{}
#   netease_available     {}
#   netease_unavailable   {}
```

> 回调在后台线程执行，请勿在回调里做耗时操作；GUI（tkinter）场景建议轮询 `state()`。

---

## 命令行

```bash
netease-lyric status          # 打印当前播放 + 歌词状态
netease-lyric json            # 输出完整状态 JSON（适合脚本/管道）
netease-lyric watch           # 持续打印歌词行（Ctrl+C 退出）
netease-lyric serve           # 启动本地 HTTP API（默认 127.0.0.1:23517）
netease-lyric overlay         # 启动可选 GUI 悬浮歌词窗
netease-lyric doctor          # 环境自检（换电脑/排查问题）
netease-lyric version
```

未安装时等价写法：`python -m netease_lyric status`。

---

## 本地 HTTP API

```bash
netease-lyric serve --port 23517
# 或 python -m netease_lyric serve
```

| 端点 | 说明 |
|---|---|
| `GET /` | 内置实时歌词网页（可作 OBS 浏览器源） |
| `GET /api/state` | 完整状态快照（同 `state()`） |
| `GET /api/now-playing` | 当前歌曲 + 进度 |
| `GET /api/line` | 当前歌词行 + 上/下一句 |
| `GET /api/lyrics` | 当前歌曲完整歌词 |
| `GET /api/stream` | SSE 状态流（状态变化推送） |
| `GET /api/health` | 健康检查 |
| `GET /api/docs` | 端点说明 |

```bash
curl http://127.0.0.1:23517/api/state
curl http://127.0.0.1:23517/api/line
curl -N http://127.0.0.1:23517/api/stream     # SSE
```

浏览器打开 `http://127.0.0.1:23517/` 就是一个实时歌词页（深色、可全屏、可给 OBS 当浏览器源）。
所有响应都带 CORS 头，方便网页/其他程序直接调用。

---

## 可选 GUI（桌面悬浮歌词窗）

```bash
netease-lyric overlay                        # 默认样式
netease-lyric overlay --width 1400 --font-size 26 --opacity 0.9 --bottom 60
netease-lyric overlay --reset-pos            # 重置窗口位置
```

- 无边框、半透明、置顶，显示 上一句 / 当前句（大字）/ 中文翻译 / 下一句 / 进度条
- 鼠标拖动移动位置（会记住）；右键菜单：重新定位播放进度 / 退出；Esc 退出
- 切歌自动换歌词、自动重新定位；暂停显示 ⏸；纯音乐显示"纯音乐 / 暂无歌词"
- 日志：`%LOCALAPPDATA%\NeteaseLyricApi\overlay.log`

GUI 只是 API 的一个消费者：`lyric_overlay.py` 风格，核心逻辑全部在接口层。

---

## 原理（逆向说明）

1. **播放进度**：网易云不注册 Windows 媒体控件（SMTC），也没有进度接口。
   本项目在 `cloudmusic.exe` 的可读内存里全量扫描，找"每经过 N 秒精确增加 N×1000 的
   int32 值"（播放钟），再用"多个同步副本数值一致"聚类确认；切歌时该值归零。
   发现后仅需 `ReadProcessMemory` 轮询几个地址，开销极小。
   **不依赖硬编码偏移**：换网易云版本/换电脑后自动重新发现（约 3~5 秒）。
2. **歌曲识别**：读客户端隐藏窗口标题（`OrpheusBrowserHost`，"歌名 - 歌手"），
   并优先用本地播放队列 `webdata/file/playingList` 精确匹配 `song_id`。
3. **歌词**：网易云歌词接口（原文 LRC + 翻译 tlyric），本地缓存。

全程只读目标进程内存，不注入、不修改。

---

## 常见问题

- **提示未检测到网易云**：先启动网易云音乐 PC 客户端并播放。
- **首次启动/切歌后 3~5 秒无歌词进度**：正在定位播放钟，属正常；之后自动恢复。
- **换网易云版本后失效**：先右键菜单/`rediscover()` 重试；仍不行说明播放钟结构被改，
  需要重新适配（欢迎反馈）。
- **没有歌词**：纯音乐、或网易云无版权歌词会显示"纯音乐 / 暂无歌词"。
- **翻译错位**：翻译来自网易云自己的 tlyric 数据，个别歌曲其时间轴本身有偏差。
- **多显示器/高 DPI**：GUI 已声明 DPI 感知并用 Win32 物理像素定位。

---

## 目录结构

```
netease-lyric-api/
├── netease_lyric/          # 核心包(安装后 import netease_lyric)
│   ├── __init__.py         # 公共导出(懒加载)
│   ├── _version.py
│   ├── clock.py            # 逆向: 播放钟发现/读取
│   ├── lyrics.py           # 歌词匹配/获取/缓存
│   ├── player.py           # 核心 API: NeteaseLyric
│   ├── server.py           # 可选: 本地 HTTP API
│   ├── cli.py              # 命令行
│   ├── overlay.py          # 可选: GUI 悬浮歌词窗
│   └── __main__.py
├── examples/
│   ├── python_api.py       # Python API 示例
│   └── http_client.py      # HTTP API 示例
├── pyproject.toml
├── requirements.txt
├── run_overlay.bat         # 双击启动 GUI
├── run_api.bat             # 双击启动 HTTP API
├── build_wheel.bat         # 构建可分发 wheel
└── README.md
```

## License

MIT
