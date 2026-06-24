"""Roboflow inference-server client for object detection on screenshots.

Talks to a self-hosted ``roboflow-inference-server-cpu`` container (the
``inference`` service in docker-compose) over its hosted-style HTTP route::

    POST {service_url}/{model_id}?api_key=...&confidence=...
    body: base64-encoded image (application/x-www-form-urlencoded)

The server pulls model weights on first request using ``api_key`` and caches
them. Response is the standard Roboflow object-detection payload — a list of
``predictions`` with **center-based** pixel coordinates.

Design mirrors two existing patterns:
- :class:`popup.close_model.CloseButtonModel` — ``available()`` gate so callers
  can degrade gracefully when no service is configured.
- :mod:`century.api` — ``httpx.AsyncClient`` + ``tenacity`` retry for transient
  network errors (but not for application-level failures).
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import cv2
import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    import numpy as np

    from config.loader import InferenceConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Detection:
    """One detected object in **pixel** coordinates of the source frame.

    ``x``/``y`` are the bounding-box *center* (Roboflow convention); ``left`` /
    ``top`` derive the top-left corner for drawing and tap targeting.
    """

    x: float
    y: float
    width: float
    height: float
    confidence: float
    class_name: str

    @property
    def left(self) -> int:
        return int(round(self.x - self.width / 2.0))

    @property
    def top(self) -> int:
        return int(round(self.y - self.height / 2.0))

    @property
    def center(self) -> tuple[int, int]:
        return int(round(self.x)), int(round(self.y))


class InferenceUnavailableError(Exception):
    """Raised when the inference service is unreachable or misconfigured."""


# Retry only transient transport errors — a bad request / 4xx is not retried.
_INFERENCE_RETRY = retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=4),
    reraise=True,
)


class RoboflowDetector:
    """Async object detector backed by a Roboflow inference server."""

    def __init__(
        self,
        *,
        service_url: str,
        model_id: str,
        api_key: str = "",
        confidence: float = 0.4,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._service_url = (service_url or "").rstrip("/")
        self._model_id = (model_id or "").strip().strip("/")
        self._api_key = api_key or ""
        self._confidence = confidence
        self._timeout = timeout_seconds
        # Reused across detect() calls — opening a fresh client per request adds
        # connection setup each tick, which hurts the fishing loop's frame rate.
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        """Lazily create + reuse one AsyncClient (must run inside an event loop)."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        """Close the reused client. Call when done with the detector."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    @classmethod
    def from_settings(cls, cfg: InferenceConfig) -> RoboflowDetector:
        return cls(
            service_url=cfg.service_url,
            model_id=cfg.fish_model_id,
            api_key=cfg.api_key,
            confidence=cfg.confidence,
            timeout_seconds=cfg.timeout_seconds,
        )

    @property
    def model_id(self) -> str:
        return self._model_id

    def available(self) -> bool:
        """True when a service URL and model id are configured.

        Does not check reachability — that surfaces as
        :class:`InferenceUnavailableError` from :meth:`detect`.
        """
        return bool(self._service_url and self._model_id)

    @staticmethod
    def _encode_jpeg_b64(image_bgr: np.ndarray) -> str:
        ok, buf = cv2.imencode(".jpg", image_bgr)
        if not ok:
            msg = "failed to JPEG-encode frame for inference"
            raise InferenceUnavailableError(msg)
        return base64.b64encode(buf.tobytes()).decode("ascii")

    @_INFERENCE_RETRY
    async def _post(self, b64: str, *, confidence: float) -> dict[str, Any]:
        url = f"{self._service_url}/{self._model_id}"
        params: dict[str, str] = {"confidence": f"{confidence}"}
        if self._api_key:
            params["api_key"] = self._api_key
        resp = await self._http().post(
            url,
            params=params,
            content=b64,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()

    async def detect(
        self,
        image_bgr: np.ndarray,
        *,
        threshold: float | None = None,
    ) -> list[Detection]:
        """Detect objects in ``image_bgr`` (a BGR numpy frame).

        Raises:
            InferenceUnavailableError: not configured, or the service could not
                be reached / returned an unusable response.
        """
        if not self.available():
            msg = "inference service is not configured (set WOS_INFERENCE_URL)"
            raise InferenceUnavailableError(msg)
        if image_bgr is None or image_bgr.size == 0:
            return []

        conf = self._confidence if threshold is None else threshold
        b64 = self._encode_jpeg_b64(image_bgr)
        try:
            body = await self._post(b64, confidence=conf)
        except httpx.HTTPStatusError as exc:
            text = exc.response.text.strip()
            msg = f"inference HTTP {exc.response.status_code}: {text[:200]}"
            raise InferenceUnavailableError(msg) from exc
        except httpx.HTTPError as exc:
            msg = f"inference request failed: {type(exc).__name__}: {exc}"
            raise InferenceUnavailableError(msg) from exc

        return self._parse(body)

    @staticmethod
    def _parse(body: dict[str, Any]) -> list[Detection]:
        preds = body.get("predictions") if isinstance(body, dict) else None
        if not isinstance(preds, list):
            return []
        out: list[Detection] = []
        for p in preds:
            if not isinstance(p, dict):
                continue
            try:
                out.append(
                    Detection(
                        x=float(p.get("x", 0.0)),
                        y=float(p.get("y", 0.0)),
                        width=float(p.get("width", 0.0)),
                        height=float(p.get("height", 0.0)),
                        confidence=float(p.get("confidence", 0.0)),
                        class_name=str(p.get("class", "") or ""),
                    )
                )
            except (TypeError, ValueError):
                logger.debug("inference: skipping malformed prediction %r", p)
        return out


def detector_from_settings(cfg: InferenceConfig | None = None) -> RoboflowDetector:
    """Build a detector from settings (loads settings if not supplied)."""
    if cfg is None:
        from config.loader import load_settings

        cfg = load_settings().inference
    return RoboflowDetector.from_settings(cfg)
