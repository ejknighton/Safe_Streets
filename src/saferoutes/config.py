"""Pydantic models for an area config file (e.g. config/smmusd.yaml).

Each class mirrors one section of the YAML.
"""

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base for every config section: unknown keys are errors, values are read-only."""

    model_config = ConfigDict(extra='forbid', frozen=True)


# --- area -------------------------------------------------------------------


class BoundaryConfig(StrictModel):
    source: Literal['tiger_unified_school_district']
    # Two-digit state FIPS code as a string ("06" = California). strict=True rejects
    # an unquoted YAML 06, which would arrive as the integer 6.
    state_fips: str = Field(pattern=r'^\d{2}$', strict=True)
    # District name exactly as it appears in the TIGER/Line NAME field
    name: str


class AreaConfig(StrictModel):
    # Used in folder names (data/raw/..., site/<id>), so lowercase letters, digits, underscores
    id: str = Field(pattern=r'^[a-z0-9_]+$')
    name: str
    boundary: BoundaryConfig
    buffer_m: float = Field(gt=0)
    # Projected CRS in meters used for all distances and areas (EPSG:3310 = California Albers)
    crs: str


# --- schools ----------------------------------------------------------------


class SchoolGroupsConfig(StrictModel):
    by: Literal['city']


class SchoolsConfig(StrictModel):
    sources: tuple[Literal['cde_pubschls', 'cde_private', 'nces_private_locations'], ...] = Field(
        min_length=1
    )
    select: Literal['within_boundary']
    sectors: tuple[Literal['public', 'private'], ...] = Field(min_length=1)
    # CDE `Virtual` codes to exclude ("F" = exclusively virtual)
    exclude_virtual: tuple[str, ...]
    # CDE `EILCode` values to keep
    instruction_levels: tuple[str, ...] = Field(min_length=1)
    groups: SchoolGroupsConfig


# --- zones ------------------------------------------------------------------


class ZoneDistances(StrictModel):
    walk_m: float = Field(gt=0)
    bike_m: float = Field(gt=0)


class ZonesConfig(StrictModel):
    elementary: ZoneDistances
    secondary: ZoneDistances


# --- network ----------------------------------------------------------------


class OsmConfig(StrictModel):
    network_types: tuple[Literal['walk', 'bike'], ...] = Field(min_length=1)
    simplify: bool


class NetworkConfig(StrictModel):
    osm: OsmConfig


# --- site -------------------------------------------------------------------


class SiteConfig(StrictModel):
    title: str
    output_dir: Path


# --- top level --------------------------------------------------------------


class Config(StrictModel):
    area: AreaConfig
    schools: SchoolsConfig
    zones: ZonesConfig
    network: NetworkConfig
    site: SiteConfig

    @model_validator(mode='after')
    def _buffer_covers_bike_zones(self) -> Self:
        # The OSM network is only downloaded out to buffer_m, so a bike zone longer than
        # the buffer would be silently cut short for schools near the boundary.
        max_bike_m = max(self.zones.elementary.bike_m, self.zones.secondary.bike_m)
        if self.area.buffer_m < max_bike_m:
            raise ValueError(
                f'area.buffer_m ({self.area.buffer_m:g}) must be at least the largest '
                f'zone bike_m ({max_bike_m:g}), or bike zones near the boundary get cut short'
            )
        return self


def load_config(path: Path | str) -> Config:
    """Read and validate an area config YAML file.

    Raises FileNotFoundError if the file is missing, ValueError if it is empty or not a
    mapping, and pydantic.ValidationError if any value is invalid.
    """
    path = Path(path)
    with path.open(encoding='utf-8') as config_file:
        data = yaml.safe_load(config_file)

    if data is None:
        raise ValueError(f'Config file is empty: {path}')
    if not isinstance(data, dict):
        raise ValueError(f'Config file must be a YAML mapping of sections: {path}')

    return Config.model_validate(data)
