"""Google Client for Drive and Docs API operations with OAuth."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger("[google_client]")


class GoogleClient:
    """Client for interacting with Drive and Docs APIs using OAuth."""

    def __init__(self, credentials: Credentials) -> None:
        self.credentials = credentials
        self.drive_service = build("drive", "v3", credentials=credentials)
        self.docs_service = build("docs", "v1", credentials=credentials)
        logger.info("Initialized GoogleClient with Drive v3 and Docs v1 APIs")

    @classmethod
    def from_oauth_config(
        cls,
        client_config_path: str,
        token_file_path: str,
        oauth_port: int,
        scopes: list[str],
    ) -> GoogleClient:
        """Create client using OAuth flow with provided scopes."""
        credentials: Credentials | None = None
        token_file = Path(token_file_path)

        logger.info("Initializing OAuth flow with scopes: %s", scopes)

        if token_file.exists():
            logger.info("Loading existing credentials from: %s", token_file)
            credentials = Credentials.from_authorized_user_file(str(token_file), scopes)
            if credentials and not credentials.refresh_token:
                logger.warning(
                    "Loaded credentials missing refresh_token; forcing OAuth flow"
                )
                credentials = None

        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                logger.info("Refreshing expired credentials")
                credentials.refresh(Request())
            else:
                logger.info("Starting OAuth flow — browser window will open")
                flow = InstalledAppFlow.from_client_secrets_file(
                    client_config_path, scopes
                )
                credentials = flow.run_local_server(
                    port=oauth_port,
                    access_type="offline",
                    prompt="consent",
                )

            if credentials is None:
                raise RuntimeError("Failed to obtain OAuth credentials")

            with open(token_file, "w") as token:
                token.write(credentials.to_json())
            logger.info("Saved new credentials to: %s", token_file)

        if credentials is None:
            raise RuntimeError("OAuth credentials are None after flow")

        return cls(credentials)

    # ---------- Drive API Methods ----------

    def list_files_in_folder(
        self,
        folder_id: str,
        *,
        include_trashed: bool = False,
    ) -> list[dict[str, Any]]:
        """
        List all files (not folders) directly inside a Drive folder.

        Returns list of dicts with keys: id, name, mimeType, modifiedTime, webViewLink.
        """
        trashed_clause = "" if include_trashed else " and trashed=false"
        query = (
            f"'{folder_id}' in parents"
            " and mimeType!='application/vnd.google-apps.folder'"
            f"{trashed_clause}"
        )

        logger.info("Listing files in Drive folder: %s", folder_id)

        try:
            files: list[dict[str, Any]] = []
            page_token: str | None = None

            while True:
                response = (
                    self.drive_service.files()
                    .list(
                        q=query,
                        fields="nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink)",
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                        pageToken=page_token,
                        orderBy="name_natural",
                    )
                    .execute()
                )
                files.extend(response.get("files", []))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            logger.info("Found %d files in folder %s", len(files), folder_id)
            return files

        except HttpError as error:
            logger.error("Failed to list files in folder %s: %s", folder_id, error)
            raise RuntimeError(f"Failed to list files in folder: {error}") from error

    def list_subfolders(self, folder_id: str) -> list[dict[str, Any]]:
        """
        List direct child folders inside a Drive folder.

        Returns list of dicts with keys: id, name, webViewLink.
        """
        query = (
            f"'{folder_id}' in parents"
            " and mimeType='application/vnd.google-apps.folder'"
            " and trashed=false"
        )

        logger.info("Listing subfolders of: %s", folder_id)

        try:
            folders: list[dict[str, Any]] = []
            page_token: str | None = None

            while True:
                response = (
                    self.drive_service.files()
                    .list(
                        q=query,
                        fields="nextPageToken,files(id,name,webViewLink)",
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                        pageToken=page_token,
                        orderBy="name_natural",
                    )
                    .execute()
                )
                folders.extend(response.get("files", []))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            logger.info("Found %d subfolders in folder %s", len(folders), folder_id)
            return folders

        except HttpError as error:
            logger.error("Failed to list subfolders: %s", error)
            raise RuntimeError(f"Failed to list subfolders: {error}") from error

    def find_subfolder_by_name(
        self, parent_id: str, subfolder_name: str
    ) -> dict[str, Any] | None:
        """Find a named direct child folder inside a parent Drive folder."""
        subfolders = self.list_subfolders(parent_id)
        for folder in subfolders:
            if folder.get("name", "") == subfolder_name:
                return folder
        return None

    def export_google_doc_as_text(self, file_id: str) -> str:
        """
        Export a Google Docs file as plain text.

        Uses the Drive API export endpoint (only works for Google Workspace files).
        Returns the text content as a string.
        """
        logger.info("Exporting Google Doc as text: %s", file_id)

        try:
            response = (
                self.drive_service.files()
                .export(fileId=file_id, mimeType="text/plain")
                .execute()
            )

            if isinstance(response, bytes):
                text = response.decode("utf-8", errors="replace")
            else:
                text = str(response)

            logger.info("Exported %d characters from Google Doc %s", len(text), file_id)
            return text

        except HttpError as error:
            logger.error("Failed to export Google Doc %s: %s", file_id, error)
            raise RuntimeError(f"Failed to export Google Doc: {error}") from error

    def download_file_bytes(self, file_id: str) -> bytes:
        """
        Download a file's raw bytes from Google Drive.

        Used for PDFs and other binary/non-Google-Workspace files.
        """
        logger.info("Downloading file bytes: %s", file_id)

        try:
            response = self.drive_service.files().get_media(fileId=file_id).execute()

            if isinstance(response, bytes):
                data = response
            else:
                data = bytes(response)

            logger.info("Downloaded %d bytes for file %s", len(data), file_id)
            return data

        except HttpError as error:
            logger.error("Failed to download file %s: %s", file_id, error)
            raise RuntimeError(f"Failed to download file: {error}") from error

    def copy_document(
        self,
        template_id: str,
        name: str,
        parent_folder_id: str,
    ) -> dict[str, Any]:
        """
        Copy a Google Docs template to a target folder.

        Returns the new document metadata including 'id' and 'webViewLink'.
        """
        logger.info(
            "Copying Docs template: %s (name: %s, parent: %s)",
            template_id,
            name,
            parent_folder_id,
        )

        body: dict[str, Any] = {
            "name": name,
            "parents": [parent_folder_id],
        }

        try:
            doc = (
                self.drive_service.files()
                .copy(
                    fileId=template_id,
                    body=body,
                    supportsAllDrives=True,
                    fields="id,webViewLink,name",
                )
                .execute()
            )
            logger.info(
                "Successfully copied document: %s (id: %s)",
                name,
                doc.get("id"),
            )
            return doc

        except HttpError as error:
            logger.error("Failed to copy document: %s", error)
            raise RuntimeError(f"Failed to copy document: {error}") from error

    # ---------- Docs API Methods ----------

    def batch_update_document(
        self,
        document_id: str,
        requests_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Apply a batch update to a Google Docs document.

        Args:
            document_id: The document ID to update.
            requests_list: List of Docs API request objects (e.g., replaceAllText).

        Returns:
            The batchUpdate API response.
        """
        logger.info(
            "Batch updating document: %s (%d requests)",
            document_id,
            len(requests_list),
        )

        try:
            response = (
                self.docs_service.documents()
                .batchUpdate(
                    documentId=document_id,
                    body={"requests": requests_list},
                )
                .execute()
            )
            logger.info("Successfully batch-updated document: %s", document_id)
            return response

        except HttpError as error:
            logger.error("Failed to batch update document %s: %s", document_id, error)
            raise RuntimeError(f"Failed to batch update document: {error}") from error
