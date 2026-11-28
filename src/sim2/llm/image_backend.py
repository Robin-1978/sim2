"""Deployment configuration and HTTP adapter for an independent image backend."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..bootstrap.artwork import MAX_ARTWORK_BYTES


_DEFAULT_SIZES = {"scene": "1536x1024", "portrait": "1024x1024"}
_QUALITY_LEVELS = {"auto", "low", "medium", "high", "xhigh", "max"}
_MAX_RESPONSE_BYTES = 4 * ((MAX_ARTWORK_BYTES + 2) // 3) + 65536


@dataclass(frozen=True)
class ArtworkConfig:
    endpoint: str
    model: str
    sizes: dict[str, str]
    style: str
    api_key_env: str | None = None
    quality: str | None = None
    output_compression: int | None = None

    @classmethod
    def load(cls, path: Path) -> ArtworkConfig:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Artwork config must be a JSON object")
        unknown = raw.keys() - {"endpoint", "model", "sizes", "style", "api_key_env", "quality", "output_compression"}
        if unknown:
            raise ValueError("Artwork config contains unsupported fields")
        endpoint = raw.get("endpoint")
        url = urlsplit(endpoint if isinstance(endpoint, str) else "")
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("Artwork endpoint must be an HTTP(S) URL without credentials or a fragment")
        model = raw.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Artwork model is required")
        sizes = raw.get("sizes", _DEFAULT_SIZES)
        if not isinstance(sizes, dict) or not sizes or not sizes.keys() <= _DEFAULT_SIZES.keys():
            raise ValueError("Artwork sizes must select scene, portrait, or both")
        if any(not isinstance(size, str) or not size.strip() or len(size) > 30 for size in sizes.values()):
            raise ValueError("Each artwork size must be a short provider size string")
        style = raw.get("style", "Painterly adventure illustration suitable for an in-game scene or portrait.")
        api_key_env = raw.get("api_key_env")
        if not isinstance(style, str) or not 0 < len(style.strip()) <= 500:
            raise ValueError("Artwork style must contain 1 to 500 characters")
        if api_key_env is not None and (not isinstance(api_key_env, str) or not api_key_env.isidentifier()):
            raise ValueError("Artwork api_key_env must name an environment variable")
        quality = raw.get("quality")
        if quality is not None and (not isinstance(quality, str) or quality not in _QUALITY_LEVELS):
            raise ValueError("Artwork quality must be auto, low, medium, high, xhigh, or max")
        compression = raw.get("output_compression")
        if compression is not None and (type(compression) is not int or not 0 <= compression <= 100):
            raise ValueError("Artwork output_compression must be a whole number from 0 to 100")
        return cls(endpoint, model, dict(sizes), style, api_key_env, quality, compression)


class ImageBackend:
    """A deployment-supplied HTTP image endpoint with a base64 WebP response."""

    def __init__(self, config: ArtworkConfig, client: httpx.Client):
        self.config = config
        self.client = client
        self.headers = {}
        if config.api_key_env:
            key = os.environ.get(config.api_key_env)
            if not key:
                raise ValueError(f"Artwork provider requires {config.api_key_env}")
            self.headers["Authorization"] = f"Bearer {key}"

    def render(self, prompt: str, kind: str) -> bytes:
        request = {"model": self.config.model, "prompt": prompt, "size": self.config.sizes[kind],
                   "n": 1, "output_format": "webp"}
        if self.config.quality is not None:
            request["quality"] = self.config.quality
        if self.config.output_compression is not None:
            request["output_compression"] = self.config.output_compression
        data = bytearray()
        with self.client.stream("POST", self.config.endpoint, headers=self.headers, json=request) as response:
            response.raise_for_status()
            for part in response.iter_bytes(chunk_size=65536):
                if len(data) + len(part) > _MAX_RESPONSE_BYTES:
                    raise ValueError("Artwork response exceeded the size limit")
                data.extend(part)
        payload = json.loads(data)
        images = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
            raise ValueError("Artwork provider must return one image")
        encoded = images[0].get("b64_json")
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_ARTWORK_BYTES + 2) // 3):
            raise ValueError("Artwork provider must return a base64 WebP")
        return base64.b64decode(encoded, validate=True)
