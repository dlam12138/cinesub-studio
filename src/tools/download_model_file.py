from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Directly download a Hugging Face model file with resume support.")
    parser.add_argument("url")
    parser.add_argument("output")
    parser.add_argument("--retries", type=int, default=20)
    args = parser.parse_args()

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)

    for attempt in range(1, args.retries + 1):
        try:
            download(args.url, output)
            print(f"Downloaded: {output}", flush=True)
            return 0
        except Exception as exc:
            print(f"Attempt {attempt}/{args.retries} failed: {exc}", file=sys.stderr, flush=True)
            if attempt == args.retries:
                raise
            time.sleep(min(10, attempt))

    return 1


def download(url: str, output: Path) -> None:
    current_size = output.stat().st_size if output.exists() else 0
    request = urllib.request.Request(url)
    if current_size:
        request.add_header("Range", f"bytes={current_size}-")
        print(f"Resuming at {current_size:,} bytes", flush=True)

    with urllib.request.urlopen(request, timeout=60) as response:
        status = getattr(response, "status", 200)
        if current_size and status == 200:
            print("Server did not resume; restarting from zero.", flush=True)
            current_size = 0

        mode = "ab" if current_size and status == 206 else "wb"
        total = response.headers.get("Content-Length")
        expected = current_size + int(total) if total else None
        last_print = time.time()

        with output.open(mode) as file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)

                now = time.time()
                if now - last_print >= 3:
                    size = output.stat().st_size
                    if expected:
                        percent = size / expected * 100
                        print(f"{size / 1024 / 1024:.1f} MB / {expected / 1024 / 1024:.1f} MB ({percent:.1f}%)", flush=True)
                    else:
                        print(f"{size / 1024 / 1024:.1f} MB", flush=True)
                    last_print = now

    if output.stat().st_size == 0:
        raise urllib.error.URLError("Downloaded file is empty.")


if __name__ == "__main__":
    raise SystemExit(main())
