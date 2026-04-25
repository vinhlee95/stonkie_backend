import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src"}


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = hostname

    if port and port not in {80, 443}:
        netloc = f"{hostname}:{port}"

    path = parsed.path.rstrip("/") or ""
    query_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.startswith("utm_") and key not in TRACKING_PARAMS
    ]
    query = urlencode(query_params, doseq=True)

    return urlunsplit(("https", netloc, path, query, ""))


def source_id_for(url: str) -> str:
    return hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()[:16]
