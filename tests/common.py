"""Shared helpers and constants for the pCloud Backup tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from custom_components.pcloud_backup.const import (
    CONF_BACKUP_FOLDER,
    CONF_REGION,
    CONF_UPLOAD_TIMEOUT_SECONDS,
)

FIXTURES = Path(__file__).parent / "fixtures"

ENTRY_ID = "entry1"
AGENT_ID = f"pcloud_backup.{ENTRY_ID}"
ACCESS_TOKEN = "test-access-token"
EU_BASE = "https://eapi.pcloud.com"
US_BASE = "https://api.pcloud.com"

BACKUP_FOLDER = "/HA Backups"
BACKUP_FOLDER_ID = 42

# Keys of the paired backup in listfolder_sample.json
PAIRED_KEY = "Automatic_backup_2025.1.0_2025-01-02_03.00_00000000"
PAIRED_FILE_ID = 101
PAIRED_METADATA_FILE_ID = 102
ORPHAN_KEY = "Manual_backup_2024-12-01"
ORPHAN_FILE_ID = 201

ENTRY_DATA: dict[str, Any] = {
    CONF_REGION: "eu",
    "auth_implementation": "pcloud_backup",
    "token": {"access_token": ACCESS_TOKEN, "token_type": "bearer"},
}
ENTRY_OPTIONS: dict[str, Any] = {
    CONF_BACKUP_FOLDER: BACKUP_FOLDER,
    CONF_UPLOAD_TIMEOUT_SECONDS: 3600,
}


def load_json_fixture(name: str) -> Any:
    """Load a JSON fixture from tests/fixtures."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def listfolder_files() -> list[dict[str, Any]]:
    """Return the file (non-folder) items of the sample listfolder response."""
    contents = load_json_fixture("listfolder_sample.json")["metadata"]["contents"]
    return [item for item in contents if not item.get("isfolder")]


def metadata_bytes(payload: dict[str, Any] | None = None) -> bytes:
    """Return a metadata.json payload as bytes (defaults to the v2 sample)."""
    if payload is None:
        payload = load_json_fixture("metadata_v2_sample.json")
    return json.dumps(payload).encode("utf-8")


def request_calls(mocked: Any, method: str, path: str) -> list:
    """Return the recorded calls for METHOD + path (query string ignored)."""
    return [
        call
        for (call_method, url), calls in mocked.requests.items()
        if call_method == method and url.path == path
        for call in calls
    ]
