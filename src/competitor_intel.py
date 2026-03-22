"""
Competitor Ad Intelligence.
Scrapes Meta Ad Library public search page using requests + BeautifulSoup.
Runs each ad through Claude for angle/offer/psychology analysis.
Stores results in competitor_ads DuckDB table.
"""

import json
import re
import subprocess
import time
from datetime import datetime
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup

from .db import _q, _q1


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

AD_LIBRARY_URL = "https://www.facebook.com/ads/library/"


def _scrape_ad_library(brand_name: str, country: str = "ALL") -> list[dict]:
    """
    Scrape Meta Ad Library for active ads by a brand name.
    Returns a list of raw ad dicts with text, CTA, days_running, media_type.
    Falls back to empty list on any error.
    """
    params = {
        "active_status": "active",
        "ad_type": "ALL",
        "country": country,
        "q": brand_name,
        "search_type": "keyword_unordered",
    }
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    url = f"{AD_LIBRARY_URL}?{query}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    ads = []

    # Meta Ad Library renders most content via JS, but the initial HTML
    # contains JSON data blobs in script tags we can parse.
    for script in soup.find_all("script"):
        text = script.string or ""
        # Look for the ad data payload
        if "adArchiveID" not in text and "ad_archive_id" not in text:
            continue
        # Try to extract JSON blobs
        for match in re.finditer(r'\{[^{}]{50,}\}', text):
            try:
                obj = json.loads(match.group())
                ad_text = (
                    obj.get("ad_creative_body")
                    or obj.get("body")
                    or obj.get("message")
                    or ""
                )
                if len(ad_text) < 10:
                    continue
                ads.append({
                    "ad_text":    ad_text[:1000],
                    "cta":        obj.get("call_to_action_type", ""),
                    "days_running": obj.get("days_delivered", 0) or 0,
                    "media_type": obj.get("media_type", "unknown"),
                    "is_active":  True,
                })
            except Exception:
                continue
        if ads:
            break

    # If JS parsing yielded nothing, try visible text cards
    if not ads:
        cards = soup.select('[data-testid="ad-card"], .x1lliihq, ._99s5')
        for card in cards[:10]:
            text = card.get_text(separator=" ", strip=True)
            if len(text) > 20:
                ads.append({
                    "ad_text":    text[:800],
                    "cta":        "",
                    "days_running": 0,
                    "media_type": "unknown",
                    "is_active":  True,
                })

    return ads[:15]  # cap at 15 ads per scrape


def _analyze_ad_with_claude(ad_text: str, brand_name: str) -> dict:
    """
    Ask Claude to analyze a single ad for angle, offer, psychology, funnel stage, persona.
    Returns a dict. Falls back to empty strings on failure.
    """
    prompt = (
        f"Analyze this Meta ad from {brand_name}. "
        "Return ONLY a JSON object (no markdown, no explanation) with these exact keys:\n"
        '{"angle": "hook or positioning used", '
        '"offer": "what they are promoting", '
        '"psychology": "triggers used (FOMO, social proof, urgency, scarcity, etc)", '
        '"funnel_stage": "ToFu or MoFu or BoFu", '
        '"persona": "target audience description", '
        '"insight": "one key takeaway for competing advertisers"}\n\n'
        f"Ad text:\n{ad_text}"
    )
    try:
        result = subprocess.run(
            ["claude", "--print", prompt],
            capture_output=True, text=True, encoding="utf-8", timeout=45
        )
        if result.returncode == 0 and result.stdout.strip():
            raw = result.stdout.strip()
            # Extract JSON from response
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                return json.loads(match.group())
    except Exception:
        pass
    return {
        "angle": "", "offer": "", "psychology": "",
        "funnel_stage": "", "persona": "", "insight": "",
    }


def _generate_pattern_summary(ads_with_analysis: list[dict], brand_name: str) -> str:
    """
    Ask Claude to summarize what the competitor is testing across all their active ads.
    """
    if not ads_with_analysis:
        return ""

    snippets = "\n".join(
        f"Ad {i+1}: angle={a.get('angle','')} | offer={a.get('offer','')} | "
        f"psychology={a.get('psychology','')} | stage={a.get('funnel_stage','')}"
        for i, a in enumerate(ads_with_analysis[:10])
    )
    prompt = (
        f"You are analyzing {brand_name}'s Meta ad library. "
        "Based on these ad analyses, write a 2-3 sentence summary of what they are currently testing. "
        "Focus on patterns in messaging angles, offers, and psychology triggers. "
        "Be specific and actionable. No preamble.\n\n"
        + snippets
    )
    try:
        result = subprocess.run(
            ["claude", "--print", prompt],
            capture_output=True, text=True, encoding="utf-8", timeout=45
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return f"Analysis of {len(ads_with_analysis)} active ads from {brand_name}."


def scrape_and_analyze(conn, client_id: int, competitor_id: int,
                       brand_name: str, country: str = "ALL") -> dict:
    """
    Full pipeline: scrape -> analyze each ad -> save to DB -> generate pattern summary.
    Returns result dict.
    """
    raw_ads = _scrape_ad_library(brand_name, country)

    if not raw_ads:
        return {
            "error": (
                f"No ads found for '{brand_name}' on Meta Ad Library. "
                "The page may require login or the brand name may not match exactly."
            ),
            "ads": [],
        }

    analyzed = []
    for ad in raw_ads:
        analysis = _analyze_ad_with_claude(ad["ad_text"], brand_name)
        ad.update(analysis)
        analyzed.append(ad)
        time.sleep(0.5)  # be polite

    pattern_summary = _generate_pattern_summary(analyzed, brand_name)

    # Save to DB
    scraped_at = datetime.utcnow().isoformat()
    rows = []
    for ad in analyzed:
        rows.append([
            competitor_id,
            ad.get("ad_text", ""),
            ad.get("cta", ""),
            ad.get("days_running", 0),
            ad.get("is_active", True),
            ad.get("media_type", ""),
            ad.get("angle", ""),
            ad.get("offer", ""),
            ad.get("psychology", ""),
            ad.get("funnel_stage", ""),
            ad.get("persona", ""),
            ad.get("insight", ""),
            scraped_at,
        ])

    if rows:
        conn.executemany("""
            INSERT INTO competitor_ads (
                competitor_id, ad_text, cta, days_running, is_active, media_type,
                angle, offer, psychology_triggers, funnel_stage, persona, insight, scraped_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)

    # Update last_scraped_at
    conn.execute(
        "UPDATE competitors SET last_scraped_at = ? WHERE id = ?",
        [scraped_at, competitor_id]
    )
    conn.commit()

    return {
        "ads":             analyzed,
        "pattern_summary": pattern_summary,
        "scraped_at":      scraped_at,
        "ad_count":        len(analyzed),
    }


def get_competitors(conn, client_id: int) -> list[dict]:
    return _q(conn, """
        SELECT id, client_id, brand_name, ad_library_url, last_scraped_at, created_at
        FROM competitors WHERE client_id = ?
        ORDER BY brand_name
    """, [client_id])


def add_competitor(conn, client_id: int, brand_name: str, ad_library_url: str = "") -> dict:
    existing = _q1(conn,
        "SELECT id FROM competitors WHERE client_id = ? AND brand_name = ?",
        [client_id, brand_name]
    )
    if existing:
        return {"error": f"'{brand_name}' already exists for this client."}
    conn.execute("""
        INSERT INTO competitors (client_id, brand_name, ad_library_url)
        VALUES (?, ?, ?)
    """, [client_id, brand_name, ad_library_url or ""])
    conn.commit()
    return _q1(conn,
        "SELECT * FROM competitors WHERE client_id = ? AND brand_name = ?",
        [client_id, brand_name]
    ) or {}


def delete_competitor(conn, competitor_id: int, client_id: int) -> None:
    conn.execute("DELETE FROM competitor_ads WHERE competitor_id = ?", [competitor_id])
    conn.execute("DELETE FROM competitors WHERE id = ? AND client_id = ?",
                 [competitor_id, client_id])
    conn.commit()


def get_competitor_ads(conn, competitor_id: int, limit: int = 20) -> list[dict]:
    return _q(conn, """
        SELECT * FROM competitor_ads
        WHERE competitor_id = ?
        ORDER BY scraped_at DESC, id DESC
        LIMIT ?
    """, [competitor_id, limit])


def get_scrape_history(conn, competitor_id: int) -> list[dict]:
    return _q(conn, """
        SELECT DISTINCT scraped_at, COUNT(*) as ad_count
        FROM competitor_ads
        WHERE competitor_id = ?
        GROUP BY scraped_at
        ORDER BY scraped_at DESC
        LIMIT 10
    """, [competitor_id])
