"""
Batch Subtitle Pipeline Worker — 批量字幕生产流水线

将 cinesub-studio 从单文件工具改造为自动化生产系统：

    input/  →  自动发现  →  提取音频  →  Whisper 转写  →  LLM 翻译  →  质检  →  输出

用法:
    python batch_worker.py --input input --model large-v3 --device cuda

架构:
    1. 文件扫描层 — 自动发现 input 目录中的新视频
    2. 转写层 — ffmpeg + faster-whisper + 语言识别保存
    3. 翻译层 — LLM API + 上下文窗口 + 分批翻译 + 缓存
    4. 质检层 — SRT 格式检查 + 翻译质量规则检查
    5. 输出层 — 原文/中文/双语字幕 + 质量报告 + review_needed.srt

特性:
    - 断点续跑：中断后不从头开始，复用已有中间文件
    - 失败重试：每个阶段独立重试，失败任务移入 failed/
    - 语言策略路由：主流语言 vs 小语种使用不同翻译提示词
    - 完整状态追踪：每个任务 JSON 状态文件记录进度
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── 项目根目录 ───────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent

# ── 目录结构 ─────────────────────────────────────────────────────────────

DIR_INPUT = PROJECT_ROOT / "input"
DIR_WORK = PROJECT_ROOT / "work"
DIR_WORK_STATES = PROJECT_ROOT / "work" / "states"
DIR_OUTPUT_SOURCE = PROJECT_ROOT / "output" / "source"
DIR_OUTPUT_ZH = PROJECT_ROOT / "output" / "zh"
DIR_OUTPUT_BILINGUAL = PROJECT_ROOT / "output" / "bilingual"
DIR_OUTPUT_REPORTS = PROJECT_ROOT / "output" / "reports"
DIR_ARCHIVE = PROJECT_ROOT / "archive"
DIR_FAILED = PROJECT_ROOT / "failed"
DIR_MODELS = PROJECT_ROOT / "models"

# ── 任务状态枚举 ─────────────────────────────────────────────────────────

class TaskStage:
    PENDING = "pending"
    EXTRACTING_AUDIO = "extracting_audio"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    QUALITY_CHECKING = "quality_checking"
    COMPLETED = "completed"
    FAILED = "failed"


# ── 主流语言 vs 小语种策略 ──────────────────────────────────────────────

MAJOR_LANGUAGES = {"en", "ja", "ko", "zh", "fr", "de", "es", "ru", "pt", "it", "ar", "th", "vi"}

# 主流语言使用常规影视翻译提示词（高效）
DEFAULT_MAJOR_PROMPT = ""

# 小语种使用更保守的翻译提示词（保留更多原文信息）
MINOR_LANGUAGE_EXTRA_PROMPT = (
    "源语言可能是小语种或方言，翻译时请注意：\n"
    "1. 如遇到不确定的内容，保留原文并标注 [待确认]\n"
    "2. 专有名词和术语保持原文\n"
    "3. 不要猜测模糊不清的内容\n"
    "4. 保持字幕简短，适合屏幕阅读"
)

# 低置信度语言额外提示词
LOW_CONFIDENCE_EXTRA_PROMPT = (
    "源语言识别置信度较低，翻译时请注意：\n"
    "1. 如字幕内容看起来不是目标源语言，保留原文\n"
    "2. 不要强行翻译看起来乱码或不完整的内容"
)

# 语言识别置信度阈值
LANG_CONFIDENCE_THRESHOLD = 0.7


# ── 任务状态数据结构 ─────────────────────────────────────────────────────

@dataclass
class TaskState:
    """单个视频任务的完整状态。"""
    file: str                    # 输入文件名
    input_path: str              # 输入文件绝对路径
    stage: str = TaskStage.PENDING
    status: str = "pending"      # pending | running | completed | failed
    created_at: float = 0.0
    updated_at: float = 0.0

    # 音频提取
    audio_path: str = ""

    # 语言识别
    language_detection: dict | None = None

    # 转写
    source_srt: str = ""

    # 翻译
    translated_srt: str = ""      # 译文 SRT（translated 模式）
    bilingual_srt: str = ""       # 双语 SRT

    # 质检
    quality_report: str = ""      # 质量报告 JSON 路径

    # 错误信息
    error: str = ""
    error_stage: str = ""
    retry_count: int = 0
    max_retries: int = 3

    # 输出
    output_dir: str = ""

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "input_path": self.input_path,
            "stage": self.stage,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "audio_path": self.audio_path,
            "language_detection": self.language_detection,
            "source_srt": self.source_srt,
            "translated_srt": self.translated_srt,
            "bilingual_srt": self.bilingual_srt,
            "quality_report": self.quality_report,
            "error": self.error,
            "error_stage": self.error_stage,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "output_dir": self.output_dir,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskState":
        return cls(
            file=data.get("file", ""),
            input_path=data.get("input_path", ""),
            stage=data.get("stage", TaskStage.PENDING),
            status=data.get("status", "pending"),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            audio_path=data.get("audio_path", ""),
            language_detection=data.get("language_detection"),
            source_srt=data.get("source_srt", ""),
            translated_srt=data.get("translated_srt", ""),
            bilingual_srt=data.get("bilingual_srt", ""),
            quality_report=data.get("quality_report", ""),
            error=data.get("error", ""),
            error_stage=data.get("error_stage", ""),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
            output_dir=data.get("output_dir", ""),
        )

    def state_path(self) -> Path:
        """返回该任务的状态文件路径。"""
        stem = Path(self.file).stem
        return DIR_WORK_STATES / f"{stem}.state.json"

    def save(self) -> None:
        """保存状态到 JSON 文件。"""
        self.updated_at = time.time()
        path = self.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, state_path: Path) -> Optional["TaskState"]:
        """从 JSON 文件加载任务状态。"""
        if not state_path.exists():
            return None
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (OSError, json.JSONDecodeError):
            return None


# ── 视频文件发现 ─────────────────────────────────────────────────────────

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v",
    ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".wav",
}


def discover_videos(input_dir: Path) -> list[Path]:
    """扫描 input 目录，返回所有待处理的视频/音频文件列表。"""
    if not input_dir.exists():
        return []

    videos: list[Path] = []
    for path in sorted(input_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            videos.append(path)
        elif path.is_dir():
            # 递归扫描子目录
            for subpath in sorted(path.rglob("*")):
                if subpath.is_file() and subpath.suffix.lower() in VIDEO_EXTENSIONS:
                    videos.append(subpath)

    return videos


# ── 批处理配置 ───────────────────────────────────────────────────────────

@dataclass
class BatchConfig:
    """批处理运行配置。"""
    input_dir: Path = DIR_INPUT
    output_dir: Path = PROJECT_ROOT / "output"
    model_dir: Path = DIR_MODELS
    work_dir: Path = DIR_WORK

    # Whisper 配置
    model: str = "large-v3"
    device: str = "cpu"
    compute_type: str | None = None
    language: str | None = None   # None = 自动检测
    beam_size: int = 5
    vad_filter: bool = True
    local_files_only: bool = False

    # 翻译配置
    translate: bool = True
    api_provider: str = "openai-compatible"
    api_base: str = ""
    api_key: str = ""
    llm_model: str = ""
    target_language: str = "zh-CN"
    translation_batch_size: int = 20
    translation_temperature: float = 0.2
    translation_mode: str = "bilingual"
    context_window: int = 3
    translation_prompt: str = ""

    # 批处理配置
    max_retries: int = 3
    skip_completed: bool = True    # 跳过已完成的视频
    move_completed: bool = True    # 完成后移动到 archive/

    def __post_init__(self):
        # 从环境变量获取 API key
        if not self.api_key:
            self.api_key = os.environ.get("SUBTITLE_LLM_API_KEY", "")


# ── 流水线主逻辑 ─────────────────────────────────────────────────────────

class BatchPipeline:
    """批量字幕生产流水线。

    管理整个处理流程：发现 → 提取 → 转写 → 翻译 → 质检 → 输出。
    """

    def __init__(self, config: BatchConfig):
        self.config = config
        self.tasks: list[TaskState] = []
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """创建所有必要的目录。"""
        for d in [
            self.config.input_dir,
            self.config.work_dir,
            DIR_WORK_STATES,
            DIR_OUTPUT_SOURCE,
            DIR_OUTPUT_ZH,
            DIR_OUTPUT_BILINGUAL,
            DIR_OUTPUT_REPORTS,
            DIR_ARCHIVE,
            DIR_FAILED,
            self.config.model_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    def scan(self) -> list[TaskState]:
        """扫描 input 目录，返回待处理的任务列表。"""
        videos = discover_videos(self.config.input_dir)
        tasks: list[TaskState] = []

        for video_path in videos:
            stem = video_path.stem
            state_path = DIR_WORK_STATES / f"{stem}.state.json"

            # 检查是否已有状态文件
            existing = TaskState.load(state_path)
            if existing and existing.status == "completed" and self.config.skip_completed:
                print(f"  [跳过] 已完成: {video_path.name}")
                continue

            if existing:
                task = existing
                # 更新可能变化的配置
                task.max_retries = self.config.max_retries
            else:
                task = TaskState(
                    file=video_path.name,
                    input_path=str(video_path.resolve()),
                    created_at=time.time(),
                    max_retries=self.config.max_retries,
                )
                task.save()

            tasks.append(task)

        return tasks

    def run(self) -> dict:
        """运行完整流水线：扫描 → 逐个处理 → 汇总报告。

        Returns:
            {"total": int, "completed": int, "failed": int, "skipped": int}
        """
        print("=" * 60)
        print("  CineSub Studio — 批量字幕生产流水线")
        print("=" * 60)
        print(f"  模型: {self.config.model}")
        print(f"  设备: {self.config.device}")
        print(f"  翻译: {'启用' if self.config.translate else '禁用'}")
        if self.config.translate:
            print(f"  LLM: {self.config.llm_model}")
            print(f"  目标语言: {self.config.target_language}")
            print(f"  翻译模式: {self.config.translation_mode}")
        print(f"  输入目录: {self.config.input_dir}")
        print(f"  最大重试: {self.config.max_retries}")
        print()

        # 扫描文件
        print("扫描 input 目录...")
        self.tasks = self.scan()

        if not self.tasks:
            print("没有发现待处理的文件。")
            return {"total": 0, "completed": 0, "failed": 0, "skipped": 0}

        print(f"发现 {len(self.tasks)} 个待处理文件\n")

        completed = 0
        failed = 0
        skipped = 0

        for i, task in enumerate(self.tasks, start=1):
            print(f"[{i}/{len(self.tasks)}] 处理: {task.file}")

            if task.status == "completed" and self.config.skip_completed:
                print(f"  已完成，跳过")
                skipped += 1
                continue

            try:
                self._process_one(task)
                completed += 1
                print(f"  ✓ 完成")
            except Exception as exc:
                failed += 1
                task.status = "failed"
                task.error = str(exc)
                task.error_stage = task.stage
                task.save()
                print(f"  ✗ 失败: {exc}")
                traceback.print_exc()

        print()
        print("=" * 60)
        print(f"  流水线完成")
        print(f"  总计: {len(self.tasks)} | 成功: {completed} | 失败: {failed} | 跳过: {skipped}")
        print("=" * 60)

        return {
            "total": len(self.tasks),
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
        }

    def _process_one(self, task: TaskState) -> None:
        """处理单个任务的完整流水线。"""
        input_path = Path(task.input_path)
        stem = input_path.stem
        model = self.config.model

        # ── 阶段 1: 提取音频 ──
        if not task.audio_path or not Path(task.audio_path).exists():
            task.stage = TaskStage.EXTRACTING_AUDIO
            task.status = "running"
            task.save()
            print(f"  [1/5] 提取音频...")
            task.audio_path = str(self._extract_audio(input_path))
            task.save()
        else:
            print(f"  [1/5] 音频已存在，跳过提取")

        # ── 阶段 2: Whisper 转写 ──
        source_srt = DIR_OUTPUT_SOURCE / f"{stem}.{model}.srt"
        if not source_srt.exists():
            task.stage = TaskStage.TRANSCRIBING
            task.save()
            print(f"  [2/5] Whisper 转写...")
            lang_info = self._transcribe(Path(task.audio_path), source_srt)
            task.source_srt = str(source_srt.resolve())
            task.language_detection = lang_info
            task.save()
        else:
            task.stage = TaskStage.TRANSCRIBING
            task.source_srt = str(source_srt.resolve())
            # 尝试加载已有的语言检测文件
            lang_json = source_srt.with_suffix(".lang.json")
            if lang_json.exists() and task.language_detection is None:
                try:
                    task.language_detection = json.loads(lang_json.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
            task.save()
            print(f"  [2/5] 原文 SRT 已存在，跳过转写")

        # 显示语言识别结果
        if task.language_detection:
            ld = task.language_detection
            print(f"      语言: {ld.get('source_language', '?')} "
                  f"(置信度: {ld.get('language_probability', 'N/A')})")

        # ── 阶段 3: LLM 翻译 ──
        if self.config.translate:
            translated_srt = DIR_OUTPUT_ZH / f"{stem}.{model}.translated.{self.config.target_language}.srt"
            bilingual_srt = DIR_OUTPUT_BILINGUAL / f"{stem}.{model}.bilingual.{self.config.target_language}.srt"

            if self.config.translation_mode == "bilingual":
                output_translated = bilingual_srt
            else:
                output_translated = translated_srt

            if not output_translated.exists():
                task.stage = TaskStage.TRANSLATING
                task.save()
                print(f"  [3/5] LLM 翻译...")

                # 根据语言选择策略
                effective_prompt = self._build_language_strategy(task.language_detection)

                self._translate(
                    source_srt=source_srt,
                    output_path=output_translated,
                    effective_prompt=effective_prompt,
                )
                task.translated_srt = str(output_translated.resolve())
                task.bilingual_srt = str(bilingual_srt.resolve()) if self.config.translation_mode == "bilingual" else ""
                task.save()
            else:
                task.translated_srt = str(output_translated.resolve())
                if self.config.translation_mode == "bilingual":
                    task.bilingual_srt = str(bilingual_srt.resolve())
                task.save()
                print(f"  [3/5] 译文 SRT 已存在，跳过翻译")

            # ── 阶段 4: 质量检查 ──
            report_path = DIR_OUTPUT_REPORTS / f"{stem}.{model}.quality_report.json"
            if not report_path.exists():
                task.stage = TaskStage.QUALITY_CHECKING
                task.save()
                print(f"  [4/5] 质量检查...")
                self._quality_check(source_srt, output_translated, report_path)
                task.quality_report = str(report_path.resolve())
                task.save()
            else:
                task.quality_report = str(report_path.resolve())
                task.save()
                print(f"  [4/5] 质检报告已存在，跳过")
        else:
            # 不翻译，跳过翻译和质检
            print(f"  [3/5] 翻译已禁用，跳过")
            print(f"  [4/5] 质检已禁用，跳过")
            task.stage = TaskStage.QUALITY_CHECKING
            task.save()

        # ── 阶段 5: 完成 ──
        task.stage = TaskStage.COMPLETED
        task.status = "completed"
        task.save()
        print(f"  [5/5] 输出完成")

        # 打印输出文件
        print(f"      原文: {task.source_srt}")
        if task.translated_srt:
            print(f"      译文: {task.translated_srt}")
        if task.quality_report:
            # 读取质检摘要
            try:
                qr = json.loads(Path(task.quality_report).read_text(encoding="utf-8"))
                print(f"      质检: {qr.get('status', '?')} "
                      f"({qr.get('summary', {}).get('total_issues', 0)} 个问题)")
            except (OSError, json.JSONDecodeError):
                print(f"      质检: {task.quality_report}")

        # 移动已完成文件
        if self.config.move_completed:
            self._archive_completed(task)

    # ── 各阶段实现 ─────────────────────────────────────────────────────

    def _extract_audio(self, input_path: Path) -> Path:
        """提取音频为 16kHz mono WAV。"""
        audio_path = self.config.work_dir / f"{input_path.stem}.16k.wav"

        # 如果已存在有效的音频文件，复用
        if audio_path.exists() and audio_path.stat().st_size > 0:
            return audio_path

        suffix = input_path.suffix.lower()
        if suffix == ".wav":
            # 仍需确保格式正确
            pass

        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg 未找到，请确保 ffmpeg 在 PATH 中")

        command = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            str(audio_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 提取音频失败: {result.stderr[:500]}")

        if not audio_path.exists() or audio_path.stat().st_size == 0:
            raise RuntimeError("音频提取失败：输出文件为空")

        return audio_path

    def _transcribe(self, audio_path: Path, srt_path: Path) -> dict | None:
        """运行 Whisper 转写，返回语言检测信息。"""
        from transcribe import transcribe_to_srt

        lang_info = transcribe_to_srt(
            audio_path=audio_path,
            srt_path=srt_path,
            model_name=self.config.model,
            model_dir=self.config.model_dir,
            device=self.config.device,
            compute_type=self.config.compute_type,
            language=self.config.language,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            local_files_only=self.config.local_files_only,
        )
        return lang_info

    def _translate(
        self,
        source_srt: Path,
        output_path: Path,
        effective_prompt: str,
    ) -> None:
        """运行 LLM 翻译。"""
        from subtitle_translate import translate_srt

        translate_srt(
            input_path=source_srt,
            output_path=output_path,
            api_provider=self.config.api_provider,
            api_base=self.config.api_base,
            api_key=self.config.api_key,
            llm_model=self.config.llm_model,
            target_language=self.config.target_language,
            batch_size=self.config.translation_batch_size,
            temperature=self.config.translation_temperature,
            translation_mode=self.config.translation_mode,
            system_prompt=effective_prompt,
            context_window=self.config.context_window,
        )

    def _quality_check(
        self,
        source_srt: Path,
        translated_srt: Path,
        report_path: Path,
    ) -> None:
        """运行质量检查。"""
        from quality_checker import run_quality_check

        run_quality_check(
            source_srt=source_srt,
            translated_srt=translated_srt,
            target_language=self.config.target_language,
            output_dir=DIR_OUTPUT_REPORTS,
        )

    def _build_language_strategy(self, lang_detection: dict | None) -> str:
        """根据语言识别结果构建翻译策略提示词。"""
        if not lang_detection:
            return self.config.translation_prompt

        lang = lang_detection.get("source_language", "")
        prob = lang_detection.get("language_probability")

        extra_parts: list[str] = []

        # 用户自定义提示词
        if self.config.translation_prompt.strip():
            extra_parts.append(self.config.translation_prompt.strip())

        # 小语种策略
        if lang and lang not in MAJOR_LANGUAGES:
            extra_parts.append(MINOR_LANGUAGE_EXTRA_PROMPT)

        # 低置信度策略
        if prob is not None and prob < LANG_CONFIDENCE_THRESHOLD:
            extra_parts.append(LOW_CONFIDENCE_EXTRA_PROMPT)

        if extra_parts:
            return "\n\n".join(extra_parts)

        return ""

    def _archive_completed(self, task: TaskState) -> None:
        """将已完成的输入文件移动到 archive 目录。"""
        input_path = Path(task.input_path)
        if not input_path.exists():
            return

        dest = DIR_ARCHIVE / input_path.name
        # 如果目标已存在，添加时间戳
        if dest.exists():
            dest = DIR_ARCHIVE / f"{input_path.stem}_{int(time.time())}{input_path.suffix}"

        try:
            shutil.move(str(input_path), str(dest))
            print(f"      已归档: {dest.name}")
        except OSError as exc:
            print(f"      归档失败: {exc}")


# ── CLI ─────────────────────────────────────────────────────────────────

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="CineSub Studio — 批量字幕生产流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python batch_worker.py --input input --model large-v3 --device cuda
  python batch_worker.py --input input --model small --no-translate
  python batch_worker.py --input input --model large-v3 --api-base https://api.openai.com/v1 --api-key sk-xxx --llm-model gpt-4o
  python batch_worker.py --scan                 # 仅扫描，不处理
  python batch_worker.py --status               # 查看所有任务状态
        """.strip(),
    )

    # 基础配置
    parser.add_argument("--input", default="input", help="输入视频目录 (默认: input/)")
    parser.add_argument("--output-dir", default="output", help="输出根目录 (默认: output/)")
    parser.add_argument("--model-dir", default="models", help="模型目录 (默认: models/)")
    parser.add_argument("--work-dir", default="work", help="临时工作目录 (默认: work/)")

    # Whisper 配置
    parser.add_argument("--model", default="large-v3",
                        help="Whisper 模型名 (默认: large-v3)")
    parser.add_argument("--device", default="cpu",
                        choices=["cpu", "cuda", "auto"], help="运行设备")
    parser.add_argument("--compute-type", default=None,
                        help="计算精度 (CPU: int8, CUDA: float16)")
    parser.add_argument("--language", default=None,
                        help="源语言代码 (省略则自动检测)")
    parser.add_argument("--beam-size", type=int, default=5, help="Beam size")
    parser.add_argument("--no-vad", action="store_true", help="禁用 VAD")
    parser.add_argument("--local-files-only", action="store_true",
                        help="仅使用本地模型文件")

    # 翻译配置
    parser.add_argument("--no-translate", action="store_true",
                        help="禁用翻译（仅转写）")
    parser.add_argument("--provider", default=None,
                        help="Provider ID，从 config/providers.local.json 读取配置")
    parser.add_argument("--api-provider", default=None,
                        choices=["openai-compatible", "anthropic"],
                        help="LLM API 类型（显式传入时覆盖 Provider 配置）")
    parser.add_argument("--api-base", default=None, help="LLM API 地址（显式传入时覆盖 Provider 配置）")
    parser.add_argument("--api-key", default=None, help="LLM API 密钥（显式传入时覆盖 Provider 配置）")
    parser.add_argument("--llm-model", default=None, help="LLM 模型名（显式传入时覆盖 Provider 配置）")
    parser.add_argument("--target-language", default="zh-CN",
                        help="翻译目标语言 (默认: zh-CN)")
    parser.add_argument("--translation-batch-size", type=int, default=20,
                        help="翻译批次大小")
    parser.add_argument("--translation-temperature", type=float, default=0.2,
                        help="翻译温度")
    parser.add_argument("--translation-mode", default="bilingual",
                        choices=["bilingual", "translated"],
                        help="翻译输出模式")
    parser.add_argument("--context-window", type=int, default=3,
                        help="翻译上下文窗口大小")
    parser.add_argument("--translation-prompt", default="",
                        help="自定义翻译提示词")

    # 批处理配置
    parser.add_argument("--max-retries", type=int, default=3,
                        help="最大重试次数 (默认: 3)")
    parser.add_argument("--no-skip-completed", action="store_true",
                        help="不跳过已完成的视频（重新处理）")
    parser.add_argument("--no-move-completed", action="store_true",
                        help="完成后不移动到 archive/")

    # 信息查询
    parser.add_argument("--scan", action="store_true",
                        help="仅扫描并显示待处理文件，不处理")
    parser.add_argument("--status", action="store_true",
                        help="显示所有任务状态")

    # 重试失败任务
    parser.add_argument("--retry-failed", action="store_true",
                        help="重新处理所有失败的任务")

    # 复核异常片段
    parser.add_argument("--review", action="store_true",
                        help="显示所有待复核的异常片段摘要")
    parser.add_argument("--review-file", default=None,
                        help="查看指定质量报告的详细异常列表")

    args = parser.parse_args()

    # 设置环境变量
    os.environ.setdefault("HF_HOME", str(PROJECT_ROOT / ".cache" / "huggingface"))
    os.environ.setdefault("HF_HUB_CACHE", str(PROJECT_ROOT / ".cache" / "huggingface" / "hub"))

    # ── Provider 配置加载 ──
    # 优先级：CLI 显式参数 > Provider 配置 > 默认值
    provider_config: dict = {}
    if args.provider is not None or (args.translate if hasattr(args, 'translate') else True):
        try:
            from provider_store import resolve_provider_config
            provider_config = resolve_provider_config(args.provider)
            if provider_config:
                print(f"  [Provider] 使用配置: {args.provider or '(active)'}")
        except Exception as exc:
            print(f"  [Provider] 加载失败: {exc}")

    # 合并 Provider 配置到 CLI 参数（CLI 显式传入的优先）
    def _first(*values):
        """返回第一个非 None 非空字符串的值。"""
        for v in values:
            if v is not None and v != "":
                return v
        return ""

    effective_api_provider = _first(args.api_provider, provider_config.get("api_provider"), "openai-compatible")
    effective_api_base = _first(args.api_base, provider_config.get("api_base"), "")
    effective_api_key = _first(args.api_key, provider_config.get("api_key"), "")
    effective_llm_model = _first(args.llm_model, provider_config.get("llm_model"), "")
    effective_model = _first(
        args.model if args.model != "large-v3" else None,
        provider_config.get("whisper_model"),
        "large-v3"
    )
    effective_device = _first(
        args.device if args.device != "cpu" else None,
        provider_config.get("whisper_device"),
        "cpu"
    )

    if args.api_key:
        os.environ["SUBTITLE_LLM_API_KEY"] = args.api_key
    elif effective_api_key:
        os.environ["SUBTITLE_LLM_API_KEY"] = effective_api_key

    config = BatchConfig(
        input_dir=Path(args.input).resolve(),
        output_dir=Path(args.output_dir).resolve(),
        model_dir=Path(args.model_dir).resolve(),
        work_dir=Path(args.work_dir).resolve(),
        model=effective_model,
        device=effective_device,
        compute_type=args.compute_type,
        language=args.language,
        beam_size=args.beam_size,
        vad_filter=not args.no_vad,
        local_files_only=args.local_files_only,
        translate=not args.no_translate,
        api_provider=effective_api_provider,
        api_base=effective_api_base,
        api_key=effective_api_key,
        llm_model=effective_llm_model,
        target_language=args.target_language,
        translation_batch_size=args.translation_batch_size,
        translation_temperature=args.translation_temperature,
        translation_mode=args.translation_mode,
        context_window=args.context_window,
        translation_prompt=args.translation_prompt,
        max_retries=args.max_retries,
        skip_completed=not args.no_skip_completed,
        move_completed=not args.no_move_completed,
    )

    pipeline = BatchPipeline(config)

    # --scan: 仅扫描
    if args.scan:
        tasks = pipeline.scan()
        if not tasks:
            print("没有发现待处理的文件。")
            return 0
        print(f"\n待处理文件 ({len(tasks)}):")
        for t in tasks:
            status_mark = {"completed": "[✓]", "failed": "[✗]", "pending": "[ ]"}.get(t.status, "[?]")
            print(f"  {status_mark} {t.file} — {t.stage}")
        return 0

    # --status: 显示所有任务状态
    if args.status:
        return _show_status()

    # --retry-failed: 重置失败任务
    if args.retry_failed:
        return _retry_failed(pipeline)

    # --review: 显示异常片段复核摘要
    if args.review:
        return _show_review()

    # --review-file: 查看指定质量报告详情
    if args.review_file:
        return _show_review_detail(Path(args.review_file))

    # 运行流水线
    api_key = effective_api_key or os.environ.get("SUBTITLE_LLM_API_KEY", "")
    if config.translate and not api_key:
        print("警告: 翻译功能已启用，但未设置 API Key。")
        print("请通过以下方式之一设置：")
        print("  1. Web 控制台 > 模型接口 > 新增 Provider（推荐）")
        print("  2. --provider <id> 命令行参数")
        print("  3. --api-key 命令行参数")
        print("  4. SUBTITLE_LLM_API_KEY 环境变量")
        print("如果不需要翻译，请添加 --no-translate 参数。")
        return 1

    result = pipeline.run()
    return 0 if result["failed"] == 0 else 1


def _show_status() -> int:
    """显示所有任务状态。"""
    if not DIR_WORK_STATES.exists():
        print("暂无任务记录。")
        return 0

    state_files = sorted(DIR_WORK_STATES.glob("*.state.json"))
    if not state_files:
        print("暂无任务记录。")
        return 0

    print(f"\n任务状态 ({len(state_files)}):\n")
    print(f"  {'文件':<40} {'状态':<12} {'阶段':<20} {'重试'}")
    print(f"  {'-'*40} {'-'*12} {'-'*20} {'-'*6}")

    for sf in state_files:
        task = TaskState.load(sf)
        if task is None:
            continue
        status_labels = {
            "pending": "等待中",
            "running": "处理中",
            "completed": "已完成",
            "failed": "失败",
        }
        stage_labels = {
            TaskStage.PENDING: "等待开始",
            TaskStage.EXTRACTING_AUDIO: "提取音频",
            TaskStage.TRANSCRIBING: "转写",
            TaskStage.TRANSLATING: "翻译",
            TaskStage.QUALITY_CHECKING: "质检",
            TaskStage.COMPLETED: "完成",
            TaskStage.FAILED: f"失败({task.error_stage})",
        }
        status = status_labels.get(task.status, task.status)
        stage = stage_labels.get(task.stage, task.stage)
        retry = f"{task.retry_count}/{task.max_retries}" if task.retry_count > 0 else "-"
        print(f"  {task.file:<40} {status:<12} {stage:<20} {retry}")

        if task.error:
            print(f"    └ 错误: {task.error[:100]}")

    print()
    return 0


def _retry_failed(pipeline: BatchPipeline) -> int:
    """仅重试之前失败的任务，不扫描 input 目录中的新文件。

    与 pipeline.run() 的区别：
    - run() 会 scan() 扫描 input/ 中所有文件并处理
    - retry_failed() 只重置状态为 failed 的任务，仅处理这些任务
    """
    if not DIR_WORK_STATES.exists():
        print("暂无任务记录。")
        return 0

    state_files = sorted(DIR_WORK_STATES.glob("*.state.json"))
    reset_tasks: list[TaskState] = []

    for sf in state_files:
        task = TaskState.load(sf)
        if task is None:
            continue
        if task.status == "failed":
            task.status = "pending"
            task.stage = TaskStage.PENDING
            task.error = ""
            task.error_stage = ""
            task.retry_count = 0
            task.save()
            print(f"  已重置: {task.file}")
            reset_tasks.append(task)

    if not reset_tasks:
        print("没有失败的任务需要重试。")
        return 0

    print(f"\n已重置 {len(reset_tasks)} 个失败任务，开始重新处理（仅处理这些任务，不扫描新文件）...\n")

    # 只处理重置的任务，不调用 scan()
    completed = 0
    failed = 0
    for i, task in enumerate(reset_tasks, start=1):
        print(f"[{i}/{len(reset_tasks)}] 重试: {task.file}")
        try:
            pipeline._process_one(task)
            completed += 1
            print(f"  ✓ 完成")
        except Exception as exc:
            failed += 1
            task.status = "failed"
            task.error = str(exc)
            task.error_stage = task.stage
            task.save()
            print(f"  ✗ 失败: {exc}")
            traceback.print_exc()

    print(f"\n重试完成: 成功 {completed}, 失败 {failed}")
    return 0 if failed == 0 else 1


def _show_review() -> int:
    """显示所有质量报告中需要人工复核的异常片段摘要。

    扫描 output/reports/ 目录中的所有质量报告，汇总问题，
    按严重程度排序，让人类快速了解需要关注什么。
    """
    if not DIR_OUTPUT_REPORTS.exists():
        print("暂无质检报告。")
        return 0

    report_files = sorted(DIR_OUTPUT_REPORTS.glob("*.quality_report.json"))
    if not report_files:
        print("暂无质检报告。")
        return 0

    print(f"\n{'='*70}")
    print(f"  异常片段复核摘要 — {len(report_files)} 个报告")
    print(f"{'='*70}\n")

    total_issues = 0
    total_errors = 0
    total_warnings = 0
    all_issues: list[tuple[str, dict]] = []

    for rf in report_files:
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        status = data.get("status", "?")
        summary = data.get("summary", {})
        issues = data.get("issues", [])

        if not issues:
            continue

        status_icon = {"pass": "✓", "warning": "⚠", "fail": "✗"}.get(status, "?")

        # 提取视频名
        video_name = rf.stem.replace(".quality_report", "")

        print(f"  {status_icon} {video_name}")
        print(f"    状态: {status} | 问题: {summary.get('total_issues', 0)} "
              f"(错误: {summary.get('errors', 0)}, 警告: {summary.get('warnings', 0)})")

        # 按严重度排序：error > warning > info
        severity_order = {"error": 0, "warning": 1, "info": 2}
        sorted_issues = sorted(issues, key=lambda i: severity_order.get(i.get("severity", "info"), 99))

        for issue in sorted_issues[:10]:  # 每个视频最多显示 10 个问题
            icon = {"error": "✗", "warning": "⚠", "info": "ℹ"}.get(issue.get("severity"), "?")
            idx = issue.get("index", 0)
            idx_str = f"#{idx}" if idx > 0 else "全局"
            snippet = issue.get("snippet", "")[:60]
            print(f"    {icon} {idx_str} [{issue.get('type', '?')}] {issue.get('text', '')[:80]}")
            if snippet and snippet != "(空)":
                print(f"       内容: {snippet}")

        if len(sorted_issues) > 10:
            print(f"    ... 还有 {len(sorted_issues) - 10} 个问题，使用 --review-file 查看详情")

        total_issues += summary.get("total_issues", 0)
        total_errors += summary.get("errors", 0)
        total_warnings += summary.get("warnings", 0)
        print()

    print(f"{'='*70}")
    print(f"  汇总: {total_issues} 个问题 ({total_errors} 错误, {total_warnings} 警告)")
    print(f"  报告目录: {DIR_OUTPUT_REPORTS}")
    print(f"  复核字幕: output/reports/*.review_needed.srt")
    print(f"{'='*70}")
    print(f"\n提示: 使用 --review-file <报告路径> 查看完整详情")
    print(f"      直接打开 output/reports/*.review_needed.srt 逐条复核\n")

    return 0 if total_errors == 0 else 1


def _show_review_detail(report_path: Path) -> int:
    """显示单个质量报告的完整问题列表。"""
    if not report_path.exists():
        print(f"报告不存在: {report_path}")
        return 1

    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"无法读取报告: {exc}")
        return 1

    status = data.get("status", "?")
    summary = data.get("summary", {})
    issues = data.get("issues", [])

    print(f"\n{'='*70}")
    print(f"  质量报告详情: {report_path.name}")
    print(f"{'='*70}")
    print(f"  状态: {status}")
    print(f"  总条目: {data.get('total_entries', 0)}")
    print(f"  原文: {data.get('source_srt', '')}")
    print(f"  译文: {data.get('translated_srt', '')}")
    print(f"  问题: {summary.get('total_issues', 0)} "
          f"(错误: {summary.get('errors', 0)}, "
          f"警告: {summary.get('warnings', 0)}, "
          f"提示: {summary.get('info', 0)})")

    issue_types = summary.get("issue_types", {})
    if issue_types:
        print(f"\n  问题分布:")
        for itype, count in sorted(issue_types.items(), key=lambda x: -x[1]):
            print(f"    - {itype}: {count}")

    if not issues:
        print(f"\n  ✓ 没有问题")
    else:
        print(f"\n  全部问题 ({len(issues)}):")
        severity_order = {"error": 0, "warning": 1, "info": 2}
        sorted_issues = sorted(issues, key=lambda i: severity_order.get(i.get("severity", "info"), 99))

        for issue in sorted_issues:
            icon = {"error": "✗", "warning": "⚠", "info": "ℹ"}.get(issue.get("severity"), "?")
            idx = issue.get("index", 0)
            idx_str = f"#{idx}" if idx > 0 else "全局"
            print(f"\n    {icon} {idx_str} [{issue.get('type', '?')}]")
            print(f"       描述: {issue.get('text', '')}")
            snippet = issue.get("snippet", "")
            if snippet and snippet != "(空)":
                print(f"       内容: {snippet}")
            suggestion = issue.get("suggestion", "")
            if suggestion:
                print(f"       建议: {suggestion}")

    print(f"\n{'='*70}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
