from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from .drive_client import DriveClient, FileMetadata, parse_drive_size
from .verifier import VerificationResult


CONFIRMATION_PHRASE = "YES_TRASH_VERIFIED_FILES"


@dataclass(frozen=True)
class TrashCandidate:
    file: FileMetadata
    local_path: Optional[Path]
    verification: VerificationResult


@dataclass(frozen=True)
class TrashResult:
    file: FileMetadata
    trashed: bool
    reason: str


def eligible_trash_candidates(candidates: Iterable[TrashCandidate]) -> List[TrashCandidate]:
    eligible: List[TrashCandidate] = []
    for candidate in candidates:
        if not candidate.verification.verified:
            continue
        if parse_drive_size(candidate.file) is None:
            continue
        if candidate.local_path is None or not candidate.local_path.exists():
            continue
        eligible.append(candidate)
    return eligible


def trash_verified_files(
    client: DriveClient,
    candidates: Iterable[TrashCandidate],
    *,
    dry_run: bool,
) -> List[TrashResult]:
    results: List[TrashResult] = []
    for candidate in candidates:
        name = str(candidate.file.get("name", "unnamed"))
        file_id = str(candidate.file.get("id", ""))

        if not candidate.verification.verified:
            results.append(TrashResult(candidate.file, False, "Not verified"))
            continue
        if parse_drive_size(candidate.file) is None:
            results.append(TrashResult(candidate.file, False, "Drive file size is missing"))
            continue
        if candidate.local_path is None or not candidate.local_path.exists():
            results.append(TrashResult(candidate.file, False, "Local file is missing"))
            continue
        if dry_run:
            results.append(TrashResult(candidate.file, False, "Dry run: would move to trash"))
            continue

        try:
            client.trash_file(file_id)
            results.append(TrashResult(candidate.file, True, "Moved to Google Drive Trash"))
        except Exception as exc:  # noqa: BLE001 - fail closed for each file
            results.append(TrashResult(candidate.file, False, f"Trash failed for {name}: {exc}"))

    return results
