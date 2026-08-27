from app.script_writer import _citation_key


def test_citation_key_matches_common_www_aliases() -> None:
    assert _citation_key("https://www.nbd.com.cn/articles/1/?utm_source=test") == _citation_key(
        "https://nbd.com.cn/articles/1"
    )
    assert _citation_key("https://www3.xinhuanet.com/legal/a.html") == _citation_key(
        "https://xinhuanet.com/legal/a.html"
    )


def test_citation_key_accepts_safe_publisher_url_variants() -> None:
    assert _citation_key(
        "https://www.coindesk.com/live/bitcoin-rally?post-id=abc123&utm_source=feed"
    ) == _citation_key("https://coindesk.com/live/bitcoin-rally")
    assert _citation_key(
        "https://economictimes.indiatimes.com/markets/bitcoin/symbol-btc.cms"
    ) == _citation_key("http://economictimes.indiatimes.com/markets/bitcoin/symbol-btc")


def test_citation_key_keeps_meaningful_query_and_path_boundaries() -> None:
    assert _citation_key("https://example.com/report?id=1") != _citation_key(
        "https://example.com/report?id=2"
    )
    assert _citation_key("https://example.com/report-a") != _citation_key(
        "https://example.com/report-b"
    )
