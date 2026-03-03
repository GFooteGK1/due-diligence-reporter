"""Configuration management for Google OAuth and APIs."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

# Load environment variables from .env file at project root
load_dotenv(Path(__file__).parent.parent.parent / ".env")


class Settings(BaseSettings):
    """Application settings."""

    # Google Cloud Configuration
    google_client_config: str = Field(
        "credentials/client_secrets.json",
        description="OAuth 2.0 client configuration file",
    )
    google_token_file: str = Field(
        ".gcp-saved-tokens.json", description="Path to store user OAuth tokens"
    )
    oauth_port: int = Field(
        8765,
        description="Port for OAuth callback server",
    )

    # Google API Scopes — Drive (read/write) + Documents (create/edit)
    google_scopes: list[str] = Field(
        default=[
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents",
        ],
        description="OAuth scopes for Drive and Docs operations",
    )

    # DD Report Template
    dd_template_google_doc_id: str = Field(
        "",
        description="Google Doc ID of the master DD report template",
    )

    # Google Drive root folder containing all site folders
    google_drive_root_folder_id: str = Field(
        "",
        description="Parent Drive folder ID that contains all site folders",
    )

    # Building Optimizer / Pricing API
    pricing_api_url: str = Field(
        "https://pricing-api-738625530258.us-central1.run.app",
        description="Base URL for the Building Optimizer pricing API",
    )
    pricing_api_key: str = Field(
        "",
        description="API key for the Building Optimizer pricing API (X-API-Key header)",
    )

    # Email (Gmail SMTP with App Password)
    email_sender: str = Field("", description="Gmail address for sending DD report emails")
    email_app_password: str = Field("", description="Gmail App Password for the sender account")
    dd_report_email_recipients: str = Field(
        "", description="Comma-separated list of recipient email addresses"
    )

    # Google Chat
    google_chat_webhook_url: str = Field(
        "", description="Google Chat incoming webhook URL for notifications"
    )

    # Logging
    log_level: str = Field("INFO", description="Logging level")

    def get_client_config_path(self) -> Path:
        """Get the path to OAuth client configuration."""
        return Path(self.google_client_config)

    def get_token_file_path(self) -> Path:
        """Get the path to the token storage file."""
        return Path(self.google_token_file)


def get_settings() -> Settings:
    """Get application settings."""
    try:
        return Settings()
    except Exception as e:
        raise ValueError(
            f"Configuration error: {e}. "
            f"Please ensure Google OAuth client config and token paths are valid. "
            f"Current working directory: {os.getcwd()}. "
            f"GOOGLE_CLIENT_CONFIG: {os.getenv('GOOGLE_CLIENT_CONFIG')}, "
            f"GOOGLE_TOKEN_FILE: {os.getenv('GOOGLE_TOKEN_FILE')}"
        ) from e
