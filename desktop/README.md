# 智译字幕工坊 / CineSub Studio Electron 便携壳

`desktop/` 是源码仓库中的 Electron 桌面壳。Python Web 应用仍是业务逻辑和 API 的唯一实现；Electron 只负责启动包内后端、显示本地窗口、选择目录并在退出时清理子进程。

## 开发模式

```powershell
.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
cd desktop
npm install
npm start
```

默认端口为 `7860`，可通过 `CINESUB_DESKTOP_PORT` 覆盖。

## 0.6.2 packaged 布局

```text
CineSubStudio.exe
resources/app/backend/    Python 后端源码和 Web UI
resources/app/python/     portable Python 与依赖
resources/app/tools/      FFmpeg 与 CUDA
data/models/              small 模型和后续导入模型
data/config/              Provider 与 Language Profile
data/output/              字幕与报告
data/work/                中间产物
data/logs/                Python 与 Electron 日志
data/uploads/             单文件任务副本
data/.cache/electron/     Electron userData 和 session cache
```

所有可写数据必须位于 EXE 同级 `data/`；不得写 `%APPDATA%` 或 `%LOCALAPPDATA%`。关闭 Electron 窗口后必须终止 Python 后端。

## 唯一发布入口

从仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -B scripts\build_portable_release.py
```

构建器使用 `electron-builder --win --dir` 生成 Electron unpacked 目录，再加入 portable Python、FFmpeg、CUDA 和 `small` 模型，扫描敏感信息并输出 ZIP/SHA256。

0.6.2 不生成 NSIS、用户可见启动脚本、自动更新或签名产物。若完整 ZIP 达到 GitHub 2 GiB 限制，构建器会生成 CPU 主包和独立 CUDA add-on。

该布局是 0.6.2 冻结基线；改变入口、目录或资源边界必须升级版本并同步测试。
