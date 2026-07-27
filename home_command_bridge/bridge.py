#!/usr/bin/env python3
"""Residence Bridge for Home Assistant.

The bridge keeps Home Assistant private behind the homeowner's network. It
opens outbound-only connections to Residence, publishes a filtered state
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
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp

VERSION = "0.1.0"
OPTIONS_PATH = Path("/data/options.json")
CREDENTIALS_PATH = Path("/data/residence-credentials.json")
CORE_REST_URL = "http://supervisor/core/api/"
CORE_WEBSOCKET_URL = "ws://supervisor/core/websocket"
PAIRING_POLL_SECONDS = 1.5
COMMAND_POLL_SECONDS = 1.5
STATE_FLUSH_SECONDS = 1.0
HEARTBEAT_SECONDS = 20.0
MAX_ATTRIBUTE_BYTES = 32_768
MAX_SNAPSHOT_BYTES = 2_000_000
MAX_PROCESSED_COMMANDS = 200
SENSITIVE_ATTRIBUTE_FRAGMENTS = ("token", "password", "secret", "credential", "api_key", "access_key")


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
            "residence_attributes_trimmed": True,
        }
    return {
        "entity_id": entity_id,
        "state": status[:1000],
        "attributes": attributes,
        "last_changed": state.get("last_changed"),
        "last_updated": state.get("last_updated"),
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


class ResidenceBridge:
    def __init__(self) -> None:
        self.options = load_json(OPTIONS_PATH, {})
        self.credentials = load_json(CREDENTIALS_PATH, {})
        self.base_url = clean_url(str(self.options.get("residence_url") or ""))
        self.pairing_code = str(self.options.get("pairing_code") or "").strip()
        self.sync_domains = {
            str(domain).lower()
            for domain in self.options.get("sync_domains", [])
            if isinstance(domain, str) and domain
        }
        self.supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
        self.log = logging.getLogger("residence")
        self.http: aiohttp.ClientSession | None = None
        self.ha: HomeAssistantSocket | None = None
        self.entities: dict[str, dict[str, Any]] = {}
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
                raise RuntimeError(str(message or f"Residence returned HTTP {response.status}."))
            if not isinstance(body, dict):
                raise RuntimeError("Residence returned an invalid response.")
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
            raise RuntimeError("Set a valid Residence URL in the app configuration.")
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
                raise RuntimeError("Residence did not return a pairing claim.")
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
            raise RuntimeError("Residence did not confirm relay registration.")
        self.credentials.update({"homeId": home_id, "bridgeRelaySecret": relay_secret})
        self.credentials.pop("claimSecret", None)
        self.credentials.pop("pendingRelaySecret", None)
        self.credentials.pop("pairingCodeFingerprint", None)
        self.save_credentials()
        self.log.info("Residence pairing confirmed. Secure relay registered.")

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
                await self.ha.command({"type": "subscribe_events", "event_type": "state_changed"})
                self.ha_connected.set()
                self.dirty.set()
                delay = 1.0
                self.log.info("Home Assistant connected; %d entities approved for Residence.", len(self.entities))

                while not self.stopping.is_set():
                    payload = await self.ha.events.get()
                    event = payload.get("event") or {}
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
            domain = str(command.get("domain") or "")
            service = str(command.get("service") or "")
            if domain not in self.sync_domains:
                raise RuntimeError(f"The {domain} domain is not approved for Residence.")
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
        format="%(asctime)s %(levelname)s [residence] %(message)s",
    )
    bridge = ResidenceBridge()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signal_name, bridge.stopping.set)
    logging.getLogger("residence").info("Residence Bridge %s starting.", VERSION)
    await bridge.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logging.getLogger("residence").critical("%s", exc)
        sys.exit(1)
