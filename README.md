# Home Command Bridge

This is the official Home Assistant app repository for Home Command Bridge.
Home Command Bridge creates an outbound-only connection between a homeowner's
Home Assistant system and their Home Command dashboard.

## Add to Home Assistant

1. Open **Settings → Apps → App store** in Home Assistant.
2. Open the store menu and choose **Repositories**.
3. Add:

   `https://github.com/NationalSports/Home-Command-Bridge`

4. Install **Home Command Bridge**.
5. Follow the pairing instructions shown in the app.

The bridge does not expose Home Assistant to the internet and does not send the
Home Assistant Supervisor credential outside the app. Only the entity domains
selected by the homeowner are synchronized.

Version 0.3 adds privacy-filtered device metadata for multi-channel products
such as DEWENWILS and explicit Alexa exposure controls for approved lights,
switches, climate entities, scenes, and media players. Apple Home remains a
local Home Assistant HomeKit Bridge setup and never sends Apple credentials
through Residence.

See [Home Command Bridge documentation](home_command_bridge/DOCS.md) for
configuration and troubleshooting.
