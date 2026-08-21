from app.script_writer import _citation_key


def test_citation_key_matches_common_www_aliases() -> None:
    assert _citation_key("https://www.nbd.com.cn/articles/1/?utm_source=test") == _citation_key(
        "https://nbd.com.cn/articles/1"
    )
    assert _citation_key("https://www3.xinhuanet.com/legal/a.html") == _citation_key(
        "https://xinhuanet.com/legal/a.html"
    )
