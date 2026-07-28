# Home Command Bridge

Home Command Bridge is the private, outbound-only connection between Home
Assistant and a Home Command dashboard.

After a homeowner enters a short-lived pairing code, the app publishes approved
Home Assistant entity state to Home Command and delivers approved Home Command
commands back to Home Assistant. It never exposes Home Assistant directly to
the internet and never sends the Supervisor credential outside the app.

Version 0.3 adds narrow, explicit Amazon Alexa exposure controls using Home
Assistant's supported voice-assistant WebSocket commands. Residence can share
only approved lights, switches, climate entities, scenes, and media players;
security-sensitive and observational domains remain excluded.

Version 0.2 added privacy-filtered device and area registry metadata. Residence
uses it to group multi-channel hardware, preserve room assignments, identify
manufacturers such as DEWENWILS, and normalize capabilities without receiving
device identifiers or network credentials.

See [DOCS.md](DOCS.md) for installation and pairing.
