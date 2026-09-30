"""Runtime settings, read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # Shared volume written by osm-updater (mounted read-only here).
    osm_data_dir: Path
    # Registry of master PBFs; by default the one osm-updater keeps on the volume.
    registry_path: Path
    # Extraction buffer around the requested polygon, in metres (§4).
    buffer_m: float
    # NoiseModelling max propagation distance; the buffer must never be smaller (§4).
    max_src_dist_m: float
    osmium_bin: str

    @classmethod
    def from_env(cls) -> "Settings":
        osm_data_dir = Path(os.environ.get("OSM_DATA_DIR", "/data/osm"))
        return cls(
            osm_data_dir=osm_data_dir,
            registry_path=Path(os.environ.get("OSM_REGISTRY", str(osm_data_dir / "countries.json"))),
            buffer_m=float(os.environ.get("EXTRACT_BUFFER_M", "200")),
            max_src_dist_m=float(os.environ.get("NM_MAX_SRC_DIST_M", "150")),
            osmium_bin=os.environ.get("OSMIUM_BIN", "osmium"),
        )