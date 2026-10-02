"""Translation / strings parity checks (no Home Assistant runtime needed)."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import pytest

from custom_components.pcloud_backup.config_flow import _backup_options_schema
from custom_components.pcloud_backup.sensor import SENSOR_TYPES

INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "pcloud_backup"
STRINGS = INTEGRATION_DIR / "strings.json"
TRANSLATIONS = sorted((INTEGRATION_DIR / "translations").glob("*.json"))


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten(data: dict[str, Any], prefix: str = "") -> set[str]:
    """Return dotted paths of all leaf keys."""
    keys: set[str] = set()
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            keys |= _flatten(value, f"{path}.")
        else:
            keys.add(path)
    return keys


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"{(\w+)}", text))


def _leaves(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    leaves: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            leaves |= _leaves(value, f"{prefix}{key}.")
        else:
            leaves[f"{prefix}{key}"] = value
    return leaves


def test_translation_files_exist() -> None:
    """English and German translations are shipped."""
    assert {path.name for path in TRANSLATIONS} >= {"en.json", "de.json"}


def test_strings_matches_english() -> None:
    """strings.json is the source of truth and en.json mirrors it exactly."""
    assert _load(STRINGS) == _load(INTEGRATION_DIR / "translations" / "en.json")


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda path: path.name)
def test_translation_keys_match_strings(path: Path) -> None:
    """Every translation has exactly the keys of strings.json (no missing or orphan keys)."""
    expected = _flatten(_load(STRINGS))
    actual = _flatten(_load(path))
    assert not expected - actual, f"missing in {path.name}"
    assert not actual - expected, f"orphan keys in {path.name}"


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda path: path.name)
def test_translation_placeholders_match_strings(path: Path) -> None:
    """Translations use the same {placeholders} as strings.json."""
    source = _leaves(_load(STRINGS))
    for key, text in _leaves(_load(path)).items():
        assert _placeholders(text) == _placeholders(source[key]), f"{path.name}: {key}"


@pytest.mark.parametrize("path", [STRINGS, *TRANSLATIONS], ids=lambda path: path.name)
def test_sensor_translation_keys_exist(path: Path) -> None:
    """Every sensor translation_key has an entity.sensor.<key>.name entry."""
    sensors = _load(path).get("entity", {}).get("sensor", {})
    for description in SENSOR_TYPES:
        assert description.translation_key in sensors, description.translation_key
        assert sensors[description.translation_key].get("name")


def test_no_orphan_sensor_translations() -> None:
    """strings.json has no entity.sensor entries without a matching sensor."""
    used = {description.translation_key for description in SENSOR_TYPES}
    assert set(_load(STRINGS)["entity"]["sensor"]) == used


def test_option_fields_are_translated() -> None:
    """All fields of the shared options schema have labels in both steps."""
    strings = _load(STRINGS)
    schema = _backup_options_schema(
        backup_folder_default="/", upload_timeout_default=600, permanent_delete_default=False
    )
    fields = {str(key) for key in schema.schema}
    assert fields <= set(strings["config"]["step"]["folder_path"]["data"])
    assert fields <= set(strings["options"]["step"]["init"]["data"])


def test_config_flow_errors_are_translated() -> None:
    """Every errors["base"] value set by the config flow has a translation."""
    source = (INTEGRATION_DIR / "config_flow.py").read_text(encoding="utf-8")
    used = set(re.findall(r'errors\["base"\] = "(\w+)"', source))
    assert used, "no errors found - regex out of date?"
    assert used <= set(_load(STRINGS)["config"]["error"])
