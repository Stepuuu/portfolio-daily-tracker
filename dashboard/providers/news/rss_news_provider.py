"""
RSS 新闻 Provider
复用 claw_try 里的多源新闻思路，为对话 Agent 提供可查询的市场新闻。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional
import asyncio
import xml.etree.ElementTree as ET

import requests


class RSSNewsProvider:
    """多源 RSS 财经新闻聚合。"""

    SOURCES = [
        ("https://www.cnbc.com/id/10000664/device/rss/rss.html", "CNBC Markets", "market", 6),
        ("https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "WSJ Markets", "market", 5),
        ("https://feeds.bbci.co.uk/news/business/rss.xml", "BBC Business", "market", 5),
        ("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC Top News", "world", 6),
        ("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC World", "world", 6),
    ]

    def __init__(self, max_age_hours: int = 120):
        self.max_age_hours = max_age_hours
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

    @property
    def name(self) -> str:
        return "RSS News (CNBC/WSJ/BBC)"

    def _parse_pub_date(self, value: str) -> Optional[datetime]:
        if not value:
            return None
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _fetch_rss_sync(self, url: str, source_name: str, category: str, max_items: int) -> Dict:
        try:
            response = requests.get(url, headers=self._headers, timeout=12)
            response.raise_for_status()
            content = response.content

            root = ET.fromstring(content)
            items = []
            fallback_items = []
            saw_parseable_pubdate = False
            cutoff = datetime.now(timezone.utc) - timedelta(hours=self.max_age_hours)

            for item in root.findall(".//item"):
                title = item.find("title")
                desc = item.find("description")
                link = item.find("link")
                pub_date = item.find("pubDate")

                if title is None or not title.text:
                    continue

                record = {
                    "source": source_name,
                    "category": category,
                    "title": title.text.strip(),
                    "link": link.text.strip() if link is not None and link.text else "",
                    "description": (desc.text or "").strip()[:240] if desc is not None else "",
                    "published": pub_date.text.strip() if pub_date is not None and pub_date.text else "",
                }

                fallback_items.append(record)
                pub_dt = self._parse_pub_date(record["published"])
                if pub_dt is not None:
                    saw_parseable_pubdate = True

                if pub_dt is None or pub_dt >= cutoff:
                    items.append(record)

                if len(items) >= max_items:
                    break

            if not items and not saw_parseable_pubdate:
                items = fallback_items[:max_items]

            return {
                "source": source_name,
                "category": category,
                "status": "ok",
                "items": items,
            }
        except Exception as e:
            return {
                "source": source_name,
                "category": category,
                "status": "error",
                "error": str(e),
                "items": [],
            }

    async def fetch_all_news(self) -> Dict[str, Dict]:
        loop = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(self._executor, self._fetch_rss_sync, url, source, category, max_items)
            for url, source, category, max_items in self.SOURCES
        ]
        results = await asyncio.gather(*tasks)
        return {item["source"]: item for item in results}

    def _build_flat_list(self, results: Dict[str, Dict]) -> List[Dict]:
        seen = set()
        flat: List[Dict] = []

        for _, result in results.items():
            if result.get("status") != "ok":
                continue

            for item in result.get("items", []):
                title_key = item["title"].lower().strip()
                if title_key in seen:
                    continue
                seen.add(title_key)
                flat.append(item)

        return flat

    async def get_latest_news(self, category: str = "all", limit: int = 8) -> List[Dict]:
        results = await self.fetch_all_news()
        flat = self._build_flat_list(results)

        if category != "all":
            flat = [item for item in flat if item.get("category") == category]

        return flat[:limit]

    async def search_news(self, keyword: str, limit: int = 5, category: str = "all") -> List[Dict]:
        keyword = keyword.strip().lower()
        if not keyword:
            return await self.get_latest_news(category=category, limit=limit)

        results = await self.fetch_all_news()
        flat = self._build_flat_list(results)

        if category != "all":
            flat = [item for item in flat if item.get("category") == category]

        matched = [
            item for item in flat
            if keyword in item.get("title", "").lower() or keyword in item.get("description", "").lower()
        ]
        return matched[:limit]
