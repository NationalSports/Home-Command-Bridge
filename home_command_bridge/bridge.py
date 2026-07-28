#!/usr/bin/env python3
"""Home Command Bridge for Home Assistant.

The bridge keeps Home Assistant private behind the homeowner's network. It
opens outbound-only connections to Home Command, publishes a filtered state
snapshot, and executes short-lived commands after the homeowner pairs it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import signal
import sys
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp

VERSION = "0.4.0"
OPTIONS_PATH = Path("/data/options.json")
CREDENTIALS_PATH = Path("/data/home-command-credentials.json")
CORE_REST_URL = "http://supervisor/core/api/"
CORE_WEBSOCKET_URL = "ws://supervisor/core/websocket"
PAIRING_POLL_SECONDS = 1.5
COMMAND_POLL_SECONDS = 1.5
STATE_FLUSH_SECONDS = 1.0
HEARTBEAT_SECONDS = 20.0
MAX_ATTRIBUTE_BYTES = 32_768
MAX_SNAPSHOT_BYTES = 1_500_000
MAX_REGISTRY_BYTES = 500_000
MAX_PROCESSED_COMMANDS = 200
ALEXA_SAFE_DOMAINS = {"light", "switch", "climate", "scene", "media_player"}
INTEGRATION_HEALTH_DOMAINS = {"tuya", "homekit", "homekit_controller", "matter"}
SENSITIVE_ATTRIBUTE_FRAGMENTS = ("token", "password", "secret", "credential", "api_key", "access_key")
REGISTRY_EVENT_TYPES = {
    "entity_registry_updated",
    "device_registry_updated",
    "area_registry_updated",
}


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else fallback
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def clean_url(value: str) -> str:
    return value.strip().rstrip("/") + "/"


def endpoint(base_url: str, path: str) -> str:
    return urljoin(clean_url(base_url), path.lstrip("/"))


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [json_safe(item) for item in value[:250]]
    if isinstance(value, dict):
        return {
            str(key): json_safe(item)
            for key, item in list(value.items())[:250]
            if not any(fragment in str(key).lower() for fragment in SENSITIVE_ATTRIBUTE_FRAGMENTS)
        }
    return str(value)


def sanitize_state(state: dict[str, Any]) -> dict[str, Any] | None:
    entity_id = state.get("entity_id")
    status = state.get("state")
    if not isinstance(entity_id, str) or not isinstance(status, str):
        return None
    attributes = json_safe(state.get("attributes") or {})
    if not isinstance(attributes, dict):
        attributes = {}
    encoded = json.dumps(attributes, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_ATTRIBUTE_BYTES:
        attributes = {
            "friendly_name": attributes.get("friendly_name"),
            "device_class": attributes.get("device_class"),
            "unit_of_measurement": attributes.get("unit_of_measurement"),
            "home_command_attributes_trimmed": True,
        }
    return {
        "entity_id": entity_id,
        "state": status[:1000],
        "attributes": attributes,
        "last_changed": state.get("last_changed"),
        "last_updated": state.get("last_updated"),
    }


def clean_registry_text(value: Any, maximum: int = 255) -> str:
    return str(value or "").strip()[:maximum]


def sanitize_registry_entity(value: dict[str, Any], categories: dict[str, Any]) -> dict[str, Any] | None:
    if value.get("disabled_by"):
        return None
    entity_id = clean_registry_text(value.get("ei") or value.get("entity_id"))
    if not entity_id or "." not in entity_id:
        return None
    category_value = value.get("ec")
    category = categories.get(str(category_value), category_value) if category_value is not None else ""
    return {
        "entity_id": entity_id,
        "platform": clean_registry_text(value.get("pl") or value.get("platform"), 100),
        "device_id": clean_registry_text(value.get("di") or value.get("device_id")),
        "area_id": clean_registry_text(value.get("ai") or value.get("area_id")),
        "name": clean_registry_text(
            value.get("en") or value.get("name") or value.get("original_name")
        ),
        "entity_category": clean_registry_text(category, 50),
        "hidden": bool(value.get("hb") or value.get("hidden_by")),
    }


def sanitize_registry_device(value: dict[str, Any]) -> dict[str, Any] | None:
    device_id = clean_registry_text(value.get("id"))
    if not device_id:
        return None
    return {
        "id": device_id,
        "area_id": clean_registry_text(value.get("area_id")),
        "name": clean_registry_text(value.get("name_by_user") or value.get("name")),
        "manufacturer": clean_registry_text(value.get("manufacturer")),
        "model": clean_registry_text(value.get("model")),
        "model_id": clean_registry_text(value.get("model_id")),
        "via_device_id": clean_registry_text(value.get("via_device_id")),
    }


def sanitize_registry_area(value: dict[str, Any]) -> dict[str, Any] | None:
    area_id = clean_registry_text(value.get("area_id") or value.get("id"))
    name = clean_registry_text(value.get("name"))
    if not area_id or not name:
        return None
    return {
        "id": area_id,
        "name": name,
        "floor_id": clean_registry_text(value.get("floor_id")),
    }


class HomeAssistantSocket:
    def __init__(self, session: aiohttp.ClientSession, token: str, log: logging.Logger):
        self.session = session
        self.token = token
        self.log = log
        self.socket: aiohttp.ClientWebSocketResponse | None = None
        self.next_id = 1
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.reader_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        self.socket = await self.session.ws_connect(CORE_WEBSOCKET_URL, heartbeat=30)
        hello = await self.socket.receive_json()
        if hello.get("type") != "auth_required":
            raise RuntimeError("Unexpected Home Assistant authentication greeting.")
        await self.socket.send_json({"type": "auth", "access_token": self.token})
        auth = await self.socket.receive_json()
        if auth.get("type") != "auth_ok":
            raise RuntimeError("Home Assistant rejected the Supervisor credential.")
        self.reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self.socket is not None
        try:
            async for message in self.socket:
                if message.type != aiohttp.WSMsgType.TEXT:
                    if message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                        break
                    continue
                payload = json.loads(message.data)
                message_id = payload.get("id")
                if payload.get("type") == "event":
                    await self.events.put(payload)
                elif isinstance(message_id, int) and message_id in self.pending:
                    future = self.pending.pop(message_id)
                    if not future.done():
                        future.set_result(payload)
        finally:
            error = RuntimeError("Home Assistant WebSocket disconnected.")
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            self.pending.clear()

    async def command(self, payload: dict[str, Any], timeout: float = 30) -> Any:
        if not self.socket or self.socket.closed:
            raise RuntimeError("Home Assistant WebSocket is not connected.")
        message_id = self.next_id
        self.next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[message_id] = future
        await self.socket.send_json({"id": message_id, **payload})
        response = await asyncio.wait_for(future, timeout)
        if not response.get("success"):
            error = response.get("error") or {}
            raise RuntimeError(str(error.get("message") or "Home Assistant command failed."))
        return response.get("result")

    async def close(self) -> None:
        if self.socket and not self.socket.closed:
            await self.socket.close()
        if self.reader_task:
            self.reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.reader_task


class HomeCommandBridge:
    def __init__(self) -> None:
        self.options = load_json(OPTIONS_PATH, {})
        self.credentials = load_json(CREDENTIALS_PATH, {})
        self.base_url = clean_url(str(self.options.get("home_command_url") or ""))
        self.pairing_code = str(self.options.get("pairing_code") or "").strip()
        self.sync_domains = {
            str(domain).lower()
            for domain in self.options.get("sync_domains", [])
            if isinstance(domain, str) and domain
        }
        self.supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
        self.log = logging.getLogger("home_command")
        self.http: aiohttp.ClientSession | None = None
        self.ha: HomeAssistantSocket | None = None
        self.entities: dict[str, dict[str, Any]] = {}
        self.registry: dict[str, list[dict[str, Any]]] = {
            "entities": [],
            "devices": [],
            "areas": [],
        }
        self.exposure: dict[str, Any] = {"available": False, "entities": {}}
        self.integration_health: dict[str, Any] = {
            "available": False,
            "bridgeVersion": VERSION,
            "homeAssistantVersion": "unknown",
            "domains": {
                domain: {
                    "configured": False,
                    "entryCount": 0,
                    "loadedEntryCount": 0,
                }
                for domain in sorted(INTEGRATION_HEALTH_DOMAINS)
            },
        }
        self.dirty = asyncio.Event()
        self.stopping = asyncio.Event()
        self.ha_connected = asyncio.Event()
        self.state_sequence = int(self.credentials.get("stateSequence") or 0)
        saved_outcomes = self.credentials.get("commandOutcomes") or {}
        self.command_outcomes: dict[str, dict[str, str]] = (
            saved_outcomes if isinstance(saved_outcomes, dict) else {}
        )

    def save_credentials(self) -> None:
        self.credentials["stateSequence"] = self.state_sequence
        self.credentials["commandOutcomes"] = dict(
            list(self.command_outcomes.items())[-MAX_PROCESSED_COMMANDS:]
        )
        save_json(CREDENTIALS_PATH, self.credentials)

    def relay_headers(self) -> dict[str, str]:
        secret = str(self.credentials.get("bridgeRelaySecret") or "")
        return {"Authorization": f"BridgeRelay {secret}"}

    def in_scope(self, entity_id: str) -> bool:
        return entity_id.partition(".")[0] in self.sync_domains

    def snapshot_entities(self) -> list[dict[str, Any]]:
        snapshot: list[dict[str, Any]] = []
        size = 0
        for state in list(self.entities.values())[:5_000]:
            encoded = json.dumps(state, separators=(",", ":")).encode("utf-8")
            if size + len(encoded) > MAX_SNAPSHOT_BYTES:
                self.log.warning(
                    "State snapshot reached the safe relay size; %d entities included.",
                    len(snapshot),
                )
                break
            snapshot.append(state)
            size += len(encoded)
        return snapshot

    def snapshot_registry(self) -> dict[str, list[dict[str, Any]]]:
        snapshot = {
            "entities": self.registry["entities"][:5_000],
            "devices": self.registry["devices"][:2_000],
            "areas": self.registry["areas"][:500],
        }
        encoded = json.dumps(snapshot, separators=(",", ":")).encode("utf-8")
        if len(encoded) <= MAX_REGISTRY_BYTES:
            return snapshot
        self.log.warning("Registry snapshot reached the safe relay size; metadata was trimmed.")
        return {
            "entities": snapshot["entities"][:2_500],
            "devices": snapshot["devices"][:1_000],
            "areas": snapshot["areas"],
        }

    async def refresh_registries(self) -> None:
        if not self.ha:
            return
        try:
            entity_result = await self.ha.command({
                "type": "config/entity_registry/list_for_display"
            })
        except RuntimeError:
            entity_result = await self.ha.command({"type": "config/entity_registry/list"})
        if isinstance(entity_result, dict):
            raw_entities = entity_result.get("entities") or []
            raw_categories = entity_result.get("entity_categories") or {}
        else:
            raw_entities = entity_result or []
            raw_categories = {}
        categories = {
            str(key): clean_registry_text(value, 50)
            for key, value in raw_categories.items()
        } if isinstance(raw_categories, dict) else {}
        entities = [
            entity
            for raw in raw_entities
            if isinstance(raw, dict)
            for entity in [sanitize_registry_entity(raw, categories)]
            if entity and self.in_scope(entity["entity_id"])
        ]

        raw_devices = await self.ha.command({"type": "config/device_registry/list"})
        devices = [
            device
            for raw in raw_devices or []
            if isinstance(raw, dict)
            for device in [sanitize_registry_device(raw)]
            if device
        ]
        referenced_devices = {
            entity["device_id"] for entity in entities if entity["device_id"]
        }
        referenced_devices.update(
            device["via_device_id"]
            for device in devices
            if device["id"] in referenced_devices and device["via_device_id"]
        )
        devices = [device for device in devices if device["id"] in referenced_devices]

        raw_areas = await self.ha.command({"type": "config/area_registry/list"})
        areas = [
            area
            for raw in raw_areas or []
            if isinstance(raw, dict)
            for area in [sanitize_registry_area(raw)]
            if area
        ]
        referenced_areas = {
            entity["area_id"] for entity in entities if entity["area_id"]
        } | {
            device["area_id"] for device in devices if device["area_id"]
        }
        self.registry = {
            "entities": entities,
            "devices": devices,
            "areas": [area for area in areas if area["id"] in referenced_areas],
        }

    async def refresh_exposure(self) -> None:
        if not self.ha:
            return
        try:
            result = await self.ha.command({"type": "homeassistant/expose_entity/list"})
        except RuntimeError as error:
            self.exposure = {"available": False, "entities": {}}
            self.log.info("Assistant exposure controls are unavailable: %s", error)
            return
        raw_entities = result.get("exposed_entities") if isinstance(result, dict) else {}
        entities: dict[str, dict[str, bool]] = {}
        if isinstance(raw_entities, dict):
            for entity_id, assistants in raw_entities.items():
                domain = str(entity_id).partition(".")[0]
                if (
                    entity_id not in self.entities
                    or domain not in ALEXA_SAFE_DOMAINS
                    or not isinstance(assistants, dict)
                ):
                    continue
                alexa = assistants.get("cloud.alexa")
                if isinstance(alexa, bool):
                    entities[entity_id] = {"cloud.alexa": alexa}
        self.exposure = {"available": True, "entities": entities}

    async def refresh_integration_health(self) -> None:
        if not self.ha:
            return
        domains = {
            domain: {
                "configured": False,
                "entryCount": 0,
                "loadedEntryCount": 0,
            }
            for domain in sorted(INTEGRATION_HEALTH_DOMAINS)
        }
        available = False
        try:
            result = await self.ha.command({"type": "config_entries/get"})
            raw_entries = result.get("entries") if isinstance(result, dict) else result
            for entry in raw_entries or []:
                if not isinstance(entry, dict):
                    continue
                domain = str(entry.get("domain") or "")
                if domain not in domains:
                    continue
                domains[domain]["entryCount"] += 1
                if str(entry.get("state") or "").lower() == "loaded":
                    domains[domain]["loadedEntryCount"] += 1
            for status in domains.values():
                status["configured"] = status["entryCount"] > 0
            available = True
        except RuntimeError as error:
            self.log.info("Integration health controls are unavailable: %s", error)

        home_assistant_version = "unknown"
        try:
            config = await self.ha.command({"type": "get_config"})
            if isinstance(config, dict):
                home_assistant_version = clean_registry_text(config.get("version"), 100) or "unknown"
        except RuntimeError:
            home_assistant_version = await self.home_assistant_version()

        self.integration_health = {
            "available": available,
            "bridgeVersion": VERSION,
            "homeAssistantVersion": home_assistant_version,
            "domains": domains,
        }

    def snapshot_integration_health(self) -> dict[str, Any]:
        eligible_entity_ids = {
            entity_id
            for entity_id in self.entities
            if entity_id.partition(".")[0] in ALEXA_SAFE_DOMAINS
        }
        exposure_entities = self.exposure.get("entities")
        explicit_entities = exposure_entities if isinstance(exposure_entities, dict) else {}
        return {
            **self.integration_health,
            "alexa": {
                "exposureAvailable": bool(self.exposure.get("available")),
                "eligibleEntityCount": len(eligible_entity_ids),
                "explicitEntityCount": len(explicit_entities),
                "explicitlyExposedCount": sum(
                    1
                    for assistants in explicit_entities.values()
                    if isinstance(assistants, dict)
                    and assistants.get("cloud.alexa") is True
                ),
            },
        }

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        assert self.http is not None
        async with self.http.request(
            method,
            endpoint(self.base_url, path),
            headers=headers,
            json=payload,
        ) as response:
            body = await response.json(content_type=None)
            if response.status not in expected:
                message = body.get("error") if isinstance(body, dict) else None
                raise RuntimeError(str(message or f"Home Command returned HTTP {response.status}."))
            if not isinstance(body, dict):
                raise RuntimeError("Home Command returned an invalid response.")
            return body

    async def home_assistant_version(self) -> str:
        assert self.http is not None
        async with self.http.get(
            CORE_REST_URL + "config",
            headers={"Authorization": f"Bearer {self.supervisor_token}"},
        ) as response:
            if response.status != 200:
                return "unknown"
            config = await response.json()
            return str(config.get("version") or "unknown")

    async def pair_if_needed(self) -> None:
        if self.credentials.get("homeId") and self.credentials.get("bridgeRelaySecret"):
            return
        if not self.base_url.startswith(("https://", "http://")):
            raise RuntimeError("Set a valid Home Command URL in the app configuration.")
        code_fingerprint = hashlib.sha256(
            self.pairing_code.upper().replace("-", "").encode("utf-8")
        ).hexdigest() if self.pairing_code else ""
        saved_fingerprint = str(self.credentials.get("pairingCodeFingerprint") or "")
        if code_fingerprint and saved_fingerprint and code_fingerprint != saved_fingerprint:
            self.credentials.pop("pairingId", None)
            self.credentials.pop("claimSecret", None)
            self.credentials.pop("pendingRelaySecret", None)
            self.save_credentials()

        pairing_id = str(self.credentials.get("pairingId") or "")
        claim_secret = str(self.credentials.get("claimSecret") or "")
        if not pairing_id or not claim_secret:
            if not self.pairing_code:
                raise RuntimeError("Enter the one-time pairing code in the app configuration.")
            device_id = str(self.credentials.get("deviceId") or secrets.token_urlsafe(24))
            self.credentials["deviceId"] = device_id
            self.save_credentials()
            identity = {
                "name": "Jarvis",
                "bridgeVersion": VERSION,
                "homeAssistantVersion": await self.home_assistant_version(),
                "deviceId": device_id,
            }
            claim = await self.request_json(
                "POST",
                "/api/bridge/pairing/claim",
                payload={"code": self.pairing_code, "bridge": identity},
                expected=(201,),
            )
            pairing = claim.get("pairing") or {}
            pairing_id = str(pairing.get("id") or "")
            claim_secret = str(claim.get("claimSecret") or "")
            if not pairing_id or not claim_secret:
                raise RuntimeError("Home Command did not return a pairing claim.")
            self.credentials.update({
                "pairingId": pairing_id,
                "claimSecret": claim_secret,
                "pairingCodeFingerprint": code_fingerprint,
            })
            self.save_credentials()
            self.log.info("Pairing code accepted. Waiting for homeowner confirmation.")
        else:
            self.log.info("Resuming homeowner confirmation for this bridge.")

        while not self.stopping.is_set():
            status = await self.request_json(
                "GET",
                f"/api/bridge/pairing/claim?id={pairing_id}",
                headers={"Authorization": f"Bridge {claim_secret}"},
            )
            if status.get("confirmed"):
                break
            await asyncio.sleep(PAIRING_POLL_SECONDS)

        relay_secret = str(self.credentials.get("pendingRelaySecret") or secrets.token_urlsafe(48))
        self.credentials["pendingRelaySecret"] = relay_secret
        self.save_credentials()
        registration = await self.request_json(
            "POST",
            "/api/bridge/relay/register",
            headers={"Authorization": f"Bridge {claim_secret}"},
            payload={"pairingId": pairing_id, "bridgeRelaySecret": relay_secret},
            expected=(200, 201),
        )
        home_id = str(registration.get("homeId") or "")
        if not home_id:
            raise RuntimeError("Home Command did not confirm relay registration.")
        self.credentials.update({"homeId": home_id, "bridgeRelaySecret": relay_secret})
        self.credentials.pop("claimSecret", None)
        self.credentials.pop("pendingRelaySecret", None)
        self.credentials.pop("pairingCodeFingerprint", None)
        self.save_credentials()
        self.log.info("Home Command pairing confirmed. Secure relay registered.")

    async def home_assistant_loop(self) -> None:
        delay = 1.0
        while not self.stopping.is_set():
            try:
                assert self.http is not None
                self.ha = HomeAssistantSocket(self.http, self.supervisor_token, self.log)
                await self.ha.connect()
                states = await self.ha.command({"type": "get_states"})
                self.entities = {}
                for raw_state in states or []:
                    entity_id = str(raw_state.get("entity_id") or "")
                    if self.in_scope(entity_id):
                        state = sanitize_state(raw_state)
                        if state:
                            self.entities[entity_id] = state
                await self.refresh_registries()
                await self.refresh_exposure()
                await self.refresh_integration_health()
                await self.ha.command({"type": "subscribe_events", "event_type": "state_changed"})
                for event_type in REGISTRY_EVENT_TYPES:
                    await self.ha.command({"type": "subscribe_events", "event_type": event_type})
                self.ha_connected.set()
                self.dirty.set()
                delay = 1.0
                self.log.info(
                    "Home Assistant connected; %d entities approved for Home Command.",
                    len(self.entities),
                )

                while not self.stopping.is_set():
                    payload = await self.ha.events.get()
                    event = payload.get("event") or {}
                    event_type = str(event.get("event_type") or "")
                    if event_type in REGISTRY_EVENT_TYPES:
                        await self.refresh_registries()
                        self.dirty.set()
                        continue
                    data = event.get("data") or {}
                    entity_id = str(data.get("entity_id") or "")
                    if not self.in_scope(entity_id):
                        continue
                    raw_state = data.get("new_state")
                    if raw_state is None:
                        self.entities.pop(entity_id, None)
                    elif isinstance(raw_state, dict):
                        state = sanitize_state(raw_state)
                        if state:
                            self.entities[entity_id] = state
                    self.dirty.set()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.ha_connected.clear()
                self.log.warning("Home Assistant connection interrupted: %s; retrying in %.0fs.", error, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
            finally:
                if self.ha:
                    await self.ha.close()
                    self.ha = None

    async def integration_health_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                await self.ha_connected.wait()
                if self.ha:
                    await self.refresh_integration_health()
                    self.dirty.set()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.log.info("Integration health refresh interrupted: %s", error)
                await asyncio.sleep(10)

    async def state_loop(self) -> None:
        last_sent = 0.0
        while not self.stopping.is_set():
            try:
                timeout = max(0.1, HEARTBEAT_SECONDS - (asyncio.get_running_loop().time() - last_sent))
                try:
                    await asyncio.wait_for(self.dirty.wait(), timeout=timeout)
                    await asyncio.sleep(STATE_FLUSH_SECONDS)
                except asyncio.TimeoutError:
                    pass
                if not self.ha_connected.is_set():
                    await asyncio.sleep(1)
                    continue
                self.dirty.clear()
                self.state_sequence += 1
                self.save_credentials()
                await self.request_json(
                    "POST",
                    "/api/bridge/relay/state",
                    headers=self.relay_headers(),
                    payload={
                        "homeId": self.credentials["homeId"],
                        "sequence": self.state_sequence,
                        "entities": self.snapshot_entities(),
                        "registry": self.snapshot_registry(),
                        "exposure": self.exposure,
                        "integrationHealth": self.snapshot_integration_health(),
                        "capturedAt": datetime.now(timezone.utc).isoformat(),
                    },
                )
                last_sent = asyncio.get_running_loop().time()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.log.warning("State relay interrupted: %s", error)
                self.dirty.set()
                await asyncio.sleep(3)

    async def acknowledge(self, command_id: str, status: str, error: str = "") -> None:
        await self.request_json(
            "POST",
            "/api/bridge/relay/commands/ack",
            headers=self.relay_headers(),
            payload={
                "homeId": self.credentials["homeId"],
                "commandId": command_id,
                "status": status,
                "error": error[:500],
            },
        )

    async def execute_command(self, command: dict[str, Any]) -> None:
        command_id = str(command.get("id") or "")
        if not command_id:
            return
        previous = self.command_outcomes.get(command_id)
        if previous:
            await self.acknowledge(command_id, previous["status"], previous.get("error", ""))
            return
        outcome = {"status": "failed", "error": "Command did not complete."}
        try:
            await self.ha_connected.wait()
            if not self.ha:
                raise RuntimeError("Home Assistant is unavailable.")
            if command.get("kind") == "expose_entities":
                assistant = str(command.get("assistant") or "")
                entity_ids = command.get("entityIds")
                should_expose = command.get("shouldExpose")
                if assistant != "cloud.alexa":
                    raise RuntimeError("Only Alexa exposure is approved for Home Command.")
                if (
                    not isinstance(entity_ids, list)
                    or not 1 <= len(entity_ids) <= 100
                    or not isinstance(should_expose, bool)
                ):
                    raise RuntimeError("The Alexa exposure command is invalid.")
                for entity_id in entity_ids:
                    domain = str(entity_id).partition(".")[0]
                    if entity_id not in self.entities or domain not in ALEXA_SAFE_DOMAINS:
                        raise RuntimeError(
                            "Alexa exposure is limited to approved lights, switches, climate, scenes, and media players."
                        )
                await self.ha.command({
                    "type": "homeassistant/expose_entity",
                    "assistants": ["cloud.alexa"],
                    "entity_ids": entity_ids,
                    "should_expose": should_expose,
                })
                await self.refresh_exposure()
                self.dirty.set()
                outcome = {"status": "completed", "error": ""}
                self.log.info(
                    "Updated Alexa exposure for %d approved entities.",
                    len(entity_ids),
                )
            else:
                domain = str(command.get("domain") or "")
                service = str(command.get("service") or "")
                if domain not in self.sync_domains:
                    raise RuntimeError(f"The {domain} domain is not approved for Home Command.")
                await self.ha.command({
                    "type": "call_service",
                    "domain": domain,
                    "service": service,
                    "target": command.get("target") or {},
                    "service_data": command.get("serviceData") or {},
                })
                outcome = {"status": "completed", "error": ""}
                self.log.info("Completed approved %s.%s command.", domain, service)
        except Exception as error:
            self.log.warning("Command %s failed: %s", command_id[:8], error)
            outcome = {"status": "failed", "error": str(error)[:500]}
        finally:
            self.command_outcomes[command_id] = outcome
            self.command_outcomes = dict(
                list(self.command_outcomes.items())[-MAX_PROCESSED_COMMANDS:]
            )
            self.save_credentials()
        await self.acknowledge(command_id, outcome["status"], outcome["error"])

    async def command_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                query = f"/api/bridge/relay/commands?homeId={self.credentials['homeId']}"
                payload = await self.request_json("GET", query, headers=self.relay_headers())
                for command in payload.get("commands") or []:
                    if isinstance(command, dict):
                        await self.execute_command(command)
                await asyncio.sleep(COMMAND_POLL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.log.warning("Command relay interrupted: %s", error)
                await asyncio.sleep(3)

    async def run(self) -> None:
        if not self.supervisor_token:
            raise RuntimeError("Home Assistant did not provide a Supervisor token.")
        timeout = aiohttp.ClientTimeout(total=35)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            self.http = session
            await self.pair_if_needed()
            tasks = [
                asyncio.create_task(self.home_assistant_loop(), name="home-assistant"),
                asyncio.create_task(self.integration_health_loop(), name="integration-health"),
                asyncio.create_task(self.state_loop(), name="state-relay"),
                asyncio.create_task(self.command_loop(), name="command-relay"),
            ]
            await self.stopping.wait()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def main() -> None:
    options = load_json(OPTIONS_PATH, {})
    level = str(options.get("log_level") or "info").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s [home_command] %(message)s",
    )
    bridge = HomeCommandBridge()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signal_name, bridge.stopping.set)
    logging.getLogger("home_command").info("Home Command Bridge %s starting.", VERSION)
    await bridge.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logging.getLogger("home_command").critical("%s", exc)
        sys.exit(1)
