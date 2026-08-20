from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence

from .drive_client import FileMetadata, parse_drive_size, total_size


def human_size(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "unknown"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{num_bytes} B"


def print_scan_summary(files: Sequence[FileMetadata]) -> None:
    print(f"\nFound {len(files)} media file(s).")
    print(f"Total possible space to free: {human_size(total_size(files))}\n")
    print_table(
        [
            {
                "name": str(file.get("name", "unnamed")),
                "size": human_size(parse_drive_size(file)),
                "mimeType": str(file.get("mimeType", "")),
                "modifiedTime": str(file.get("modifiedTime", "")),
            }
            for file in files
        ],
        ["name", "size", "mimeType", "modifiedTime"],
    )


def print_table(rows: Sequence[Mapping[str, str]], columns: Sequence[str]) -> None:
    if not rows:
        return
    widths = {
        column: min(
            max(len(column), *(len(str(row.get(column, ""))) for row in rows)),
            60,
        )
        for column in columns
    }
    header = " | ".join(column.ljust(widths[column]) for column in columns)
    print(header)
    print("-+-".join("-" * widths[column] for column in columns))
    for row in rows:
        values = []
        for column in columns:
            value = str(row.get(column, ""))
            if len(value) > widths[column]:
                value = value[: widths[column] - 1] + "..."
            values.append(value.ljust(widths[column]))
        print(" | ".join(values))


def save_scan_reports(files: Sequence[FileMetadata], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = report_dir / f"drive-media-scan-{timestamp}.csv"
    json_path = report_dir / f"drive-media-scan-{timestamp}.json"

    rows = [_report_row(file) for file in files]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else _csv_fields())
        writer.writeheader()
        writer.writerows(rows)

    payload = {
        "generatedAt": datetime.now().isoformat(),
        "count": len(files),
        "totalBytes": total_size(files),
        "files": files,
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return csv_path, json_path


def _csv_fields() -> List[str]:
    return [
        "id",
        "name",
        "mimeType",
        "sizeBytes",
        "sizeHuman",
        "createdTime",
        "modifiedTime",
        "parents",
        "webViewLink",
    ]


def _report_row(file: FileMetadata) -> dict[str, str]:
    size = parse_drive_size(file)
    return {
        "id": str(file.get("id", "")),
        "name": str(file.get("name", "")),
        "mimeType": str(file.get("mimeType", "")),
        "sizeBytes": "" if size is None else str(size),
        "sizeHuman": human_size(size),
        "createdTime": str(file.get("createdTime", "")),
        "modifiedTime": str(file.get("modifiedTime", "")),
        "parents": ",".join(file.get("parents", []) or []),
        "webViewLink": str(file.get("webViewLink", "")),
    }
