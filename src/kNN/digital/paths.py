"""On-disk layout for the chief-profile digit kNN."""
from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from config.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path


@lru_cache(maxsize=1)
def digital_data_dir() -> Path:
    return repo_root() / "data" / "kNN" / "digital"


def dataset_dir() -> Path:
    return digital_data_dir() / "dataset"


def model_path() -> Path:
    return digital_data_dir() / "model.yml"
