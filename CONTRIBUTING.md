# 贡献指南

感谢你愿意一起完善 `netease-lyric-api`。这是一个 Windows 专属项目，核心目标是：
**只读网易云音乐 PC 客户端，稳定地提供实时歌词、播放进度与歌曲信息 API。**

## 先看这些

- [README](README.md)：功能、安装、三种调用方式、更新日志与兼容性说明。
- [技术分享](docs/技术分享-netease-lyric-api.md)：播放钟扫描、架构、缓存与已知局限。
- [Issues](https://github.com/maker114/netease-lyric-api/issues)：报告问题或认领任务。

## 开发环境

- Windows 10/11
- Python 3.8+
- 网易云音乐 PC 客户端（仅运行完整测试不需要，涉及内存/悬浮窗的改动需要）
- 可选：Pillow（悬浮窗封面）、tkinter（悬浮窗）

```bat
git clone https://github.com/maker114/netease-lyric-api.git
cd netease-lyric-api
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev,overlay]"
```

## 测试

离线测试不依赖网易云客户端和网络：

```bat
python -m unittest discover -s tests -v
python -m compileall -q netease_lyric tests
```

涉及内存读取、窗口定位、悬浮窗或真实网络请求的改动，请额外在 Windows + 正在播放的网易云客户端上实测，
并在 PR 中注明：Windows 版本、网易云版本、Python 版本、实测歌曲/场景。

## 代码约定

- 核心包保持**纯标准库 + numpy**；新增必需依赖需要先讨论。Pillow 只能作为可选依赖。
- 保持 Python 3.8 兼容（不要使用 3.9+ 才有的语法或标准库 API）。
- 核心读取逻辑（`clock.py` / `player.py`）只读目标进程，**不注入、不修改客户端**，不提供播放控制。
- 公开 API、JSON 字段、CLI 输出、HTTP 端点发生变化时，必须同步更新 README 与更新日志。
- 优先做**增量、向后兼容**的改动；破坏性改动需要有明确理由并在更新日志中标注。
- 新增缓存/网络请求要考虑超时和失败降级，不能阻塞歌词/进度主流程。

## 提交 PR

1. 从 `main` 切分支（如 `feat/xxx`、`fix/xxx`）。
2. 写清动机、改动点与测试结果。
3. 确认 `python -m unittest discover -s tests -v` 全部通过。
4. 涉及用户可见变化时更新 README 的更新日志。
5. 提交 PR，使用仓库的 PR 模板。

## 发布（维护者）

- 版本号遵循 SemVer；功能新增升 MINOR，修复升 PATCH。
- 发布前更新 README 更新日志、构建 wheel、打 `vX.Y.Z` tag。
- 推送 `v*` tag 后，`.github/workflows/release.yml` 会自动构建 wheel 并创建 GitHub Release。
- 详细曝光/发布清单见 [docs/发布与曝光清单.md](docs/发布与曝光清单.md)。

## 许可

提交的代码默认以 [MIT License](LICENSE) 授权。
