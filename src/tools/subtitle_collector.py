#!/usr/bin/env python3
"""
subtitle_collector.py — 字幕样本收集器（支持网络+本地双模式）

用途：
    收集多语种、多类型电影的人工翻译字幕样本，用于后续风格分析。

两种模式：
    1. 网络模式（--download）：从 OpenSubtitles 下载（需 API Key，限流）
    2. 本地模式（默认）：整理你已有的字幕文件到标准目录结构

目录结构：
    data/reference_subtitles/
    ├── french/
    │   ├── drama/
    │   │   ├── The_Intouchables_2011.fre.srt
    │   │   └── Amelie_2001.fre.srt
    │   └── action/
    └── japanese/
        └── anime/
            └── Spirited_Away_2001.jpn.srt

本地模式用法（推荐）：
    # 1. 把已有字幕放到任意目录，如 C:/Users/You/Downloads/subtitles/
    # 2. 运行整理：
    python subtitle_collector.py --local-dir "C:/Users/You/Downloads/subtitles" --lang fre --genre drama
    # 3. 脚本会自动按语言/类型分类，复制到 data/reference_subtitles/

网络模式用法（备选）：
    # 1. 在 https://www.opensubtitles.com 注册获取 API Key
    # 2. 设置环境变量：export OPENSUBTITLES_API_KEY="your_key"
    # 3. 运行：
    python subtitle_collector.py --download --lang fre,spa,jpn --genre drama --count 5

双语字幕配对：
    本地模式下，脚本会自动识别同名但不同语言后缀的文件对：
    - 英文原文: Movie.eng.srt / Movie.en.srt
    - 目标译文: Movie.fre.srt / Movie.fr.srt
    配对的文件会放到 bilingual/ 子目录中。
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# ── 项目根目录（工具脚本独立运行）──────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "reference_subtitles"
API_BASE = "https://api.opensubtitles.com/api/v1"

# 语言后缀映射（用于识别文件语言）
LANG_SUFFIXES = {
    "eng": "english", "en": "english", "english": "english",
    "fre": "french", "fra": "french", "fr": "french", "french": "french",
    "spa": "spanish", "es": "spanish", "spanish": "spanish",
    "jpn": "japanese", "ja": "japanese", "japanese": "japanese",
    "ger": "german", "deu": "german", "de": "german", "german": "german",
    "ita": "italian", "it": "italian", "italian": "italian",
    "kor": "korean", "ko": "korean", "korean": "korean",
    "chi": "chinese", "zho": "chinese", "zh": "chinese", "chinese": "chinese",
    "rus": "russian", "ru": "russian", "russian": "russian",
    "por": "portuguese", "pt": "portuguese", "portuguese": "portuguese",
    "dut": "dutch", "nld": "dutch", "nl": "dutch", "dutch": "dutch",
    "tur": "turkish", "tr": "turkish", "turkish": "turkish",
    "pol": "polish", "pl": "polish", "polish": "polish",
    "swe": "swedish", "sv": "swedish", "swedish": "swedish",
    "ara": "arabic", "ar": "arabic", "arabic": "arabic",
    "hin": "hindi", "hi": "hindi", "hindi": "hindi",
    "tha": "thai", "th": "thai", "thai": "thai",
    "vie": "vietnamese", "vi": "vietnamese", "vietnamese": "vietnamese",
}

SOURCE_LANGS = {"english", "eng", "en"}


def _lang_dir_name(suffix: str) -> str:
    """将文件后缀转为目录名。"""
    return LANG_SUFFIXES.get(suffix.lower(), suffix.lower())


def _detect_lang_from_filename(filename: str) -> str | None:
    """从文件名检测语言后缀。例: Movie.fre.srt → 'french'"""
    name_lower = filename.lower()
    # 匹配 .xxx.srt 或 .xxx.ass 等
    m = re.search(r'\.([a-z]{2,6})\.(srt|ass|ssa|sub)$', name_lower)
    if m:
        suffix = m.group(1)
        return LANG_SUFFIXES.get(suffix)
    return None


def _is_source_lang(filename: str) -> bool:
    """判断是否为原文（英语）字幕。"""
    name_lower = filename.lower()
    m = re.search(r'\.([a-z]{2,6})\.(srt|ass|ssa|sub)$', name_lower)
    if m:
        return m.group(1) in SOURCE_LANGS
    return False


# ── 本地模式：整理已有字幕 ──────────────────────────────────────────────────


def scan_local_subtitles(local_dir: Path, output_dir: Path,
                         target_langs: list[str], genres: list[str]) -> dict[str, Any]:
    """扫描本地字幕文件，按语言/类型分类整理到输出目录。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {"total_scanned": 0, "copied": 0, "paired": 0, "skipped": 0, "by_lang": {}}

    # 收集所有字幕文件
    srt_files = []
    for ext in ("*.srt", "*.ass", "*.ssa", "*.sub"):
        srt_files.extend(local_dir.rglob(ext))

    if not srt_files:
        print(f"WARNING: 在 {local_dir} 中未找到字幕文件。")
        return stats

    print(f"扫描到 {len(srt_files)} 个字幕文件。")

    # 按基础名称分组（不含语言后缀）
    groups: dict[str, list[Path]] = {}
    for f in srt_files:
        base = re.sub(r'\.([a-z]{2,6})\.(srt|ass|ssa|sub)$', '', f.name, flags=re.I)
        groups.setdefault(base, []).append(f)

    # 处理每组
    for base_name, files in groups.items():
        # 分离原文和译文
        source_files = [f for f in files if _is_source_lang(f.name)]
        target_files = [f for f in files if not _is_source_lang(f.name)]

        for f in target_files:
            lang = _detect_lang_from_filename(f.name)
            if not lang:
                continue
            if target_langs and lang not in [l.lower() for l in target_langs]:
                stats["skipped"] += 1
                continue

            stats["total_scanned"] += 1
            stats["by_lang"][lang] = stats["by_lang"].get(lang, 0) + 1

            # 确定类型（如果文件名包含类型关键词）
            genre = _detect_genre_from_filename(f.name) or (genres[0] if genres else "unknown")

            # 创建目录
            lang_folder = output_dir / _lang_dir_name(lang) / genre
            lang_folder.mkdir(parents=True, exist_ok=True)

            dest = lang_folder / f.name
            if dest.exists():
                print(f"  已存在，跳过: {dest.name}")
                stats["skipped"] += 1
                continue

            shutil.copy2(f, dest)
            print(f"  ✓ 复制: {f.name} → {dest}")
            stats["copied"] += 1

            # 如果有对应的原文文件，也复制到 bilingual 子目录
            if source_files:
                bilingual_dir = lang_folder / "bilingual"
                bilingual_dir.mkdir(parents=True, exist_ok=True)
                for src in source_files:
                    src_dest = bilingual_dir / src.name
                    if not src_dest.exists():
                        shutil.copy2(src, src_dest)
                dest_bilingual = bilingual_dir / f.name
                if not dest_bilingual.exists():
                    shutil.copy2(f, dest_bilingual)
                stats["paired"] += 1

    return stats


def _detect_genre_from_filename(filename: str) -> str | None:
    """从文件名推测电影类型。"""
    name_lower = filename.lower()
    genre_keywords = {
        "drama": ["drama"],
        "action": ["action", "adventure"],
        "comedy": ["comedy", "funny"],
        "animation": ["animation", "anime", "cartoon"],
        "horror": ["horror", "thriller", "scary"],
        "scifi": ["sci.fi", "science", "fiction", "space"],
        "romance": ["romance", "romantic", "love"],
        "documentary": ["documentary", "doc"],
    }
    for genre, keywords in genre_keywords.items():
        for kw in keywords:
            if kw in name_lower:
                return genre
    return None


# ── 网络模式：OpenSubtitles API ──────────────────────────────────────────────


def _api_key() -> str:
    key = os.environ.get("OPENSUBTITLES_API_KEY", "").strip()
    if not key:
        print("ERROR: 未设置 OPENSUBTITLES_API_KEY 环境变量。")
        print("请访问 https://www.opensubtitles.com 注册并获取 API Key。")
        print("然后设置: export OPENSUBTITLES_API_KEY='your_key'")
        sys.exit(1)
    return key


def _api_request(path: str, method: str = "GET", data: dict | None = None) -> dict:
    url = f"{API_BASE}{path}"
    headers = {
        "Api-Key": _api_key(),
        "Content-Type": "application/json",
        "User-Agent": "CineSubStudio/1.0",
    }
    body = json.dumps(data).encode("utf-8") if data else None
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 401:
            print("ERROR: API Key 无效。请检查 OPENSUBTITLES_API_KEY。")
            sys.exit(1)
        elif exc.code == 429:
            print("WARNING: 达到 API 速率限制（每日 20 次）。请明日再试。")
            return {"error": "rate_limit"}
        else:
            print(f"ERROR: API HTTP {exc.code}: {exc.reason}")
            return {"error": f"http_{exc.code}"}
    except Exception as exc:
        print(f"ERROR: API 请求失败: {exc}")
        return {"error": str(exc)}


def _search_subtitles(lang: str, query: str = "", genre: str = "", year: int = 0, limit: int = 10) -> list[dict]:
    params = [f"languages={lang}", f"order_by=ratings", f"order_direction=desc"]
    if query:
        params.append(f"query={query}")
    if genre:
        params.append(f"genre={genre}")
    if year:
        params.append(f"year={year}")
    path = f"/subtitles?{'&'.join(params)}"
    resp = _api_request(path)
    if "error" in resp:
        return []
    data = resp.get("data", [])
    candidates = []
    for item in data:
        attrs = item.get("attributes", {})
        if attrs.get("ai_translated", False) or attrs.get("machine_translated", False):
            continue
        candidates.append(item)
    return candidates[:limit]


def _download_subtitle(file_id: int) -> bytes | None:
    resp = _api_request("/download", method="POST", data={"file_id": file_id})
    if "error" in resp:
        return None
    link = resp.get("link", "")
    if not link:
        return None
    try:
        req = Request(link, headers={"User-Agent": "CineSubStudio/1.0"})
        with urlopen(req, timeout=60) as resp_file:
            content = resp_file.read()
    except Exception as exc:
        print(f"  下载失败: {exc}")
        return None

    try:
        with zipfile.ZipFile(BytesIO(content)) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".srt"):
                    return zf.read(name)
    except zipfile.BadZipFile:
        pass

    try:
        return gzip.decompress(content)
    except Exception:
        pass

    return content


def _sanitize_filename(name: str) -> str:
    keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_- ")
    return "".join(c if c in keep else "_" for c in name).strip().replace(" ", "_")


def download_from_opensubtitles(
    languages: list[str],
    genres: list[str],
    count_per_lang_genre: int,
    output_dir: Path,
    queries: list[str],
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0, "by_lang": {}}

    for lang in languages:
        lang_dir = output_dir / _lang_dir_name(lang)
        stats["by_lang"][lang] = {"total": 0, "success": 0}
        for genre in genres:
            genre_dir = lang_dir / genre
            genre_dir.mkdir(parents=True, exist_ok=True)

            print(f"\n[{lang}] {genre} — 搜索中...")
            candidates = _search_subtitles(lang, genre=genre, limit=count_per_lang_genre * 3)
            if not candidates:
                for q in queries[:3]:
                    candidates = _search_subtitles(lang, query=q, limit=count_per_lang_genre * 2)
                    if candidates:
                        break

            if not candidates:
                print(f"  未找到 {lang}/{genre} 的字幕。")
                continue

            collected = 0
            for item in candidates:
                if collected >= count_per_lang_genre:
                    break
                attrs = item.get("attributes", {})
                title = attrs.get("feature_details", {}).get("title", "unknown")
                year = attrs.get("feature_details", {}).get("year", 0)
                file_id = attrs.get("files", [{}])[0].get("file_id", 0)
                if not file_id:
                    continue

                safe_name = _sanitize_filename(f"{title}_{year}")
                srt_path = genre_dir / f"{safe_name}.{lang}.srt"
                if srt_path.exists():
                    print(f"  已存在，跳过: {srt_path.name}")
                    stats["skipped"] += 1
                    collected += 1
                    continue

                print(f"  下载: {title} ({year}) ...")
                content = _download_subtitle(file_id)
                if content is None:
                    stats["failed"] += 1
                    continue

                try:
                    text = content.decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        text = content.decode("latin-1")
                    except UnicodeDecodeError:
                        text = content.decode("utf-8", errors="replace")

                srt_path.write_text(text, encoding="utf-8")
                print(f"  ✓ 保存: {srt_path.name}")
                stats["success"] += 1
                collected += 1
                stats["total"] += 1
                stats["by_lang"][lang]["success"] += 1
                time.sleep(1.5)

    return stats


# ── 主流程 ───────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="字幕样本收集器（本地+网络双模式）")
    parser.add_argument(
        "--local-dir", type=str, default="",
        help="本地字幕目录路径。指定后进入本地模式，整理已有字幕。"
    )
    parser.add_argument(
        "--download", action="store_true",
        help="启用网络模式，从 OpenSubtitles 下载字幕（需 API Key）"
    )
    parser.add_argument(
        "--lang", default="",
        help="目标语种，逗号分隔。例: fre,spa,jpn。本地模式时用于筛选。"
    )
    parser.add_argument(
        "--genre", default="drama,action,comedy,animation",
        help="电影类型，逗号分隔。默认: drama,action,comedy,animation"
    )
    parser.add_argument(
        "--count", type=int, default=5,
        help="网络模式：每种语种+类型下载数量。默认: 5"
    )
    parser.add_argument(
        "--output", type=str, default=str(DEFAULT_OUTPUT_DIR),
        help=f"输出目录。默认: {DEFAULT_OUTPUT_DIR}"
    )
    parser.add_argument(
        "--query", type=str, default="",
        help="网络模式：额外搜索关键词，逗号分隔"
    )
    args = parser.parse_args()

    output_dir = Path(args.output).resolve()
    genres = [g.strip() for g in args.genre.split(",")]
    languages = [l.strip() for l in args.lang.split(",")] if args.lang else []
    queries = [q.strip() for q in args.query.split(",")] if args.query else []

    print("=" * 60)
    print("CineSub Studio — 字幕样本收集器")
    print("=" * 60)

    if args.local_dir:
        # 本地模式
        local_dir = Path(args.local_dir).resolve()
        if not local_dir.exists():
            print(f"ERROR: 本地目录不存在: {local_dir}")
            return 1
        print(f"模式: 本地整理")
        print(f"来源: {local_dir}")
        print(f"目标: {output_dir}")
        print(f"筛选语种: {languages or '全部'}")
        print(f"默认类型: {genres}")
        print("-" * 60)
        stats = scan_local_subtitles(local_dir, output_dir, languages, genres)
    elif args.download:
        # 网络模式
        if not languages:
            print("ERROR: 网络模式需要指定 --lang")
            return 1
        print(f"模式: 网络下载（OpenSubtitles）")
        print(f"目标: {output_dir}")
        print(f"语种: {languages}")
        print(f"类型: {genres}")
        print(f"每种组合: {args.count} 部")
        print("-" * 60)
        stats = download_from_opensubtitles(languages, genres, args.count, output_dir, queries)
    else:
        print("ERROR: 请指定模式：--local-dir <path> 或 --download")
        print("")
        print("示例：")
        print('  本地模式: python subtitle_collector.py --local-dir "C:/Users/Me/subs" --lang fre')
        print('  网络模式: python subtitle_collector.py --download --lang fre,spa --count 5')
        return 1

    print("\n" + "=" * 60)
    print("收集完成")
    print("=" * 60)
    if args.local_dir:
        print(f"扫描总数: {stats['total_scanned']}")
        print(f"成功复制: {stats['copied']}")
        print(f"双语配对: {stats['paired']}")
        print(f"跳过: {stats['skipped']}")
    else:
        print(f"总计下载: {stats['success']}")
        print(f"已存在跳过: {stats['skipped']}")
        print(f"失败: {stats['failed']}")
    for lang, count in stats.get("by_lang", {}).items():
        if isinstance(count, dict):
            print(f"  [{lang}] 成功: {count.get('success', 0)}")
        else:
            print(f"  [{lang}] 成功: {count}")
    print("-" * 60)
    print("提示：接下来运行 subtitle_analyzer.py 分析这些字幕。")
    print(f'  .venv\Scripts\python.exe -B src\tools\subtitle_analyzer.py "{output_dir}" --recursive')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
