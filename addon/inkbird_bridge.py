#!/usr/bin/env python3
"""
MQTT Bridge for Inkbird ISC-027BW 2.0 BBQ Fan Controller
v1.4 - Open lid detection with auto-restart.
"""

import tinytuya
import paho.mqtt.client as mqtt
import base64
import struct
import time
import json
import threading
import logging
import sys
import os

# ──────────────────── CONFIGURATION ────────────────────
OPTIONS_FILE = "/data/options.json"
_opts = {}
if os.path.exists(OPTIONS_FILE):
    with open(OPTIONS_FILE) as f:
        _opts = json.load(f)

TUYA_DEV_ID   = _opts.get("tuya_device_id", os.environ.get("TUYA_DEV_ID", ""))
TUYA_IP       = _opts.get("tuya_ip", os.environ.get("TUYA_IP", ""))
TUYA_KEY      = _opts.get("tuya_key", os.environ.get("TUYA_KEY", ""))
TUYA_VER      = 3.4

MQTT_HOST     = _opts.get("mqtt_host", os.environ.get("MQTT_HOST", "core-mosquitto"))
MQTT_PORT     = int(_opts.get("mqtt_port", os.environ.get("MQTT_PORT", "1883")))
MQTT_USER     = _opts.get("mqtt_user", os.environ.get("MQTT_USER", ""))
MQTT_PASS     = _opts.get("mqtt_pass", os.environ.get("MQTT_PASS", ""))

POLL_INTERVAL = int(_opts.get("poll_interval", os.environ.get("POLL_INTERVAL", "30")))
TOPIC_PREFIX  = "inkbird/isc027bw"
HA_DISCOVERY  = "homeassistant"
DEVICE_NAME   = "Inkbird ISC-027BW"
UNIQUE_PREFIX = "inkbird_isc027bw"
# ────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("inkbird")


def crc16_modbus(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc

def f10_to_c(val):
    return round((val / 10 - 32) * 5 / 9, 1)

def c_to_f10(celsius):
    return int((celsius * 9 / 5 + 32) * 10)

def decode_101(b64val):
    raw = base64.b64decode(b64val)
    if len(raw) >= 9:
        vals = struct.unpack("<4H", raw[:8])
        temps = [round((v / 10 - 32) * 5 / 9, 1) for v in vals]
        return {
            "grill_temp": temps[0],
            "probe1_temp": temps[1],
            "probe2_temp": temps[2],
            "probe3_temp": temps[3],
            "fan_speed": raw[8],
        }
    return None

def decode_settings(raw):
    if not raw or len(raw) < 24:
        return {}
    return {
        "work_status": "ON" if raw[0] else "OFF",
        "grill_target": round(f10_to_c(struct.unpack_from("<H", raw, 6)[0])),
        "probe1_target": round(f10_to_c(struct.unpack_from("<H", raw, 10)[0])),
        "probe2_target": round(f10_to_c(struct.unpack_from("<H", raw, 12)[0])),
        "probe3_target": round(f10_to_c(struct.unpack_from("<H", raw, 14)[0])),
    }


DEVICE_INFO = {
    "identifiers": [UNIQUE_PREFIX],
    "name": DEVICE_NAME,
    "model": "ISC-027BW 2.0",
    "manufacturer": "Inkbird",
}

def publish_discovery(mqttc):
    sensors = [
        ("grill_temp",   "Grill Temperature",   "°C", "temperature", "measurement"),
        ("probe1_temp",  "Probe 1 Temperature", "°C", "temperature", "measurement"),
        ("probe2_temp",  "Probe 2 Temperature", "°C", "temperature", "measurement"),
        ("probe3_temp",  "Probe 3 Temperature", "°C", "temperature", "measurement"),
        ("fan_speed",    "Fan Speed",            "%",  None,          "measurement"),
    ]
    for key, name, unit, dev_class, state_class in sensors:
        uid = f"{UNIQUE_PREFIX}_{key}"
        cfg = {
            "name": name,
            "unique_id": uid,
            "state_topic": f"{TOPIC_PREFIX}/sensor",
            "value_template": "{{ value_json." + key + " }}",
            "unit_of_measurement": unit,
            "state_class": state_class,
            "availability_topic": f"{TOPIC_PREFIX}/status",
            "device": DEVICE_INFO,
        }
        if dev_class:
            cfg["device_class"] = dev_class
        if key == "fan_speed":
            cfg["icon"] = "mdi:fan"
        mqttc.publish(f"{HA_DISCOVERY}/sensor/{uid}/config", json.dumps(cfg), retain=True)

    targets = [
        ("grill_target",  "Grill Target",   0, 400),
        ("probe1_target", "Probe 1 Target", 0, 300),
        ("probe2_target", "Probe 2 Target", 0, 300),
        ("probe3_target", "Probe 3 Target", 0, 300),
    ]
    for key, name, mn, mx in targets:
        uid = f"{UNIQUE_PREFIX}_{key}"
        cfg = {
            "name": name,
            "unique_id": uid,
            "state_topic": f"{TOPIC_PREFIX}/settings",
            "value_template": "{{ value_json." + key + " }}",
            "command_topic": f"{TOPIC_PREFIX}/set/{key}",
            "unit_of_measurement": "°C",
            "device_class": "temperature",
            "min": mn,
            "max": mx,
            "step": 1,
            "suggested_display_precision": 0,
            "availability_topic": f"{TOPIC_PREFIX}/status",
            "device": DEVICE_INFO,
        }
        mqttc.publish(f"{HA_DISCOVERY}/number/{uid}/config", json.dumps(cfg), retain=True)

    uid_sw = f"{UNIQUE_PREFIX}_power"
    sw_cfg = {
        "name": "Power",
        "unique_id": uid_sw,
        "state_topic": f"{TOPIC_PREFIX}/settings",
        "value_template": "{{ value_json.work_status }}",
        "command_topic": f"{TOPIC_PREFIX}/set/power",
        "payload_on": "ON",
        "payload_off": "OFF",
        "icon": "mdi:grill",
        "availability_topic": f"{TOPIC_PREFIX}/status",
        "device": DEVICE_INFO,
    }
    mqttc.publish(f"{HA_DISCOVERY}/switch/{uid_sw}/config", json.dumps(sw_cfg), retain=True)

    uid_lid = f"{UNIQUE_PREFIX}_lid"
    lid_cfg = {
        "name": "Lid",
        "unique_id": uid_lid,
        "state_topic": f"{TOPIC_PREFIX}/sensor",
        "value_template": "{{ value_json.lid }}",
        "icon": "mdi:grill-outline",
        "availability_topic": f"{TOPIC_PREFIX}/status",
        "device": DEVICE_INFO,
    }
    mqttc.publish(f"{HA_DISCOVERY}/sensor/{uid_lid}/config", json.dumps(lid_cfg), retain=True)
    log.info("HA discovery published (incl. open lid sensor)")


SETTING_MAP = {
    "grill_target":  6,
    "probe1_target": 10,
    "probe2_target": 12,
    "probe3_target": 14,
}

LID_RISE_COUNT = 3  # consecutive rising readings before auto-restart


def send_power_on(dev):
    """Read current settings, set work_status=1, append CRC, write back."""
    dev.set_socketTimeout(3)
    r = dev.set_value(107, base64.b64encode(bytes(40)).decode())
    if not r:
        return None
    dps = r.get("dps", r.get("data", {}).get("dps", {}))
    if "107" not in dps:
        return None
    raw = bytearray(base64.b64decode(dps["107"]))
    buf = bytearray(raw[:40])
    buf[0] = 1
    crc = crc16_modbus(bytes(buf))
    blob = bytes(buf) + struct.pack("<H", crc)
    wr = dev.set_value(107, base64.b64encode(blob).decode())
    dev.set_socketTimeout(1)
    return wr


def main():
    if not TUYA_DEV_ID or not TUYA_IP or not TUYA_KEY:
        log.error("Missing Tuya config!")
        sys.exit(1)

    log.info("Inkbird Bridge v1.4 (open lid auto-restart)")
    log.info("Device: %s @ %s", TUYA_DEV_ID, TUYA_IP)

    # ── MQTT ──
    mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if MQTT_USER:
        mqttc.username_pw_set(MQTT_USER, MQTT_PASS)

    cmd_queue = []
    cmd_lock = threading.Lock()

    def on_connect(client, userdata, flags, rc, properties=None):
        log.info("MQTT connected")
        client.subscribe(f"{TOPIC_PREFIX}/set/#")
        publish_discovery(client)

    def on_message(client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="replace")
        log.info("CMD: %s = %s", msg.topic, payload)
        with cmd_lock:
            cmd_queue.append((msg.topic, payload))

    mqttc.on_connect = on_connect
    mqttc.on_message = on_message
    mqttc.will_set(f"{TOPIC_PREFIX}/status", "offline", retain=True)

    while True:
        try:
            mqttc.connect(MQTT_HOST, MQTT_PORT)
            break
        except Exception as e:
            log.warning("MQTT fail: %s - retry 10s", e)
            time.sleep(10)

    mqttc.loop_start()
    time.sleep(0.5)
    mqttc.publish(f"{TOPIC_PREFIX}/status", "online", retain=True)

    # ── Tuya persistent connection ──
    dev = None
    last_data = 0
    last_poll = 0
    msg_count = 0

    # ── Open lid detection ──
    lid_open = False
    prev_fan = -1
    prev_grill = 0.0
    rising_count = 0
    work_on = False

    def connect_tuya():
        nonlocal dev
        try:
            if dev:
                try:
                    dev.close()
                except:
                    pass
            d = tinytuya.Device(TUYA_DEV_ID, TUYA_IP, TUYA_KEY)
            d.set_version(TUYA_VER)
            d.set_socketPersistent(True)
            d.set_socketTimeout(1)
            s = d.status()
            if s and "Err" not in str(s):
                dev = d
                log.info("Tuya connected, listening...")
                return True
            log.warning("Tuya: %s", s)
        except Exception as e:
            log.warning("Tuya connect: %s", e)
        dev = None
        return False

    def publish_data(data):
        nonlocal last_data, msg_count, lid_open, prev_fan, prev_grill, rising_count, work_on
        if not data:
            return
        dps = data.get("dps", data.get("data", {}).get("dps", {}))
        for dp_id, dp_val in dps.items():
            if dp_id == "101" and isinstance(dp_val, str):
                sensors = decode_101(dp_val)
                if sensors:
                    fan = sensors["fan_speed"]
                    grill = sensors["grill_temp"]

                    # Open lid detection: fan drops to 0 while device is ON
                    if work_on and prev_fan > 0 and fan == 0:
                        lid_open = True
                        rising_count = 0
                        log.warning("OPEN LID detected! Fan %d%% -> 0%%, grill=%.1fC", prev_fan, grill)
                        mqttc.publish(f"{TOPIC_PREFIX}/lid", "OPEN", retain=True)

                    # Auto-restart: lid is open and temp is rising (lid closed back)
                    if lid_open and fan == 0:
                        if grill > prev_grill + 0.3:
                            rising_count += 1
                        else:
                            rising_count = 0

                        if rising_count >= LID_RISE_COUNT:
                            log.info("Lid closed! Temp rising for %d readings (%.1fC). Sending ON...", rising_count, grill)
                            try:
                                wr = send_power_on(dev)
                                if wr:
                                    publish_data(wr)
                                    log.info("Auto-restart OK!")
                                lid_open = False
                                rising_count = 0
                                mqttc.publish(f"{TOPIC_PREFIX}/lid", "CLOSED", retain=True)
                            except Exception as e:
                                log.warning("Auto-restart failed: %s", e)

                    if fan > 0 and lid_open:
                        lid_open = False
                        rising_count = 0
                        mqttc.publish(f"{TOPIC_PREFIX}/lid", "CLOSED", retain=True)
                        log.info("Lid closed (fan running)")

                    prev_fan = fan
                    prev_grill = grill

                    sensors["lid"] = "OPEN" if lid_open else "CLOSED"
                    mqttc.publish(f"{TOPIC_PREFIX}/sensor", json.dumps(sensors))
                    msg_count += 1
                    if msg_count % 10 == 1:
                        log.info(
                            "G=%.1f P1=%.1f P2=%.1f P3=%.1f Fan=%d%% Lid=%s",
                            sensors["grill_temp"], sensors["probe1_temp"],
                            sensors["probe2_temp"], sensors["probe3_temp"],
                            sensors["fan_speed"], sensors["lid"],
                        )
                    last_data = time.time()
            elif dp_id == "107" and isinstance(dp_val, str):
                raw = bytearray(base64.b64decode(dp_val))
                settings = decode_settings(raw)
                work_on = settings.get("work_status") == "ON"
                mqttc.publish(f"{TOPIC_PREFIX}/settings", json.dumps(settings))
                last_data = time.time()

    connect_tuya()
    log.info("Running. Listening for device updates + poll backup every %ds.", POLL_INTERVAL)

    try:
        while True:
            # Reconnect if needed
            if dev is None:
                if not connect_tuya():
                    time.sleep(5)
                    continue

            # Process pending HA commands
            with cmd_lock:
                cmds = list(cmd_queue)
                cmd_queue.clear()

            for topic, payload in cmds:
                key = topic.rsplit("/", 1)[-1]
                try:
                    dev.set_socketTimeout(3)
                    r = dev.set_value(107, base64.b64encode(bytes(40)).decode())
                    if not r:
                        raise Exception("no response")
                    dps = r.get("dps", r.get("data", {}).get("dps", {}))
                    if "107" not in dps:
                        raise Exception("no dp107")
                    raw = bytearray(base64.b64decode(dps["107"]))
                    buf = bytearray(raw[:40])

                    if key == "power":
                        buf[0] = 1 if payload.upper() == "ON" else 0
                        log.info("Power -> %s", "ON" if buf[0] else "OFF")
                    elif key in SETTING_MAP:
                        struct.pack_into("<H", buf, SETTING_MAP[key], c_to_f10(float(payload)))
                        log.info("%s -> %s°C", key, payload)
                    else:
                        continue

                    crc = crc16_modbus(bytes(buf))
                    blob = bytes(buf) + struct.pack("<H", crc)
                    wr = dev.set_value(107, base64.b64encode(blob).decode())
                    publish_data(wr)
                    log.info("Write OK")
                    dev.set_socketTimeout(1)
                except Exception as e:
                    log.warning("Command error: %s", e)
                    dev = None
                    break

            if dev is None:
                continue

            # LISTEN for device push messages (main data source)
            try:
                data = dev.receive()
                if data and "Err" not in str(data):
                    publish_data(data)
            except Exception as e:
                if "timed out" not in str(e).lower():
                    log.warning("Receive error: %s", e)
                    dev = None
                    continue

            # Backup poll if no data received for a while
            now = time.time()
            if now - last_data > POLL_INTERVAL and now - last_poll > POLL_INTERVAL:
                last_poll = now
                try:
                    dev.set_socketTimeout(3)
                    r = dev.set_value(106, True)
                    if r:
                        publish_data(r)
                    r2 = dev.set_value(107, base64.b64encode(bytes(40)).decode())
                    if r2:
                        publish_data(r2)
                    dev.set_socketTimeout(1)
                    log.info("Backup poll done")
                except Exception as e:
                    log.warning("Poll error: %s", e)
                    dev = None

    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        mqttc.publish(f"{TOPIC_PREFIX}/status", "offline", retain=True)
        time.sleep(0.5)
        mqttc.loop_stop()
        mqttc.disconnect()
        if dev:
            dev.close()


if __name__ == "__main__":
    main()
