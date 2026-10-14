"""Load YAML config files."""
import os
from pathlib import Path
from types import SimpleNamespace

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_yaml(path):
    """Read a YAML file; relative paths resolve from the project root."""
    path = Path(path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def load_config(path=None):
    """
    Controller config as an attribute object. Path: argument, else the
    RESILIENT_CONFIG environment variable (set per experiment run), else
    config/controller_config.yaml.
    Keys under `controller:` become attributes (config.N_intervals);
    every other section stays a dict (config.scoring_weights['w1_capacity']).
    """
    path = path or os.environ.get('RESILIENT_CONFIG',
                                  'config/controller_config.yaml')
    raw = load_yaml(path)
    config = SimpleNamespace(**raw.get('controller', {}))
    for section, values in raw.items():
        if section != 'controller':
            setattr(config, section, values)
    return config
