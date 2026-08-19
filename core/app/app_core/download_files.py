import argparse
import hashlib
import logging
import os
from pathlib import Path
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://map.isv.uni-stuttgart.de/pando/"

FILES_TO_DOWNLOAD = [
    "gadm_adm0.gpkg",
    "gadm_adm1.gpkg",
    "gadm_adm2.gpkg",
    "gadm_adm3.gpkg",
    "ghs_pop_global.tif",
]

# Mapping filename to (relative_subfolder, target_filename) inside core/data/
DATASET_MAP = {
    "gadm_adm0.gpkg": ("gadm", "gadm_adm0.gpkg"),
    "gadm_adm1.gpkg": ("gadm", "gadm_adm1.gpkg"),
    "gadm_adm2.gpkg": ("gadm", "gadm_adm2.gpkg"),
    "gadm_adm3.gpkg": ("gadm", "gadm_adm3.gpkg"),
    "ghs_pop_global.tif": ("ghs_pop", "ghs_pop_global.tif"),
}

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB chunks for efficient IO with large files

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def get_dataset_target_path(filename: str, base_dir: Path) -> Path:
    """Returns the absolute target Path for a dataset under base_dir (typically core/data)."""
    subfolder, target_name = DATASET_MAP.get(filename, ("", filename))
    if subfolder:
        return base_dir / subfolder / target_name
    return base_dir / target_name


def check_global_datasets(data_root: Optional[Path] = None) -> dict[str, dict]:
    """Returns presence, path, and size for all global ADM / POP datasets."""
    if data_root is None:
        from core.app.app_core.project import tool_root
        data_root = Path(tool_root()) / "core" / "data"

    results = {}
    for filename in FILES_TO_DOWNLOAD:
        target_path = get_dataset_target_path(filename, data_root)
        exists = target_path.exists() and target_path.stat().st_size > 0
        size_bytes = target_path.stat().st_size if exists else 0
        results[filename] = {
            "filename": filename,
            "path": target_path,
            "exists": exists,
            "size_bytes": size_bytes,
        }
    return results


def compute_file_hash(
    file_path: Path, algorithm: str = "sha256", chunk_size: int = CHUNK_SIZE
) -> str:
    """Computes hash digest for a given file reading in chunks to conserve memory."""
    hasher = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def download_file(
    source_url: str,
    target_path: Path,
    chunk_size: int = CHUNK_SIZE,
    progress_callback: Optional[Callable[[int, int, float], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> None:
    """Downloads a file in chunks using atomic write pattern (.tmp extension)."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    req = Request(source_url, headers={"User-Agent": "PANDO V1.0 (Urban-Act Tool)"})

    try:
        with urlopen(req) as response, open(temp_path, "wb") as out_file:
            total_size = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            mb_total = total_size / (1024 * 1024)
            if mb_total > 0:
                logging.info(f"Start downloading {target_path.name} ({mb_total:.1f} MB)")
            else:
                logging.info(f"Start downloading {target_path.name}")

            while chunk := response.read(chunk_size):
                if cancel_check and cancel_check():
                    raise InterruptedError("Download cancelled by user.")
                out_file.write(chunk)
                downloaded += len(chunk)
                percent = (downloaded / total_size * 100) if total_size > 0 else 0.0
                if progress_callback:
                    progress_callback(downloaded, total_size, percent)

        # Atomic replacement upon successful completion
        temp_path.replace(target_path)
        logging.info(f"Successfully downloaded: {target_path.name}")

    except (HTTPError, URLError, OSError, InterruptedError) as err:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise RuntimeError(f"Failed to download {source_url}: {err}") from err


def _head_file_size(url: str) -> int:
    """Return Content-Length for *url* via a HEAD request, or 0 on failure."""
    try:
        req = Request(url, method="HEAD", headers={"User-Agent": "PANDO V1.0 (Urban-Act Tool)"})
        with urlopen(req, timeout=15) as resp:
            return int(resp.headers.get("Content-Length", 0))
    except Exception:
        return 0


def process_downloads(
    base_url: str = DEFAULT_BASE_URL,
    output_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[str, int, int, float], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    detail_progress_callback: Optional[Callable] = None,
) -> None:
    """Orchestrates validation, hashing, and download execution for global datasets.

    *detail_progress_callback*, when provided, is called with:
        (filename, file_downloaded, file_total, file_index, total_files,
         cumulative_downloaded, cumulative_total)
    """
    if output_dir is None:
        from core.app.app_core.project import tool_root
        output_dir = Path(tool_root()) / "core" / "data"

    output_dir.mkdir(parents=True, exist_ok=True)
    if not base_url.endswith("/"):
        base_url += "/"

    total_files = len(FILES_TO_DOWNLOAD)

    # --- Pre-flight: determine which files need downloading and their sizes ---
    file_sizes: dict[str, int] = {}
    skip_set: set[str] = set()
    for filename in FILES_TO_DOWNLOAD:
        if cancel_check and cancel_check():
            return
        target_file = get_dataset_target_path(filename, output_dir)
        hash_file = target_file.parent / f"{target_file.name}.sha256"
        if target_file.exists() and target_file.stat().st_size > 0:
            current_hash = compute_file_hash(target_file)
            if hash_file.exists():
                expected_hash = hash_file.read_text().strip()
                if current_hash.lower() == expected_hash.lower():
                    file_sizes[filename] = target_file.stat().st_size
                    skip_set.add(filename)
                    continue
            else:
                hash_file.write_text(f"{current_hash}\n")
                file_sizes[filename] = target_file.stat().st_size
                skip_set.add(filename)
                continue
        # Need to download – get size via HEAD
        file_url = urljoin(base_url, filename)
        file_sizes[filename] = _head_file_size(file_url)

    cumulative_total = sum(file_sizes.values())
    cumulative_downloaded = sum(file_sizes[fn] for fn in skip_set)

    for file_index, filename in enumerate(FILES_TO_DOWNLOAD, start=1):
        if cancel_check and cancel_check():
            logging.info("Download process cancelled.")
            break

        file_url = urljoin(base_url, filename)
        target_file = get_dataset_target_path(filename, output_dir)
        hash_file = target_file.parent / f"{target_file.name}.sha256"

        if filename in skip_set:
            logging.info(f"Skipping {filename}: Already present and verified.")
            if progress_callback:
                progress_callback(filename, 100, 100, 100.0)
            if detail_progress_callback:
                detail_progress_callback(
                    filename, file_sizes[filename], file_sizes[filename],
                    file_index, total_files, cumulative_downloaded, cumulative_total,
                )
            continue

        logging.info(f"Starting download for {filename} from {file_url}")
        file_total = file_sizes.get(filename, 0)

        def file_progress(downloaded, total, percent, _fn=filename, _fi=file_index):
            nonlocal cumulative_downloaded, file_total
            # Update file_total from actual Content-Length if we got 0 from HEAD
            if file_total == 0 and total > 0:
                old = file_sizes.get(_fn, 0)
                file_sizes[_fn] = total
                file_total = total
                nonlocal cumulative_total
                cumulative_total = cumulative_total - old + total
            if progress_callback:
                progress_callback(_fn, downloaded, total, percent)
            if detail_progress_callback:
                detail_progress_callback(
                    _fn, downloaded, total,
                    _fi, total_files,
                    cumulative_downloaded + downloaded, cumulative_total,
                )

        download_file(
            file_url,
            target_file,
            progress_callback=file_progress,
            cancel_check=cancel_check,
        )

        cumulative_downloaded += file_sizes.get(filename, 0)

        final_hash = compute_file_hash(target_file)
        hash_file.write_text(f"{final_hash}\n")
        logging.info(f"Saved hash record for {target_file.name} ({final_hash[:12]}...)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download spatial datasets with hash checking and chunked execution."
    )
    parser.add_argument(
        "base_url",
        nargs="?",
        type=str,
        default=DEFAULT_BASE_URL,
        help=f"Base URL directory hosting target files (default: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to store downloaded files (default: core/data)",
    )

    args = parser.parse_args()
    process_downloads(args.base_url, args.output_dir)


if __name__ == "__main__":
    main()