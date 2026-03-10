#!/bin/sh

# Setup script for Due Diligence Reporter MCP Server
echo "Setting up Due Diligence Reporter MCP Server..." >&2

# Install dependencies using uv
echo "Installing dependencies..." >&2
uv sync > /dev/null 2>&1

# Install the package in editable mode so the module can be found
echo "Installing due_diligence_reporter package..." >&2
uv pip install -e . > /dev/null 2>&1

# Create credentials directory
mkdir -p credentials

# --- OAuth2 token setup ---
# Priority 1: Build token from .env / environment variables (same method as cron workflows)
# This uses the known-good refresh token from GitHub secrets.
if [ -n "$OAUTH_REFRESH_TOKEN" ] && [ -n "$OAUTH_CLIENT_ID" ] && [ -n "$OAUTH_CLIENT_SECRET" ]; then
    echo "Building OAuth2 token from environment variables..." >&2
    python3 -c "
import json, os
data = {
    'token': None,
    'refresh_token': os.environ['OAUTH_REFRESH_TOKEN'],
    'token_uri': 'https://oauth2.googleapis.com/token',
    'client_id': os.environ['OAUTH_CLIENT_ID'],
    'client_secret': os.environ['OAUTH_CLIENT_SECRET'],
    'scopes': [
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/documents',
        'https://www.googleapis.com/auth/gmail.modify',
    ],
}
with open('.gcp-saved-tokens.json', 'w') as f:
    json.dump(data, f, indent=2)

# Also create client_secrets.json for compatibility
secrets = {
    'web': {
        'client_id': os.environ['OAUTH_CLIENT_ID'],
        'client_secret': os.environ['OAUTH_CLIENT_SECRET'],
        'auth_uri': 'https://accounts.google.com/o/oauth2/v2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
    }
}
with open('credentials/client_secrets.json', 'w') as f:
    json.dump(secrets, f, indent=2)
"
    echo "OAuth2 credentials configured from environment variables" >&2

# Priority 2: Fetch from MCP Hive platform (legacy fallback)
elif [ -n "$API_KEY" ] && [ -n "$API_BASE_URL" ] && [ -n "$HIVE_INSTANCE_ID" ]; then
    echo "Fetching OAuth2 configuration from MCP Hive..." >&2

    if curl -s -X GET "$API_BASE_URL/api/hive-instances/$HIVE_INSTANCE_ID/oauth2-config" \
        -H "x-api-key: $API_KEY" > oauth_response.json 2>&1; then

        echo "Configuring OAuth2 credentials from MCP Hive..." >&2

        jq '{
          "client_id": .oauthKeys.client_id,
          "client_secret": .oauthKeys.client_secret,
          "refresh_token": .credentials.refresh_token,
          "token": .credentials.access_token,
          "token_uri": "https://oauth2.googleapis.com/token",
          "type": "authorized_user",
          "scopes": [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents",
            "https://www.googleapis.com/auth/gmail.modify"
          ]
        }' oauth_response.json > .gcp-saved-tokens.json

        jq '{"web": .oauthKeys}' oauth_response.json > credentials/client_secrets.json

        echo "OAuth2 credentials configured from MCP Hive" >&2
        rm oauth_response.json
    else
        echo "OAuth2 configuration fetch failed, will use manual setup" >&2
    fi
else
    echo "No OAuth2 credentials found in environment — using existing token files" >&2
fi

echo "Setup complete!" >&2

# Output final JSON configuration to stdout (MANDATORY)
cat << EOF
{
  "command": "uv",
  "args": ["run", "due-diligence-reporter-mcp"],
  "env": {
    "GOOGLE_CLIENT_CONFIG": "credentials/client_secrets.json",
    "GOOGLE_TOKEN_FILE": ".gcp-saved-tokens.json"
  },
  "cwd": "$(pwd)"
}
EOF
