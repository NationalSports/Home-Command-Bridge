# Changelog

## 0.1.1

- Allow the Home Assistant base image's S6 Overlay and Bashio startup paths in
  the custom AppArmor profile so the bridge can launch securely.

## 0.1.0

- Add homeowner-confirmed, one-time Home Command pairing.
- Add protected relay credential storage.
- Stream filtered Home Assistant state with heartbeat and reconnect.
- Deliver and acknowledge short-lived Home Assistant service commands.
- Remove sensitive and oversized entity attributes before upload.
