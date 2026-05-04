# Inkbird ISC-027BW 2.0 — Home Assistant Integration

Full local control of the Inkbird ISC-027BW 2.0 BBQ fan controller via Home Assistant.

This was previously impossible — neither the Tuya Cloud API nor the standard
Tuya local protocol supports controlling this device. The INKBIRD app uses a
proprietary OEM cloud layer inaccessible to developers.

**We reverse-engineered the protocol and discovered that the device MCU requires
a CRC-16/Modbus checksum appended to DP 107 write payloads.** Without this
checksum, the device silently ignores all write commands.

## Features

- **Power ON/OFF** control
- **Target temperature** setting for grill + 3 meat probes
- **Live sensor data**: 4 temperatures + fan speed percentage
- **Real-time updates**: device pushes changes automatically (~3-5s interval)
- **Home Assistant MQTT auto-discovery**: entities appear automatically

## Quick Start

### 1. Get Device Credentials

1. Add the device to Smart Life / INKBIRD app
2. Create a project on [Tuya IoT Platform](https://iot.tuya.com/)
3. Link your app account under Cloud → Development
4. Get `device_id` and `local_key` from API Explorer → Device Management

### 2. Install the Add-on

1. Copy `addon/` contents to `/addons/inkbird-bridge/` on your HA instance (via Samba share or SSH)
2. Settings → Add-ons → Add-on Store → ⋮ → Check for updates
3. Find **Inkbird ISC-027BW Bridge** under Local add-ons
4. Install → Configure (device ID, IP, local key, MQTT credentials) → Start
5. Enable "Start on boot"

### 3. Done

The device appears under Settings → Devices & Services → MQTT with:
- 5 sensors (Grill/Probe 1-3 temp, Fan speed)
- 4 number controls (target temperatures)
- 1 switch (Power ON/OFF)

## Protocol Documentation

See [PROTOCOL.md](PROTOCOL.md) for the full reverse-engineered protocol
specification, including byte maps, CRC algorithm, and code examples.

## File Structure

```
├── README.md           # This file
├── PROTOCOL.md         # Full protocol documentation
└── addon/
    ├── config.yaml     # HA add-on configuration
    ├── build.yaml      # Docker build configuration
    ├── Dockerfile      # Container build file
    └── inkbird_bridge.py  # MQTT bridge (Python)
```

## Requirements

- Home Assistant OS or Supervised
- Mosquitto MQTT broker add-on
- Inkbird ISC-027BW 2.0 on the same local network
- Tuya IoT Platform account (free) for device credentials

## License

MIT — use freely, share widely. If this helped you, consider sharing on the
[Home Assistant Community](https://community.home-assistant.io/).
