# Changelog

## 0.4.0

- Publish privacy-filtered setup health for the official Tuya, HomeKit Bridge,
  HomeKit Device, and Matter integrations without relaying config-entry titles
  or credentials.
- Publish Bridge and Home Assistant versions plus Alexa eligible, explicit, and
  explicitly exposed entity counts.
- Refresh integration health every minute so Residence can verify setup without
  requiring an add-on restart.

## 0.3.0

- Read Home Assistant's explicit voice-assistant exposure preferences.
- Accept a narrow Alexa-only exposure command for approved lights, switches,
  climate entities, scenes, and media players.
- Keep locks, alarm panels, covers, cameras, sensors, trackers, and vehicles
  outside Residence's Alexa exposure control.

## 0.2.0

- Publish privacy-filtered Home Assistant entity, device, and area registry
  metadata with each relay snapshot.
- Group multi-entity hardware into Residence devices with manufacturer,
  integration-platform, room, and capability provenance.
- Add first-class DEWENWILS detection through Home Assistant's Tuya integration.
- Exclude device identifiers, serial numbers, network addresses, and config-entry
  data from the relay.

## 0.1.2

- Add approved entity support for vehicle location, action buttons, numeric
  controls, and selectable settings.
- Prepare the bridge for official Tesla Fleet entities while preserving the
  homeowner-controlled domain allowlist.

## 0.1.1

- Allow the Home Assistant base image's S6 Overlay and Bashio startup paths in
  the custom AppArmor profile so the bridge can launch securely.

## 0.1.0

- Add homeowner-confirmed, one-time Home Command pairing.
- Add protected relay credential storage.
- Stream filtered Home Assistant state with heartbeat and reconnect.
- Deliver and acknowledge short-lived Home Assistant service commands.
- Remove sensitive and oversized entity attributes before upload.
