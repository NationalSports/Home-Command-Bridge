# Residence Bridge

Residence Bridge is the private, outbound-only connection between Home
Assistant and Residence Home Command.

After a homeowner enters a short-lived pairing code, the app publishes approved
Home Assistant entity state to Residence and delivers approved Residence
commands back to Home Assistant. It never exposes Home Assistant directly to
the internet and never sends the Supervisor credential outside the app.

See [DOCS.md](DOCS.md) for installation and pairing.
