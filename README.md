# Residence Bridge

This is the official Home Assistant app repository for Residence Bridge.
Residence Bridge creates an outbound-only connection between a homeowner's
Home Assistant system and Residence Home Command.

## Add to Home Assistant

1. Open **Settings → Apps → App store** in Home Assistant.
2. Open the store menu and choose **Repositories**.
3. Add:

   `https://github.com/NationalSports/Residence-Bridge`

4. Install **Residence Bridge**.
5. Follow the pairing instructions shown in the app.

The bridge does not expose Home Assistant to the internet and does not send the
Home Assistant Supervisor credential outside the app. Only the entity domains
selected by the homeowner are synchronized.

See [Residence Bridge documentation](residence_bridge/DOCS.md) for configuration
and troubleshooting.
