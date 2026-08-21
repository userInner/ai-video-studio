from app.research import Sub2APIResearcher


def test_parse_research_response_extracts_and_deduplicates_citations() -> None:
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "核验结论：标题需要保留一审限定。",
                        "annotations": [
                            {"type": "url_citation", "title": "证监会公告", "url": "https://www.csrc.gov.cn/example?utm_source=openai"},
                            {"type": "url_citation", "title": "证监会公告", "url": "https://www.csrc.gov.cn/example"},
                            {"type": "url_citation", "title": "新华社报道", "url": "https://www.xinhuanet.com/example"},
                        ],
                    }
                ],
            }
        ]
    }
    result = Sub2APIResearcher.parse_response(payload)
    assert result.memo.startswith("核验结论")
    assert len(result.sources) == 2
    assert result.sources[0].publisher == "中国证监会"
    assert result.sources[0].credibility == "primary"
    assert "utm_source" not in result.sources[0].url
