# netease-lyric-api

网易云音乐 PC 端的**实时歌词 + 歌曲信息读取 API**，附带一个可选的**桌面悬浮歌词窗**。

只读 `cloudmusic.exe` 进程内存，不修改网易云客户端、不需要登录、不注入 DLL。
除了歌词和播放进度，还能拿到当前歌曲的**歌手（作者）、专辑名和专辑封面**。

> 平台：**Windows 10/11**　Python：**3.8+**　依赖：**numpy**（悬浮窗封面显示可选 **Pillow**）

---

## Part 1. 歌词 / 歌曲信息读取 API

### 1.1 功能简介

一句话：**在你自己的程序里，实时拿到网易云当前播放的歌曲信息（歌手/专辑/封面）、进度和歌词。**

- 当前歌曲：歌名、歌手（作者）、专辑、封面、时长、`song_id`
- 封面：提供封面图 URL，并自动缓存为本地图片；HTTP API 可直接取图
- 实时播放进度：毫秒级，带插值
- 歌词：当前行、上一句、下一句、中文翻译
- 三种调用方式：Python 库、命令行、本地 HTTP API（JSON + SSE）
- 全程只读，不修改客户端，不需要登录

### 1.2 实现方式

1. **播放进度**：读取网易云进程内存里的“播放钟”。先扫描 int32 毫秒值，
   找不到再回退 double 秒值；用多个同步副本互相校验。地址失效会自动重新发现。
   不依赖硬编码偏移，换电脑、换版本都能自动适配。
2. **歌曲识别**：读取客户端窗口标题“歌名 - 歌手”，再和本地播放队列匹配
   `song_id`；匹配不到时用搜索接口兜底。拿到 `song_id` 后再请求 song detail
   接口，补全歌手列表、专辑名和封面地址。
3. **歌词与封面**：请求网易云歌词接口，取原文 LRC 和翻译 tlyric，并缓存到本地；
   再根据 song detail 返回的封面地址自动下载封面并缓存（失败不影响歌词）。
4. **HTTP API**：用 Python 标准库实现，不需要额外的 Web 框架；
   状态变化通过 SSE 推送。
5. 所有操作**只读目标进程内存**，不注入、不修改任何东西。

### 1.3 安装教程

从 GitHub 拉取源码并安装：

```bash
git clone git@github.com:maker114/netease-lyric-api.git
cd netease-lyric-api
pip install .
```

也可以先构建 wheel，再拷到其他电脑安装：

```bash
python -m pip wheel . --no-deps --no-build-isolation -w dist
pip install dist\netease_lyric_api-1.1.0-py3-none-any.whl
```

安装后会得到两个命令：

- `netease-lyric`：接口 / 命令行
- `netease-lyric-overlay`：桌面悬浮歌词窗（可选 GUI）

不想安装也可以直接在源码目录用：

```bash
python -m netease_lyric status
```

装好后建议先跑一次环境自检：

```bash
netease-lyric doctor
```

离线跑一遍测试（在源码目录执行，不需要网易云客户端/网络）：

```bash
python -m unittest discover -s tests
```

### 1.4 调用方式

#### 方式一：Python API

```python
from netease_lyric import NeteaseLyric

with NeteaseLyric() as api:                 # 构造即开始后台同步
    if not api.wait_for_ready(timeout=10):
        print("没有检测到正在播放的歌曲")
        raise SystemExit

    track = api.now_playing()               # 当前歌曲
    print(track.title)                      # "歌名 - 歌手"
    print(track.artists)                    # 歌手列表 ["歌手1", "歌手2"]
    print(track.album)                      # 专辑名
    print(track.cover_url)                  # 封面图地址

    cover = api.ensure_cover()              # 懒下载封面, 返回本地路径(失败为 None)
    if cover:
        print(cover)

    print(api.position_ms())                # 实时进度（毫秒）
    print(api.duration_ms())                # 歌曲时长（毫秒）

    line = api.current_line()               # 当前歌词行
    if line:
        print(line.text, "|", line.translation)
```

常用方法：

| 方法 | 作用 |
|---|---|
| `wait_for_ready(timeout=10)` | 等待首次就绪 |
| `now_playing()` | 当前歌曲 `Track`，未识别返回 `None` |
| `cover_path()` | 已缓存的封面本地路径，未缓存返回 `None` |
| `ensure_cover()` | 懒下载当前歌曲封面，返回本地路径（失败 `None`） |
| `position_ms()` | 实时进度（毫秒） |
| `duration_ms()` | 歌曲时长（毫秒） |
| `is_paused()` | 是否暂停 |
| `current_line()` | 当前歌词行 `LyricLine` |
| `lyrics()` | 完整歌词列表 |
| `state()` | 完整状态快照 `dict` |
| `add_listener(fn)` / `remove_listener(fn)` | 订阅 / 取消事件 |
| `rediscover()` | 强制重新定位播放进度 |

`now_playing()` 返回的 `Track`（也是 `state()["track"]`）包含歌曲详情：

| 字段 | 说明 |
|---|---|
| `name` / `artist` / `title` | 歌名 / 歌手（窗口标题解析） / `"歌名 - 歌手"` |
| `artists` | 网易云标准歌手列表，如 `["Clean Bandit", "Jess Glynne"]` |
| `album` / `album_id` | 专辑名 / 专辑 id |
| `cover_url` | 专辑封面图地址（https） |
| `cover_path` | 本地已缓存的封面文件路径，未缓存为 `""` |
| `song_id` | 网易云歌曲 id |
| `duration_ms` / `source` | 时长（毫秒） / 匹配来源（`queue` / `search`） |

也可以直接使用模块级函数：

```python
from netease_lyric import get_song_detail, ensure_cover

detail = get_song_detail(28095742)   # 只查歌曲详情(带缓存)
cover = ensure_cover(28095742, detail["cover_url"])  # 下载/复用封面缓存
```

更多用法见 [examples/python_api.py](examples/python_api.py)。

#### 方式二：命令行

```bash
netease-lyric status          # 打印当前播放和歌词
netease-lyric json            # 输出完整状态 JSON
netease-lyric watch           # 持续打印歌词行
netease-lyric cover           # 打印当前歌曲封面路径
netease-lyric cover --out cover.jpg   # 保存封面到指定文件
netease-lyric serve           # 启动本地 HTTP API
netease-lyric doctor          # 环境自检
netease-lyric version         # 版本号
```

未安装时把 `netease-lyric` 换成 `python -m netease_lyric`。

`status` 会同时显示专辑名和封面缓存路径；需要机器可读输出时请用 `json`。

#### 方式三：本地 HTTP API

启动：

```bash
netease-lyric serve --port 23517
```

默认地址是 `http://127.0.0.1:23517`，可用端点：

| 端点 | 说明 |
|---|---|
| `GET /` | 内置实时歌词网页（含封面，可作 OBS 浏览器源） |
| `GET /api/state` | 完整状态快照（`track` 含歌手列表、专辑名、封面字段） |
| `GET /api/now-playing` | 当前歌曲 + 进度 |
| `GET /api/line` | 当前歌词行 + 上/下一句 |
| `GET /api/lyrics` | 当前歌曲完整歌词 |
| `GET /api/cover` | 当前歌曲封面图片（未缓存时懒下载，失败返回 404） |
| `GET /api/stream` | SSE 状态流 |
| `GET /api/health` | 健康检查 |
| `GET /api/docs` | 端点说明 |

示例：

```bash
curl http://127.0.0.1:23517/api/state
curl http://127.0.0.1:23517/api/line
curl -o cover.jpg http://127.0.0.1:23517/api/cover   # 保存当前歌曲封面
curl -N http://127.0.0.1:23517/api/stream      # SSE
```

所有响应都带 CORS 头，网页或其他程序可以直接调用。
HTTP 示例见 [examples/http_client.py](examples/http_client.py)。

### 1.5 环境需求

- **Windows 10/11**（需要读取网易云 PC 客户端的内存，其他系统不支持）
- 已安装并正在运行**网易云音乐 PC 客户端**
- **Python 3.8+**
- **numpy >= 1.20**
- 能访问 `music.163.com`（歌词 / 歌曲详情）和 `*.music.126.net`（封面图）
- 本程序和网易云客户端使用**相同权限**运行（都普通权限，或都管理员权限）
- 只有 GUI 需要 `tkinter`；显示封面还需要可选的 `Pillow`，纯 API 调用都不需要

### 1.6 注意事项

- 仅支持 Windows，Linux / macOS 无法读取网易云内存。
- 首次启动或切歌后，需要 3~5 秒定位播放钟；这期间进度可能暂时为空，之后会自动恢复。
- 只读不修改；但网易云大版本更新有可能改变播放钟结构。遇到失效先运行
  `rediscover()` 或 `netease-lyric doctor`，仍不行则需要更新适配。
- 纯音乐、无版权歌曲会显示“暂无歌词”，这是正常现象，不是故障。
- 封面图首次获取需要访问网易云图片 CDN；失败时 `cover_path` 为空、`/api/cover` 返回 404，
  不影响歌词和播放进度。
- 缓存目录：`%LOCALAPPDATA%\NeteaseLyricApi\cache`（封面在 `covers\`，
  歌曲详情在 `detailcache.json`）。
- 翻译来自网易云自己的 tlyric 数据，个别歌曲的时间轴本身可能有偏差。
- 本项目和网易云官方无关，仅供个人学习、研究使用，请遵守网易云服务条款。

---

## Part 2. 歌词显示 GUI

### 2.1 功能简介

一个无边框、半透明、置顶的桌面悬浮歌词窗，用来在桌面上显示正在播放的歌词。

- 显示上一句 / 当前句（大字）/ 中文翻译 / 下一句 / 进度条
- 显示专辑封面（需 Pillow）、专辑名与歌手
- 鼠标拖动移动位置，位置会自动记住
- 右键菜单：重新定位播放进度 / 退出；按 `Esc` 退出
- 切歌自动换歌词，暂停显示 ⏸，纯音乐显示“纯音乐 / 暂无歌词”
- 数据来自 Part 1 的歌词读取 API，GUI 本身不读内存

![GUI 预览](docs/preview.png)

带专辑封面 / 专辑名 / 歌手的效果：

![封面、专辑与歌手](docs/demo-overlay.png)

### 2.2 安装方式

GUI 和 Part 1 在**同一个安装包**里，先完成 Part 1 的安装即可：

```bash
pip install .
```

GUI 需要 `tkinter`。Windows 官方 Python 默认自带，可用
`netease-lyric doctor` 检查；如果没有，重装 Python 时勾选 tcl/tk 即可。

显示专辑封面还需要可选的 `Pillow`（不影响歌词、专辑名和歌手文字；
未安装时自动退化为纯文字显示）：

```bash
pip install Pillow
# 从源码目录安装 overlay 可选依赖:
pip install ".[overlay]"
# 已安装的包:
pip install "netease-lyric-api[overlay]"
```

安装后直接启动：

```bash
netease-lyric overlay
# 或
netease-lyric-overlay
```

未安装时：

```bash
python -m netease_lyric overlay
```

源码目录里也可以直接双击 `run_overlay.bat` 启动。

### 2.3 使用方式

先启动网易云音乐并播放歌曲，然后运行 `netease-lyric overlay`。
默认样式：

```bash
netease-lyric overlay
```

常用参数：

```bash
netease-lyric overlay --width 1400 --font-size 26 --opacity 0.9 --bottom 60
netease-lyric overlay --reset-pos      # 重置窗口位置
netease-lyric overlay --debug          # 输出调试日志
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--width` | `1300` | 窗口宽度（像素） |
| `--font-size` | `24` | 当前歌词字号 |
| `--opacity` | `0.88` | 窗口不透明度（0~1） |
| `--bottom` | `48` | 距离屏幕底部的高度（像素） |
| `--offset` | `60` | 歌词时间轴偏移（毫秒） |
| `--reset-pos` | - | 忽略上次保存的位置 |
| `--debug` | - | 输出调试日志 |

操作说明：

- **拖动**：按住窗口任意位置拖动
- **右键**：菜单里可以重新定位播放进度或退出
- **Esc**：退出
- 窗口位置保存在 `%LOCALAPPDATA%\NeteaseLyricApi\window.json`
- 运行日志在 `%LOCALAPPDATA%\NeteaseLyricApi\overlay.log`

如果刚开始没有歌词，先确认网易云正在播放，并等待 3~5 秒让 API 定位播放进度。

---

## 目录结构

```
netease-lyric-api/
├── netease_lyric/          # 核心包（import netease_lyric）
│   ├── player.py           # 核心 API：NeteaseLyric
│   ├── lyrics.py           # 歌曲详情 / 歌词匹配 / 获取 / 缓存
│   ├── clock.py            # 播放进度读取
│   ├── cli.py              # 命令行
│   ├── server.py           # 可选：本地 HTTP API
│   ├── overlay.py          # 可选：桌面悬浮歌词窗（含封面/专辑/歌手）
│   └── _version.py
├── examples/               # Python / HTTP 调用示例
├── tests/                  # 标准库 unittest 测试（不需要网易云/网络）
├── docs/preview.png        # GUI 预览图
├── docs/demo-overlay.png   # 带封面/专辑/歌手的悬浮窗截图
├── pyproject.toml
├── requirements.txt
├── run_api.bat
├── run_overlay.bat
├── build_wheel.bat
└── README.md
```

## License

MIT

## 更新日志

### v1.1.0（2026-10-31）

**新增与改进**

- 歌曲详情：通过网易云 song detail 接口获取 `artists`（歌手/作者列表）、`album`（专辑名）、
  `album_id`、`cover_url`（封面地址）和 `duration`，并写入 `Track`、`state()`、
  `/api/state`、`/api/now-playing` 与 SSE 的 `track`。
- 封面：自动下载并缓存到
  `%LOCALAPPDATA%\NeteaseLyricApi\cache\covers\{song_id}.jpg`；
  新增 `Track.cover_url` / `Track.cover_path`、`NeteaseLyric.cover_path()` /
  `ensure_cover()`、命令行 `netease-lyric cover [--out cover.jpg]`、
  HTTP `GET /api/cover`，内置网页也会显示封面。
- 悬浮窗：显示专辑封面（Pillow 可选）、专辑名和歌手；没有 Pillow 时自动退化为纯文字。
- 缓存：新增 `detailcache.json`；播放队列解析补充专辑/封面/歌手；搜索索引扩展字段。
- 测试：新增 `tests/`（标准库 `unittest`，离线可跑，60 个用例）。

**兼容性（相对 v1.0.0，非破坏性 MINOR）**

- Python API、HTTP 既有端点、CLI 既有命令、缓存格式、悬浮窗 `window.json` 均向后兼容，
  旧代码无需修改即可运行在新版本上。
- 旧缓存可直接复用；新版本写的缓存也能被 v1.0.0 读取，可安全降级。
- 基础依赖不变（仍只有 `numpy`）；`Pillow` 是可选依赖 `overlay` extra。
- 版本号由 `1.0.0` 升到 `1.1.0`，`api_version` 同步变化。

**需要注意（非破坏，但可能影响严格客户端）**

- **JSON 新增字段**：`track` 增加 `artists / album / album_id / cover_url / cover_path`，
  `/api/docs` 增加 `GET /api/cover`。使用严格 JSON Schema（禁止额外字段）或
  全量字典等值断言的调用方需要放行新增字段。
- **CLI 文本输出变化**：`netease-lyric status` 在标题后新增“专辑”行、在信息行后新增
  “封面”行。按行号解析文本的脚本请改用 `netease-lyric json` 或直接读 JSON 字段。
- **首次播放多 2 个网络请求**：song detail + 封面图（缓存命中后为 0 次）。网络不可达时，
  `lyric_loading` 最坏会因 detail（8 秒）+ 封面（10 秒）超时多等一会儿；
  歌词和旧字段不受影响，封面失败时 `cover_path=""`、`/api/cover` 返回 404。
- **`get_song_lyrics()` 增加封面下载副作用**：如果测试只 mock 了歌词接口
  `lyrics._api`，可能仍会触发封面下载；请同时 mock `urllib.request.urlopen`，
  或先准备好封面缓存。
- **`/api/cover` 是 v1.1.0 新增端点**：旧服务端会返回 404，新客户端应回退到
  `track.cover_url` 自行下载；旧客户端连接新服务端则无需修改。
- 本版获取的“作者”指**歌手（artists）**；作词/作曲等 credits 尚未提供。

### v1.0.0

- 首个公开版本：读取网易云播放状态、进度和歌词，提供 Python API、命令行、
  本地 HTTP API（JSON + SSE）与可选桌面悬浮歌词窗。
