# 项目 Git 与发布基线清单

基线采集：2026-07-15，实施前 `22` 个已修改、`57` 个未跟踪项，共 `79` 项。该快照仅用于分组，不代表已暂存或批准提交；本轮未执行 stage、commit、reset、clean 或文件重组。

2026-07-16 续跑仍遵守同一边界；新增安全失败路径、测试和验收记录继续留在未暂存工作区，不改变实施前快照含义。

## 建议提交分组

1. 架构拆分：`src/web/` API 模块、`src/tools/runtime_paths.py`、Pipeline helper 与对应测试。
2. v0.6 打包：`desktop/`、`packaging/windows/`、portable 构建脚本、品牌资源与外测文档。
3. 阶段 3–5 ASR：benchmark、候选 dry-run、混合语言证据、验收记录；生产默认保持 `off`。
4. 安全与发布基线：会话安全、配置恢复、`VERSION`、CI、阶段 5.1 文档与测试。
5. 测试与文档：独立回归测试、README、roadmap、acceptance 记录。

## 目录归类

| 路径 | 分类 | 默认处理 |
|---|---|---|
| `packaging/` | Windows 构建源码、第三方声明；其 runtime 子目录是本地大产物 | 脚本/声明可审查提交，runtime 不提交 |
| `desktop/build/` | 安装包图标等品牌资产 | 人工确认来源与许可后提交 |
| `.superdesign/` | 设计工具元数据 | 非运行必需；单独审查后决定 |
| `audit/` | 本地审计、快照或证据产物 | 默认不作为产品源码，先脱敏再决定 |

## 发布边界

当前安装包仅为 External Test Preview；不启用自动更新，不承诺干净 VM 兼容已通过，不实现 ASS。`config/*.local.json`、用户媒体、字幕正文、模型、缓存、runtime 和诊断包均不得进入 Git。
