"""Tests for the Home Assistant-side camera snapshot boundary."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BRIDGE_DIRECTORY = Path(__file__).resolve().parents[1] / "home_command_bridge"
sys.path.insert(0, str(BRIDGE_DIRECTORY))

from camera_policy import (  # noqa: E402
    BLINK_MIN_TRIGGER_SECONDS,
    MAX_CAMERA_SNAPSHOT_BYTES,
    camera_provider,
    camera_registry_metadata,
    entity_is_in_scope,
    remaining_camera_trigger_delay,
    safe_camera_entity_id,
    validate_camera_snapshot,
)


class CameraPolicyTests(unittest.TestCase):
    def test_camera_entity_ids_are_exact(self) -> None:
        self.assertTrue(safe_camera_entity_id("camera.front_door"))
        for value in (
            "light.front_door",
            "camera.FrontDoor",
            "camera.front-door",
            "camera.front/door",
            "camera.",
        ):
            self.assertFalse(safe_camera_entity_id(value))

    def test_camera_allowlist_limits_only_the_camera_domain(self) -> None:
        domains = {"camera", "light"}
        selected = {"camera.front_door"}
        self.assertTrue(entity_is_in_scope("camera.front_door", domains, selected))
        self.assertFalse(entity_is_in_scope("camera.backyard", domains, selected))
        self.assertTrue(entity_is_in_scope("light.kitchen", domains, selected))
        self.assertFalse(entity_is_in_scope("lock.front_door", domains, selected))
        self.assertTrue(entity_is_in_scope("camera.backyard", domains, set()))

    def test_provider_detection_handles_blink_aarlo_and_eufy_registry_names(self) -> None:
        self.assertEqual(camera_provider("camera.blink_entry", {}), "blink")
        self.assertEqual(camera_provider("camera.aarlo_patio", {}), "arlo")
        self.assertEqual(
            camera_provider("camera.entrance", {"integration": "eufy_security"}),
            "eufy",
        )
        self.assertEqual(camera_provider("camera.spring_drive", {}), "")

    def test_camera_registry_metadata_enriches_camera_states_only(self) -> None:
        metadata = camera_registry_metadata(
            [
                {
                    "entity_id": "camera.entrance",
                    "platform": "eufy_security",
                    "device_id": "device-one",
                },
                {
                    "entity_id": "light.porch",
                    "platform": "hue",
                    "device_id": "device-two",
                },
            ],
            [
                {
                    "id": "device-one",
                    "manufacturer": "Eufy",
                    "model": "EufyCam 3",
                },
            ],
        )
        self.assertEqual(
            metadata,
            {
                "camera.entrance": {
                    "integration": "eufy_security",
                    "manufacturer": "Eufy",
                    "model": "EufyCam 3",
                },
            },
        )

    def test_blink_trigger_delay_enforces_the_documented_minimum(self) -> None:
        self.assertEqual(remaining_camera_trigger_delay(None, 100.0), 0.0)
        self.assertEqual(
            remaining_camera_trigger_delay(98.0, 100.0),
            BLINK_MIN_TRIGGER_SECONDS - 2.0,
        )
        self.assertEqual(remaining_camera_trigger_delay(90.0, 100.0), 0.0)

    def test_supported_raster_snapshot_is_accepted(self) -> None:
        self.assertEqual(
            validate_camera_snapshot(200, "image/jpeg; charset=binary", b"jpeg"),
            "image/jpeg",
        )
        self.assertEqual(
            validate_camera_snapshot(200, "IMAGE/WEBP", b"webp"),
            "image/webp",
        )

    def test_camera_http_failure_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTP 503"):
            validate_camera_snapshot(503, "image/jpeg", b"error")

    def test_unsafe_content_types_are_rejected(self) -> None:
        for content_type in ("image/svg+xml", "text/html", "application/octet-stream", ""):
            with self.assertRaisesRegex(ValueError, "unsupported image"):
                validate_camera_snapshot(200, content_type, b"content")

    def test_empty_and_oversized_images_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty or too large"):
            validate_camera_snapshot(200, "image/jpeg", b"")
        with self.assertRaisesRegex(ValueError, "empty or too large"):
            validate_camera_snapshot(
                200,
                "image/jpeg",
                b"x" * (MAX_CAMERA_SNAPSHOT_BYTES + 1),
            )
        self.assertEqual(
            validate_camera_snapshot(
                200,
                "image/png",
                b"x" * MAX_CAMERA_SNAPSHOT_BYTES,
            ),
            "image/png",
        )


if __name__ == "__main__":
    unittest.main()
