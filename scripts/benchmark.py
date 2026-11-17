"""Sabit link listesiyle sağlayıcı/süre ölçüm araci.

Kullanım:
    .\\.venv\\Scripts\\python.exe scripts\benchmark.py links.txt [rapor.json]

`links.txt` satır başına bir bağlantı içerir. `#` ile başlayan satırlar
yorum olarak sayılır. Her bağlantı `download_media` üzerinden indirilir ve
sağlayıcı bazlı süre/başarı istatistikleri raporlanır.

Botun Discord'a bağlanmasına ihtiyacı yoktur; yalnızca indirme çekirdeği
çalıştırılır.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from media_bot.download import download_media
from media_bot.links import PostLink, find_links

LOG = logging.getLogger("benchmark")


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (k - f) * (ordered[c] - ordered[f])


async def _bench_one(link: PostLink, max_bytes: int, index: int) -> dict:
    start = time.monotonic()
    result: dict = {
        "index": index,
        "url": link.url,
        "platform": link.platform,
        "ok": False,
        "provider": None,
        "elapsed_ms": 0,
        "files": 0,
        "bytes": 0,
        "error": None,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="bench-") as tmp:
            files, provider = await download_media(
                link, Path(tmp), max_bytes,
                cookie_file=None, cobalt_url=None, cobalt_key=None,
                gallery_rs_path=None, user_id=None,
            )
        result["ok"] = True
        result["provider"] = provider
        result["files"] = len(files)
        result["bytes"] = sum(p.stat().st_size for p in files if p.is_file())
    except Exception as exc:  # noqa: BLE001 - raporlama amacıyla yakala
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["elapsed_ms"] = int((time.monotonic() - start) * 1000)
    return result


def _summarise(results: list[dict]) -> dict:
    by_provider: dict[str, list[float]] = {}
    by_platform: dict[str, list[float]] = {}
    ok = 0
    fail = 0
    for r in results:
        if r["ok"]:
            ok += 1
            key = r["provider"] or "unknown"
            by_provider.setdefault(key, []).append(r["elapsed_ms"])
            by_platform.setdefault(r["platform"], []).append(r["elapsed_ms"])
        else:
            fail += 1
    return {
        "total": len(results),
        "ok": ok,
        "fail": fail,
        "by_provider": {
            name: {
                "count": len(vals),
                "p50_ms": round(_percentile(vals, 50), 1),
                "p95_ms": round(_percentile(vals, 95), 1),
                "min_ms": round(min(vals), 1),
                "max_ms": round(max(vals), 1),
            }
            for name, vals in sorted(by_provider.items())
        },
        "by_platform": {
            name: {
                "count": len(vals),
                "p50_ms": round(_percentile(vals, 50), 1),
                "p95_ms": round(_percentile(vals, 95), 1),
                "min_ms": round(min(vals), 1),
                "max_ms": round(max(vals), 1),
            }
            for name, vals in sorted(by_platform.items())
        },
    }


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sağlayıcı/süre ölçüm aracı")
    parser.add_argument("links_file", help="Satır başına bir bağlantı içeren dosya")
    parser.add_argument("report", nargs="?", default="benchmark-report.json",
                        help="Çıkış JSON dosyası (varsayılan: benchmark-report.json)")
    parser.add_argument("--max-bytes", type=int, default=50 * 1024 * 1024,
                        help="Yükleme sınırı (bayt), varsayılan 50 MB")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    links_file = Path(args.links_file)
    if not links_file.is_file():
        LOG.error("Bağlantı dosyası bulunamadı: %s", links_file)
        return 2

    links: list[PostLink] = []
    for line in links_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        found = find_links(line, max_links=1)
        if found:
            links.extend(found)
        else:
            LOG.warning("Bağlantı tanınamadı: %s", line)

    if not links:
        LOG.error("Ölçülebilir bağlantı bulunamadı.")
        return 2

    LOG.info("%d bağlantı ölçülecek", len(links))
    results = [await _bench_one(link, args.max_bytes, i)
               for i, link in enumerate(links, 1)]
    summary = _summarise(results)
    report = {"summary": summary, "results": results}

    out_path = Path(args.report)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    LOG.info("Tamam: %d başarılı / %d başarısız", summary["ok"], summary["fail"])
    for provider, stats in summary["by_provider"].items():
        LOG.info("  %-18s n=%-3d p50=%-6s p95=%-6s min=%-6s max=%-6s ms",
                 provider, stats["count"], stats["p50_ms"], stats["p95_ms"],
                 stats["min_ms"], stats["max_ms"])
    LOG.info("Rapor: %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
