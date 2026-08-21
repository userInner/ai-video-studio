from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .config import Settings
from .schemas import SourceContract


class ResearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResearchPack:
    memo: str
    sources: list[SourceContract]
    is_demo: bool = False

    def as_dict(self) -> dict:
        return {
            "memo": self.memo,
            "sources": [source.model_dump(mode="json") for source in self.sources],
            "is_demo": self.is_demo,
        }


def _canonical_url(value: str) -> str:
    parts = urlsplit(value)
    query = [(key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ""))


def _publisher(url: str, title: str) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    known = {
        "csrc.gov.cn": "中国证监会",
        "wenshu.court.gov.cn": "中国裁判文书网",
        "court.gov.cn": "最高人民法院",
        "gov.cn": "中国政府网",
        "xinhuanet.com": "新华社",
        "people.com.cn": "人民网",
        "thepaper.cn": "澎湃新闻",
        "caixin.com": "财新",
    }
    for domain, name in known.items():
        if host == domain or host.endswith(f".{domain}"):
            return name
    return title.strip() or host


def _credibility(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    if host.endswith(".gov.cn") or host in {"gov.cn", "court.gov.cn"}:
        return "primary"
    if any(domain in host for domain in ("xinhuanet.com", "people.com.cn", "thepaper.cn", "caixin.com")):
        return "strong"
    return "supporting"


class Sub2APIResearcher:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, user_input: str) -> ResearchPack:
        key = self.settings.resolved_sub2api_key()
        if not key:
            if self.settings.allow_demo_fallback:
                return self.demo_pack()
            raise ResearchError("Sub2API 尚未配置")

        prompt = f"""
当前日期：{datetime.now().astimezone().date().isoformat()}。
请核验用户给出的标题或想法：{user_input}

你只负责事实调研，不负责设计视频角度。必须使用联网搜索，并输出中文核验备忘录：
1. 先给出核验结论，指出标题正确、错误或缺少限定词的部分；
2. 给出关键时间线和直接影响；
3. 优先法院、监管机构、政府、公司公告等原始来源，再用主流媒体交叉验证；
4. 每个重要事实都附搜索引用；
5. 明确区分事实、推断和目前无法证实的信息。
""".strip()
        payload = {
            "model": self.settings.text_model,
            "tools": [{"type": "web_search", "search_context_size": "medium"}],
            "tool_choice": "required",
            "input": prompt,
            "max_output_tokens": 1800,
        }
        last_error: Exception | None = None
        for attempt in range(1, self.settings.research_max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.settings.research_timeout_seconds) as client:
                    response = await client.post(
                        f"{self.settings.sub2api_base_url.rstrip('/')}/responses",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json=payload,
                    )
                if response.status_code >= 500:
                    raise ResearchError(f"Sub2API 暂时不可用（{response.status_code}）")
                if response.status_code >= 400:
                    detail = response.json().get("error", {}).get("message", response.text[:300])
                    raise ResearchError(f"Sub2API 搜索不兼容：{detail}")
                return self.parse_response(response.json())
            except (httpx.HTTPError, ResearchError, ValueError) as exc:
                last_error = exc
                if attempt < self.settings.research_max_attempts:
                    await asyncio.sleep(2 ** (attempt - 1))

        if self.settings.allow_demo_fallback:
            return self.demo_pack()
        raise ResearchError(f"联网调研失败：{last_error}") from last_error

    @staticmethod
    def parse_response(payload: dict) -> ResearchPack:
        memo_parts: list[str] = []
        citations: dict[str, tuple[str, str]] = {}
        for item in payload.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") != "output_text":
                    continue
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    memo_parts.append(text.strip())
                for annotation in content.get("annotations", []):
                    url = annotation.get("url")
                    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
                        continue
                    canonical = _canonical_url(url)
                    title = annotation.get("title") if isinstance(annotation.get("title"), str) else ""
                    citations[canonical] = (title, canonical)

        memo = "\n\n".join(memo_parts)
        if not memo:
            raise ResearchError("搜索响应缺少核验正文")
        sources = [
            SourceContract(
                title=title or urlsplit(url).netloc,
                url=url,
                publisher=_publisher(url, title),
                published_at="",
                credibility=_credibility(url),
                summary="此来源被联网核验备忘录直接引用。",
            )
            for title, url in citations.values()
        ][:8]
        if len(sources) < 2:
            raise ResearchError("搜索响应中的可核验来源不足两条")
        return ResearchPack(memo=memo, sources=sources)

    @staticmethod
    def demo_pack() -> ResearchPack:
        return ResearchPack(
            memo="当前为产品流程演示，尚未得到实时来源，进入正式制作前必须重新联网核验。",
            is_demo=True,
            sources=[
                SourceContract(
                    title="国务院政策文件库（待定向检索）",
                    url="https://www.gov.cn/zhengce/",
                    publisher="中国政府网",
                    published_at="",
                    credibility="primary",
                    summary="正式流程将从权威原始材料开始核验。",
                ),
                SourceContract(
                    title="中国裁判文书网（待定向检索）",
                    url="https://wenshu.court.gov.cn/",
                    publisher="最高人民法院",
                    published_at="",
                    credibility="primary",
                    summary="涉及司法事实时用于交叉核验裁判信息。",
                ),
            ],
        )
