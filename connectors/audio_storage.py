"""Object storage connector for generated recap audio.

Owns the GCS client. Services pass bytes + an object key and get back a key to
persist; signed URLs are minted per request at read time because they expire.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import timedelta

from google.cloud import storage
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "stonkie-recap-audio"
DEFAULT_SIGNED_URL_TTL = timedelta(hours=6)


def _credentials_from_env():
    """Build service-account credentials from GOOGLE_APPLICATION_CREDENTIALS_JSON.

    Same base64-encoded-JSON convention as scripts/export_financial_report.py.
    Signed URLs need a private key to sign with, so plain ADC user credentials
    are not sufficient here -- returns None to fall back to ADC only when the
    env var is absent (e.g. on Cloud Run with a bound service account).
    """
    raw = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not raw:
        return None
    try:
        info = json.loads(base64.b64decode(raw).decode("utf-8"))
        return service_account.Credentials.from_service_account_info(info)
    except Exception:
        logger.exception("recap_audio.credentials_decode_failed")
        return None


class AudioStorageConnector:
    """Uploads/serves recap audio objects in GCS."""

    def __init__(self, bucket_name: str | None = None, client: storage.Client | None = None) -> None:
        self._bucket_name = bucket_name or os.getenv("GCS_AUDIO_BUCKET", DEFAULT_BUCKET)
        self._client = client
        self._bucket = None

    def _get_bucket(self):
        # Lazily built so importing this module (and the API process at large)
        # does not require GCP credentials to be present.
        if self._bucket is None:
            if self._client is None:
                credentials = _credentials_from_env()
                self._client = storage.Client(credentials=credentials) if credentials else storage.Client()
            self._bucket = self._client.bucket(self._bucket_name)
        return self._bucket

    def upload(self, key: str, data: bytes, *, content_type: str = "audio/mpeg") -> str:
        blob = self._get_bucket().blob(key)
        blob.upload_from_string(data, content_type=content_type)
        logger.info("recap_audio.uploaded key=%s bytes=%d", key, len(data))
        return key

    def signed_url(self, key: str, *, ttl: timedelta = DEFAULT_SIGNED_URL_TTL) -> str:
        blob = self._get_bucket().blob(key)
        return blob.generate_signed_url(version="v4", expiration=ttl, method="GET")
