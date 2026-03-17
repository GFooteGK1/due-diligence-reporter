#!/usr/bin/env python3
"""Generate a new OAuth refresh token for the Due Diligence Reporter.

Run from project root:
    uv run python scripts/generate_oauth_token.py

Signs in via browser. Use the correct Google account (e.g. edu.ops@trilogy.com).
"""

import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/gmail.modify",
]

# Load web client config and convert to installed app format
# (InstalledAppFlow needs "installed" type to use localhost redirect)
with open("credentials/client_secrets.json") as f:
    config = json.load(f)

if "web" in config:
    config = {"installed": config.pop("web")}

flow = InstalledAppFlow.from_client_config(config, scopes=SCOPES)
creds = flow.run_local_server(port=8090, access_type="offline", prompt="consent")

print()
print("=== REFRESH TOKEN (copy this into OAUTH_REFRESH_TOKEN GitHub secret) ===")
print(creds.refresh_token)
