from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


FileMetadata = Dict[str, Any]


class DriveClient:
    def __init__(self, credentials_path: Path, token_path: Path) -> None:
        from googleapiclient.discovery import build

        from .auth import get_credentials

        creds = get_credentials(credentials_path, token_path)
        self.service = build("drive", "v3", credentials=creds)

    def list_media_files(
        self,
        *,
        include_images: bool,
        min_size_bytes: int,
        limit: Optional[int],
    ) -> List[FileMetadata]:
        mime_clauses = ["mimeType contains 'video/'"]
        if include_images:
            mime_clauses.append("mimeType contains 'image/'")

        query = (
            "trashed = false "
            "and mimeType != 'application/vnd.google-apps.document' "
            "and mimeType != 'application/vnd.google-apps.spreadsheet' "
            "and mimeType != 'application/vnd.google-apps.presentation' "
            f"and ({' or '.join(mime_clauses)})"
        )
        fields = (
            "nextPageToken, files("
            "id,name,mimeType,size,createdTime,modifiedTime,parents,webViewLink"
            ")"
        )

        files: List[FileMetadata] = []
        page_token: Optional[str] = None

        while True:
            remaining = None if limit is None else max(limit - len(files), 0)
            if remaining == 0:
                break

            page_size = min(1000, remaining or 1000)
            response = (
                self.service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields=fields,
                    pageToken=page_token,
                    pageSize=page_size,
                    orderBy="quotaBytesUsed desc",
                )
                .execute()
            )

            for item in response.get("files", []):
                size = parse_drive_size(item)
                if size is not None and size >= min_size_bytes:
                    files.append(item)
                    if limit is not None and len(files) >= limit:
                        break

            page_token = response.get("nextPageToken")
            if not page_token or (limit is not None and len(files) >= limit):
                break

        files.sort(key=lambda f: parse_drive_size(f) or 0, reverse=True)
        return files

    def download_file(self, file_id: str, destination: Path, total_size: int) -> None:
        from googleapiclient.http import MediaIoBaseDownload
        from tqdm import tqdm

        request = self.service.files().get_media(fileId=file_id)
        destination.parent.mkdir(parents=True, exist_ok=True)

        with destination.open("wb") as handle:
            downloader = MediaIoBaseDownload(handle, request)
            done = False
            with tqdm(
                total=total_size,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=destination.name,
            ) as progress:
                last_progress = 0
                while not done:
                    status, done = downloader.next_chunk()
                    if status:
                        current = int(status.resumable_progress)
                        progress.update(current - last_progress)
                        last_progress = current

    def trash_file(self, file_id: str) -> FileMetadata:
        return (
            self.service.files()
            .update(fileId=file_id, body={"trashed": True}, fields="id,name,trashed")
            .execute()
        )


def parse_drive_size(file_metadata: FileMetadata) -> Optional[int]:
    raw_size = file_metadata.get("size")
    if raw_size in (None, ""):
        return None
    try:
        return int(raw_size)
    except (TypeError, ValueError):
        return None


def total_size(files: Iterable[FileMetadata]) -> int:
    return sum(parse_drive_size(file) or 0 for file in files)
