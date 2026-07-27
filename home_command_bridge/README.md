# Home Command Bridge

Home Command Bridge is the private, outbound-only connection between Home
Assistant and a Home Command dashboard.

After a homeowner enters a short-lived pairing code, the app publishes approved
Home Assistant entity state to Home Command and delivers approved Home Command
commands back to Home Assistant. It never exposes Home Assistant directly to
the internet and never sends the Supervisor credential outside the app.

See [DOCS.md](DOCS.md) for installation and pairing.
