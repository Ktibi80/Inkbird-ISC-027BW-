# Inkbird ISC-027BW 2.0 — Tuya Local Control Protocol

## Background

The Inkbird ISC-027BW 2.0 is a WiFi/Bluetooth BBQ fan controller that uses the
Tuya platform. While the device connects to WiFi and is discoverable via
`tinytuya`, **all standard write commands are silently ignored** by the MCU
firmware — both through the Tuya local protocol and the Tuya Cloud API.

The Tuya Cloud API reports:
- Category: `wsdcg` (Temperature & Humidity Sensor) — no control functions
- `v1.0/devices/{id}/commands` → `"command or value not support"`
- `v2.0/cloud/thing/{id}/shadow/actions` → `"illegal param"`

The INKBIRD app controls the device through a proprietary OEM cloud layer that
is inaccessible to third-party developers.

**This document describes the reverse-engineered protocol that enables full
local control from Home Assistant, tinytuya, or any Tuya-compatible tool.**

---

## Discovery

| Property       | Value                          |
|----------------|--------------------------------|
| Protocol       | Tuya 3.4                       |
| Product ID     | `8e49mqp3zvmnajlq`             |
| Category       | `wsdcg`                        |
| Known DPs      | 101, 106, 107, 108, 120        |

Use `tinytuya` to connect:

```python
import tinytuya

d = tinytuya.Device(DEVICE_ID, IP_ADDRESS, LOCAL_KEY)
d.set_version(3.4)
```

Obtain `DEVICE_ID` and `LOCAL_KEY` from the
[Tuya IoT Platform](https://iot.tuya.com/) → Cloud → API Explorer → Device
Management → Get Device Information.

> **Warning:** DP 120 is a WiFi pairing/factory reset trigger. **Never write
> to DP 120** — it will put the device into pairing mode and wipe all settings.

---

## Reading Sensor Data (DP 101)

DP 101 contains live sensor readings (4 temperature probes + fan speed).
Trigger a sensor report by writing `True` to DP 106:

```python
r = d.set_value(106, True)
# Response contains DP 101 as a base64-encoded string
```

### DP 101 Format (9 bytes, Little-Endian)

| Offset | Size | Type     | Description                     |
|--------|------|----------|---------------------------------|
| 0–1    | 2    | uint16LE | Grill temperature (°F × 10)     |
| 2–3    | 2    | uint16LE | Probe 1 temperature (°F × 10)   |
| 4–5    | 2    | uint16LE | Probe 2 temperature (°F × 10)   |
| 6–7    | 2    | uint16LE | Probe 3 temperature (°F × 10)   |
| 8      | 1    | uint8    | Fan speed (0–100 %)             |

### Decoding Example

```python
import base64, struct

raw = base64.b64decode(response_dps["101"])
temps_f10 = struct.unpack('<4H', raw[:8])
fan_speed = raw[8]

for i, label in enumerate(["Grill", "Probe1", "Probe2", "Probe3"]):
    celsius = round((temps_f10[i] / 10 - 32) * 5 / 9, 1)
    print(f"{label}: {celsius}°C")
print(f"Fan: {fan_speed}%")
```

---

## Reading Settings (DP 107) — Query Mode

Send a 40-byte all-zeros payload to DP 107 to retrieve the current settings:

```python
import base64

query = base64.b64encode(bytes(40)).decode()
r = d.set_value(107, query)
raw = base64.b64decode(r["dps"]["107"])  # 40 bytes returned
```

### DP 107 Settings Format (40 bytes, Little-Endian)

| Offset | Size | Type     | Description                        |
|--------|------|----------|------------------------------------|
| 0      | 1    | uint8    | Work status (0 = OFF, 1 = ON)      |
| 1–5    | 5    | —        | Reserved (zeros)                   |
| 6–7    | 2    | uint16LE | Grill target temperature (°F × 10) |
| 8–9    | 2    | uint16LE | Grill alarm high (°F × 10)         |
| 10–11  | 2    | uint16LE | Probe 1 target temperature (°F × 10) |
| 12–13  | 2    | uint16LE | Probe 2 target temperature (°F × 10) |
| 14–15  | 2    | uint16LE | Probe 3 target temperature (°F × 10) |
| 16–17  | 2    | uint16LE | Mode / internal setting              |
| 18–19  | 2    | uint16LE | Probe 1 alarm high (°F × 10)       |
| 20–21  | 2    | uint16LE | Probe 2 alarm high (°F × 10)       |
| 22–23  | 2    | uint16LE | Probe 3 alarm high (°F × 10)       |
| 24–39  | 16   | —        | Additional settings / reserved       |

---

## Writing Settings (DP 107) — The CRC Secret

**This is the key discovery.** The device MCU silently rejects any DP 107 write
that is exactly 40 bytes. To make the MCU accept a write, you must append a
**CRC-16/Modbus** checksum in **Little-Endian** byte order, making the total
payload **42 bytes**.

### CRC-16/Modbus Algorithm

```python
def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc
```

### Write Procedure

```python
import struct

# 1. Read current settings (40 bytes)
query = base64.b64encode(bytes(40)).decode()
r = d.set_value(107, query)
settings = bytearray(base64.b64decode(r["dps"]["107"]))

# 2. Modify desired fields
settings[0] = 1  # Turn ON

# Set grill target to 150°C
target_f10 = int((150 * 9/5 + 32) * 10)  # = 3020
struct.pack_into('<H', settings, 6, target_f10)

# 3. Calculate CRC-16/Modbus over the 40 bytes
crc = crc16_modbus(bytes(settings[:40]))

# 4. Append CRC as Little-Endian uint16 → 42-byte payload
payload = bytes(settings[:40]) + struct.pack('<H', crc)

# 5. Send to device
d.set_value(107, base64.b64encode(payload).decode())
```

### Why This Works

| Payload size | CRC present | Device behavior                     |
|--------------|-------------|--------------------------------------|
| 40 bytes     | No          | Echo current settings (read-only)    |
| 42 bytes     | Invalid CRC | Echo current settings (ignored)      |
| **42 bytes** | **Valid CRC** | **Accept write, update settings** |

The Inkbird firmware added a CRC validation layer on top of the standard Tuya
MCU protocol. This is not documented anywhere in Tuya's developer documentation
and is specific to Inkbird's firmware implementation.

---

## Complete Control Examples

### Turn ON / OFF

```python
settings[0] = 1   # ON
settings[0] = 0   # OFF
# + CRC + send
```

### Set Target Temperatures

```python
def c_to_f10(celsius):
    return int((celsius * 9/5 + 32) * 10)

struct.pack_into('<H', settings, 6,  c_to_f10(150))  # Grill → 150°C
struct.pack_into('<H', settings, 10, c_to_f10(72))   # Probe 1 → 72°C
struct.pack_into('<H', settings, 12, c_to_f10(72))   # Probe 2 → 72°C
struct.pack_into('<H', settings, 14, c_to_f10(72))   # Probe 3 → 72°C
# + CRC + send
```

### Passive Listener (Real-Time Updates)

The device automatically pushes DP 101 and DP 107 updates every 3–5 seconds
when the work status is ON. Use a persistent connection:

```python
d.set_socketPersistent(True)
d.set_socketTimeout(1)
d.status()  # establish session

while True:
    data = d.receive()
    if data:
        dps = data.get("dps", {})
        if "101" in dps:
            # decode sensor data
        if "107" in dps:
            # decode settings update
```

---

## Open Lid Detection

The Inkbird firmware has a built-in "open lid" feature. When a rapid temperature
drop is detected, the fan stops (0%) to prevent oxygen from feeding the fire.
The device stays in ON state but the fan will not restart automatically.

**There is no dedicated DP for lid state.** Detection is based on:

| Condition | Meaning |
|-----------|---------|
| `work_status` = ON, `fan` > 0% | Normal operation |
| `work_status` = ON, `fan` = 0% | **Open lid** (or target reached) |
| `work_status` = OFF | Device off |

### Auto-Restart (v1.4+)

The MQTT bridge monitors the grill temperature during open lid state. When
3 consecutive readings show a rising temperature (>0.3°C per reading), the
bridge assumes the lid has been closed and automatically sends an ON command
to restart the fan. This eliminates the need to manually press the power
button after closing the lid.

---

## Home Assistant Integration

A complete MQTT bridge add-on is included that exposes:

- **Sensors:** Grill/Probe 1–3 temperature, fan speed, lid state
- **Controls:** Power ON/OFF switch, grill/probe target temperature sliders
- **Auto-discovery:** Appears automatically in HA via MQTT discovery

### Installation

1. Copy the `addon/` folder to `/addons/inkbird-bridge/` on your HA instance
2. Go to Settings → Add-ons → Add-on Store → Check for updates
3. Install "Inkbird ISC-027BW Bridge" from Local add-ons
4. Configure your device ID, IP, local key, and MQTT credentials
5. Start the add-on

### Requirements

- Home Assistant OS or Supervised
- Mosquitto MQTT broker add-on
- The device must be on the same local network
- Device ID and Local Key from [Tuya IoT Platform](https://iot.tuya.com/)

---

## Acknowledgments

Protocol reverse-engineered in May 2025 through:

1. Sniffing device DP updates while controlling via the INKBIRD app
2. Discovering the 42-byte vs 40-byte write behavior
3. Brute-forcing the CRC algorithm against two known data+checksum pairs
4. Identifying CRC-16/Modbus (Little-Endian) as the exact algorithm

This work was done because neither the Tuya standard API, the Tuya local
protocol, nor any community resource documented a way to control this device.
