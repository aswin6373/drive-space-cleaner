from __future__ import annotations

import os
import stat
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = ["https://www.googleapis.com/auth/drive"]


def _save_token(token_path: Path, creds: Credentials) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600: token holds a refresh secret


def get_credentials(credentials_path: Path, token_path: Path) -> Credentials:
    """Load or create OAuth credentials for an installed desktop app.

    A stored token that can no longer be refreshed (expired, revoked, or
    scope-changed) is discarded and the interactive flow runs again, instead
    of failing every run until the user deletes token.json manually.
    """
    creds: Credentials | None = None

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception:
            creds = None  # malformed token file: fall through to a fresh login

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None  # stale/revoked refresh token: re-authenticate

    if creds is None or not creds.valid:
        if not credentials_path.exists():
            raise FileNotFoundError(
                f"Missing OAuth credentials file: {credentials_path}. "
                "Create Desktop OAuth credentials in Google Cloud Console and save them as credentials.json."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        creds = flow.run_local_server(port=0)

    _save_token(token_path, creds)
    return creds
