import pytest

from noise_worker.geometry import buffer_wgs84, parse_polygon
from noise_worker.regions import RegionError, find_region, load_regions, parse_poly

from conftest import AREA


def area_at(lon, lat, size=0.01):
    return buffer_wgs84(parse_polygon({"type": "Polygon", "coordinates": [[
        [lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]]}), 200)


def test_parse_poly_with_hole():
    text = ("test\n1\n 0 0\n 10 0\n 10 10\n 0 10\n 0 0\nEND\n"
            "!2\n 4 4\n 6 4\n 6 6\n 4 6\n 4 4\nEND\nEND\n")
    area = parse_poly(text)
    assert area.area == 100 - 4


def test_picks_the_smallest_region_that_covers_the_area(settings):
    regions = load_regions(settings.registry_path, settings.osm_data_dir)
    assert sorted(r.code for r in regions) == ["BIG", "SMALL"]
    assert find_region(buffer_wgs84(parse_polygon(AREA), 200), regions).code == "SMALL"


def test_falls_back_to_the_bigger_region(settings):
    regions = load_regions(settings.registry_path, settings.osm_data_dir)
    # Crosses SMALL's east edge (10.45) but is well inside BIG.
    assert find_region(area_at(10.445, 44.80), regions).code == "BIG"


def test_swapped_lat_lon_is_outside_every_region(settings):
    regions = load_regions(settings.registry_path, settings.osm_data_dir)
    with pytest.raises(RegionError, match="outside every region"):
        find_region(area_at(44.80, 10.30), regions)


def test_area_across_the_edge_of_every_region(settings):
    regions = load_regions(settings.registry_path, settings.osm_data_dir)
    with pytest.raises(RegionError, match="crosses the boundary of BIG"):
        find_region(area_at(11.995, 45.0), regions)


def test_region_without_pbf(settings):
    (settings.osm_data_dir / "small-latest.osm.pbf").unlink()
    regions = load_regions(settings.registry_path, settings.osm_data_dir)
    with pytest.raises(RegionError, match="not downloaded yet"):
        find_region(buffer_wgs84(parse_polygon(AREA), 200), regions)


def test_regions_without_poly_are_skipped(settings):
    (settings.osm_data_dir / "big.poly").unlink()
    assert [r.code for r in load_regions(settings.registry_path, settings.osm_data_dir)] == ["SMALL"]