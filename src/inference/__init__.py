"""Object-detection inference clients (Roboflow inference server).

Feature debuggers that need to locate objects a fixed template can't match
(e.g. the Fishing Tournament fish detector) call out to a self-hosted Roboflow
inference server over HTTP. See :mod:`inference.roboflow_client`.
"""
from __future__ import annotations

from inference.roboflow_client import (
    Detection,
    InferenceUnavailableError,
    RoboflowDetector,
    detector_from_settings,
)

__all__ = [
    "Detection",
    "InferenceUnavailableError",
    "RoboflowDetector",
    "detector_from_settings",
]
