#!/usr/bin/env python3
"""
MOTORRAD PULSE — collect_news.py

실제 인터넷 뉴스를 RSS/Feed를 통해 수집하여 data/raw_news.json에 저장한다.

핵심 원칙 (요청서 STEP 3 기준):
- title, url, source, publishedAt은 절대 AI가 만들지 않는다. 실제 수집 데이터만 사용.
- 존재하지 않는 URL을 임의로 만들지 않는다.
- 각 소스는 최근 48시간 이내 기사만 채택한다.
- 그룹별 최대 5개. 억지로 5개를 채우지 않는다 (0개도 정상 상태).
- 한 소스가 실패해도 다른 소스 수집은 계속 진행한다.
- 실패한 그룹은 기존 raw_news.json의 해당 그룹 데이터를 그대로 보존한다 (삭제하지 않음).
- summary/importance/whyItMatters/bmwInsight/category는 null로 둔다 (AI 분석은 STEP 4).
"""

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote

import feedparser
import requests
from dateutil import parser as dateutil_parser

# ==========================================================
# 설정
# ==========================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
RAW_NEWS_PATH = os.path.join(DATA_DIR, "raw_news.json")

MAX_PER_GROUP = 5
LOOKBACK_HOURS = 48
REQUEST_TIMEOUT = 15
USER_AGENT = "MotorradPulseNewsCollector/1.0 (+https://github.com/)"

# 추적 파라미터 (요청서 14번)
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "oc"}

# sourceGroup 표시명 (요청서 4번 — 기존 UI와 동일하게 유지)
SOURCE_GROUP_LABELS = {
    "bmw": "BMW",
    "ducati": "Ducati",
    "triumph": "Triumph",
    "harley": "Harley-Davidson",
    "honda": "Honda",
    "yamaha": "Yamaha",
    "naver": "Naver",
    "google": "Google",
    "kmnews": "KMNEWS",
}

# 브랜드 키워드 분류용 (naver/google 혼합 검색 결과에서 브랜드 그룹 판별용)
BRAND_KEYWORDS = {
    "bmw": ["bmw motorrad", "bmw gs", "bmw r1300", "bmw f900", "bmw ce ", "bmw motorcycle", "비엠더블유 모토라드", "bmw 모토라드"],
    "ducati": ["ducati", "두카티"],
    "triumph": ["triumph motorcycle", "triumph tiger", "triumph street", "triumph bonneville", "triumph speed", "triumph scrambler", "triumph trident", "triumph daytona", "트라이엄프"],
    "harley": ["harley-davidson", "harley davidson", "livewire", "할리데이비슨", "할리 데이비슨"],
    "honda": ["honda motorcycle", "honda africa twin", "honda cbr", "honda cb ", "honda gold wing", "honda nc750", "honda rebel", "혼다 모터사이클", "혼다코리아"],
    "yamaha": ["yamaha motorcycle", "yamaha mt-", "yamaha tenere", "yamaha r1", "yamaha r7", "yamaha tracer", "야마하 모터사이클", "야마하코리아"],
}

# ==========================================================
# 소스 정의
# 우선순위: 국내 전문지(이륜차신문) > 국내 언론사(한국어 키워드 필터) > Google News 한국어 검색
# 요청에 따라 해외 전용 소스(RideApart, Honda EU 등)는 제외하고 한국 뉴스 중심으로 구성한다.
# 각 항목: (sourceGroup, source 표시명, sourceType, feed_url, keyword_filter 또는 None)
# ==========================================================

def google_news_rss_kr(query: str) -> str:
    """Google News 한국어/한국 로케일 검색 기반 RSS URL 생성. 쿼리는 명시적으로 URL 인코딩한다."""
    encoded_query = quote(query)
    return f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"


def naver_news_search_url(query: str) -> str:
    """네이버는 검색결과에 대한 공식 RSS를 제공하지 않는다.
    (네이버 자체 오픈API는 Client ID/Secret 등록이 필요해 완전 무료·무등록 원칙에 맞지 않아 사용하지 않는다.)
    대신 Google 검색에 "네이버뉴스"라는 표기가 붙는 기사를 우선 노출시키기 위해
    검색어 뒤에 "네이버뉴스"를 붙인다. site: 연산자는 결과를 사실상 무작위로 만들고
    개수를 크게 줄이므로 사용하지 않는다."""
    encoded_query = quote(f"{query} 네이버뉴스")
    return f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"


SOURCES = [
    # ---- 한국이륜차신문(KMNEWS) — 자체 RSS가 없어 Google 검색으로 그 매체 기사만 노출 ----
    {"sourceGroup": "kmnews", "source": "한국이륜차신문", "sourceType": "media", "url": google_news_rss_kr("이륜차신문"), "keyword_filter": None},
    {"sourceGroup": "kmnews", "source": "한국이륜차신문", "sourceType": "media", "url": google_news_rss_kr("KMNEWS 모터사이클"), "keyword_filter": None},

    # ---- 브랜드별 국내 뉴스: 검색어를 다양화(브랜드명 + 대표 모델명)해서 결과량을 늘린다 ----
    {"sourceGroup": "bmw", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("BMW 모토라드"), "keyword_filter": None},
    {"sourceGroup": "bmw", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("BMW 모토라드"), "keyword_filter": None},
    {"sourceGroup": "bmw", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("BMW GS 오토바이"), "keyword_filter": None},

    {"sourceGroup": "ducati", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("두카티 오토바이"), "keyword_filter": None},
    {"sourceGroup": "ducati", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("두카티 오토바이"), "keyword_filter": None},
    {"sourceGroup": "ducati", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("두카티 코리아"), "keyword_filter": None},

    {"sourceGroup": "triumph", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("트라이엄프 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "triumph", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("트라이엄프 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "triumph", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("트라이엄프 코리아"), "keyword_filter": None},

    {"sourceGroup": "harley", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("할리데이비슨 오토바이"), "keyword_filter": None},
    {"sourceGroup": "harley", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("할리데이비슨 오토바이"), "keyword_filter": None},
    {"sourceGroup": "harley", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("할리데이비슨 코리아"), "keyword_filter": None},

    {"sourceGroup": "honda", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("혼다 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "honda", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("혼다 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "honda", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("혼다코리아 오토바이"), "keyword_filter": None},

    {"sourceGroup": "yamaha", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("야마하 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "yamaha", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("야마하 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "yamaha", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("야마하코리아 오토바이"), "keyword_filter": None},

    # ---- NAVER 그룹: 이륜차 업계 일반 뉴스 중 네이버 노출 우선 ----
    {"sourceGroup": "naver", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("이륜차 신제품"), "keyword_filter": None},
    {"sourceGroup": "naver", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("오토바이 신모델"), "keyword_filter": None},
    {"sourceGroup": "naver", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("모터사이클 행사"), "keyword_filter": None},
    {"sourceGroup": "naver", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("이륜차 시장 판매"), "keyword_filter": None},

    # ---- GOOGLE 그룹: 이륜차 업계 일반 뉴스 (구글 전체 검색) ----
    {"sourceGroup": "google", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("이륜차 업계"), "keyword_filter": None},
    {"sourceGroup": "google", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("오토바이 신제품"), "keyword_filter": None},
    {"sourceGroup": "google", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("모터사이클 마케팅 프로모션"), "keyword_filter": None},
    {"sourceGroup": "google", "source": "Google", "sourceType": "market_report", "url": google_news_rss_kr("이륜차 시장 전망"), "keyword_filter": None},

    # ---- 한국경제 전체뉴스 RSS — 이륜차 키워드로 필터링해서 보조 소스로 활용, GOOGLE 그룹에 포함 ----
    {
        "sourceGroup": "google",
        "source": "한국경제",
        "sourceType": "media",
        "url": "https://www.hankyung.com/feed/all-news",
        "keyword_filter": ["이륜차", "오토바이", "모터사이클", "bmw 모토라드", "할리데이비슨", "두카티", "야마하", "혼다 모터사이클"],
    },
]


# ==========================================================
# 유틸리티 함수
# ==========================================================

def log(msg: str):
    print(msg, flush=True)


def normalize_url(url: str) -> str:
    """추적 파라미터를 제거한 canonical URL 반환 (요청서 14번)"""
    if not url:
        return url
    try:
        parts = urlsplit(url)
        query_pairs = parse_qsl(parts.query, keep_blank_values=True)
        cleaned_pairs = [(k, v) for k, v in query_pairs if k.lower() not in TRACKING_PARAMS]
        cleaned_query = urlencode(cleaned_pairs)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, cleaned_query, ""))
    except Exception:
        return url


def normalize_title(title: str) -> str:
    """중복 판단용 제목 정규화 (요청서 15번). 화면 표시용 원본 title은 별도 보존."""
    if not title:
        return ""
    t = title.lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\s]", "", t)
    return t


def parse_date(raw_date) -> str | None:
    """다양한 날짜 포맷을 ISO 8601(Asia/Seoul, +09:00)로 변환. 실패 시 None (요청서 16번: 임의 날짜 생성 금지)"""
    if not raw_date:
        return None
    try:
        if isinstance(raw_date, time.struct_time):
            dt = datetime.fromtimestamp(time.mktime(raw_date), tz=timezone.utc)
        else:
            dt = dateutil_parser.parse(raw_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        kst = timezone(timedelta(hours=9))
        dt_kst = dt.astimezone(kst)
        return dt_kst.isoformat()
    except Exception:
        return None


def classify_source_group(title: str, summary: str, default_group: str | None) -> str | None:
    """제목/요약 텍스트에서 브랜드 키워드를 찾아 sourceGroup을 분류.
    default_group이 이미 지정되어 있으면 그대로 사용.
    브랜드가 매칭되지 않으면 google 그룹으로 폴백한다 (naver/kmnews는 출처 자체가
    이미 sourceGroup으로 명시되어 있으므로 이 함수에서 다시 추정하지 않는다)."""
    if default_group:
        return default_group

    text = f"{title} {summary}".lower()

    for group, keywords in BRAND_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return group

    return "google"


def generate_id(url: str) -> str:
    """URL 기반 안정적 ID 생성 (요청서 13번)"""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def is_within_lookback(published_at_iso: str | None, hours: int = LOOKBACK_HOURS) -> bool:
    """최근 N시간 이내 게시된 기사인지 확인 (요청서 10번)"""
    if not published_at_iso:
        return False
    try:
        dt = datetime.fromisoformat(published_at_iso)
        now = datetime.now(dt.tzinfo)
        return (now - dt) <= timedelta(hours=hours)
    except Exception:
        return False


# ==========================================================
# 핵심 수집 함수
# ==========================================================

def collect_rss(source_config: dict) -> tuple[list[dict], str | None]:
    """단일 RSS 소스에서 기사를 수집한다.
    반환: (수집된 기사 리스트, 오류 메시지 또는 None)
    이 함수 내부에서 예외가 발생해도 상위로 전파하지 않고 오류 메시지로 반환한다 (요청서 17번)."""

    url = source_config["url"]
    source_group = source_config["sourceGroup"]
    source_name = source_config["source"]
    source_type = source_config["sourceType"]
    keyword_filter = source_config.get("keyword_filter")

    try:
        headers = {"User-Agent": USER_AGENT}
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)

        if feed.bozo and not feed.entries:
            return [], f"피드 파싱 실패 (bozo): {feed.bozo_exception}"

        articles = []
        for entry in feed.entries:
            title = getattr(entry, "title", "").strip()
            link = getattr(entry, "link", "").strip()
            summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            summary = re.sub(r"<[^>]+>", "", summary).strip()

            if not title or not link:
                continue

            # keyword_filter가 있으면 제목/요약에 키워드가 포함된 것만 채택 (요청서 6, 8번)
            if keyword_filter:
                text = f"{title} {summary}".lower()
                if not any(kw.lower() in text for kw in keyword_filter):
                    continue

            raw_date = getattr(entry, "published_parsed", None) or getattr(entry, "published", None)
            published_at = parse_date(raw_date)

            if not is_within_lookback(published_at):
                continue

            clean_url = normalize_url(link)
            resolved_group = classify_source_group(title, summary, source_group)

            articles.append({
                "title": title,
                "url": clean_url,
                "source": source_name,
                "sourceType": source_type,
                "sourceGroup": resolved_group,
                "publishedAt": published_at,
                "summary_raw": summary[:300],  # 중복판단 참고용, 최종 저장 시 제거
            })

        return articles, None

    except requests.exceptions.RequestException as e:
        return [], f"네트워크 오류: {e}"
    except Exception as e:
        return [], f"알 수 없는 오류: {e}"


def remove_duplicates(articles: list[dict]) -> list[dict]:
    """URL 및 정규화된 제목 기준 중복 제거 (요청서 14, 15번)"""
    seen_urls = set()
    seen_titles = set()
    unique = []

    for a in articles:
        url_key = a["url"]
        title_key = normalize_title(a["title"])

        if url_key in seen_urls or title_key in seen_titles:
            continue

        seen_urls.add(url_key)
        seen_titles.add(title_key)
        unique.append(a)

    return unique


def load_existing_news() -> dict:
    """기존 raw_news.json을 읽는다. 없으면 빈 구조 반환 (요청서 18번: 안전장치)"""
    if not os.path.exists(RAW_NEWS_PATH):
        return {"lastCollectedAt": None, "news": []}
    try:
        with open(RAW_NEWS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "news" not in data:
                data["news"] = []
            return data
    except Exception as e:
        log(f"[WARNING] 기존 raw_news.json 로드 실패, 빈 상태로 시작: {e}")
        return {"lastCollectedAt": None, "news": []}


def merge_news(existing_news: list[dict], newly_collected: dict[str, list[dict]], failed_groups: set[str]) -> list[dict]:
    """그룹별 신규 수집 결과를 기존 데이터와 병합.
    실패한 그룹은 기존 데이터를 그대로 보존한다 (요청서 18번 핵심 안전장치)."""

    existing_by_group: dict[str, list[dict]] = {}
    for item in existing_news:
        g = item.get("sourceGroup", "unknown")
        existing_by_group.setdefault(g, []).append(item)

    merged: list[dict] = []

    for group in SOURCE_GROUP_LABELS.keys():
        if group in failed_groups:
            # 수집 실패 -> 기존 데이터 유지
            merged.extend(existing_by_group.get(group, []))
            continue

        new_items = newly_collected.get(group, [])
        old_items = existing_by_group.get(group, [])

        # 신규 수집 URL 집합
        new_urls = {item["url"] for item in new_items}

        # 기존 항목 중 신규로 대체되지 않은 것만 남기고, 신규 항목을 앞에 배치
        remaining_old = [item for item in old_items if item["url"] not in new_urls]

        combined = new_items + remaining_old
        # 최신순 정렬 후 그룹당 최대 5개 (요청서 11번: 억지로 채우지 않음, 있는 만큼만)
        combined.sort(key=lambda x: x.get("publishedAt") or "", reverse=True)
        merged.extend(combined[:MAX_PER_GROUP])

    return merged


def save_json(data: dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ==========================================================
# 메인 실행
# ==========================================================

def main():
    log("=" * 60)
    log("[MOTORRAD PULSE NEWS COLLECTOR]")
    log("=" * 60)

    existing_data = load_existing_news()
    existing_news = existing_data.get("news", [])

    collected_by_group: dict[str, list[dict]] = {g: [] for g in SOURCE_GROUP_LABELS}
    failed_groups: set[str] = set()
    warnings: list[str] = []
    counts: dict[str, int] = {g: 0 for g in SOURCE_GROUP_LABELS}
    duplicates_removed_total = 0

    all_new_articles: list[dict] = []

    for src in SOURCES:
        label = src["sourceGroup"] or "(키워드 자동분류)"
        log(f"\n[수집 중] {src['source']} ({label}) — {src['url'][:80]}")

        articles, error = collect_rss(src)

        if error:
            log(f"  [WARNING] {error}")
            warnings.append(f"{src['source']} ({label}): {error}")
            if src["sourceGroup"]:
                failed_groups.add(src["sourceGroup"])
            continue

        log(f"  -> {len(articles)}건 수집 (48시간 이내, 필터 적용 후)")
        all_new_articles.extend(articles)

    # 전체 중복 제거
    before = len(all_new_articles)
    deduped = remove_duplicates(all_new_articles)
    duplicates_removed_total = before - len(deduped)

    # 최종 스키마로 변환 (요청서 12번 구조). summary_raw -> description으로 보존하여
    # STEP4-FREE 규칙 기반 요약 생성 시 1순위 데이터로 사용한다.
    now_kst = datetime.now(timezone(timedelta(hours=9))).isoformat()

    for a in deduped:
        a["description"] = a.pop("summary_raw", "")
        a["id"] = generate_id(a["url"])
        a["collectedAt"] = now_kst
        a["category"] = None
        a["summary"] = None
        a["importance"] = None
        a["whyItMatters"] = None
        a["bmwInsight"] = None
        a["isTopNews"] = False
        a["aiProcessed"] = False

        group = a["sourceGroup"]
        if group in collected_by_group:
            collected_by_group[group].append(a)
        else:
            # 알 수 없는 그룹으로 분류된 경우 motorcycle_media로 폴백
            a["sourceGroup"] = "motorcycle_media"
            collected_by_group["motorcycle_media"].append(a)

    for group, items in collected_by_group.items():
        counts[group] = len(items)

    merged_news = merge_news(existing_news, collected_by_group, failed_groups)

    result = {
        "lastCollectedAt": now_kst,
        "news": merged_news,
    }

    save_json(result, RAW_NEWS_PATH)

    # ---- 로그 요약 (요청서 19번 형식) ----
    log("\n" + "=" * 60)
    log("[수집 결과 요약]")
    log("=" * 60)
    for group, label in SOURCE_GROUP_LABELS.items():
        status = " (실패, 기존 데이터 유지)" if group in failed_groups else ""
        log(f"{label}: {counts[group]} collected{status}")

    log(f"\nDuplicates removed: {duplicates_removed_total}")
    log(f"Total saved: {len(merged_news)}")

    if warnings:
        log("\n[WARNING 목록]")
        for w in warnings:
            log(f"- {w}")

    log("\n완료: data/raw_news.json 저장됨")


if __name__ == "__main__":
    main()
