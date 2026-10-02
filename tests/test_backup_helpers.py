"""Tests for the pure helpers on PCloudBackupAgent."""

from __future__ import annotations

from homeassistant.components.backup import AgentBackup
from homeassistant.core import HomeAssistant
import pytest

from custom_components.pcloud_backup.backup import PCloudBackupAgent

from .common import AGENT_ID, ENTRY_ID


@pytest.fixture
def agent(hass: HomeAssistant, mock_api) -> PCloudBackupAgent:
    """Return a backup agent bound to a mocked API."""
    return PCloudBackupAgent(hass, ENTRY_ID, api=mock_api)


def _backup(backup_id: str) -> AgentBackup:
    return AgentBackup(
        addons=[],
        backup_id=backup_id,
        date="2025-01-02T03:00:00+00:00",
        database_included=True,
        extra_metadata={},
        folders=[],
        homeassistant_included=True,
        homeassistant_version="2025.1.0",
        name="Automatic backup 2025.1.0",
        protected=False,
        size=1,
    )


def test_agent_identity(agent: PCloudBackupAgent) -> None:
    """The agent slug follows the "<domain>.<entry_id>" convention."""
    assert agent.domain == "pcloud_backup"
    assert agent.unique_id == ENTRY_ID
    assert agent.slug == AGENT_ID


@pytest.mark.parametrize(
    ("filename", "key"),
    [("backup.tar", "backup"), ("my.backup.tar", "my.backup"), ("backup.zip", "backup.zip")],
)
def test_metadata_key_from_backup_name(agent: PCloudBackupAgent, filename: str, key: str) -> None:
    """Only a trailing .tar is stripped."""
    assert agent._metadata_key_from_backup_name(filename) == key


def test_metadata_key_from_metadata_name(agent: PCloudBackupAgent) -> None:
    """The .metadata.json suffix is stripped."""
    assert agent._metadata_key_from_metadata_name("backup.metadata.json") == "backup"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (f"{AGENT_ID}:backup.tar", "backup.tar"),
        ("other:with:colons", "with:colons"),
        ("backup.tar", "backup.tar"),
    ],
)
def test_extract_backup_filename(agent: PCloudBackupAgent, value: str, expected: str) -> None:
    """The part after the first colon is the filename; plain names pass through."""
    assert agent._extract_backup_filename(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(f"{AGENT_ID}:backup.tar", "backup"), ("backup", "backup"), ("backup.tar", "backup")],
)
def test_metadata_key_from_input(agent: PCloudBackupAgent, value: str, expected: str) -> None:
    """Identifiers are reduced to the metadata key."""
    assert agent._metadata_key_from_input(value) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Automatic_backup_2025.1.0.tar", "Automatic backup 2025.1.0.tar"),
        ("My%20Backup.tar", "My Backup.tar"),
        ("Mixed name_with underscore", "Mixed name_with underscore"),
        ("plain", "plain"),
    ],
)
def test_decode_display_name(agent: PCloudBackupAgent, name: str, expected: str) -> None:
    """URL-encoding is decoded and slug underscores become spaces."""
    assert agent._decode_display_name(name) == expected


def test_metadata_key_for_backup_slug_id(agent: PCloudBackupAgent) -> None:
    """Slug-format backup IDs carry the metadata key after the colon."""
    assert agent._metadata_key_for_backup(_backup(f"{AGENT_ID}:some_key")) == "some_key"


def test_metadata_key_for_backup_filename_fallback(agent: PCloudBackupAgent) -> None:
    """Other backup IDs fall back to HA's suggested filename (without .tar)."""
    assert (
        agent._metadata_key_for_backup(_backup("a1b2c3d4"))
        == "Automatic_backup_2025.1.0_2025-01-02_03.00_00000000"
    )
