from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

from .cleaner import (
    CONFIRMATION_PHRASE,
    TrashCandidate,
    eligible_trash_candidates,
    trash_verified_files,
)
from .config import ensure_runtime_dirs, load_config
from .downloader import DownloadResult, download_drive_file
from .drive_client import DriveClient, FileMetadata, parse_drive_size
from .report import human_size, print_scan_summary, print_table, save_scan_reports
from .scanner import scan_drive


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="drive-space-cleaner-agent",
        description="Safely scan, download, verify, and trash large Google Drive media files.",
    )
    parser.add_argument("--credentials", help="Path to Google OAuth Desktop credentials JSON.")
    parser.add_argument("--token", help="Path to local OAuth token JSON.")

    subparsers = parser.add_subparsers(dest="command")

    for name in ("scan", "download"):
        command = subparsers.add_parser(name)
        add_common_scan_args(command)
        command.add_argument("--dry-run", action="store_true", help="Show actions without changing files or Drive.")

    download = subparsers.choices["download"]
    download.add_argument("--output", help="Download folder. Defaults to ./downloads.")
    download.add_argument(
        "--trash-after-download",
        action="store_true",
        help="After verified downloads, ask before moving Drive copies to Trash.",
    )

    return parser


def add_common_scan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--include-images", action="store_true", help="Include image/* files as well as videos.")
    parser.add_argument("--min-size-mb", type=float, default=500, help="Minimum Drive file size in MB.")
    parser.add_argument("--limit", type=int, help="Maximum number of matching files to process.")


def configure_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "drive-space-cleaner-agent.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
    return log_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    raw_args = with_default_scan_command(list(sys.argv[1:] if argv is None else argv))
    args = parser.parse_args(raw_args)

    if args.command is None:
        args.command = "scan"

    config = load_config(
        credentials_path=args.credentials,
        token_path=args.token,
        download_dir=getattr(args, "output", None),
    )
    ensure_runtime_dirs(config)
    log_path = configure_logging(config.log_dir)
    logging.info("Started command=%s dry_run=%s", args.command, getattr(args, "dry_run", False))

    try:
        client = DriveClient(config.credentials_path, config.token_path)
    except Exception as exc:  # noqa: BLE001 - show friendly CLI error
        logging.exception("Authentication/setup failed")
        print(f"Could not connect to Google Drive: {exc}", file=sys.stderr)
        print(f"Log file: {log_path}", file=sys.stderr)
        return 1

    try:
        files = scan_drive(
            client,
            include_images=args.include_images,
            min_size_mb=args.min_size_mb,
            limit=args.limit,
        )
        logging.info("Scan found %s files", len(files))
        print_scan_summary(files)
        csv_path, json_path = save_scan_reports(files, config.report_dir)
        print(f"\nReports saved:\nCSV: {csv_path}\nJSON: {json_path}")

        if args.command == "scan":
            print(f"Log file: {log_path}")
            return 0

        results = run_downloads(client, files, config.download_dir, dry_run=args.dry_run)
        if args.trash_after_download:
            run_trash_flow(client, results, dry_run=args.dry_run)
        else:
            print("\nTrash step skipped. Add --trash-after-download to request safe trashing after verification.")

        print(f"\nLog file: {log_path}")
        return 0
    except KeyboardInterrupt:
        logging.warning("Interrupted by user")
        print("\nInterrupted. No permanent deletion was performed.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - top-level CLI guard
        logging.exception("Command failed")
        print(f"Command failed safely: {exc}", file=sys.stderr)
        print("No permanent deletion was performed.", file=sys.stderr)
        print(f"Log file: {log_path}", file=sys.stderr)
        return 1


def with_default_scan_command(raw_args: List[str]) -> List[str]:
    command_names = {"scan", "download"}
    if not raw_args or any(arg in command_names for arg in raw_args) or any(arg in {"-h", "--help"} for arg in raw_args):
        return raw_args or ["scan"]

    prefix: List[str] = []
    index = 0
    while index < len(raw_args):
        arg = raw_args[index]
        if arg in {"--credentials", "--token"} and index + 1 < len(raw_args):
            prefix.extend(raw_args[index : index + 2])
            index += 2
            continue
        if arg.startswith("--credentials=") or arg.startswith("--token="):
            prefix.append(arg)
            index += 1
            continue
        break

    return [*prefix, "scan", *raw_args[index:]]


def run_downloads(
    client: DriveClient,
    files: List[FileMetadata],
    output_dir: Path,
    *,
    dry_run: bool,
) -> List[DownloadResult]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results: List[DownloadResult] = []

    print(f"\nDownload folder: {output_dir}")
    if dry_run:
        print("Dry run enabled: files will not be downloaded.")

    for file in files:
        name = str(file.get("name", "unnamed"))
        size = parse_drive_size(file)
        logging.info("Download candidate id=%s name=%s size=%s", file.get("id"), name, size)
        result = download_drive_file(client, file, output_dir, dry_run=dry_run)
        results.append(result)
        if result.error:
            logging.error("Download failed id=%s error=%s", file.get("id"), result.error)
            print(f"Failed: {name} ({result.error})")
        elif result.verification.verified:
            action = "Skipped existing verified" if result.skipped else "Downloaded and verified"
            logging.info("%s id=%s path=%s", action, file.get("id"), result.local_path)
            print(f"{action}: {name}")
        else:
            logging.warning("Not verified id=%s reason=%s", file.get("id"), result.verification.reason)
            print(f"Not verified: {name} ({result.verification.reason})")

    return results


def run_trash_flow(client: DriveClient, results: List[DownloadResult], *, dry_run: bool) -> None:
    candidates = [
        TrashCandidate(result.file, result.local_path, result.verification)
        for result in results
    ]
    eligible = eligible_trash_candidates(candidates)

    print("\nVerified files eligible for Google Drive Trash:")
    print_table(
        [
            {
                "filename": str(candidate.file.get("name", "unnamed")),
                "size": human_size(parse_drive_size(candidate.file)),
                "local path": str(candidate.local_path or ""),
                "verification": candidate.verification.reason,
            }
            for candidate in eligible
        ],
        ["filename", "size", "local path", "verification"],
    )

    skipped = len(candidates) - len(eligible)
    if skipped:
        print(f"\nSkipped {skipped} file(s) because they were not verified or lacked safe metadata.")

    if not eligible:
        print("No files are eligible for trashing.")
        return

    if dry_run:
        print("Dry run enabled: no Drive files will be moved to Trash.")
        trash_results = trash_verified_files(client, eligible, dry_run=True)
    else:
        print(
            "\nThis will move only the verified Drive copies above to Google Drive Trash. "
            "It will not permanently delete anything."
        )
        confirmation = input(f"Type {CONFIRMATION_PHRASE} to continue: ").strip()
        if confirmation != CONFIRMATION_PHRASE:
            logging.warning("Trash confirmation declined")
            print("Confirmation did not match. Trash step cancelled.")
            return
        trash_results = trash_verified_files(client, eligible, dry_run=False)

    for result in trash_results:
        logging.info(
            "Trash result id=%s name=%s trashed=%s reason=%s",
            result.file.get("id"),
            result.file.get("name"),
            result.trashed,
            result.reason,
        )
        print(f"{'Trashed' if result.trashed else 'Skipped'}: {result.file.get('name')} - {result.reason}")


if __name__ == "__main__":
    raise SystemExit(main())
