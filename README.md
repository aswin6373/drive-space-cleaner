# Drive Space Cleaner Agent

A safe command-line tool for freeing Google Drive storage. It scans your Drive for large media files, downloads selected videos and optional photos to your computer, verifies the downloaded file size, and only then can move the Google Drive copy to Trash after explicit confirmation.

This project uses the official Google Drive API Python client, OAuth desktop login, Drive media downloads, and `files.update` with `{ "trashed": true }`.

## Safety First

- Default behavior is scan-only.
- Files are never permanently deleted.
- The tool only moves verified Drive files to Google Drive Trash.
- Trashing requires `--trash-after-download` and the exact typed confirmation `YES_TRASH_VERIFIED_FILES`.
- Downloaded files must exist locally and match the Drive-reported size before they are eligible for trashing.
- Google Docs, Sheets, and Slides export files are not processed.
- By default, only videos are scanned. Images are included only with `--include-images`.
- Every run writes logs to `logs/drive-space-cleaner-agent.log`.

## Project Layout

```text
drive-space-cleaner-agent/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── credentials.example.json
├── src/
│   ├── main.py
│   ├── auth.py
│   ├── drive_client.py
│   ├── scanner.py
│   ├── downloader.py
│   ├── verifier.py
│   ├── cleaner.py
│   ├── report.py
│   └── config.py
├── downloads/
│   └── .gitkeep
└── reports/
```

## Google Cloud Setup

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project or choose an existing project.
3. Go to **APIs & Services > Library**.
4. Search for **Google Drive API** and enable it.
5. Go to **APIs & Services > OAuth consent screen**.
6. Configure the consent screen. For personal use, External plus Testing is usually enough.
7. Add your Google account as a test user if the app is in testing mode.
8. Go to **APIs & Services > Credentials**.
9. Click **Create Credentials > OAuth client ID**.
10. Choose **Desktop app**.
11. Download the JSON file.
12. Rename it to `credentials.json`.
13. Place it in the project root beside this README.

Do not commit `credentials.json` or `token.json`. They are ignored by `.gitignore`.

## Installation

```bash
cd drive-space-cleaner-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
cd drive-space-cleaner-agent
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Usage

The first run opens a browser window for Google OAuth login and stores `token.json` locally.

Scan videos larger than 500 MB:

```bash
python -m src.main scan --min-size-mb 500
```

Scan videos and photos larger than 100 MB:

```bash
python -m src.main scan --include-images --min-size-mb 100
```

Limit scan results:

```bash
python -m src.main scan --min-size-mb 500 --limit 20
```

Download matching videos to `./downloads`:

```bash
python -m src.main download --min-size-mb 500 --output ./downloads
```

Dry-run download behavior:

```bash
python -m src.main download --min-size-mb 500 --output ./downloads --dry-run
```

Download, verify, then ask before moving verified Drive copies to Trash:

```bash
python -m src.main download --min-size-mb 500 --output ./downloads --trash-after-download
```

Include photos in the download and safe-trash flow:

```bash
python -m src.main download --include-images --min-size-mb 100 --limit 20 --trash-after-download
```

## Reports

Every scan saves both CSV and JSON reports in `reports/`. Reports include:

- Drive file ID
- Name
- MIME type
- Size
- Created and modified timestamps
- Parent IDs
- Web view link

The terminal also shows the largest files sorted by size and the total possible space to free.

## Downloads and Verification

Downloads go to `./downloads` unless `--output` is provided. Filenames are cleaned for macOS, Linux, and Windows compatibility.

The tool avoids overwriting existing files. If a matching local file already exists with the same size, the file is skipped and treated as verified. After a successful verified download, a sidecar metadata file is written next to it:

```text
example-video.mp4.metadata.json
```

## Restoring Files From Google Drive Trash

Because this tool only moves files to Trash, you can restore them:

1. Open [Google Drive](https://drive.google.com/).
2. Select **Trash** in the left sidebar.
3. Right-click the file.
4. Choose **Restore**.

Google may automatically delete items that remain in Trash for long enough according to Google Drive’s current policy, so restore anything you need promptly.

## Troubleshooting

**Missing `credentials.json`**

Create OAuth Desktop credentials in Google Cloud Console, download the JSON file, rename it to `credentials.json`, and place it in the project root.

**OAuth app is blocked or user not allowed**

Check the OAuth consent screen. If the app is in testing mode, add your Google account as a test user.

**Drive API not enabled**

Enable the Google Drive API in **APIs & Services > Library** for the same Google Cloud project that owns your OAuth client.

**Size is missing**

Some Drive files do not expose a normal byte size. The tool skips those for downloading and trash eligibility.

**A download failed**

The tool logs the error and does not trash that Drive file. Re-run the command after checking your network connection and available disk space.

**Need to re-authenticate**

Delete `token.json` and run a command again. Keep `credentials.json`.

## Notes

This is a normal automation CLI, not an AI agent. It does not use an LLM. It relies on Drive metadata, local file checks, and explicit user confirmation.
