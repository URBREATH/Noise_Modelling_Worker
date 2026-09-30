"""Input polygon (§6.2)."""
from __future__ import annotations

from typing import Any

from shapely.geometry import Polygon, shape
from pyproj import Transformer
from shapely.ops import transform

# Metric CRS used downstream by NoiseModelling (ETRS89-LAEA Europe).
METRIC_CRS = "EPSG:3035"

_to_metric = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True).transform
_to_wgs84 = Transformer.from_crs(METRIC_CRS, "EPSG:4326", always_xy=True).transform

class PolygonError(ValueError):
    pass


def parse_polygon(obj: Any) -> Polygon:
    """GeoJSON Polygon, lon/lat WGS84, one exterior ring and no holes.

    Also accepts the OGC qualified value {"value": {...}} and a Feature wrapping a Polygon.
    """
    if isinstance(obj, dict) and set(obj) == {"value"}:
        obj = obj["value"]
    if isinstance(obj, dict) and obj.get("type") == "Feature":
        obj = obj.get("geometry")
    if not isinstance(obj, dict) or obj.get("type") != "Polygon":
        raise PolygonError("expected a GeoJSON Polygon")
    rings = obj.get("coordinates")
    if not isinstance(rings, list) or len(rings) != 1:
        raise PolygonError("polygon must have exactly one ring (holes are not supported)")
    ring = rings[0]
    if not isinstance(ring, list) or len(ring) < 4:
        raise PolygonError("ring needs at least 4 positions")
    for pos in ring:
        if not (isinstance(pos, list) and len(pos) >= 2
                and all(isinstance(v, (int, float)) for v in pos[:2])):
            raise PolygonError(f"invalid position {pos!r}")
        lon, lat = pos[0], pos[1]
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise PolygonError(f"position {pos!r} is not lon/lat (expected [lon, lat] in degrees)")
    if ring[0][:2] != ring[-1][:2]:
        raise PolygonError("ring is not closed (first and last position differ)")
    poly = shape(obj)
    if not poly.is_valid:
        raise PolygonError("polygon is not valid (self-intersecting or degenerate)")
    if poly.area == 0:
        raise PolygonError("polygon has zero area")
    return poly
    
def to_metric(geom):
    return transform(_to_metric, geom)


def to_wgs84(geom):
    return transform(_to_wgs84, geom)


def buffer_wgs84(poly: Polygon, metres: float) -> Polygon:
    """Buffer a lon/lat polygon by `metres`, measured in EPSG:3035. Result is lon/lat."""
    return to_wgs84(to_metric(poly).buffer(metres))


def check_buffer(buffer_m: float, max_src_dist_m: float) -> None:
    """A buffer smaller than confMaxSrcDist silently drops sources near the edge (§4)."""
    if buffer_m < max_src_dist_m:
        raise ValueError(
            f"extraction buffer {buffer_m:g} m is smaller than confMaxSrcDist {max_src_dist_m:g} m")