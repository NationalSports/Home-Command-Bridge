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
summary, and Home Assistant's Supervisor credential stays local. The bridge
also publishes a restricted view of Home Assistant's registries so Residence
can group entities into devices and rooms. That view contains display names,
manufacturer, model, area, and integration platform. It excludes hardware
identifiers, serial numbers, MAC addresses, network addresses, and config-entry
titles/details. Bridge 0.4 publishes only setup counts and loaded state for
Tuya, HomeKit Bridge, HomeKit Device, and Matter.

The app opens outbound connections only. No port forwarding, router changes,
public Home Assistant URL, or long-lived access token is required.

## Add Amazon Alexa

1. Enable Alexa in Home Assistant using **Home Assistant Cloud**, or finish an
   existing manual Alexa Smart Home skill setup.
2. Upgrade and restart Home Command Bridge 0.4.0 or newer.
3. Open **Integrations → Amazon Alexa** in Residence.
4. Review each eligible device and choose **Share with Alexa**.
5. Ask Alexa to discover devices if the new endpoints do not appear
   automatically.

Residence changes only Home Assistant's explicit `cloud.alexa` exposure
preference. It does not receive Amazon credentials or perform Alexa account
linking. Only approved lights, switches, climate entities, scenes, and media
players are eligible. Locks, alarms, cameras, covers, sensors, trackers, and
vehicles are rejected by the Residence API and the local bridge.

## Add Apple Home

Residence supports both local HomeKit directions:

1. To publish approved Home Assistant devices to Apple Home, open
   **Integrations → Apple Home**, choose **Open HomeKit Bridge setup**, select
   the entities to publish, and scan the pairing code in Apple Home.
2. To import a compatible HomeKit accessory into Residence, choose
   **Open HomeKit Device setup**. The accessory must be available to pair with
   Home Assistant and is imported individually.
3. If desired, publish an imported accessory back to Apple Home through the
   HomeKit Bridge.

Apple credentials, the HomeKit pairing code, and Apple Home data never pass
through Residence. A HomeKit accessory generally cannot remain paired to Apple
Home and Home Assistant as two HomeKit controllers at once. Begin with a small
accessory set and keep each bridge below Apple Home's 150-accessory limit.

## Add Smart Life / Tuya

1. Confirm the device works in Smart Life or Tuya Smart.
2. Open **Integrations → Smart Life / Tuya** in Residence.
3. Choose **Open Tuya setup**.
4. Retrieve the Smart Life user code and scan Home Assistant's QR code with the
   Smart Life app.
5. Return to Residence. Bridge 0.4 refreshes setup health within one minute and
   groups approved Tuya devices automatically.

Residence receives no Smart Life password, user code, QR content, account
identifier, or config-entry title.

## Add DEWENWILS devices

DEWENWILS models do not all use the same provisioning path. For models visible
in Tuya or Smart Life:

1. Pair the product in the app named by its manual.
2. Add Home Assistant's official **Tuya** integration and authorize that
   household.
3. Confirm the DEWENWILS device and its switch channels appear in Home
   Assistant.
4. Keep `switch`, `sensor`, `number`, and `select` in **Sync domains**. Add
   `light` when the product exposes lighting controls.
5. Restart Home Command Bridge after upgrading to 0.4.0 or newer.
6. Open **Integrations → DEWENWILS** in Residence.

Residence identifies the brand from Home Assistant's manufacturer/model
metadata, groups multi-channel products by device ID, assigns the Home
Assistant area, and exposes power or energy monitoring only when the model
provides those entities. Commands are sent back through Home Assistant; the
Residence cloud never receives the Tuya account credential.

## Add Matter

Open **Integrations → Matter** in Residence and choose **Open Matter setup**.
Commission the device with its QR code or setup number in Home Assistant.
Residence receives only the approved entity/device view after commissioning;
the Matter setup code remains local.

## Add a Rivian vehicle

Residence can display a Rivian vehicle that is already represented by approved
Home Assistant entities. This route does not ask Residence for a Rivian username,
password, token, or complete VIN.

1. Add the vehicle provider you trust to Home Assistant and confirm the vehicle
   appears as one device in the device registry.
2. Keep `sensor`, `binary_sensor`, `lock`, `climate`, and `device_tracker` in
   **Sync domains**.
3. Restart Home Command Bridge after changing its domain list.
4. Open **Vehicles → Vehicle services → Rivian** in Residence and choose
   **Scan Jarvis again**.

Residence recognizes Rivian, R1T, R1S, R2, and R3 model evidence from the
approved Home Assistant device and entity metadata. Battery, range, charging,
lock, climate, and location are shown only when matching entities are present.
Remote commands remain unavailable until Residence can identify and verify the
provider's Home Assistant service contract safely.

## Troubleshooting

- **Waiting for a pairing code:** create a fresh code in Home Command, paste it
  into the app configuration, save, and restart.
- **Waiting for approval:** leave the app running and confirm Jarvis in
  Home Command.
- **Offline in Home Command:** check this app's Log tab and confirm Home Assistant
  and the host both have internet access.
- **Pairing code expired:** create a new code; codes expire after ten minutes.
