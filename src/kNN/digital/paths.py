"""On-disk layout for the chief-profile digit kNN."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from config.paths import repo_root


@lru_cache(maxsize=1)
def digital_data_dir() -> Path:
    return repo_root() / "data" / "kNN" / "digital"


def dataset_dir() -> Path:
    return digital_data_dir() / "dataset"


def model_path() -> Path:
    return digital_data_dir() / "model.yml"
