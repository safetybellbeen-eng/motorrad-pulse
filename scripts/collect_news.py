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

# ==========================================================
# 신뢰 국내 매체 화이트리스트 (Allowlist)
# ==========================================================
# 이전 버전은 "알려진 외국 도메인을 차단"하는 블랙리스트 방식이었는데,
# .com/.net 도메인은 무조건 통과시키는 구멍이 있어 실제로
# fortunebusinessinsights.com(미국 시장조사 업체), vietnam.vn 등이 계속 새어 들어왔다.
# 새로운 외국 사이트가 나올 때마다 목록을 추가해야 하는 블랙리스트는 구조적으로
# 뚫릴 수밖에 없으므로, 반대로 "이 목록에 있는 도메인만 허용한다"는
# 화이트리스트 방식으로 바꾼다. 목록에 없는 도메인은 국내든 해외든 기본적으로 차단된다.
TRUSTED_DOMAINS = {
    # 이번 STEP4-FREE에서 실제 사용 중인 소스
    "kmnews.net", "www.kmnews.net",
    "mbzine.com", "www.mbzine.com",
    "hankyung.com", "www.hankyung.com",
    # 국내 자동차/이륜차 전문지 (실제 검색으로 접근 가능 확인된 매체)
    "carguy.kr", "www.carguy.kr",
    "dailycar.co.kr", "www.dailycar.co.kr",
    # 국내 주요 경제/종합지 (Google 검색 결과에 자주 노출되는 매체)
    "ebn.co.kr", "www.ebn.co.kr",
    "edaily.co.kr", "www.edaily.co.kr",
    "news1.kr", "www.news1.kr",
    "yna.co.kr", "www.yna.co.kr",
    "newsis.com", "www.newsis.com",
    "mk.co.kr", "www.mk.co.kr",
    "asiae.co.kr", "www.asiae.co.kr",
    "fnnews.com", "www.fnnews.com",
    "etnews.com", "www.etnews.com",
    "sedaily.com", "www.sedaily.com",
    "heraldcorp.com", "www.heraldcorp.com",
    "khan.co.kr", "www.khan.co.kr",
    "hani.co.kr", "www.hani.co.kr",
    "donga.com", "www.donga.com",
    "chosun.com", "www.chosun.com",
    "joongang.co.kr", "www.joongang.co.kr",
    "seoul.co.kr", "www.seoul.co.kr",
    "kmib.co.kr", "www.kmib.co.kr",
}


def is_trusted_domain(url: str) -> bool:
    """최종 원문 URL의 도메인이 신뢰 화이트리스트에 있는지 확인한다.
    목록에 없으면 국내 사이트로 보이더라도 기본적으로 차단한다
    (요청서 원칙: 신뢰할 수 있는 뉴스가 부족하면 빈 자리를 허용한다,
    낮은 품질/미확인 Source로 억지로 채우지 않는다)."""
    try:
        netloc = urlsplit(url).netloc.lower()
    except Exception:
        return False
    return netloc in TRUSTED_DOMAINS


# ==========================================================
# Source Quality Policy — 신뢰도 낮은 출처 차단 (Allowlist/Blocklist)
# 한 곳에서 관리하여 향후 쉽게 추가/삭제할 수 있도록 한다.
# ==========================================================

# 개인 블로그/카페/커뮤니티/재게시 사이트 — Market Intelligence 자료로 부적합하여 전면 차단
BLOCKED_DOMAINS = [
    "blog.naver.com", "m.blog.naver.com", "post.naver.com", "m.post.naver.com",
    "cafe.naver.com", "m.cafe.naver.com",
    "tistory.com", "brunch.co.kr",
    "reddit.com", "dcinside.com",
    "instagram.com", "facebook.com",  # 게시물 링크가 RSS에 섞여 들어오는 경우 방지
]


def is_blocked_domain(url: str) -> bool:
    """최종 기사 URL의 도메인이 차단 목록에 있으면 True.
    Aggregator(Google 검색 등) 자체가 아니라 실제 기사가 걸린 최종 도메인 기준으로 판단한다."""
    url_lower = url.lower()
    return any(domain in url_lower for domain in BLOCKED_DOMAINS)


# kmnews(한국이륜차신문 등), naver, google 검색 결과에는 이륜차 전문지라도
# 자동차/전기차 기사가 섞여 나오는 경우가 있다 (예: 카가이는 자동차 종합 매체).
# 그래서 sourceGroup과 무관하게 모든 수집 기사에 대해 실제로 이륜차/오토바이
# 관련 기사인지 검증한다 (아래 collect_rss에서 has_motorcycle_context를 전수 적용).

MOTORCYCLE_CONTEXT_KEYWORDS = [
    "모터사이클", "오토바이", "이륜차", "motorcycle", "motorbike", "bike", "라이더", "라이딩",
    "cbr", "africa twin", "gold wing", "cb1000", "cb750", "cb400", "nc750", "rebel",
    "mt-", "tenere", "r1", "r7", "tracer", "야마하코리아", "혼다코리아",
    "두카티", "ducati", "트라이엄프", "triumph", "할리데이비슨", "harley",
    "bmw 모토라드", "모토라드", "motorrad", "카와사키", "kawasaki", "ninja",
    "스쿠터", "scooter", "헬멧", "바이커", "투어링", "어드벤처 바이크",
]

# 자동차/전기차 전용으로 명백히 판단되는 키워드 — 이 키워드가 있으면서
# 위의 이륜차 키워드가 함께 없으면 자동차 뉴스로 간주해 제외한다.
AUTOMOTIVE_ONLY_KEYWORDS = [
    "폴스타", "polestar", "테슬라", "tesla", "현대차", "기아", "제네시스",
    "sedan", "세단", "suv", "전기차 보조금", "자율주행", "자동차보험", "완성차",
]


def has_motorcycle_context(title: str, summary: str) -> bool:
    """이륜차 관련 키워드가 있으면 True.
    단, 자동차 전용 키워드가 있고 이륜차 키워드가 전혀 없으면 명확히 False로 판단한다
    (예: '폴스타, 보증 연장 프로그램 출시' 처럼 이륜차 매체 검색에 섞여 들어온
    순수 자동차 기사를 걸러내기 위함)."""
    text = f"{title} {summary}".lower()

    has_bike_keyword = any(kw.lower() in text for kw in MOTORCYCLE_CONTEXT_KEYWORDS)
    has_auto_only_keyword = any(kw.lower() in text for kw in AUTOMOTIVE_ONLY_KEYWORDS)

    if has_auto_only_keyword and not has_bike_keyword:
        return False

    return has_bike_keyword


def resolve_real_article_url(link: str, source_href: str | None) -> str:
    """Google News RSS의 <link>는 항상 news.google.com/rss/articles/... 형태의
    암호화된 리다이렉트 URL이다. 2024년 이전에는 이 URL을 base64로 디코딩하면
    바로 원문 주소가 나왔지만, Google이 인코딩 방식을 바꾸면서 그 방법은 더 이상
    통하지 않는다. 지금 확실하게 작동하는 방법은 다음과 같다:

    1) Google News 기사 페이지(news.google.com/rss/articles/{id})에 접속해서
       페이지 안에 있는 서명값(data-n-a-sg)과 타임스탬프(data-n-a-ts)를 읽는다.
    2) 이 두 값을 가지고 Google의 내부 API(batchexecute)에 정해진 형식으로
       재요청을 보내면, 응답 안에 실제 원문 URL이 들어있다.

    1순위: RSS의 <source url="..."> 속성이 실제 기사 경로를 담고 있으면 그것을 우선 사용
           (추가 요청 없이 빠르고 확실함).
    2순위: 위 방법이 안 통하면 batchexecute 디코딩을 시도한다.
    3순위: 그래도 실패하면 원래 링크를 그대로 반환한다
           (요청서 17번: 실패해도 전체 수집이 중단되지 않아야 한다).
    """
    if "news.google.com" not in link:
        return link

    if source_href and "news.google.com" not in source_href:
        if len(urlsplit(source_href).path.strip("/")) > 0:
            return source_href

    decoded = _decode_google_news_url(link)
    if decoded:
        return decoded

    return link


def _decode_google_news_url(link: str) -> str | None:
    """Google News 링크에서 실제 원문 URL을 추출한다 (batchexecute 방식).
    어느 단계든 실패하면 None을 반환해서 호출부가 안전하게 원본 링크로 폴백하게 한다."""
    try:
        article_id_match = re.search(r"/articles/([^?/]+)", link)
        if not article_id_match:
            return None
        article_id = article_id_match.group(1)

        page_resp = requests.get(
            f"https://news.google.com/rss/articles/{article_id}",
            headers={"User-Agent": USER_AGENT},
            timeout=8,
        )
        page_resp.raise_for_status()

        sig_match = re.search(r'data-n-a-sg="([^"]+)"', page_resp.text)
        ts_match = re.search(r'data-n-a-ts="([^"]+)"', page_resp.text)
        if not sig_match or not ts_match:
            return None
        signature = sig_match.group(1)
        timestamp = ts_match.group(1)

        payload = [
            "Fbv4je",
            (
                '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
                'null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
                f'"{article_id}",{timestamp},"{signature}"]'
            ),
        ]

        batch_resp = requests.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            },
            data={"f.req": json.dumps([[payload]])},
            timeout=8,
        )
        batch_resp.raise_for_status()

        # 응답은 앞에 안전을 위한 접두 문자열()]}')이 붙은 특수 형식이다.
        raw_text = batch_resp.text
        json_start = raw_text.find("[[")
        if json_start == -1:
            return None
        parsed = json.loads(raw_text[json_start:].splitlines()[0])
        # 중첩 구조 안에서 실제 URL 문자열을 찾는다 (Google 응답 구조가 자주 바뀔 수 있어 유연하게 탐색)
        inner = json.loads(parsed[0][2])
        real_url = inner[1]
        if isinstance(real_url, str) and real_url.startswith("http"):
            return real_url
        return None
    except Exception:
        return None


def shorten_display_url(url: str, max_length: int = 60) -> str:
    """팀 공유용 텍스트에서 너무 긴 링크가 그대로 노출되지 않도록 표시용으로만 축약한다.
    실제 하이퍼링크(href)는 원본 그대로 유지하고, 화면에 보이는 글자만 짧게 만든다."""
    if len(url) <= max_length:
        return url
    parts = urlsplit(url)
    short = f"{parts.scheme}://{parts.netloc}{parts.path}"
    if len(short) <= max_length:
        return short
    return short[:max_length - 1] + "…"


def extract_source_name_from_title(title: str, fallback: str) -> tuple[str, str]:
    """Google News RSS의 title은 보통 '기사 제목 - 언론사명' 형태다.
    실제 언론사명을 분리해서 화면에 정확히 표시하고, 제목에서는 제거한다.
    분리에 실패하면 원본 title과 fallback 소스명을 그대로 반환한다."""
    if " - " in title:
        possible_title, possible_source = title.rsplit(" - ", 1)
        # 언론사명은 보통 짧다 (30자 이내). 너무 길면 실제로는 제목의 일부일 수 있으므로 분리하지 않는다.
        if possible_title and 0 < len(possible_source) <= 30:
            return possible_title.strip(), possible_source.strip()
    return title, fallback


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

    # ---- 월간모터바이크(mbzine.com) — 실제 접근 가능한 국내 모터사이클 전문지, 검증 완료 ----
    {"sourceGroup": "kmnews", "source": "월간모터바이크", "sourceType": "media", "url": google_news_rss_kr("월간모터바이크"), "keyword_filter": None},
    {"sourceGroup": "kmnews", "source": "월간모터바이크", "sourceType": "media", "url": google_news_rss_kr("mbzine 모터사이클"), "keyword_filter": None},

    # ---- 브랜드별 국내 뉴스: 브랜드명 + 이벤트성 키워드(국내 출시/신차/코리아)로 검색어를 다양화 ----
    {"sourceGroup": "bmw", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("BMW 모토라드"), "keyword_filter": None},
    {"sourceGroup": "bmw", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("BMW 모토라드"), "keyword_filter": None},
    {"sourceGroup": "bmw", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("BMW 모토라드 국내 출시"), "keyword_filter": None},
    {"sourceGroup": "bmw", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("BMW GS 오토바이"), "keyword_filter": None},

    {"sourceGroup": "ducati", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("두카티 오토바이"), "keyword_filter": None},
    {"sourceGroup": "ducati", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("두카티 오토바이"), "keyword_filter": None},
    {"sourceGroup": "ducati", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("두카티 코리아 신차"), "keyword_filter": None},

    {"sourceGroup": "triumph", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("트라이엄프 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "triumph", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("트라이엄프 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "triumph", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("트라이엄프 코리아 신차"), "keyword_filter": None},

    {"sourceGroup": "harley", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("할리데이비슨 오토바이"), "keyword_filter": None},
    {"sourceGroup": "harley", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("할리데이비슨 오토바이"), "keyword_filter": None},
    {"sourceGroup": "harley", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("할리데이비슨 코리아 신차"), "keyword_filter": None},

    {"sourceGroup": "honda", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("혼다 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "honda", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("혼다 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "honda", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("혼다코리아 오토바이 출시"), "keyword_filter": None},

    {"sourceGroup": "yamaha", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("야마하 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "yamaha", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("야마하 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "yamaha", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("야마하코리아 오토바이 출시"), "keyword_filter": None},

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

    중요: default_group이 브랜드 전용 소스(bmw/ducati/triumph/harley/honda/yamaha)이면
    그대로 신뢰한다. 하지만 default_group이 매체 소스(kmnews/naver/google)이면,
    실제 기사 내용에 브랜드 키워드가 있는지 먼저 확인해서 그 브랜드로 재분류한다.
    (이전 버전은 default_group을 무조건 그대로 썼기 때문에, 한국이륜차신문을 거쳐
    들어온 BMW 기사가 전부 kmnews 그룹에만 쌓이고 BMW 필터에는 하나도 안 뜨는
    문제가 있었다.)"""
    text = f"{title} {summary}".lower()

    if default_group and default_group not in ("kmnews", "naver", "google"):
        return default_group

    for group, keywords in BRAND_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return group

    return default_group or "google"


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

            # Google News RSS의 "제목 - 언론사명" 형태에서 실제 언론사명을 분리해
            # source에 정확히 반영한다 (기존에는 무조건 검색 쿼리 함수를 만든 소스명("Google" 등)만 썼음)
            clean_title, resolved_source_name = extract_source_name_from_title(title, source_name)

            # keyword_filter가 있으면 제목/요약에 키워드가 포함된 것만 채택 (요청서 6, 8번)
            if keyword_filter:
                text = f"{clean_title} {summary}".lower()
                if not any(kw.lower() in text for kw in keyword_filter):
                    continue

            raw_date = getattr(entry, "published_parsed", None) or getattr(entry, "published", None)
            published_at = parse_date(raw_date)

            if not is_within_lookback(published_at):
                continue

            # ---- Google News 리다이렉트 링크를 실제 원문 기사 URL로 먼저 변환한다 ----
            # (팀 공유 시 news.google.com/rss/articles/... 같은 매우 긴 비원문 링크가 나가던 문제 해결)
            # 이 최종 URL을 확정한 다음에야 도메인 기준의 외국매체/차단 판단이 정확해진다.
            # 순서가 바뀌면(제목 텍스트만 보고 먼저 판단하면) 제목에 출처 표식이 없는 외국 기사가
            # 그대로 통과해버리는 문제가 있었다.
            source_href = None
            if hasattr(entry, "source") and isinstance(entry.source, dict):
                source_href = entry.source.get("href")
            real_link = resolve_real_article_url(link, source_href)
            clean_url = normalize_url(real_link)

            # 신뢰 화이트리스트에 있는 도메인만 채택한다 (요청서: 신뢰할 수 있는 국내 언론사만).
            # 목록에 없으면 국내처럼 보여도 확인이 안 된 것이므로 기본적으로 제외한다.
            if not is_trusted_domain(clean_url):
                continue

            # 저품질 도메인(블로그/카페 등) 최종(원문) URL 기준 차단 — 화이트리스트에 실수로
            # 등록될 가능성에 대비한 이중 안전장치
            if is_blocked_domain(clean_url):
                continue

            resolved_group = classify_source_group(clean_title, summary, source_group)

            # 모든 기사에 대해 실제 이륜차/오토바이 관련 기사인지 검증한다.
            # (특정 그룹에만 적용하면, 분류 로직이 실수로 다른 그룹에 넣었을 때
            # 이 검증을 피해갈 수 있어 전체 기사에 항상 적용하는 것이 더 안전하다)
            if not has_motorcycle_context(clean_title, summary):
                continue

            articles.append({
                "title": clean_title,
                "url": clean_url,
                "source": resolved_source_name,
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
    실패한 그룹은 기존 데이터를 그대로 보존한다 (요청서 18번 핵심 안전장치).

    단, 기존 데이터도 현재 필터 정책(외국 매체 차단, 저품질 도메인 차단)으로 다시 검증한다.
    필터 정책이 나중에 추가/강화된 경우, 예전에 필터 적용 전에 저장된 위반 데이터가
    "기존 데이터 보존" 로직 때문에 계속 화면에 남아있는 문제가 있었기 때문이다."""

    def passes_current_policy(item: dict) -> bool:
        url = item.get("url", "")
        title = item.get("title", "")
        if not is_trusted_domain(url):
            return False
        if is_blocked_domain(url):
            return False
        if not has_motorcycle_context(title, ""):
            return False
        return True

    existing_by_group: dict[str, list[dict]] = {}
    for item in existing_news:
        if not passes_current_policy(item):
            continue
        g = item.get("sourceGroup", "unknown")
        existing_by_group.setdefault(g, []).append(item)

    merged: list[dict] = []

    for group in SOURCE_GROUP_LABELS.keys():
        if group in failed_groups:
            # 수집 실패 -> 기존 데이터 유지 (단, 위에서 이미 현재 정책으로 걸러진 상태)
            merged.extend(existing_by_group.get(group, []))
            continue

        new_items = newly_collected.get(group, [])
        old_items = existing_by_group.get(group, [])

        # 신규 수집 URL 및 정규화된 제목 집합.
        # URL만 비교하면 안 되는 이유: Google 리다이렉트 해제 성공 여부에 따라
        # 같은 기사가 오늘은 실제 원문 URL로, 어제는 news.google.com 리다이렉트
        # URL로 저장됐을 수 있다. 이 경우 URL은 다르지만 제목은 동일하므로,
        # 제목 기준으로도 겹치는지 확인해야 진짜 중복을 걸러낼 수 있다.
        new_urls = {item["url"] for item in new_items}
        new_titles = {normalize_title(item["title"]) for item in new_items}

        # 기존 항목 중 신규로 대체되지 않은 것만 남기고, 신규 항목을 앞에 배치
        remaining_old = [
            item for item in old_items
            if item["url"] not in new_urls and normalize_title(item["title"]) not in new_titles
        ]

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
            # 알 수 없는 그룹으로 분류된 경우 google로 폴백 (현재 그룹 체계: bmw/ducati/triumph/
            # harley/honda/yamaha/naver/google/kmnews — 예전 체계인 motorcycle_media를 쓰면
            # 어느 필터에도 걸리지 않는 유령 데이터가 되므로 반드시 현재 체계의 값이어야 한다)
            a["sourceGroup"] = "google"
            collected_by_group["google"].append(a)

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
