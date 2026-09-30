import copy

import pytest

from noise_worker.geometry import PolygonError, buffer_wgs84, check_buffer, parse_polygon, to_metric

from conftest import AREA


def test_accepts_polygon_feature_and_ogc_value():
    assert parse_polygon(AREA).bounds == (10.300, 44.800, 10.310, 44.806)
    assert parse_polygon({"type": "Feature", "properties": {}, "geometry": AREA}).equals(parse_polygon(AREA))
    assert parse_polygon({"value": AREA}).equals(parse_polygon(AREA))


def broken(change):
    poly = copy.deepcopy(AREA)
    change(poly)
    return poly


@pytest.mark.parametrize("bad, message", [
    ({"type": "Point", "coordinates": [10.3, 44.8]}, "expected a GeoJSON Polygon"),
    (broken(lambda p: p["coordinates"].append(p["coordinates"][0])), "exactly one ring"),
    (broken(lambda p: p["coordinates"][0].pop()), "not closed"),
    (broken(lambda p: p["coordinates"][0][1].__setitem__(1, 95)), "not lon/lat"),
    (broken(lambda p: p["coordinates"][0].__setitem__(slice(1, 3), [[10.310, 44.806], [10.310, 44.800]])),
     "not valid"),
    ({"type": "Polygon", "coordinates": [[[10.3, 44.8], [10.3, 44.8], [10.3, 44.8], [10.3, 44.8]]]},
     "not valid"),
])
def test_rejects_bad_polygons(bad, message):
    with pytest.raises(PolygonError, match=message):
        parse_polygon(bad)


def test_buffer_is_measured_in_metres():
    poly = parse_polygon(AREA)
    buffered = buffer_wgs84(poly, 200)
    assert buffered.contains(poly)
    # Every point of the new edge is ~200 m from the original polygon (the buffer's arcs
    # are approximated by chords, so a little less than 200 m in between vertices).
    gap = to_metric(buffered).exterior.distance(to_metric(poly))
    assert 198 < gap <= 200.01


def test_buffer_must_cover_propagation_distance():
    check_buffer(200, 150)
    check_buffer(150, 150)
    with pytest.raises(ValueError, match="smaller than confMaxSrcDist"):
        check_buffer(100, 150)