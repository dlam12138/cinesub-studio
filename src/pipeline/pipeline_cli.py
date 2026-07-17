from __future__ import annotations

import argparse

from segment_asr_routing_integration import DEFAULT_APPLY_WINDOW_SECONDS, DEFAULT_MAX_APPLY_WINDOWS


def build_pipeline_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CineSub Studio batch subtitle pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  .\\.venv\\Scripts\\python.exe -B src\\pipeline\\batch_worker.py --input input --model large-v3 --device cuda\n"
            "  .\\.venv\\Scripts\\python.exe -B src\\pipeline\\batch_worker.py --input input --model small --no-translate\n"
            "  .\\.venv\\Scripts\\python.exe -B src\\pipeline\\batch_worker.py --scan\n"
            "  .\\.venv\\Scripts\\python.exe -B src\\pipeline\\batch_worker.py --status"
        ),
    )
    parser.add_argument("--input", default="input", help="Input media directory (default: input/)")
    parser.add_argument("--output-dir", default="output", help="Output root directory (default: output/)")
    parser.add_argument("--model-dir", default="models", help="Model directory (default: models/)")
    parser.add_argument("--work-dir", default="work", help="Work directory (default: work/)")
    parser.add_argument("--model", default="large-v3", help="Whisper model name (default: large-v3)")
    parser.add_argument("--device", default="auto", choices=["cpu", "cuda", "auto"], help="Compute device")
    parser.add_argument("--compute-type", default=None, help="Compute type, e.g. int8 or float16")
    parser.add_argument("--language", default=None, help="Source language code; omit to auto-detect")
    parser.add_argument("--beam-size", type=int, default=5, help="Beam size")
    parser.add_argument("--no-vad", action="store_true", help="Disable VAD")
    parser.add_argument("--local-files-only", action="store_true", help="Use local model files only")
    parser.add_argument("--asr-experiment-mode", choices=["off", "dry_run", "apply"], default=None, help="ASR candidate mode; default comes from Language Profile or off.")
    parser.add_argument("--asr-candidate-id", default=None, help="Registered ASR candidate id.")
    parser.add_argument("--no-translate", action="store_true", help="Disable translation")
    parser.add_argument("--provider", default=None, help="Provider ID from config/providers.local.json")
    parser.add_argument("--language-profile", default=None, help="Language Profile ID")
    parser.add_argument("--api-provider", default=None, choices=["openai-compatible", "anthropic"], help="LLM API provider")
    parser.add_argument("--api-base", default=None, help="LLM API base URL")
    parser.add_argument("--api-key", default=None, help="LLM API key")
    parser.add_argument("--llm-model", default=None, help="LLM model name")
    parser.add_argument(
        "--translation-quality-model", default=None,
        help="Optional model for preview repair candidates and judging.",
    )
    parser.add_argument("--target-language", default="zh-CN", help="Translation target language")
    parser.add_argument("--translation-batch-size", type=int, default=20, help="Translation batch size")
    parser.add_argument("--translation-temperature", type=float, default=0.2, help="Translation temperature")
    parser.add_argument("--translation-mode", default="bilingual", choices=["bilingual", "translated"], help="Translation output mode")
    parser.add_argument("--context-window", type=int, default=3, help="Translation context window")
    parser.add_argument("--translation-prompt", default="", help="Custom translation prompt")
    parser.add_argument("--translation-reliability-mode", choices=["off", "preview"], default=None, help="Translation recovery mode; default comes from Language Profile or off.")
    parser.add_argument("--translation-max-extra-requests", type=int, default=None, help="Shared preview recovery/repair request budget (0-50).")
    parser.add_argument("--subtitle-formats", default=None, help="Subtitle output formats. ASS is reserved, e.g. srt,ass.")
    parser.add_argument("--ass-style-id", default=None, help="Reserved ASS style id. No .ass file is generated.")
    parser.add_argument("--segment-asr-routing", default="off", choices=["off", "dry_run", "apply"], help="Experimental segment ASR routing mode. Defaults to off.")
    parser.add_argument("--segment-routing-confidence-threshold", type=float, default=0.70, help="Confidence threshold for segment routing dry-run analysis.")
    parser.add_argument("--segment-routing-min-segments", type=int, default=1, help="Minimum segment count for usable segment routing evidence.")
    parser.add_argument("--segment-routing-strict", action="store_true", help="Fail instead of falling back when experimental segment routing fails.")
    parser.add_argument("--segment-routing-window-seconds", type=float, default=DEFAULT_APPLY_WINDOW_SECONDS, help="Apply-only full-coverage routing window length in seconds.")
    parser.add_argument("--segment-routing-max-windows", type=int, default=DEFAULT_MAX_APPLY_WINDOWS, help="Apply-only maximum routed windows before fallback or strict failure.")
    parser.add_argument("--segment-routing-allow-large-run", action="store_true", help="Allow apply to exceed --segment-routing-max-windows.")
    parser.add_argument("--max-retries", type=int, default=3, help="Maximum retries")
    parser.add_argument("--no-skip-completed", action="store_true", help="Reprocess completed tasks")
    parser.add_argument("--no-move-completed", action="store_true", help="Do not move completed inputs to archive/")
    parser.add_argument("--scan", action="store_true", help="Scan pending files without processing")
    parser.add_argument("--status", action="store_true", help="Show task status")
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed tasks")
    parser.add_argument("--review", action="store_true", help="Show review summary from quality reports")
    parser.add_argument("--review-file", default=None, help="Show details for one quality report")
    return parser
