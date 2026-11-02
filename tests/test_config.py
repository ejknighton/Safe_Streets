import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from saferoutes.config import Config, load_config

SMMUSD_CONFIG = Path(__file__).parent.parent / 'config' / 'smmusd.yaml'


@pytest.fixture
def smmusd_data() -> dict:
    """A fresh, editable copy of the SMMUSD config as a plain dict."""
    with SMMUSD_CONFIG.open(encoding='utf-8') as config_file:
        return copy.deepcopy(yaml.safe_load(config_file))


def test_smmusd_config_loads():
    config = load_config(SMMUSD_CONFIG)

    assert config.area.id == 'smmusd'
    assert config.area.boundary.state_fips == '06'
    assert config.zones.secondary.bike_m == 3200


def test_unknown_key_is_rejected(smmusd_data):
    smmusd_data['zones']['elementary']['walk_mm'] = 800

    with pytest.raises(ValidationError, match='walk_mm'):
        Config.model_validate(smmusd_data)


def test_buffer_smaller_than_bike_zone_is_rejected(smmusd_data):
    # A +25% sensitivity run on secondary bike distance: 3200 m -> 4000 m
    smmusd_data['zones']['secondary']['bike_m'] = 4000

    with pytest.raises(ValidationError, match='buffer_m'):
        Config.model_validate(smmusd_data)


def test_buffer_equal_to_bike_zone_is_allowed(smmusd_data):
    smmusd_data['area']['buffer_m'] = 3200

    config = Config.model_validate(smmusd_data)

    assert config.area.buffer_m == 3200


def test_empty_config_file_is_rejected(tmp_path):
    empty_file = tmp_path / 'empty.yaml'
    empty_file.write_text('', encoding='utf-8')

    with pytest.raises(ValueError, match='empty'):
        load_config(empty_file)
