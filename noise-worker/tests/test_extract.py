import json
from dataclasses import replace

import osmium
import pytest

from noise_worker.extract import ExtractError, extract_for_job, run_osmium_extract
from noise_worker.geometry import parse_polygon

from conftest import AREA, write_pbf


def test_extract_keeps_the_buffer_and_drops_what_is_far(settings, tmp_path):
    job = tmp_path / "job"
    summary = extract_for_job(AREA, job, settings)

    assert summary["region"] == "SMALL"
    # Inside + inside the 200 m buffer; the building 2 km away is gone.
    assert summary["counts"] == {"buildings": 2, "highways": 1}
    for name in ("input_polygon.geojson", "extract_area.geojson", "area.osm.pbf", "extract.json"):
        assert (job / name).exists()
    assert json.loads((job / "extract.json").read_text())["counts"] == summary["counts"]


def test_roads_crossing_the_edge_stay_whole(settings, tmp_path):
    extract_for_job(AREA, tmp_path / "job", settings)
    lons = [n.location.lon for n in osmium.FileProcessor(str(tmp_path / "job" / "area.osm.pbf"), osmium.osm.NODE)]
    assert max(lons) == pytest.approx(10.400)  # the far end of the road, 7 km outside the area


def test_small_buffer_is_rejected_before_any_work(settings, tmp_path):
    with pytest.raises(ValueError, match="smaller than confMaxSrcDist"):
        extract_for_job(AREA, tmp_path / "job", replace(settings, buffer_m=100))
    assert not (tmp_path / "job").exists()


def test_extract_without_roads_fails(settings, tmp_path):
    write_pbf(settings.osm_data_dir / "small-latest.osm.pbf", [(10.305, 44.803)], [])
    with pytest.raises(ExtractError, match="no roads"):
        extract_for_job(AREA, tmp_path / "job", settings)


def test_osmium_failure_leaves_no_output(tmp_path):
    job = tmp_path / "job"
    with pytest.raises(ExtractError, match="osmium extract failed"):
        run_osmium_extract(tmp_path / "missing.osm.pbf", parse_polygon(AREA), job)
    assert not (job / "area.osm.pbf").exists()