# v0.4 Desktop Preview Release Cut

## Release Baseline

- **Commit:** `684ed09` (`684ed094d19cb4c1dce6be1762ecd9576ca3ecd5`)
- **Commit message:** `v0.4.1: harden translation structured output handling`
- **Product name:** 智译字幕工坊 / CineSub Studio
- **Preview version:** v0.4 Desktop Preview
- **Date:** 2025-07-08

## Summary

整理并冻结 `智译字幕工坊 / CineSub Studio v0.4 Desktop Preview` 作为可内测预览基线。

这是 release cut / preview baseline，不新增功能，不改算法。

## Verified Capabilities (已验证能力)

| Capability | Status |
|---|---|
| Electron 桌面启动 | ✅ |
| 后端自动启动（伴随 Electron 生命周期） | ✅ |
| 关闭 Electron 清理后端 | ✅ |
| 批量文件夹选择器 | ✅ |
| Provider / Profile 设置 | ✅ |
| Runtime diagnostics | ✅ |
| Recent jobs | ✅ |
| 真实法语样例 ASR + translation + quality check | ✅ |
| 输出下载 links 200 | ✅ |
| 中文路径启动 | ✅ |
| 品牌改名（智译字幕工坊 / CineSub Studio） | ✅ |
| Translation structured output 兜底 | ✅ |

## Test Results

### Core tests
```
tests\test_translation_structured_output.py ......  PASSED
tests\test_branding_text.py .......................  PASSED
tests\test_electron_shell_readiness.py ...........  PASSED
tests\test_electron_folder_picker.py .............  PASSED
tests\test_premium_ui_refresh.py .................  PASSED
```

### Full suite (excluding out-of-scope portable release tests)
```
All tests passed (excluding tests\test_windows_portable_release_readiness.py)
```

Note: `test_windows_portable_release_readiness.py` requires PowerShell subprocess, unavailable in current test environment. This is acceptable because portable release bundling is explicitly **not** in scope for this preview cut.

### Git hygiene
- `git diff --check`: ✅ no whitespace errors

## Known Limitations (已知限制)

- 仍需要本地 Python / `.venv`
- Electron 依赖 `npm install` 手动安装
- FFmpeg 未内置（依赖系统 PATH 或手动配置）
- Models（ASR whisper 模型）未内置，首次使用需下载
- DeepSeek / API 网络仍可能外部失败
- 没有 installer / setup.exe
- 没有 code signing
- 没有 auto-update
- 不是正式 public release

## Blockers Cleared (不再阻塞 v0.4 preview 的问题)

- DeepSeek structured output 注释 / incomplete output 已兜底（commit `684ed09`）

## Backlog (仍作为后续方向)

- Installer / electron-builder
- Bundled portable Python runtime
- Model / FFmpeg provisioning UI
- ASR 算法调优（segment language routing 等）
- Translation resume 更细粒度审计
- 配音 / TTS

## Usage Snapshot

```text
智译字幕工坊 / CineSub Studio v0.4 Desktop Preview
一个可运行的本地桌面预览版：
Electron 启动 Web 工作台，支持单文件/批量字幕识别、大模型翻译、任务历史、运行环境诊断。
```

## Quick Start (for preview testers)

1. 确保本地有 Python 3.12 和 `.venv` 已安装
2. 确保 `npm install` 已在 `desktop/` 目录执行
3. 确保 FFmpeg 在系统 PATH 中
4. 配置 Provider（DeepSeek API Key）和 Language Profile
5. 运行：
   ```powershell
   cd desktop
   npm start
   ```
6. Electron 窗口将自动打开，后端 127.0.0.1:7860 自动启动
7. 关闭窗口自动清理后端进程

## Commit & Tag

Commit message:
```
v0.4: cut desktop preview baseline
```

Tag:
```bash
git tag -a v0.4-desktop-preview -m "智译字幕工坊 / CineSub Studio v0.4 Desktop Preview"
```

Do not push tag unless explicitly requested.
