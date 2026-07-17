# v0.6.1 External Test Stabilization

状态：`completed`（本机自动化与打包范围）。

## 范围

- 收拢 v0.6.1 Windows External Test Preview 基线。
- 增加 portable `.venv` 配置检查和显式原子修复。
- 加固无界面启动与本地客户端断连行为。
- 保持 ASR 候选、翻译可靠性增强为 `no_go/off`，ASS 继续 reserved。

## 自动验证

- 单进程全量 pytest 连续两轮通过：每轮 `565 passed`，并将 `PytestUnhandledThreadExceptionWarning` 提升为 error。
- portable `.venv` 配置检查通过：UTF-8 中文路径、base interpreter 和当前 `.venv` 一致；未重建 `.venv`。
- 新增测试覆盖缺失/损坏配置、中文路径、目标不存在、重复修复、原子替换失败和显式安装脚本开关。
- 原生依赖导入诊断改为短生命周期子进程，避免 Web 请求线程加载 `ctranslate2/faster_whisper` 后污染主进程。
- HTTP handler 单元测试不再创建后台监听线程；源码 smoke 继续使用真实 `127.0.0.1` 端口。
- Ruff 增量检查、版本一致性、Node/PowerShell 语法、策略扫描、基础导入、翻译/质检 self-test 和 `git diff --check` 通过。
- 项目 smoke 通过：首页与 `/api/runtime/diagnostics` 均返回 200；Pipeline scan/status 只读执行完成。
- Pipeline review 如实保留现有 `188` 个问题（8 errors、180 warnings），不登记为字幕质量通过，也不作为本轮运行时稳定化阻断项。

## Windows 发布验证

| Flavor | Installer | Bytes | SHA-256 | CUDA |
| --- | --- | ---: | --- | --- |
| CPU/auto | `desktop/release/cpu/CineSubStudio-0.6.1-windows-x64-cpu-setup.exe` | `274375729` | `ACE0D83DBF07849B10B579A0031F4D1D9157A06DED24CF652EB132EEFF9C2E0C` | absent |
| GPU | `desktop/release/gpu/CineSubStudio-0.6.1-windows-x64-gpu-setup.exe` | `1266475811` | `6A1603F5CA14AE15EE5CC8493ED5EB0A657CF325198E71B99950CA35A6A14FD6` | present |

- 两个 flavor 的 manifest 均为 schema 1、version `0.6.1`，携带 portable Python、FFmpeg/FFprobe，不携带模型或 NVIDIA 驱动。
- Manifest 只登记当前版本/flavor 的安装器，旧版本安装器不会混入当前发布证据。
- 两个 `win-unpacked` 后端的 homepage、diagnostics、app-info 均返回 200。
- 两个 packaged diagnostics 均报告 `runtime_layout=packaged`、`python_source=packaged-python`；CPU 报告 CUDA unavailable，GPU 报告 CUDA ready。
- 两个 packaged 后端退出后端口均关闭，没有观察到遗留后端进程。

短样本：`tests/e2e_samples/fr_short/34584660077-1-192.mp4`，现有本地 `small` 模型，强制法语，不启用翻译。

- CPU：`cpu/int8`，`local_files_only=True`，约 297 秒，输出 SRT `25862` bytes。
- GPU：`cuda/float16`，`local_files_only=True`，约 59 秒，输出 SRT `29811` bytes。
- 两次运行均保持 ASR experiment 和 segment routing 为 `off`，未下载模型、未调用 LLM、未评价字幕质量。

## 明确保留的未完成项

- [ ] 干净 Windows 10 VM 安装、启动、卸载。
- [ ] 干净 Windows 11 VM 安装、启动、卸载。
- [ ] GPU 包在无兼容驱动机器上的诊断。
- [ ] 真实长片 ASR/翻译质量人工验收。

在以上项目完成前，v0.6.1 只能标记为 External Test Preview，不得宣称零配置正式版。
