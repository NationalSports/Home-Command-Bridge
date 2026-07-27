# Home Command Bridge

## Before you begin

Create a pairing code in Home Command:

1. Open **Integrations** in Home Command.
2. Choose **Home Assistant**.
3. Choose **Pair Home Command Bridge** and then **Create pairing code**.

## Configure and pair

1. Install Home Command Bridge from the Home Command app repository.
2. Open its **Configuration** tab.
3. Keep the supplied Home Command URL, or enter the URL for your Home Command
   deployment.
4. Enter the one-time code shown in Home Command.
5. Save the configuration and start Home Command Bridge.
6. Return to Home Command and confirm the Jarvis identity shown there.

The app saves its relay credential in protected Home Assistant app data. The
pairing code is retired as soon as it is used.

## What Home Command can access

Only entity domains selected in **Sync domains** are published. Sensitive
attribute names are removed, oversized attributes are reduced to a safe
summary, and Home Assistant's Supervisor credential stays local.

The app opens outbound connections only. No port forwarding, router changes,
public Home Assistant URL, or long-lived access token is required.

## Troubleshooting

- **Waiting for a pairing code:** create a fresh code in Home Command, paste it
  into the app configuration, save, and restart.
- **Waiting for approval:** leave the app running and confirm Jarvis in
  Home Command.
- **Offline in Home Command:** check this app's Log tab and confirm Home Assistant
  and the host both have internet access.
- **Pairing code expired:** create a new code; codes expire after ten minutes.
