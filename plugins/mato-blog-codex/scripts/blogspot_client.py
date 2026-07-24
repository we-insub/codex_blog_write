"""Standalone Blogger/Google Drive client for the Mato OneQ workflow."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Iterable

try:
    from .integrations import BLOGSPOT_TOKEN_SECRET_KEY, get_secret, set_secret
except ImportError:
    from integrations import BLOGSPOT_TOKEN_SECRET_KEY, get_secret, set_secret  # type: ignore[no-redef]


BLOGGER_SCOPES = (
    "https://www.googleapis.com/auth/blogger",
    "https://www.googleapis.com/auth/drive.file",
)


class BlogspotError(RuntimeError):
    """A Blogger or Google OAuth failure."""


class BlogspotClient:
    """OAuth client that keeps tokens in current-user DPAPI storage."""

    def __init__(self, client_id: str, client_secret: str, *, scopes: Iterable[str] = BLOGGER_SCOPES) -> None:
        self.client_id = str(client_id or "").strip()
        self.client_secret = str(client_secret or "").strip()
        self.scopes = tuple(dict.fromkeys(str(scope) for scope in scopes))
        self.service: Any | None = None
        if not self.client_id or not self.client_secret:
            raise ValueError("Google OAuth client ID and client secret are required")

    @staticmethod
    def _deps() -> tuple[Any, Any, Any, Any]:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build

            return Request, Credentials, InstalledAppFlow, build
        except ModuleNotFoundError as exc:
            raise BlogspotError("Google API packages are missing; run bootstrap.py to install this project's requirements") from exc

    def _credentials(self, *, allow_interactive: bool) -> Any:
        Request, Credentials, InstalledAppFlow, _build = self._deps()
        raw = get_secret(BLOGSPOT_TOKEN_SECRET_KEY)
        creds = None
        if raw:
            try:
                token = json.loads(raw)
                saved_client = str(token.get("client_id") or "").strip()
                if saved_client == self.client_id and set(self.scopes).issubset(set(token.get("scopes") or [])):
                    creds = Credentials.from_authorized_user_info(token, self.scopes)
            except (ValueError, TypeError):
                creds = None
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if not creds or not creds.valid:
            if not allow_interactive:
                raise BlogspotError("Blogger OAuth authorization is required")
            client_config = {
                "installed": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, self.scopes)
            creds = flow.run_local_server(port=0)
        set_secret(BLOGSPOT_TOKEN_SECRET_KEY, creds.to_json())
        return creds

    def authorize(self) -> list[dict[str, str]]:
        _Request, _Credentials, _Flow, build = self._deps()
        self.service = build("blogger", "v3", credentials=self._credentials(allow_interactive=True))
        return self.list_blogs()

    def _ensure_service(self) -> Any:
        if self.service is None:
            _Request, _Credentials, _Flow, build = self._deps()
            self.service = build("blogger", "v3", credentials=self._credentials(allow_interactive=False))
        return self.service

    def list_blogs(self) -> list[dict[str, str]]:
        data = self._ensure_service().blogs().listByUser(userId="self").execute()
        return [
            {"id": str(item.get("id") or ""), "name": str(item.get("name") or ""), "url": str(item.get("url") or "")}
            for item in data.get("items", [])
            if isinstance(item, dict)
        ]

    def upload_image_to_drive(self, image_path: Path) -> str:
        """Upload one owned image and make the resulting Drive file publicly readable."""

        if not image_path.is_file():
            raise BlogspotError(f"image file does not exist: {image_path}")
        _Request, _Credentials, _Flow, build = self._deps()
        try:
            from googleapiclient.http import MediaIoBaseUpload
        except ModuleNotFoundError as exc:  # pragma: no cover - guarded by bootstrap
            raise BlogspotError("Google API packages are missing") from exc
        credentials = self._credentials(allow_interactive=False)
        drive = build("drive", "v3", credentials=credentials)
        mime = "image/jpeg" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        created = drive.files().create(
            body={"name": image_path.name},
            media_body=MediaIoBaseUpload(io.BytesIO(image_path.read_bytes()), mimetype=mime, resumable=False),
            fields="id",
        ).execute()
        file_id = str(created.get("id") or "")
        if not file_id:
            raise BlogspotError("Google Drive did not return an image file ID")
        drive.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"}, fields="id").execute()
        return f"https://drive.google.com/thumbnail?id={file_id}&sz=w1200"

    def create_post(self, *, blog_id: str, title: str, body_html: str, labels: Iterable[str] | None, status: str) -> dict[str, Any]:
        if not str(blog_id or "").strip():
            raise ValueError("Blogspot blog ID is required")
        payload: dict[str, Any] = {"title": str(title or "Untitled"), "content": str(body_html or "")}
        clean_labels = [str(item).strip() for item in (labels or []) if str(item).strip()]
        if clean_labels:
            payload["labels"] = clean_labels
        try:
            return self._ensure_service().posts().insert(
                blogId=str(blog_id), body=payload, isDraft=str(status).lower() != "publish"
            ).execute()
        except Exception as exc:
            raise BlogspotError(f"Blogger upload failed: {exc}") from exc
