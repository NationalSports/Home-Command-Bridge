"""Validation shared by the Home Command camera relay and its tests."""

from __future__ import annotations

import re

MAX_CAMERA_SNAPSHOT_BYTES = 5_000_000
CAMERA_SNAPSHOT_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
BLINK_MIN_TRIGGER_SECONDS = 5.0


def safe_camera_entity_id(value: str) -> bool:
    return bool(re.fullmatch(r"camera\.[a-z0-9_]+", value))


def entity_is_in_scope(
    entity_id: str,
    sync_domains: set[str],
    camera_entities: set[str],
) -> bool:
    domain = entity_id.partition(".")[0]
    if domain not in sync_domains:
        return False
    if domain == "camera" and camera_entities:
        return entity_id in camera_entities
    return True


def camera_provider(entity_id: str, attributes: dict[str, object]) -> str:
    values = [
        entity_id,
        str(attributes.get("friendly_name") or ""),
        str(attributes.get("integration") or attributes.get("platform") or ""),
        str(attributes.get("manufacturer") or attributes.get("brand") or ""),
        str(attributes.get("model") or attributes.get("model_name") or ""),
    ]
    haystack = re.sub(r"[_-]+", " ", " ".join(values)).lower()
    if re.search(r"\bblink\b", haystack):
        return "blink"
    if re.search(r"\b(?:aarlo|arlo)\b", haystack):
        return "arlo"
    if re.search(r"\beufy(?:cam)?\b", haystack):
        return "eufy"
    return ""


def camera_registry_metadata(
    entity_registry: list[dict[str, object]],
    device_registry: list[dict[str, object]],
) -> dict[str, dict[str, str]]:
    devices = {
        str(device.get("id")): device
        for device in device_registry
        if device.get("id")
    }
    metadata: dict[str, dict[str, str]] = {}
    for entity in entity_registry:
        entity_id = str(entity.get("entity_id") or "")
        if not safe_camera_entity_id(entity_id):
            continue
        values: dict[str, str] = {}
        platform = str(entity.get("platform") or "")
        if platform:
            values["integration"] = platform
        device = devices.get(str(entity.get("device_id") or ""), {})
        manufacturer = str(device.get("manufacturer") or "")
        model = str(device.get("model") or "")
        if manufacturer:
            values["manufacturer"] = manufacturer
        if model:
            values["model"] = model
        if values:
            metadata[entity_id] = values
    return metadata


def remaining_camera_trigger_delay(
    last_trigger_at: float | None,
    now: float,
    minimum_seconds: float = BLINK_MIN_TRIGGER_SECONDS,
) -> float:
    if last_trigger_at is None:
        return 0.0
    return max(0.0, minimum_seconds - (now - last_trigger_at))


def validate_camera_snapshot(
    status: int,
    content_type_header: str,
    image: bytes,
) -> str:
    if status != 200:
        raise ValueError(f"Home Assistant camera returned HTTP {status}.")
    content_type = content_type_header.partition(";")[0].strip().lower()
    if content_type not in CAMERA_SNAPSHOT_CONTENT_TYPES:
        raise ValueError("Home Assistant camera returned an unsupported image.")
    if not image or len(image) > MAX_CAMERA_SNAPSHOT_BYTES:
        raise ValueError("Home Assistant camera snapshot is empty or too large.")
    return content_type
