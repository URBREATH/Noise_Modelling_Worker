"""Country registry (§3.4): one master PBF per participating country."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

REQUIRED_FIELDS = ("name", "pbf_file", "download_url", "updates_url")


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    pbf_file: str
    download_url: str
    updates_url: str
    md5_url: str | None = None
    poly_url: str | None = None

    def pbf_path(self, data_dir: Path) -> Path:
        return data_dir / self.pbf_file

    def poly_path(self, data_dir: Path) -> Path | None:
        """Country boundary (Geofabrik .poly), kept next to the PBF for Phase 2's country lookup."""
        if not self.poly_url:
            return None
        return data_dir / PurePosixPath(urlparse(self.poly_url).path).name


def load_registry(path: Path) -> dict[str, Country]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RegistryError(f"registry not found: {path}")
    except json.JSONDecodeError as e:
        raise RegistryError(f"registry {path} is not valid JSON: {e}")

    entries = raw.get("countries")
    if not isinstance(entries, dict) or not entries:
        raise RegistryError(f"registry {path}: 'countries' must be a non-empty object")

    countries: dict[str, Country] = {}
    seen_files: set[str] = set()
    for code, entry in entries.items():
        missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
        if missing:
            raise RegistryError(f"registry {path}: {code} is missing {', '.join(missing)}")
        pbf_file = entry["pbf_file"]
        if Path(pbf_file).is_absolute() or ".." in Path(pbf_file).parts:
            raise RegistryError(f"registry {path}: {code} pbf_file must be relative to the data dir")
        if not pbf_file.endswith(".osm.pbf"):
            raise RegistryError(f"registry {path}: {code} pbf_file must end in .osm.pbf")
        if pbf_file in seen_files:
            raise RegistryError(f"registry {path}: {code} reuses pbf_file {pbf_file}")
        seen_files.add(pbf_file)
        countries[code.upper()] = Country(
            code=code.upper(),
            name=entry["name"],
            pbf_file=pbf_file,
            download_url=entry["download_url"],
            updates_url=entry["updates_url"],
            md5_url=entry.get("md5_url"),
            poly_url=entry.get("poly_url"),
        )
    return countries


def ensure_registry(registry_path: Path, default_registry: Path) -> bool:
    """Seed the registry on the shared volume from the bundled default. Returns True if copied."""
    if registry_path.exists():
        return False
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = registry_path.with_name(f".{registry_path.name}.tmp")
    shutil.copyfile(default_registry, tmp)
    tmp.replace(registry_path)
    return True


def select(countries: dict[str, Country], codes: list[str] | None) -> list[Country]:
    if not codes:
        return list(countries.values())
    unknown = [c for c in codes if c.upper() not in countries]
    if unknown:
        raise RegistryError(f"unknown country code(s): {', '.join(unknown)}")
    return [countries[c.upper()] for c in codes]