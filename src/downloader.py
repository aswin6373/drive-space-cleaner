from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .drive_client import DriveClient, FileMetadata, parse_drive_size
from .verifier import VerificationResult, verify_download


@dataclass(frozen=True)
class DownloadResult:
    file: FileMetadata
    local_path: Optional[Path]
    downloaded: bool
    skipped: bool
    verification: VerificationResult
    error: Optional[str] = None


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "unnamed"
    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}_file"
    return cleaned[:180]


def unique_destination(output_dir: Path, original_name: str, file_id: str) -> Path:
    safe_name = safe_filename(original_name)
    candidate = output_dir / safe_name
    if not candidate.exists():
        return candidate

    stem = candidate.stem or "file"
    suffix = candidate.suffix
    candidate = output_dir / f"{stem}_{file_id}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = output_dir / f"{stem}_{file_id}_{counter}{suffix}"
        counter += 1
    return candidate


def existing_verified_file(output_dir: Path, file_metadata: FileMetadata) -> Optional[Path]:
    expected_size = parse_drive_size(file_metadata)
    if expected_size is None:
        return None

    safe_name = safe_filename(str(file_metadata.get("name", "unnamed")))
    candidates = [output_dir / safe_name]
    candidates.extend(output_dir.glob(f"*{file_metadata.get('id')}*"))
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size == expected_size:
            return candidate
    return None


def download_drive_file(
    client: DriveClient,
    file_metadata: FileMetadata,
    output_dir: Path,
    *,
    dry_run: bool,
) -> DownloadResult:
    expected_size = parse_drive_size(file_metadata)
    if expected_size is None:
        return DownloadResult(
            file=file_metadata,
            local_path=None,
            downloaded=False,
            skipped=True,
            verification=verify_download(Path("__missing__"), None),
            error="Drive file size is missing; skipping download and trash eligibility",
        )

    existing = existing_verified_file(output_dir, file_metadata)
    if existing:
        return DownloadResult(
            file=file_metadata,
            local_path=existing,
            downloaded=False,
            skipped=True,
            verification=verify_download(existing, expected_size),
        )

    destination = unique_destination(
        output_dir,
        str(file_metadata.get("name", "unnamed")),
        str(file_metadata.get("id", "unknown")),
    )

    if dry_run:
        return DownloadResult(
            file=file_metadata,
            local_path=destination,
            downloaded=False,
            skipped=True,
            verification=VerificationResult(False, "Dry run: not downloaded"),
        )

    try:
        client.download_file(str(file_metadata["id"]), destination, expected_size)
        verification = verify_download(destination, expected_size)
        if verification.verified:
            write_sidecar_metadata(destination, file_metadata, verification)
        return DownloadResult(
            file=file_metadata,
            local_path=destination,
            downloaded=True,
            skipped=False,
            verification=verification,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed per file
        if destination.exists():
            destination.unlink(missing_ok=True)
        return DownloadResult(
            file=file_metadata,
            local_path=destination,
            downloaded=False,
            skipped=False,
            verification=VerificationResult(False, "Download failed"),
            error=str(exc),
        )


def write_sidecar_metadata(
    local_path: Path,
    file_metadata: FileMetadata,
    verification: VerificationResult,
) -> None:
    sidecar = local_path.with_name(f"{local_path.name}.metadata.json")
    payload = {
        "downloadedAt": datetime.now(timezone.utc).isoformat(),
        "localPath": str(local_path.resolve()),
        "verification": {
            "verified": verification.verified,
            "reason": verification.reason,
        },
        "driveFile": file_metadata,
    }
    sidecar.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
