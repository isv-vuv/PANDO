"""Script to crawl Geofabrik HTML tables and update geofabrik-index.json & geofabrik_sizes.json.

Can be run standalone:
    python core/scripts/osm/update_geofabrik_index_and_sizes.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GEOFABRIK_INDEX_URL = "https://download.geofabrik.de/index-v1.json"
GEOFABRIK_BASE_URL = "https://download.geofabrik.de/"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def parse_size_str_to_bytes(size_str: str) -> Optional[int]:
    """Converts strings like '(51 MB)', '(51&nbsp;MB)', '3.8 GB', '82 MB', '520 KB' into integer bytes."""
    clean = str(size_str).replace("&nbsp;", " ").replace("\xa0", " ").strip()
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([kKMmGgTt]?[bB])", clean)
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    unit = m.group(2).upper()
    if "G" in unit:
        return int(val * (1024 ** 3))
    elif "M" in unit:
        return int(val * (1024 ** 2))
    elif "K" in unit:
        return int(val * 1024)
    elif "T" in unit:
        return int(val * (1024 ** 4))
    return int(val)


def crawl_geofabrik_sizes(
    max_pages: int = 300,
    max_workers: int = 10,
    timeout: int = 10,
    user_agent: str = DEFAULT_USER_AGENT,
    log_func: Optional[Callable[[str], None]] = None,
) -> dict[str, int]:
    """Crawls all Geofabrik HTML pages concurrently and extracts exact table sizes for all PBF files."""
    headers = {"User-Agent": user_agent}
    visited_pages = set()
    pages_to_visit = ["https://download.geofabrik.de/index.html"]
    all_sizes: dict[str, int] = {}

    def _log(msg: str):
        if log_func:
            log_func(msg)
        else:
            logger.info(msg)

    def fetch_page(url: str):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return url, resp.text
        except Exception:
            pass
        return url, None

    _log("Starte Crawling der Geofabrik-HTML-Tabellen...")
    start_time = time.time()

    while pages_to_visit and len(visited_pages) < max_pages:
        batch = pages_to_visit[:max_workers * 2]
        pages_to_visit = pages_to_visit[max_workers * 2:]

        batch_to_fetch = [u for u in batch if u not in visited_pages]
        for u in batch_to_fetch:
            visited_pages.add(u)

        if not batch_to_fetch:
            continue

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = pool.map(fetch_page, batch_to_fetch)

        for page_url, html in results:
            if not html:
                continue
            clean_html = html.replace("&nbsp;", " ").replace("\xa0", " ")

            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", clean_html, re.IGNORECASE | re.DOTALL)
            for row in rows:
                m_pbf = re.search(r'href=["\']([^"\']+\.osm\.pbf)["\']', row, re.IGNORECASE)
                if m_pbf:
                    pbf_href = m_pbf.group(1).strip()
                    full_pbf_url = urljoin(page_url, pbf_href)

                    m_size = re.search(r'\(?\b([0-9]+(?:\.[0-9]+)?\s*[kKMmGgTt]?[bB])\b\)?', row, re.IGNORECASE)
                    if m_size:
                        sz = parse_size_str_to_bytes(m_size.group(1))
                        if sz and sz > 0:
                            all_sizes[full_pbf_url] = sz

                m_sub = re.search(r'<a\s+[^>]*href=["\']([^"\']+\.html)["\'][^>]*>', row, re.IGNORECASE)
                if m_sub:
                    sub_url = urljoin(page_url, m_sub.group(1).strip())
                    if sub_url.startswith("https://download.geofabrik.de/") and sub_url not in visited_pages:
                        if sub_url not in pages_to_visit:
                            pages_to_visit.append(sub_url)

    elapsed = time.time() - start_time
    _log(f"{len(visited_pages)} Geofabrik-HTML-Seiten in {elapsed:.1f}s gescannt. {len(all_sizes)} PBF-Größen gefunden.")
    return all_sizes


def update_geofabrik_index_and_sizes(
    base_dir: Optional[str | Path] = None,
    log_func: Optional[Callable[[str], None]] = None,
) -> dict:
    """Downloads latest geofabrik-index.json and crawls geofabrik_sizes.json."""
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent / "data" / "osm"
    else:
        base_dir = Path(base_dir)

    base_dir.mkdir(parents=True, exist_ok=True)
    index_file = base_dir / "geofabrik-index.json"
    sizes_file = base_dir / "geofabrik_sizes.json"

    def _log(msg: str):
        if log_func:
            log_func(msg)
        else:
            logger.info(msg)

    # 1. Download geofabrik-index.json
    _log("Lade aktuellen geofabrik-index.json herunter...")
    temp_index = index_file.with_suffix(".tmp")
    req = Request(GEOFABRIK_INDEX_URL, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urlopen(req, timeout=15) as resp:
        content = resp.read()
        index_data = json.loads(content.decode("utf-8"))
        with open(temp_index, "w", encoding="utf-8") as out:
            json.dump(index_data, out, ensure_ascii=False, indent=2)
    temp_index.replace(index_file)
    _log(f"geofabrik-index.json erfolgreich gespeichert ({len(index_data.get('features', []))} Regionen).")

    # 2. Crawl live sizes from website
    crawled_sizes = crawl_geofabrik_sizes(log_func=_log)

    # 3. Match against index features and save
    matched = 0
    final_dict = {}
    for f in index_data.get("features", []):
        pbf = f.get("properties", {}).get("urls", {}).get("pbf")
        if not pbf:
            continue
        fn = os.path.basename(pbf).lower()
        if pbf in crawled_sizes:
            final_dict[pbf] = crawled_sizes[pbf]
            matched += 1
        else:
            for k, v in crawled_sizes.items():
                if os.path.basename(k).lower() == fn:
                    final_dict[pbf] = v
                    matched += 1
                    break

    with open(sizes_file, "w", encoding="utf-8") as out:
        json.dump(final_dict, out, ensure_ascii=False, indent=2)

    _log(f"geofabrik_sizes.json erfolgreich aktualisiert ({matched} von {len(index_data.get('features', []))} Größen zugeordnet).")
    return {"index": index_data, "sizes": final_dict, "matched": matched}


if __name__ == "__main__":
    update_geofabrik_index_and_sizes()
