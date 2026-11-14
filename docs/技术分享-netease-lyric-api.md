# netease-lyric-api 技术分享

> **一句话**：在你的程序里，实时拿到网易云音乐 PC 端"正在播什么、播到哪、在唱哪句"——只读进程内存，不注入、不改客户端、不需要登录。
>
> **版本锚点**：`netease-lyric-api v1.1.0`（提交 `fae807e`，2026-10-31）｜**撰写**：2026-11-01｜**平台**：Windows 10/11 + Python 3.8+
>
> ⚠️ 本文只描述 v1.1.0；第 4 节说明哪些内容会失效以及如何自查。

---

## 目录

- [0. 五分钟速览](#0-五分钟速览)
- [1. 这个项目能干什么](#1-这个项目能干什么)
  - [1.1 它解决的问题](#11-它解决的问题)
  - [1.2 能拿到哪些数据](#12-能拿到哪些数据)
  - [1.3 四种使用形态](#13-四种使用形态)
  - [1.4 场景与边界](#14-场景与边界)
- [2. 它是怎么做到的](#2-它是怎么做到的)
  - [2.1 全局架构](#21-全局架构)
  - [2.2 播放进度：从内存里"认"出播放钟](#22-播放进度从内存里认出播放钟)
  - [2.3 从歌名到 song_id](#23-从歌名到-song_id)
  - [2.4 歌词时间轴与当前行](#24-歌词时间轴与当前行)
  - [2.5 并发模型](#25-并发模型)
  - [2.6 网络与缓存](#26-网络与缓存)
  - [2.7 输出层](#27-输出层)
  - [2.8 关键取舍](#28-关键取舍)
  - [2.9 已知局限](#29-已知局限)
- [3. 安装与使用](#3-安装与使用)
  - [3.1 环境要求](#31-环境要求)
  - [3.2 安装](#32-安装)
  - [3.3 装完先自检](#33-装完先自检)
  - [3.4 用法一：Python API](#34-用法一python-api)
  - [3.5 用法二：命令行](#35-用法二命令行)
  - [3.6 用法三：本地 HTTP API](#36-用法三本地-http-api)
  - [3.7 用法四：桌面悬浮歌词窗](#37-用法四桌面悬浮歌词窗)
  - [3.8 排障速查](#38-排障速查)
- [4. 文档时效性](#4-文档时效性)
- [附录 A：源码索引](#附录-a源码索引)
- [附录 B：关键常量速查](#附录-b关键常量速查)

---

## 0. 五分钟速览

| 项目 | 内容 |
|---|---|
| 名称 | `netease-lyric-api`（包名 `netease_lyric`） |
| 版本 | v1.1.0（2026-10-31） |
| 定位 | 网易云音乐 PC 端实时播放状态 / 歌曲信息 / 歌词读取库 + 可选悬浮歌词窗 |
| 平台 | 仅 Windows 10/11（需读 `cloudmusic.exe` 内存） |
| 依赖 | Python ≥ 3.8、`numpy ≥ 1.20`；`Pillow` 仅悬浮窗封面可选 |
| 形态 | Python 库 / 命令行 / 本地 HTTP API（JSON + SSE）/ 悬浮窗 |
| 边界 | **只读**内存，不注入、不改客户端、不登录 |
| 许可 | MIT，与网易云官方无关 |

---

## 1. 这个项目能干什么

### 1.1 它解决的问题

做桌面歌词、直播字幕、OBS 浮层时都会撞墙：**拿不到网易云此刻在放什么、播到第几秒。** 现有替代方案各有硬伤：

| 方案 | 问题 |
|---|---|
| 未公开的 Web API | 只能按 `song_id` 查，不知道"当前"是哪首 |
| 窗口标题 | 只有"歌名 - 歌手"，没有进度 |
| SMTC / 系统媒体控件 | 网易云 PC 端不提供可依赖的进度 |
| 注入 DLL / Hook | 侵入强、易被拦、有合规风险 |

本项目的路：**只读**扫描目标进程里的播放钟，用窗口标题和网易云自身接口补齐元数据，对外只暴露一个 `state()` 快照。

### 1.2 能拿到哪些数据

| 数据 | 内容 | 来源 |
|---|---|---|
| 歌曲 | 歌名、歌手、`title` | 窗口标题 |
| 详情 | `song_id`、`artists`、`album`、`album_id`、时长、`source` | 播放队列 / 搜索 / song detail |
| 封面 | `cover_url` + 本地缓存路径 | song detail → 图片 CDN |
| 进度 | `position_ms`、`duration_ms`、`progress`、`paused` | 内存播放钟（含插值） |
| 歌词 | 当前/上/下一句、完整歌词、逐行翻译 | LRC + tlyric |
| 状态 | `netease_running`、`clock_ready`、`lyric_loading`、`syncing`、`error` | 状态机 |
| 事件 | 切歌、歌词就绪、换行、定位成功/失败、客户端上下线 | 回调（SSE 仅同步状态快照） |

### 1.3 四种使用形态

四种形态共用同一个 `NeteaseLyric` 核心：Python 程序、命令行、浏览器/OBS（经本地 HTTP API）、悬浮窗 GUI 都只是它的消费者；核心向下只读 `cloudmusic.exe` 内存，并通过 HTTPS 访问网易云 Web 接口。

- **Python 库**：`NeteaseLyric` 对象 + 事件监听。
- **命令行**：`status / json / watch / cover / serve / doctor / version / overlay`。
- **HTTP API**：默认 `127.0.0.1:23517`，8 个 API 端点（含 SSE）+ 可直接做 OBS 源的网页。
- **悬浮窗**：无边框、半透明、置顶，显示歌词、翻译、进度条、封面、专辑与歌手。

### 1.4 场景与边界

典型场景：直播/录屏歌词浮层、桌面美化、自建播放看板、音乐数据采集、课堂演示。

明确边界：**仅 Windows**（非 Windows 访问核心对象直接抛 `RuntimeError`）；**只读**，不提供播放控制；需客户端正在运行；v1.1.0 的"作者"指歌手，不提供作词/作曲 credits；不登录，无私有数据。

---

## 2. 它是怎么做到的

### 2.1 全局架构

代码分四层，上层只依赖下层：

- **输出层**：`cli.py`（命令行）、`server.py`（HTTP + SSE）、`overlay.py`（tkinter 悬浮窗），只消费 `state()` 快照与事件。
- **编排层**：`player.py` 的 `NeteaseLyric`，含 0.1s 轮询线程、状态机与事件、歌词抓取线程、内存扫描线程；用 RLock 保护状态、用代际号防止串数据。
- **采集层**：`clock.py`，负责窗口/进程定位、只读内存扫描与轮询。
- **数据层**：`lyrics.py`，负责播放队列匹配、搜索、song detail、歌词解析、封面下载与四级本地缓存。

调用方向是：输出层 → 编排层 → 采集层 / 数据层；数据层内部的网络请求与本地缓存相互独立。

核心思想：**把"慢、易失效"的部分全部关进后台线程，对外只给可 JSON 序列化的快照。**

### 2.2 播放进度：从内存里"认"出播放钟

这是项目最硬的一块。播放进度不是固定偏移上的字段，换版本/换机器就会变，所以 **运行时现场识别，不硬编码偏移**。

**定位进程**（`clock.py:122-162`）：`FindWindowW("OrpheusBrowserHost")` 找主窗口；失配时 `EnumWindows` + `GetWindowThreadProcessId` + `QueryFullProcessImageNameW`，挑镜像名以 `cloudmusic.exe` 结尾、标题不是"桌面歌词/迷你播放器/网易云音乐"的窗口；再 `OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION)`，错误 5 给中文提示（要求同权限运行）。

**扫描流水线**：

按顺序分五步执行：

1. **定位进程**：`FindWindowW` / `EnumWindows` 找到 `cloudmusic.exe`，`OpenProcess` 拿到只读句柄。
2. **枚举区域**：`VirtualQueryEx` 遍历所有可读内存区域。
3. **粗筛**：8MB 分块读取，numpy 筛出 1000~3,600,000 毫秒区间内的 int32 值。
4. **时间复筛**：sleep 1.2s 后重读，校验"期望值 = 原值 + Δt×1000"（容差 300ms）；再 sleep 0.8s 用 250ms 复筛一次。
5. **聚类定址**：按 120ms 聚类，保留样本数 ≥2 的簇，取中位数并做时长上界校验，得到 `aliases`。

若 int32 连续尝试 3 次失败，则整体回退到 double 秒值扫描，再走一遍同样的五步（共最多 6 轮）。发现成功后，每 0.1s 只读这几个地址，不再扫描。

四个关键判据：**值域**（1s~60min）、**速率**（每秒精确 +1000）、**副本数**（≥2 个地址一致）、**上界**（不超过已知时长 +8s；聚类后另有 +10s 校验）。只读 `ReadProcessMemory`，不注入、不写内存。

**稳态**（`clock.py:385-412`）：只读至多 10 个别名地址；有效值 ≥3 且极差 >400ms 时判定"副本打架"返回 `None`；否则取中位数。**冷启动贵、稳态几乎零成本。**

**失效恢复**：

| 场景 | 处理 |
|---|---|
| 启动/切歌后无地址 | 独立线程扫描；仍无则约每 3s 重试（扫描本身更久时顺延） |
| 切歌后地址可能失效 | 2s 后"归零校验"：读到 `None` 或 `>25s` 即重扫 |
| 程序启动时歌已播一半 | 首次识别不做归零校验，避免误判 |
| 暂时读不到 | 切歌后 15 秒内用墙钟估算，之后冻结等待 |

> README 说"首次启动/切歌后需 3~5 秒定位"，主要花在全内存扫描 + 两次时间复筛上；跑在独立线程，不卡主循环。

### 2.3 从歌名到 song_id

内存能读到进度，但读不到歌曲 ID，于是两级匹配 + 失败降级：

链路按优先级排列，命中即停：

1. **本地播放队列**（`playingList`）：歌名精确 + 歌手包含匹配，最准；
2. **搜索接口**：队列未命中时按"歌名 歌手"搜索，结果写入 `songcache.json`；
3. **放弃歌曲元数据**：无 `song_id` 时不请求歌词，只保留窗口标题解析出的歌名/歌手；
   进度与标题不受影响。

命中 `song_id` 后，先取歌词（LRC + tlyric），再请求 song detail 补 `artists` / `album` / `cover_url`，最后下载封面。

优先本地播放队列，因为它是**客户端自己解析的数据**，与正在播放的版本完全一致；搜索只做兜底。`song detail` 结果与队列/搜索信息做字段级合并后写入 `detailcache.json`。

### 2.4 歌词时间轴与当前行

- **LRC 解析**：正则兼容 `[mm:ss]`、`[mm:ss.xx]`、`[mm:ss:xx]`；一行多时间戳展开；小数位补成毫秒；同一毫秒取首条。
- **清洗**：丢弃空行；丢弃开头 1.5s 内的 `作词:/作曲:/编曲:` 等元数据行；丢弃开头 10s 内与歌名/歌手相同的 credits 行。
- **翻译**：按时间戳**完全相等**配对，不做模糊匹配。
- **定位**：`bisect_right(times, pos + offset) - 1`，O(log n)；前奏返回 `-1`，末尾 clamp 到最后一行；`state()` 顺带给出上/下一句。
- **平滑**：播放中把进度按真实时间外推，**最多 0.5 秒**，避免调用方低频读取时进度跳变。
- **暂停**：不看播放状态，看原始进度是否 **1.5 秒**未变。

### 2.5 并发模型

三个线程：

- **轮询线程**（常驻，0.1s）：窗口 → 标题 → 进度 → 当前行；切歌时派生歌词线程，无地址时派生扫描线程。
- **歌词线程**（每首歌一个）：`song_id` → 歌词 → 详情 → 封面，回填 `Track`。
- **发现线程**（临时）：全内存扫描，写回 `aliases` 与位置。

三者的结果最终都汇入 `state()` 快照，供输出层读取。

三个机制：**RLock + 快照**（对外只返回新 dict）；**代际号 `_song_gen`**（切歌即 +1，异步结果回来发现代际不符就丢弃，杜绝串歌）；**发现串行化**（同一时刻只一个扫描线程，中途强制请求排到结束后补跑）。监听器逐个 `try/except`，单个回调异常不影响主循环。

### 2.6 网络与缓存

网络层只用标准库 `urllib.request`（超时 8/10/12s，带 `Referer` 与 Chrome UA），不引入 `requests`、不引入 Web 框架。

| 缓存 | 文件 | 键 / 内容 | 失效 |
|---|---|---|---|
| 搜索索引 | `songcache.json` | `歌名\|歌手`（小写） | 无 TTL |
| 歌曲详情 | `detailcache.json` | `song_id` → 专辑/歌手/封面/时长 | 缺 album 且缺 cover_url 时重取 |
| 歌词 | `{song_id}.json` | `[(ms, 原文, 翻译)]` | 无 TTL |
| 封面 | `covers/{song_id}.jpg` | 500×500 | 非 0 字节即复用 |

缓存目录 `%LOCALAPPDATA%\NeteaseLyricApi\cache`（可用 `NETEASE_LYRIC_CACHE_DIR` 覆盖）。详情与封面用 `.tmp` + `os.replace` 原子写；封面串行下载、0 字节视为损坏；封面失败返回空串，**不影响歌词与进度**，`/api/cover` 返回 404。

### 2.7 输出层

- **CLI**：人类可读的 `status`、机器可读的 `json`、逐行打印的 `watch`。
- **HTTP**：`ThreadingHTTPServer`（HTTP/1.1），全响应带 CORS `*`；端点 `/`、`/api/state`、`/api/now-playing`、`/api/line`、`/api/lyrics`、`/api/cover`、`/api/stream`、`/api/health`、`/api/docs`。SSE 以 `(song_id, 行号, paused, 进度/500ms, clock_ready, lyric_ready)` 为签名，变化才推，15s 一次心跳。
- **悬浮窗**：无边框 + 置顶 + `-alpha`，`SetWindowPos` 定位；三级 DPI 兼容；120×120 封面（无 Pillow 自动退化纯文字）；拖动、右键菜单、`Esc` 退出；位置存 `window.json`；100ms 刷新，文本变化时才改文本（进度条每 100ms 更新）。

### 2.8 关键取舍

| 决策 | 放弃的方案 | 理由 |
|---|---|---|
| 运行时扫描播放钟 | 硬编码偏移 | 换机换版本即废；扫描一次稳态近零成本 |
| 只读 `ReadProcessMemory` | 注入 / Hook | 无侵入、不破坏客户端、降低误报与合规风险 |
| 多副本 + 中位数 + 极差校验 | 只认一个地址 | 可交叉验证、剔除滞后副本与死地址 |
| 窗口标题作歌曲信源 | 解析内存结构 | 结构稳定、无版本耦合；代价是需补 `song_id` |
| 标准库 urllib + http.server | requests / Flask | 唯一依赖只有 numpy，装完即用 |
| numpy | 纯 Python 扫描 | 数百 MB~数 GB 的可读内存必须向量化 |
| 失败降级 | 强依赖网络 | 断网只丢歌词与专辑/歌手/封面等元数据，进度、标题解析与切歌不受影响 |

### 2.9 已知局限

1. 无显式 32/64 位处理，默认 Python 与客户端同为 64 位。
2. `PlaybackClock.h/aliases` 被两个线程共享且未加锁，属设计上的竞态点。
3. 持续读失败不会自动重扫，需切歌触发校验或手动 `rediscover()`。
4. 网络无重试、缓存无 TTL，官方修正歌词后本地不更新。
5. 歌曲匹配是字符串启发式，同名不同版本可能错配。
6. 封面在歌词线程同步下载：网络差时封面自身最多等 10 秒，叠加 song detail 的 8 秒，最坏会把歌词就绪拖慢约 18 秒。

---

## 3. 安装与使用

### 3.1 环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11 |
| 客户端 | 已安装并**正在运行**网易云音乐 PC 客户端 |
| Python | 3.8+（本文实测 3.11.9） |
| 依赖 | `numpy ≥ 1.20`；可选 `Pillow`、`tkinter`（GUI） |
| 网络 | 可访问 `music.163.com` 与 `*.music.126.net` |
| 权限 | 与网易云客户端**相同权限**运行 |

### 3.2 安装

```bash
git clone git@github.com:maker114/netease-lyric-api.git
cd netease-lyric-api
pip install .
```

> SSH 不便可用 `git clone https://github.com/maker114/netease-lyric-api.git`。

离线分发可先构建 wheel：

```bash
python -m pip wheel . --no-deps --no-build-isolation -w dist
pip install dist\netease_lyric_api-1.1.0-py3-none-any.whl
```

构建后 wheel 位于 `dist/`（`dist/` 不入 git）。

安装后得到 `netease-lyric` 与 `netease-lyric-overlay` 两个命令。不安装也可在源码目录用 `python -m netease_lyric ...`。悬浮窗封面需要 Pillow：

```bash
pip install Pillow
# 或从源码目录：pip install ".[overlay]"
# 已安装的包（PyPI 发布后可用）：pip install "netease-lyric-api[overlay]"
```

### 3.3 装完先自检

```bash
netease-lyric doctor
python -m unittest discover -s tests   # 离线测试，不需要客户端/网络
```

`doctor` 逐项检查系统、Python、numpy、tkinter、Pillow、窗口、内存读取权限、播放钟、歌词接口，并给中文修复建议（全通过退出码 0，有失败为 2）。实测：**60 tests, OK（约 6 秒，Python 3.11.9）**。

### 3.4 用法一：Python API

```python
from netease_lyric import NeteaseLyric

with NeteaseLyric() as api:                 # 构造即开始后台同步
    if not api.wait_for_ready(timeout=10):
        raise SystemExit("没有检测到正在播放的歌曲")

    track = api.now_playing()               # Track
    print(track.title, track.artists, track.album, track.cover_url)

    print(api.position_ms(), api.duration_ms(), api.is_paused())
    cover = api.ensure_cover()              # 懒下载封面，失败返回 None
    line = api.current_line()               # LyricLine
    if line:
        print(line.text, "|", line.translation)
```

| 方法 | 作用 |
|---|---|
| `wait_for_ready(t)` / `wait_until_playing(t)` | 等就绪 / 等"有进度且未暂停" |
| `now_playing()` / `current_line()` / `lyrics()` | 当前歌曲 / 当前行 / 全部歌词 |
| `position_ms()` / `duration_ms()` / `is_paused()` | 进度 / 时长 / 暂停 |
| `cover_path()` / `ensure_cover()` | 已缓存路径 / 懒下载 |
| `state()` | 完整状态快照 dict |
| `add_listener(fn)` / `remove_listener(fn)` | 订阅 / 取消事件 |
| `rediscover()` | 强制重新定位播放进度 |

`Track` 字段：`name / artist / title / artists / album / album_id / cover_url / cover_path / song_id / duration_ms / source`。事件：`song_change`、`lyric_ready`、`lyric_none`、`line_change`、`clock_discovered`、`clock_discovery_failed`、`netease_available`、`netease_unavailable`。

模块级函数（不需播放状态）：

```python
from netease_lyric import get_song_detail, ensure_cover
detail = get_song_detail(28095742)
cover = ensure_cover(28095742, detail["cover_url"])
```

示例见 [`examples/python_api.py`](../examples/python_api.py)。

### 3.5 用法二：命令行

```bash
netease-lyric status                   # 当前播放 + 歌词
netease-lyric json                     # 完整状态 JSON
netease-lyric watch                    # 持续打印歌词行
netease-lyric cover --out cover.jpg    # 打印/保存封面
netease-lyric serve                    # 启动 HTTP API
netease-lyric doctor                   # 环境自检
netease-lyric version                  # 版本号
```

未安装时用 `python -m netease_lyric` 替代。

### 3.6 用法三：本地 HTTP API

```bash
netease-lyric serve --port 23517       # 默认 http://127.0.0.1:23517

curl http://127.0.0.1:23517/api/state
curl -o cover.jpg http://127.0.0.1:23517/api/cover
curl -N http://127.0.0.1:23517/api/stream
```

浏览器打开 `http://127.0.0.1:23517/` 即内置实时歌词页，OBS 添加「浏览器源」指向它即可直播歌词。默认仅本机监听，需局域网用 `--host 0.0.0.0` 显式开放；接口无鉴权。示例见 [`examples/http_client.py`](../examples/http_client.py)。

### 3.7 用法四：桌面悬浮歌词窗

```bash
netease-lyric overlay
netease-lyric-overlay
python -m netease_lyric overlay        # 未安装时
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--width` | 1300 | 窗口宽度 |
| `--font-size` | 24 | 当前歌词字号 |
| `--opacity` | 0.88 | 不透明度 |
| `--bottom` | 48 | 距屏幕底部像素 |
| `--offset` | 60 | 歌词时间轴偏移（ms） |
| `--reset-pos` | - | 忽略上次位置 |
| `--debug` | - | 输出调试日志 |

拖动移动；右键可重新定位播放进度或退出；`Esc` 退出。位置存 `%LOCALAPPDATA%\NeteaseLyricApi\window.json`，日志在 `overlay.log`。

### 3.8 排障速查

| 现象 | 处理 |
|---|---|
| 未检测到网易云 | 确认 `cloudmusic.exe` 运行中；跑 `doctor` |
| 读取被拒绝（错误 5） | 与网易云同权限运行（都管理员或都普通） |
| 有歌名无进度 | 等 3~5 秒；仍不行 `rediscover()` 或重启 |
| 有进度无歌词 | 纯音乐/无版权属正常；否则检查 `music.163.com` 连通性 |
| 封面不显示 | 首次需访问图片 CDN；`/api/cover` 404 属正常降级 |
| 悬浮窗无封面 | `pip install Pillow` 后重启 |
| 歌词偏早/偏晚 | 用 `--offset` 微调毫秒（悬浮窗默认 60，其余默认 0） |

---

## 4. 文档时效性

### 4.1 版本锚点

| 项 | 值 |
|---|---|
| 对应版本 | **v1.1.0** |
| 发布提交 | `fae807e`（"发布 v1.1.0：新增歌手/专辑/封面获取与展示"） |
| 版本日期 | 2026-10-31 |
| 撰写日期 | 2026-11-01 |
| 事实来源 | 该提交的源码、README、`pyproject.toml`、`tests/` |
| 已实测 | 60 tests, OK（Python 3.11.9） |

本文档只覆盖 v1.1.0，不含 v1.0.0 之前或之后的变化。例如 v1.0.0 的 `Track` 已有 `album` 字段但恒为空串，没有歌手列表/封面能力。

### 4.2 易过期清单

1. **网易云内存结构**（最高风险）：播放钟存储方式、副本数量、窗口类名可能随客户端大版本变。运行时扫描不依赖固定偏移，但仍假设"存在随播放线性增长的数值 + 多个一致副本"。
2. **网易云 Web 接口**（高风险）：`/api/song/detail`、`/api/song/lyric`、`/api/search/get` 均非公开，可能改路径/加风控；失效只影响歌词与专辑/歌手/封面等元数据（队列未命中时也会影响 `song_id` 解析），不影响进度、标题解析与切歌。
3. **本地播放队列路径**（中风险）：`%LOCALAPPDATA%\Netease\CloudMusic\webdata\file\playingList` 及其结构。
4. **缓存位置与格式**（低风险）：`%LOCALAPPDATA%\NeteaseLyricApi\cache`。
5. **Python / Windows 版本**：需 Python 3.8+、Windows 10/11。

### 4.3 自查方法

```bash
netease-lyric version            # 期望 1.1.0
git log --oneline -1 v1.1.0     # 期望 fae807e
netease-lyric doctor
python -m unittest discover -s tests
```

判断规则：**版本仍是 1.1.0 且 `v1.1.0` tag 指向 `fae807e`，本文的字段/命令/端点/路径都成立；版本一变，请以新版 README 与源码为准。**

### 4.4 后续兼容提示

v1.1.0 相对 v1.0.0 是非破坏性 MINOR，但以下变化对严格客户端不友好，可作为判断未来版本影响的参照：

- JSON 新增 `track.artists/album_id/cover_url/cover_path`；`album` 字段自 v1.1.0 起有值。严格 Schema 需放行新增字段。
- CLI `status` 文本新增"专辑""封面"行，按行号解析的脚本应改用 `json`。
- 首次播放多 2 个请求（详情 + 封面），网络不可达时 `lyric_loading` 最坏多等约 18 秒。
- `/api/cover` 是新增端点，旧服务端 404，新客户端应回退到 `cover_url`。
- `get_song_lyrics()` 带封面下载副作用，测试需同时 mock 图片请求。

---

## 附录 A：源码索引

| 主题 | 位置 |
|---|---|
| 公开 API 与懒加载 | `netease_lyric/__init__.py` |
| 窗口/进程定位 | `clock.py:122-162` |
| 内存区域枚举 | `clock.py:170-186` |
| 扫描 / 复筛 / 聚类 | `clock.py:250-346` |
| 发现主流程（i32→f64） | `clock.py:348-382` |
| 稳态读取 | `clock.py:385-412` |
| 状态机主循环 | `player.py:386-618` |
| 切歌处理 | `player.py:455-482` |
| 歌词抓取与代际号 | `player.py:485-530` |
| 进度 / 暂停 / 插值 | `player.py:533-561` |
| 状态快照 | `player.py:276-318` |
| 播放队列解析 | `lyrics.py:113-188` |
| 详情与缓存 | `lyrics.py:191-307` |
| 封面下载 | `lyrics.py:310-349` |
| LRC 解析 | `lyrics.py:353-393` |
| 搜索兜底 | `lyrics.py:396-463` |
| HTTP 与 SSE | `server.py:173-272` |
| 悬浮窗 | `overlay.py:91-360` |
| 命令行与 doctor | `cli.py:139-321` |

## 附录 B：关键常量速查

| 常量 | 值 | 含义 |
|---|---|---|
| 扫描分块 | 8 MB | 内存读取粒度 |
| 候选值域 | 1000 ~ 3,600,000 | int32 毫秒候选（1s~60min） |
| 复筛容差 | 300ms → 250ms | i32；f64 为 0.03s → 0.02s |
| 复筛等待 | 1.2s + 0.8s | 两次采样间隔 |
| 聚类窗口 / 最少副本 | 120ms / 2 | 多副本一致判据 |
| 发现上限 | 3 次/模式 × 2 模式 | i32 失败回退 f64 |
| 一致性阈值 | 极差 > 400ms 判无效 | 副本打架时不猜 |
| 轮询间隔 | 0.10s（下限 0.02s） | 主状态机 |
| 暂停判定 | 1.5s 未变 | 无接口时的替代 |
| 发现重试 | 3.0s | 无地址时 |
| 切歌归零校验 | +2s，阈值 25s | 判断旧地址是否还有效 |
| 墙钟兜底 | 15s | 读不到时估算进度 |
| 进度插值上限 | 0.5s | 防外推跑飞 |
| 网络超时 | 8 / 12 / 10s | detail / lyric / cover |
| HTTP 端口 | 23517 | `serve` 默认 |
| 悬浮窗默认 | 1300×226 / 字号 24 | 样式 |

---

**免责声明**：本项目与网易云音乐官方无关，仅供个人学习、研究使用，请遵守网易云服务条款。HTTP API 默认无鉴权，`--host 0.0.0.0` 仅用于可信局域网，请勿直接暴露公网；歌词/封面版权归原权利方，缓存仅供个人使用。本文档仅对 `netease-lyric-api v1.1.0` 负责。
