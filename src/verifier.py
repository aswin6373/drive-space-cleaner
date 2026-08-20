from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    reason: str


def verify_download(local_path: Path, expected_size: Optional[int]) -> VerificationResult:
    if expected_size is None:
        return VerificationResult(False, "Drive file size is missing")
    if not local_path.exists():
        return VerificationResult(False, "Local file does not exist")
    actual_size = local_path.stat().st_size
    if actual_size != expected_size:
        return VerificationResult(
            False,
            f"Size mismatch: local={actual_size} bytes, drive={expected_size} bytes",
        )
    return VerificationResult(True, "Verified")
