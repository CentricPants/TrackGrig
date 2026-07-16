"""
device_simulator.py
Simulates a fleet of GPS-enabled vehicles publishing location pings over MQTT.

Usage:
  python device_simulator.py --devices 10
  python device_simulator.py --devices 5 --misbehave veh_002   # force anomalies for testing
"""

import argparse
import json
import math
import os
import random
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

# Overridable via environment variables - defaults match
# mosquitto/setup_auth.sh's defaults so this works out of the box locally.
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USERNAME = os.environ.get("MQTT_DEVICE_USERNAME", "device_client")
MQTT_PASSWORD = os.environ.get("MQTT_DEVICE_PASSWORD", "device-client-pw-change-me")
MQTT_CA_CERT = os.environ.get(
    "MQTT_CA_CERT",
    os.path.join(os.path.dirname(__file__), "..", "mosquitto", "certs", "ca.crt"),
)

# Riyadh-ish starting bounding box, purely for a plausible demo map
START_LAT_RANGE = (24.60, 24.80)
START_LON_RANGE = (46.60, 46.85)

KM_PER_DEG_LAT = 111.0  # ~constant everywhere on Earth


class SimulatedDevice:
    def __init__(self, device_id: str, misbehave: bool = False):
        self.device_id = device_id
        self.api_key = None  # filled in by register step below
        self.lat = random.uniform(*START_LAT_RANGE)
        self.lon = random.uniform(*START_LON_RANGE)
        self.speed = random.uniform(20, 80)
        self.bearing = random.uniform(0, 360)  # degrees, direction of travel
        self.misbehave = misbehave
        self.last_tick = time.monotonic()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{device_id}")
        # This is the MQTT-transport identity: it proves "I'm a legitimate
        # fleet device" to the broker, and is shared across all simulated
        # vehicles (see mosquitto/setup_auth.sh for why). It is NOT the
        # same thing as self.api_key below, which is this device's
        # individual, application-level identity once messages reach the
        # backend.
        self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.client.tls_set(ca_certs=MQTT_CA_CERT)

    def connect(self):
        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        self.client.loop_start()

    def _step_position(self):
        """
        Move the vehicle for whatever amount of real time actually elapsed
        since the last ping, at whatever speed we're reporting - so a
        "well-behaved" vehicle's displacement is always physically
        consistent with its own speed field. This matters because the
        backend's teleport check compares distance/time against implied
        speed: if movement weren't tied to the reported speed, an honest
        vehicle could randomly appear to teleport just from bad luck in
        the random walk, which would make the anomaly detector look
        unreliable in a demo.
        """
        now = time.monotonic()
        elapsed_s = now - self.last_tick
        self.last_tick = now

        if self.misbehave and random.random() < 0.3:
            # Deliberately break physical consistency, to exercise the
            # anomaly detector end-to-end. This is intentional test data,
            # not a bug - contrast with the "else" branch below.
            choice = random.choice(["teleport", "speed"])
            if choice == "teleport":
                self.lat += random.uniform(-0.5, 0.5)
                self.lon += random.uniform(-0.5, 0.5)
            else:
                self.speed = random.uniform(150, 220)
            return

        # Normal driving: gently vary speed and heading, then move exactly
        # as far as (speed * elapsed_time) implies - this is what keeps
        # well-behaved vehicles from ever tripping the teleport check.
        self.speed = max(5, min(100, self.speed + random.uniform(-5, 5)))
        self.bearing = (self.bearing + random.uniform(-15, 15)) % 360

        distance_km = self.speed * (elapsed_s / 3600.0)
        bearing_rad = math.radians(self.bearing)
        km_per_deg_lon = KM_PER_DEG_LAT * math.cos(math.radians(self.lat)) or 1e-6

        self.lat += (distance_km * math.cos(bearing_rad)) / KM_PER_DEG_LAT
        self.lon += (distance_km * math.sin(bearing_rad)) / km_per_deg_lon

    def publish_ping(self):
        self._step_position()
        payload = {
            "device_id": self.device_id,
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "speed": round(self.speed, 1),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "api_key": self.api_key,
        }
        topic = f"fleet/location/{self.device_id}"
        self.client.publish(topic, json.dumps(payload))
        print(f"[{self.device_id}] -> {payload}")

    def run_forever(self):
        self.connect()
        while True:
            self.publish_ping()
            time.sleep(random.uniform(2, 5))


def register_devices_via_rest(device_ids, backend_url="http://localhost:8000"):
    """
    In this demo, device API keys are created lazily the first time the
    backend sees a device over MQTT (see backend/mqtt_client.py:register_device).
    Since the simulator publishes the very first message itself, we generate
    a placeholder key locally and let the backend assign the *real* one on
    first contact - subsequent pings then need to match it. To keep this
    simple and self-consistent, we instead import the backend's own
    registration function directly when running everything on one machine.
    """
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
    import models  # noqa: E402
    models.init_db()
    keys = {}
    for device_id in device_ids:
        keys[device_id] = models.register_device(device_id)
    return keys


def main():
    parser = argparse.ArgumentParser(description="PhantomTrack device simulator")
    parser.add_argument("--devices", type=int, default=10, help="number of simulated vehicles")
    parser.add_argument("--misbehave", nargs="*", default=[], help="device_ids that should occasionally trigger anomalies")
    args = parser.parse_args()

    device_ids = [f"veh_{i:03d}" for i in range(1, args.devices + 1)]
    keys = register_devices_via_rest(device_ids)

    devices = []
    for device_id in device_ids:
        d = SimulatedDevice(device_id, misbehave=(device_id in args.misbehave))
        d.api_key = keys[device_id]
        devices.append(d)

    threads = [threading.Thread(target=d.run_forever, daemon=True) for d in devices]
    for t in threads:
        t.start()

    print(f"Simulating {len(devices)} devices. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping simulator.")


if __name__ == "__main__":
    main()
