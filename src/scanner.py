from __future__ import annotations

from typing import List, Optional

from .drive_client import DriveClient, FileMetadata


def scan_drive(
    client: DriveClient,
    *,
    include_images: bool,
    min_size_mb: float,
    limit: Optional[int],
) -> List[FileMetadata]:
    min_size_bytes = int(min_size_mb * 1024 * 1024)
    return client.list_media_files(
        include_images=include_images,
        min_size_bytes=min_size_bytes,
        limit=limit,
    )
