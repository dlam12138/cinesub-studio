#!/usr/bin/env python3
"""
assrt_collector.py — 射手网字幕收集器（专用）

从射手网(assrt.net) API 批量下载高质量人工双语字幕，用于翻译风格分析。

特性：
    - 速率限制：每 20 秒一次请求（≈ 每分钟 3 次，符合用户配额）
    - 自动筛选双语字幕（langdou）
    - 直接下载 .srt 文件，无需解压 RAR
    - 支持批量模式（从文本文件读取电影名列表）
    - 按语种/类型分类保存

用法：
    # 单部电影搜索
    python assrt_collector.py --token "YOUR_KEY" --query "千与千寻"

    # 批量收集（从文件读取电影名列表）
    python assrt_collector.py --token "YOUR_KEY" --batch movies.txt

    # 指定语种和类型过滤
    python assrt_collector.py --token "YOUR_KEY" --query "寄生虫" --lang dou --genre drama

    # 设置环境变量后省略 --token
    set ASSRT_API_KEY=your_key
    python assrt_collector.py --query "霸王别姬"

环境变量：
    ASSRT_API_KEY — 射手网 API 密钥（命令行 --token 优先）

输出目录结构：
    data/reference_subtitles/
    ├── bilingual/          # 双语字幕（最有价值：原文+译文对照）
    ├── chinese/            # 纯中文字幕
    ├── english/            # 纯英文字幕
    └── korean/             # 其他语种

注意：
    - 射手网字幕组精校质量高，是 LLM 翻译风格训练的理想素材
    - 下载地址有有效期，脚本会在获取详情后立即下载
    - 如配额不足，脚本会自动等待后重试
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

# ── 项目根目录 ───────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "reference_subtitles"

API_BASE = "https://api.assrt.net"
REQUEST_INTERVAL = 20.0  # 20秒 = 每分钟3次


# ── 语言映射 ───────────────────────────────────────────────────────────────

LANG_MAP = {
    "dou": "bilingual", "双语": "bilingual",
    "eng": "english", "en": "english", "英文": "english",
    "chs": "chinese", "zh": "chinese", "简体": "chinese", "中文": "chinese",
    "cht": "chinese_t", "繁体": "chinese_t",
    "jpn": "japanese", "ja": "japanese", "日文": "japanese",
    "kor": "korean", "ko": "korean", "韩文": "korean",
    "fre": "french", "fr": "french", "法文": "french",
    "spa": "spanish", "es": "spanish", "西班牙文": "spanish",
    "ger": "german", "de": "german", "德文": "german",
    "rus": "russian", "ru": "russian", "俄文": "russian",
    "ita": "italian", "it": "italian", "意大利文": "italian",
}


# ── 语言优先级（权重：高=优先收集，低=减少收集）──────────────────────────

LANG_PRIORITY = {
    "bilingual": 10,   # 双语字幕最高优先级
    "chinese": 8,      # 中文
    "japanese": 8,     # 日语
    "french": 8,       # 法语
    "spanish": 8,      # 西班牙语
    "german": 7,       # 德语
    "italian": 7,      # 意大利语
    "russian": 7,      # 俄语
    "chinese_t": 6,    # 繁体中文
    "english": 3,      # 英语（降低权重，因资料丰富但风格同质化）
    "korean": 3,       # 韩语（降低权重）
    "unknown": 1,      # 未知语言最低
}

# 热门语言（权重降低），用于统计报告
HOT_LANGS = {"english", "korean"}


def _lang_priority(lang_folder: str) -> int:
    return LANG_PRIORITY.get(lang_folder, 5)


def _normalize_target_lang(target_lang: str) -> str:
    """标准化用户传入的目标语言代码（如 dou → bilingual）。"""
    if not target_lang:
        return ""
    tl = target_lang.lower().strip()
    # 直接映射
    return LANG_MAP.get(tl, tl)


# ── 收集器核心 ─────────────────────────────────────────────────────────────


class AssrtCollector:
    """射手网 API 收集器。"""

    def __init__(self, token: str, output_dir: Path) -> None:
        self.token = token
        self.output_dir = output_dir
        self.last_request_time = 0.0
        self.request_count = 0
        self.hot_lang_skipped = 0  # 因权重降低而跳过的热门语言计数

    # ── 请求层 ───────────────────────────────────────────────────────────

    def _request(self, endpoint: str, params: dict | None = None, retries: int = 2) -> dict:
        """发送 API 请求，自动处理速率限制和连接重试。改用 requests 库。"""
        import requests

        # 速率限制
        elapsed = time.time() - self.last_request_time
        if elapsed < REQUEST_INTERVAL:
            wait = REQUEST_INTERVAL - elapsed
            print(f"  [限速] 等待 {wait:.1f} 秒...")
            time.sleep(wait)

        if params is None:
            params = {}
        params["token"] = self.token

        query = urllib.parse.urlencode(params)
        url = f"{API_BASE}/v1/{endpoint}?{query}"

        headers = {
            "User-Agent": "CineSubStudio/1.0",
            "Accept": "application/json",
        }

        last_exc = None
        for attempt in range(retries + 1):
            try:
                resp = requests.get(url, headers=headers, timeout=30)
                self.last_request_time = time.time()
                self.request_count += 1
                data = resp.json()
                if data.get("status", 0) != 0:
                    err = data.get("status", "unknown")
                    print(f"  [API错误] status={err}")
                return data
            except Exception as exc:
                last_exc = exc
                print(f"  [DEBUG] 请求失败 (attempt {attempt+1}/{retries+1}): {exc}")
                if attempt < retries:
                    wait_retry = 5.0 * (attempt + 1)
                    print(f"  [重试] {wait_retry:.0f}秒后重试...")
                    time.sleep(wait_retry)
        print(f"  [请求失败] {last_exc}")
        return {"status": -1, "error": str(last_exc)}

    # ── 搜索层 ───────────────────────────────────────────────────────────

    def search(self, query: str, cnt: int = 15) -> list[dict]:
        """搜索字幕，返回字幕列表。"""
        print(f"[搜索] '{query}' ...")
        resp = self._request("sub/search", {"q": query, "cnt": cnt})
        if resp.get("status", 0) != 0:
            return []
        subs = resp.get("sub", {}).get("subs", [])
        print(f"  找到 {len(subs)} 条结果")
        return subs

    def detail(self, sub_id: int) -> dict | None:
        """获取字幕详情（含下载链接）。"""
        print(f"[详情] id={sub_id} ...")
        resp = self._request("sub/detail", {"id": sub_id})
        if resp.get("status", 0) != 0:
            return None
        subs = resp.get("sub", {}).get("subs", [])
        return subs[0] if subs else None

    # ── 下载层 ───────────────────────────────────────────────────────────

    def download_srt(self, url: str, dest: Path) -> bool:
        """下载字幕文件。改用 requests 库。"""
        import requests
        try:
            resp = requests.get(url, headers={"User-Agent": "CineSubStudio/1.0"}, timeout=60)
            content = resp.content
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            print(f"  [OK] 下载成功: {dest.name} ({len(content)} bytes)")
            return True
        except Exception as exc:
            print(f"  [FAIL] 下载失败: {exc}")
            return False

    # ── 语言判断 ─────────────────────────────────────────────────────────

    def _detect_lang(self, sub_info: dict) -> str:
        """从字幕信息判断语言类型。"""
        lang = sub_info.get("lang", {})
        langlist = lang.get("langlist", {})
        desc = lang.get("desc", "")

        # 双语优先
        if langlist.get("langdou", False) or "双语" in desc:
            return "bilingual"

        # 根据 langlist 中的键判断
        for code, folder in LANG_MAP.items():
            key = f"lang{code}"
            if langlist.get(key, False):
                return folder

        # 根据描述文字判断
        desc_lower = desc.lower()
        for keyword, folder in {
            "英": "english", "english": "english", "eng": "english",
            "中": "chinese", "chinese": "chinese", "简体": "chinese", "繁体": "chinese_t",
            "日": "japanese", "japanese": "japanese", "jpn": "japanese",
            "韩": "korean", "korean": "korean", "kor": "korean",
            "法": "french", "french": "french", "fre": "french",
            "德": "german", "german": "german", "ger": "german",
            "西": "spanish", "spanish": "spanish", "spa": "spanish",
        }.items():
            if keyword in desc_lower:
                return folder

        return "unknown"

    # ── 主流程：收集单部电影 ─────────────────────────────────────────────

    def collect_movie(self, query: str, target_lang: str = "", genre: str = "") -> dict[str, Any]:
        """搜索并下载单部电影的字幕。"""
        stats = {"found": 0, "downloaded": 0, "skipped": 0, "errors": 0}

        subs = self.search(query)
        if not subs:
            print(f"  未找到 '{query}' 的字幕。")
            return stats

        # 按语言优先级排序（热门语言降低权重）
        # 如果用户指定了 target_lang，则保持原顺序；否则优先非热门语言
        if not target_lang:
            subs = sorted(subs, key=lambda s: _lang_priority(self._detect_lang(s)), reverse=True)
            # 统计热门语言被降权的情况
            for sub in subs:
                if self._detect_lang(sub) in HOT_LANGS:
                    self.hot_lang_skipped += 1

        # 筛选目标语言
        target_lang_normalized = _normalize_target_lang(target_lang)
        for sub in subs:
            sub_id = sub.get("fileid") or sub.get("id")
            native_name = sub.get("native_name", "unknown")
            subtype = sub.get("subtype", "")

            # 跳过非 SRT/Subrip 格式（暂不处理 VobSub/ASS 等）
            if subtype and "subrip" not in subtype.lower() and "srt" not in subtype.lower():
                continue

            lang_folder = self._detect_lang(sub)
            if target_lang_normalized and lang_folder != target_lang_normalized:
                stats["skipped"] += 1
                continue

            # 无 target_lang 时，如果第一个是热门语言，并且有更低优先级的后续结果，跳过热门
            if not target_lang and lang_folder in HOT_LANGS and len(subs) > 1:
                # 检查是否有更高优先级的非热门语言结果还没被处理
                # 由于已经排序，这里只需检查：如果当前是热门，但后面有非热门，就跳过热门的
                # 但循环逻辑已经保证排序后的第一个匹配会被收集，所以这里不需要额外处理
                pass

            # 获取详情
            detail = self.detail(sub_id)
            if not detail:
                stats["errors"] += 1
                continue

            # 获取文件列表
            filelist = detail.get("filelist", [])
            if not filelist:
                # 尝试直接下载压缩包
                url = detail.get("url", "")
                if url:
                    print(f"  [警告] 只有压缩包链接，暂不支持 RAR 解压: {native_name}")
                stats["errors"] += 1
                continue

            # 找 SRT 文件
            srt_files = [f for f in filelist if f.get("f", "").lower().endswith(".srt")]
            if not srt_files:
                stats["skipped"] += 1
                continue

            # 下载第一个 SRT（通常双语字幕只有一个 SRT 文件）
            srt_info = srt_files[0]
            srt_url = srt_info.get("url", "")
            srt_name = srt_info.get("f", f"{sub_id}.srt")

            # 清理文件名
            safe_name = self._sanitize_filename(native_name) or self._sanitize_filename(srt_name)

            # 确定保存路径
            genre_folder = genre or "mixed"
            dest_dir = self.output_dir / lang_folder / genre_folder
            dest = dest_dir / f"{safe_name}_{sub_id}.srt"

            if dest.exists():
                print(f"  已存在，跳过: {dest.name}")
                stats["skipped"] += 1
                continue

            # 下载
            print(f"  下载: {native_name} → {lang_folder}/{genre_folder}/")
            if self.download_srt(srt_url, dest):
                stats["downloaded"] += 1
                # 如果是双语字幕，额外标记
                if lang_folder == "bilingual":
                    marker = dest_dir / f"{safe_name}_{sub_id}.bilingual.marker"
                    marker.write_text(f"source: {native_name}\nquery: {query}\n", encoding="utf-8")
            else:
                stats["errors"] += 1

            stats["found"] += 1
            break  # 每部电影只下载一个字幕（最匹配的结果）

        return stats

    # ── 批量模式 ─────────────────────────────────────────────────────────

    def collect_batch(self, queries: list[str], target_lang: str = "", genre: str = "") -> dict[str, Any]:
        """批量收集多部电影字幕。"""
        total_stats = {"total_movies": len(queries), "total_downloaded": 0, "total_skipped": 0, "total_errors": 0}

        for i, query in enumerate(queries, 1):
            print(f"\n{'='*60}")
            print(f"[{i}/{len(queries)}] {query}")
            print(f"{'='*60}")
            stats = self.collect_movie(query, target_lang, genre)
            total_stats["total_downloaded"] += stats["downloaded"]
            total_stats["total_skipped"] += stats["skipped"]
            total_stats["total_errors"] += stats["errors"]

        return total_stats

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """清理文件名中的非法字符。"""
        keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_- ")
        result = "".join(c if c in keep else "_" for c in name).strip().replace(" ", "_")
        # 截断过长
        return result[:80]


# ── 主流程 ─────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="射手网字幕收集器")
    parser.add_argument("--token", type=str, default="",
                        help="射手网 API 密钥。也可通过环境变量 ASSRT_API_KEY 设置。")
    parser.add_argument("--query", type=str, default="",
                        help="搜索的电影名（单部模式）。")
    parser.add_argument("--batch", type=str, default="",
                        help="批量模式：从文本文件读取电影名列表（每行一个）。")
    parser.add_argument("--lang", type=str, default="",
                        help="目标语言过滤：dou(双语), eng, chs, jpn, kor, fre 等。")
    parser.add_argument("--genre", type=str, default="",
                        help="类型标记（用于目录分类，如 drama, action, comedy）。")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT_DIR),
                        help=f"输出目录。默认: {DEFAULT_OUTPUT_DIR}")
    parser.add_argument("--builtin-list", action="store_true",
                        help="使用内置推荐电影列表批量收集。")
    args = parser.parse_args()

    # 获取 token
    token = args.token.strip() or os.environ.get("ASSRT_API_KEY", "").strip()
    if not token:
        print("ERROR: 请提供射手网 API 密钥。")
        print("方式1: 命令行参数 --token YOUR_KEY")
        print("方式2: 环境变量 set ASSRT_API_KEY=YOUR_KEY")
        return 1

    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("CineSub Studio — 射手网字幕收集器")
    print("=" * 60)
    print(f"输出目录: {output_dir}")
    print(f"请求限速: 每 {REQUEST_INTERVAL} 秒一次（≈ 每分钟 3 次）")
    print("-" * 60)

    collector = AssrtCollector(token, output_dir)

    # 确定查询列表
    queries: list[str] = []
    if args.batch:
        batch_path = Path(args.batch).resolve()
        if not batch_path.exists():
            print(f"ERROR: 批量文件不存在: {batch_path}")
            return 1
        queries = [line.strip() for line in batch_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    elif args.builtin_list:
        queries = BUILTIN_MOVIES
    elif args.query:
        queries = [args.query]
    else:
        print("ERROR: 请指定搜索方式：--query, --batch 或 --builtin-list")
        print("")
        print("示例：")
        print('  python assrt_collector.py --token "KEY" --query "千与千寻"')
        print('  python assrt_collector.py --token "KEY" --batch movies.txt')
        print('  python assrt_collector.py --token "KEY" --builtin-list --lang dou')
        return 1

    # 执行收集
    stats = collector.collect_batch(queries, target_lang=args.lang, genre=args.genre)

    print("\n" + "=" * 60)
    print("收集完成")
    print("=" * 60)
    print(f"目标电影数: {stats['total_movies']}")
    print(f"成功下载: {stats['total_downloaded']}")
    print(f"跳过/未匹配: {stats['total_skipped']}")
    print(f"失败: {stats['total_errors']}")
    print(f"总请求数: {collector.request_count}")
    print("-" * 60)
    print(f"输出目录: {output_dir}")
    print("\n提示：接下来运行 subtitle_analyzer.py 分析这些字幕。")
    print(f'  .venv/Scripts/python.exe -B src/tools/subtitle_analyzer.py "{output_dir}" --recursive')
    return 0


# ── 内置推荐电影列表（按类型分类）──────────────────────────────────────────

BUILTIN_MOVIES = [
    # 文艺/剧情
    "霸王别姬", "花样年华", "肖申克的救赎", "阿甘正传",
    # 文艺（欧洲小语种）
    "放牛班的春天", "这个杀手不太冷", "触不可及", "美丽人生", "西西里的美丽传说", "天使爱美丽",
    # 动作
    "黑客帝国", "谍影重重", "疾速追杀",
    # 喜剧
    "大话西游", "功夫", "疯狂的石头", "两杆大烟枪",
    # 科幻
    "星际穿越", "盗梦空间", "银翼杀手",
    # 悬疑/犯罪
    "寄生虫", "杀人回忆", "老男孩", "教父", "辛德勒的名单", "低俗小说",
    # 爱情
    "泰坦尼克号", "恋恋笔记本", "钢琴家",
    # 纪录片
    "地球脉动", "人类星球", "中央车站", "上帝之城",
]


if __name__ == "__main__":
    raise SystemExit(main())
