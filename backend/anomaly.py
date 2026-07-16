"""
anomaly.py
Rules-based anomaly detection for incoming GPS pings.

Three checks, matching the spec:
1. Speed anomaly     -> speed above a hard threshold
2. Teleportation      -> distance travelled since last ping is physically
                         impossible given the elapsed time
3. Geofence violation -> ping falls inside a predefined restricted polygon/circle
"""

from datetime import datetime
from math import radians, sin, cos, sqrt, atan2
from typing import Optional

SPEED_LIMIT_KMH = 120.0
TELEPORT_MAX_KMH = 300.0  # if implied speed between two pings exceeds this, flag it

# Restricted zones as real polygons (arbitrary shape), not circles.
# Each zone is a list of (lat, lon) vertices tracing the boundary in order
# (the polygon is implicitly closed - you don't need to repeat the first
# point at the end). Edit these to match your actual restricted areas.
#
# NOTE ON ACCURACY: this treats (lat, lon) as flat planar coordinates for
# the ray-casting test below, which is the standard simplification for
# small areas (a city district, an airport perimeter, a depot). It is NOT
# accurate for polygons spanning large regions (hundreds of km) or areas
# very close to the poles, where the true shape on a sphere/ellipsoid
# diverges meaningfully from a flat projection. For that you'd want a
# proper geodesic library (e.g. shapely + pyproj with an equal-area CRS).
RESTRICTED_ZONES = [
    {
        "name": "Restricted Zone Alpha",
        "polygon": [
            (24.7050, 46.6650),
            (24.7050, 46.7350),
            (24.6700, 46.7500),
            (24.6550, 46.7100),
            (24.6700, 46.6600),
        ],
    },
]


def point_in_polygon(lat: float, lon: float, polygon: list) -> bool:
    """
    Standard ray-casting point-in-polygon test. Casts a ray from the point
    to the right (increasing lon) and counts how many polygon edges it
    crosses - odd crossings means the point is inside.

    `polygon` is a list of (lat, lon) tuples defining the boundary in order.
    """
    n = len(polygon)
    inside = False
    x, y = lon, lat  # treat lon as x, lat as y for a flat-plane test
    p1x, p1y = polygon[0][1], polygon[0][0]
    for i in range(1, n + 1):
        p2x, p2y = polygon[i % n][1], polygon[i % n][0]
        if y > min(p1y, p2y) and y <= max(p1y, p2y) and x <= max(p1x, p2x):
            if p1y != p2y:
                x_intersect = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
            else:
                x_intersect = p1x
            if p1x == p2x or x <= x_intersect:
                inside = not inside
        p1x, p1y = p2x, p2y
    return inside


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def check_speed(speed: float) -> Optional[dict]:
    if speed > SPEED_LIMIT_KMH:
        return {
            "type": "SPEED",
            "description": f"Speed {speed:.1f} km/h exceeds limit of {SPEED_LIMIT_KMH} km/h",
        }
    return None


def check_teleport(prev_row, lat: float, lon: float, timestamp: str) -> Optional[dict]:
    if prev_row is None:
        return None
    try:
        t_prev = datetime.fromisoformat(prev_row["timestamp"].replace("Z", "+00:00"))
        t_now = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None

    dt_hours = (t_now - t_prev).total_seconds() / 3600.0
    if dt_hours <= 0:
        return None

    dist_km = haversine_km(prev_row["lat"], prev_row["lon"], lat, lon)
    implied_speed = dist_km / dt_hours

    if implied_speed > TELEPORT_MAX_KMH:
        return {
            "type": "TELEPORT",
            "description": (
                f"Implied speed {implied_speed:.1f} km/h over {dist_km:.2f} km "
                f"in {dt_hours*3600:.1f}s is physically implausible"
            ),
        }
    return None


def check_geofence(lat: float, lon: float) -> Optional[dict]:
    for zone in RESTRICTED_ZONES:
        if point_in_polygon(lat, lon, zone["polygon"]):
            return {
                "type": "GEOFENCE",
                "description": f"Entered restricted zone '{zone['name']}'",
            }
    return None


def evaluate(prev_row, lat: float, lon: float, speed: float, timestamp: str) -> list:
    """Run all anomaly checks and return a list of triggered alerts (dicts)."""
    alerts = []
    for check in (
        check_speed(speed),
        check_teleport(prev_row, lat, lon, timestamp),
        check_geofence(lat, lon),
    ):
        if check:
            alerts.append(check)
    return alerts
