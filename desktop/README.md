# 智译字幕工坊 / CineSub Studio Electron 便携壳

`desktop/` 是智译字幕工坊的 Electron 桌面壳。Python Web 应用仍是业务逻辑与 API 的唯一实现，Electron 只负责启动包内后端、打开本地窗口、目录选择和退出时清理子进程。

## 开发模式

开发模式使用项目 `.venv` 和源码根目录：

```powershell
.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
cd desktop
npm start
```

默认端口为 `7860`，可通过 `CINESUB_DESKTOP_PORT` 覆盖。

## 0.6.2 便携模式

- 入口为解压目录内的 `CineSubStudio.exe`。
- Python 位于 `resources/app/python/`。
- FFmpeg 和可选 CUDA 位于 `resources/app/tools/`。
- 后端源码位于 `resources/app/backend/`。
- `small` 模型、配置、缓存、日志和字幕产物全部位于 EXE 同级 `data/`。
- Electron 的 userData、session cache 和日志也重定向到 `data/`，不写 `%APPDATA%` 或 `%LOCALAPPDATA%`。
- 关闭 Electron 窗口时终止 Python 后端。

正式构建入口：

首次构建桌面依赖时，在 `desktop/` 目录运行 `npm install`。正式构建统一从仓库根目录调用：

```powershell
.\.venv\Scripts\python.exe -B scripts\build_portable_release.py
```

## Packaged mode（便携运行模式）

打包后由 `CineSubStudio.exe` 启动 `resources/app/` 内的 portable Python 后端；程序数据全部定向到 EXE 同级 `data/`。

构建器会准备 portable runtime、调用 `electron-builder --win --dir`、加入 `small` 模型、执行敏感信息扫描并生成 ZIP/SHA256。不会生成 NSIS 安装器。若完整包达到 GitHub 2 GiB 单文件限制，会生成 CPU 主包和独立 CUDA add-on。

## 冻结约束

0.6.2 不提供代码签名、自动更新或安装器。发布构建接口以 0.6.2 为冻结基线；后续改变目录布局、资源边界或启动契约必须升级版本并同步测试。
