from services.market_recap.url_utils import canonicalize_url, source_id_for


class TestSourceIdFor:
    def test_returns_stable_16_char_hex_for_same_url(self):
        url = "https://example.com/article"
        assert source_id_for(url) == source_id_for(url)
        assert len(source_id_for(url)) == 16
        assert all(c in "0123456789abcdef" for c in source_id_for(url))

    def test_tracking_variants_share_source_id(self):
        assert source_id_for("https://example.com/article?utm_source=newsletter") == source_id_for(
            "https://example.com/article"
        )

    def test_different_urls_have_different_source_ids(self):
        assert source_id_for("https://example.com/article-a") != source_id_for("https://example.com/article-b")


class TestCanonicalizeUrl:
    def test_strips_tracking_params(self):
        assert (
            canonicalize_url("https://example.com/article?utm_source=newsletter&fbclid=abc&keep=1")
            == "https://example.com/article?keep=1"
        )

    def test_removes_fragment(self):
        assert canonicalize_url("https://example.com/article#section") == "https://example.com/article"

    def test_normalizes_scheme_host_port_and_trailing_slash(self):
        assert canonicalize_url("http://EXAMPLE.com:80/article/") == "https://example.com/article"
