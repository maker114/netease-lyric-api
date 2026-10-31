# netease-lyric-api

网易云音乐 PC 端的**实时歌词读取 API**，附带一个可选的**桌面悬浮歌词窗**。

只读 `cloudmusic.exe` 进程内存，不修改网易云客户端、不需要登录、不注入 DLL。

> 平台：**Windows 10/11**　Python：**3.8+**　依赖：**numpy**

---

## Part 1. 歌词读取 API

### 1.1 功能简介

一句话：**在你自己的程序里，实时拿到网易云当前播放的歌曲、进度和歌词。**

- 当前歌曲：歌名、歌手、专辑、时长、`song_id`
- 实时播放进度：毫秒级，带插值
- 歌词：当前行、上一句、下一句、中文翻译
- 三种调用方式：Python 库、命令行、本地 HTTP API（JSON + SSE）
- 全程只读，不修改客户端，不需要登录

### 1.2 实现方式

1. **播放进度**：读取网易云进程内存里的“播放钟”。先扫描 int32 毫秒值，
   找不到再回退 double 秒值；用多个同步副本互相校验。地址失效会自动重新发现。
   不依赖硬编码偏移，换电脑、换版本都能自动适配。
2. **歌曲识别**：读取客户端窗口标题“歌名 - 歌手”，再和本地播放队列匹配
   `song_id`；匹配不到时用搜索接口兜底。
3. **歌词**：请求网易云歌词接口，取原文 LRC 和翻译 tlyric，并缓存到本地。
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
pip install dist\netease_lyric_api-1.0.0-py3-none-any.whl
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
| `position_ms()` | 实时进度（毫秒） |
| `duration_ms()` | 歌曲时长（毫秒） |
| `is_paused()` | 是否暂停 |
| `current_line()` | 当前歌词行 `LyricLine` |
| `lyrics()` | 完整歌词列表 |
| `state()` | 完整状态快照 `dict` |
| `add_listener(fn)` / `remove_listener(fn)` | 订阅 / 取消事件 |
| `rediscover()` | 强制重新定位播放进度 |

更多用法见 [examples/python_api.py](examples/python_api.py)。

#### 方式二：命令行

```bash
netease-lyric status          # 打印当前播放和歌词
netease-lyric json            # 输出完整状态 JSON
netease-lyric watch           # 持续打印歌词行
netease-lyric serve           # 启动本地 HTTP API
netease-lyric doctor          # 环境自检
netease-lyric version         # 版本号
```

未安装时把 `netease-lyric` 换成 `python -m netease_lyric`。

#### 方式三：本地 HTTP API

启动：

```bash
netease-lyric serve --port 23517
```

默认地址是 `http://127.0.0.1:23517`，可用端点：

| 端点 | 说明 |
|---|---|
| `GET /` | 内置实时歌词网页（可作 OBS 浏览器源） |
| `GET /api/state` | 完整状态快照 |
| `GET /api/now-playing` | 当前歌曲 + 进度 |
| `GET /api/line` | 当前歌词行 + 上/下一句 |
| `GET /api/lyrics` | 当前歌曲完整歌词 |
| `GET /api/stream` | SSE 状态流 |
| `GET /api/health` | 健康检查 |
| `GET /api/docs` | 端点说明 |

示例：

```bash
curl http://127.0.0.1:23517/api/state
curl http://127.0.0.1:23517/api/line
curl -N http://127.0.0.1:23517/api/stream      # SSE
```

所有响应都带 CORS 头，网页或其他程序可以直接调用。
HTTP 示例见 [examples/http_client.py](examples/http_client.py)。

### 1.5 环境需求

- **Windows 10/11**（需要读取网易云 PC 客户端的内存，其他系统不支持）
- 已安装并正在运行**网易云音乐 PC 客户端**
- **Python 3.8+**
- **numpy >= 1.20**
- 能访问 `music.163.com`（用于获取歌词）
- 本程序和网易云客户端使用**相同权限**运行（都普通权限，或都管理员权限）
- 只有 GUI 需要 `tkinter`，纯 API 调用不需要

### 1.6 注意事项

- 仅支持 Windows，Linux / macOS 无法读取网易云内存。
- 首次启动或切歌后，需要 3~5 秒定位播放钟；这期间进度可能暂时为空，之后会自动恢复。
- 只读不修改；但网易云大版本更新有可能改变播放钟结构。遇到失效先运行
  `rediscover()` 或 `netease-lyric doctor`，仍不行则需要更新适配。
- 纯音乐、无版权歌曲会显示“暂无歌词”，这是正常现象，不是故障。
- 翻译来自网易云自己的 tlyric 数据，个别歌曲的时间轴本身可能有偏差。
- 本项目和网易云官方无关，仅供个人学习、研究使用，请遵守网易云服务条款。

---

## Part 2. 歌词显示 GUI

### 2.1 功能简介

一个无边框、半透明、置顶的桌面悬浮歌词窗，用来在桌面上显示正在播放的歌词。

- 显示上一句 / 当前句（大字）/ 中文翻译 / 下一句 / 进度条
- 鼠标拖动移动位置，位置会自动记住
- 右键菜单：重新定位播放进度 / 退出；按 `Esc` 退出
- 切歌自动换歌词，暂停显示 ⏸，纯音乐显示“纯音乐 / 暂无歌词”
- 数据来自 Part 1 的歌词读取 API，GUI 本身不读内存

![GUI 预览](docs/preview.png)

### 2.2 安装方式

GUI 和 Part 1 在**同一个安装包**里，先完成 Part 1 的安装即可：

```bash
pip install .
```

GUI 需要 `tkinter`。Windows 官方 Python 默认自带，可用
`netease-lyric doctor` 检查；如果没有，重装 Python 时勾选 tcl/tk 即可。

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
│   ├── lyrics.py           # 歌词匹配 / 获取 / 缓存
│   ├── clock.py            # 播放进度读取
│   ├── cli.py              # 命令行
│   ├── server.py           # 可选：本地 HTTP API
│   ├── overlay.py          # 可选：桌面悬浮歌词窗
│   └── _version.py
├── examples/               # Python / HTTP 调用示例
├── docs/preview.png        # GUI 预览图
├── pyproject.toml
├── requirements.txt
├── run_api.bat
├── run_overlay.bat
├── build_wheel.bat
└── README.md
```

## License

MIT
