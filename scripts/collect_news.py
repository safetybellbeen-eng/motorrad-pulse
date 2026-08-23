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

# STEP 9.3: Trusted Domain/Source Tier, Motorcycle Context, Brand Attribution 정책은
# analyze_news_free.py와 공통으로 쓰는 단일 모듈(news_policy.py)에서 가져온다.
# news_policy.py는 collect_news.py/analyze_news_free.py 어느 쪽도 import하지 않는
# 단방향 구조라 순환 import가 발생하지 않는다.
from news_policy import (
    TRUSTED_DOMAINS,
    get_source_tier,
    get_source_quality_score,
    is_trusted_domain,
    has_motorcycle_context,
    SOURCE_GROUP_LABELS,
    detect_brand_groups,
)

# ==========================================================
# 설정
# ==========================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
RAW_NEWS_PATH = os.path.join(DATA_DIR, "raw_news.json")

MAX_PER_GROUP = 5
LOOKBACK_HOURS = 48

# STEP 9.1: 수집 실패 그룹(해당 소스에 일시적으로 접근하지 못한 경우)에 대한 안전장치.
# 정상 수집이 성공한 그룹은 신규/기존 관계없이 항상 LOOKBACK_HOURS(48h) 기준을 그대로 적용하고,
# "수집 자체가 실패한" 그룹에 한해서만 최대 RETENTION_HOURS(72h)까지 기존 데이터를 살려둔다
# (그사이 다음 정상 수집이 성공하면 다시 48h 기준으로 걸러진다). 72h를 넘긴 기사는 실패 그룹이라도
# "현재 뉴스"로 유지하지 않는다 — 즉 정상 상태에서 48~72시간짜리 기사가 화면에 남는 일은 없다.
RETENTION_HOURS = 72
REQUEST_TIMEOUT = 15
USER_AGENT = "MotorradPulseNewsCollector/1.0 (+https://github.com/)"

# 추적 파라미터 (요청서 14번)
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "oc"}

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


# STEP 10-KR: 해외 Direct Source(BMW Global/Yamaha Global/Visordown/ADV Pulse)는
# enabled=False로 껐지만, 국내 검색 쿼리(kmnews/naver/google)가 "우연히" 이 해외
# 공식/전문매체 도메인의 기사를 링크로 물어올 이론적 가능성이 남아있다(요청 사항 5번).
# 자동 DROP은 하지 않고, 저장 직전 로그로만 관찰한다.
#
# www.press.bmwgroup.com은 주의가 필요하다 — BMW Korea(/korea/...)도 같은 도메인을
# 쓰기 때문에, 도메인만으로 판단하면 정상적으로 활성화된 BMW Korea 기사까지 "해외"로
# 잘못 표시된다. 그래서 이 도메인만 경로(path)까지 함께 확인해서 "/korea/"가 아닌
# 경우에만(Global 등 다른 국가 섹션) 해외로 판정한다.
_FOREIGN_ONLY_DOMAINS = {
    "global.yamaha-motor.com",
    "visordown.com", "www.visordown.com",
    "advpulse.com", "www.advpulse.com",
}
_BMW_PRESSCLUB_DOMAINS = {"www.press.bmwgroup.com", "press.bmwgroup.com"}


def is_foreign_direct_domain(url: str) -> bool:
    """이 기사가 (국내 정책상 비활성화된) 해외 Direct Source와 같은 도메인에서 왔는지 판정.
    KR-Only Monitor 로그 전용 — 수집 게이트에는 관여하지 않는다(요청 사항: 자동 DROP 금지)."""
    try:
        parts = urlsplit(url)
        netloc = parts.netloc.lower()
    except Exception:
        return False

    if netloc in _FOREIGN_ONLY_DOMAINS:
        return True

    if netloc in _BMW_PRESSCLUB_DOMAINS:
        # BMW Korea Press(/korea/...)는 활성화된 정상 국내 소스이므로 제외하고,
        # 그 외 경로(/global/... 등 다른 국가 섹션)만 해외로 판정한다.
        return "/korea/" not in parts.path.lower()

    return False


# ==========================================================
# Business Relevance Score — STEP 9 핵심: 이륜차 관련 기사라도
# BMW Motorrad 마케팅/사업 관점에서 가치가 낮으면 우선순위를 낮추거나 제외한다.
# 요청서 18번 예시 점수를 채택하되, 한국어 키워드를 추가했다.
# ==========================================================

BUSINESS_RELEVANCE_KEYWORDS_POSITIVE = {
    # 신제품/출시
    "신제품": 15, "신모델": 15, "new model": 15, "출시": 12, "launch": 12, "공개": 10,
    "국내 출시": 15,
    # 가격/판매/시장
    "가격": 12, "price": 12, "판매": 12, "sales": 12, "판매량": 12, "등록대수": 12,
    "시장점유율": 15, "market share": 15,
    # 유통/전략
    "딜러": 10, "dealer": 10, "유통망": 10, "프로모션": 10, "campaign": 10,
    "캠페인": 10, "브랜드 전략": 10, "partnership": 10, "파트너십": 10, "제휴": 10,
    # 고객/커뮤니티
    "고객 이벤트": 8, "customer event": 8, "이벤트": 8, "커뮤니티": 6, "community": 6,
    "투어링": 8, "touring": 8, "어드벤처": 6,
    # STEP 10-KR.2 요청 4번: AUDIT에서 실제로 확인된 False Negative(할리데이비슨
    # 코리아의 랠리/투어/오픈하우스 등 실제 브랜드 행사 기사가 DROP되는 문제)를
    # 최소 보강한다. 기존 "이벤트"/"투어링"과 동일한 8점 구간으로 맞췄다
    # (과도한 점수 부여 금지 요청 반영). "투어"는 일반 문맥에서도 쓰일 수 있는
    # 단어이지만, 사건사고 표현(사고/충돌 등)이 있으면 has_motorcycle_context
    # 단계(Incident Gate)에서 이미 Hard Exclude되므로 이 게이트까지 도달하는
    # "투어" 포함 기사는 사건사고가 아닌 경우로 한정된다(요청 5번, fixture로 검증).
    "랠리": 8, "rally": 8, "페스티벌": 8, "festival": 8, "축제": 8,
    "오픈하우스": 8, "open house": 8, "투어": 8,
    "시승": 8, "시승회": 8, "test ride": 8, "라이딩 이벤트": 8, "riding event": 8,
    # 기술/제품 트렌드
    "전동화": 10, "electric": 10, "배터리": 8, "battery": 8, "adas": 10,
    "커넥티비티": 8, "connectivity": 8, "안전기술": 8,
    # 정책/공급망
    "규제": 12, "regulation": 12, "정책": 8, "policy": 8, "공장": 8, "factory": 8,
    "생산": 8, "production": 8, "투자": 10, "investment": 10, "리콜": 12, "recall": 12,
}

BUSINESS_RELEVANCE_KEYWORDS_NEGATIVE = {
    # 요청서 17, 19번: Soft Relevance — 무조건 제거하지 않는다(Hard Exclude와 구분).
    # 특히 Motorsport는 "무조건 제거하지 말고 데이터 분포를 Audit한 후 판단"하라는
    # 요청서 17번 단서에 따라, 이번 STEP에서는 점수만 낮추고 수집 게이트에서는
    # 하드 컷하지 않는다(아래 passes_business_relevance_gate 참고).
    "레이스": 6, "레이스 결과": 6, "경주 결과": 6, "race result": 6,
    "연예인": 8, "celebrity": 8,
    "개인 후기": 6, "커뮤니티 잡담": 6,
}

BUSINESS_RELEVANCE_THRESHOLD = 3


def compute_business_relevance_score(title: str, summary: str) -> int:
    """제목/요약에서 업무 가치 키워드를 찾아 순 점수(긍정 - 부정)를 계산한다(요청서 18번).
    사건사고 등은 has_motorcycle_context 단계에서 이미 Hard Exclude되므로
    이 함수는 그 뒤에 남은, "이륜차 기사이지만 마케팅 업무 가치가 있는지"만 판단한다.
    이 점수 자체는 저장되어 향후 정렬/우선순위에 쓰이고, 수집 게이트 통과 여부는
    아래 passes_business_relevance_gate가 별도로 판단한다(Hard Filter와 Soft Score 구분,
    요청서 19번)."""
    text = f"{title} {summary}".lower()
    score = 0
    for kw, points in BUSINESS_RELEVANCE_KEYWORDS_POSITIVE.items():
        if kw.lower() in text:
            score += points
    for kw, points in BUSINESS_RELEVANCE_KEYWORDS_NEGATIVE.items():
        if kw.lower() in text:
            score -= points
    return score


def passes_business_relevance_gate(title: str, summary: str) -> bool:
    """수집 여부를 결정하는 게이트(Hard Filter 성격)와, 점수 자체(Soft Score,
    compute_business_relevance_score)를 분리한다(요청서 19번).

    - Soft Relevance 키워드(레이스/연예인/개인 후기 등)가 있으면, 점수가 낮더라도
      게이트는 통과시킨다 — 요청서 17번이 "Motorsport를 무조건 제거하지 말라"고
      명시했기 때문이다. 대신 businessRelevanceScore가 낮게 저장되어 향후
      TOP NEWS 등 우선순위 계산에서 자연스럽게 밀리게 된다.
    - Soft Relevance 키워드가 전혀 없는데 순 점수가 Threshold 미만이면
      (=업무 가치 신호가 아예 없는 기사) 게이트에서 제외한다."""
    text = f"{title} {summary}".lower()
    has_soft_relevance_signal = any(kw.lower() in text for kw in BUSINESS_RELEVANCE_KEYWORDS_NEGATIVE)
    if has_soft_relevance_signal:
        return True
    return compute_business_relevance_score(title, summary) >= BUSINESS_RELEVANCE_THRESHOLD


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
    {"sourceGroup": "kmnews", "source": "한국이륜차신문", "sourceType": "media", "url": google_news_rss_kr("이륜차신문 신차"), "keyword_filter": None},
    {"sourceGroup": "kmnews", "source": "한국이륜차신문", "sourceType": "media", "url": google_news_rss_kr("이륜차신문 프로모션"), "keyword_filter": None},

    # ---- 월간모터바이크(mbzine.com) — 실제 접근 가능한 국내 모터사이클 전문지, 검증 완료 ----
    {"sourceGroup": "kmnews", "source": "월간모터바이크", "sourceType": "media", "url": google_news_rss_kr("월간모터바이크"), "keyword_filter": None},
    {"sourceGroup": "kmnews", "source": "월간모터바이크", "sourceType": "media", "url": google_news_rss_kr("mbzine 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "kmnews", "source": "월간모터바이크", "sourceType": "media", "url": google_news_rss_kr("월간모터바이크 시승기"), "keyword_filter": None},

    # ---- 카가이(carguy.kr) — 이번 STEP에서 확인된 화이트리스트 매체를 직접 겨냥 ----
    # STEP 9 AUDIT: "카가이 모터사이클"과 "카가이 오토바이"는 서로 겹치는 결과를
    # 반환할 가능성이 높은 중복 쿼리로 확인되어 하나로 통합한다(요청서 9번).
    {"sourceGroup": "kmnews", "source": "카가이", "sourceType": "media", "url": google_news_rss_kr("카가이 모터사이클 오토바이"), "keyword_filter": None},

    # ---- 브랜드별 국내 뉴스: 브랜드명 + 이벤트성 키워드(국내 출시/신차/코리아/프로모션)로 검색어를 다양화 ----
    {"sourceGroup": "bmw", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("BMW 모토라드"), "keyword_filter": None},
    {"sourceGroup": "bmw", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("BMW 모토라드"), "keyword_filter": None},
    {"sourceGroup": "bmw", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("BMW 모토라드 국내 출시"), "keyword_filter": None},
    {"sourceGroup": "bmw", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("BMW GS 오토바이"), "keyword_filter": None},
    {"sourceGroup": "bmw", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("BMW 모토라드 코리아 이벤트"), "keyword_filter": None},
    {"sourceGroup": "bmw", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("BMW 모토라드 프로모션"), "keyword_filter": None},

    {"sourceGroup": "ducati", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("두카티 오토바이"), "keyword_filter": None},
    {"sourceGroup": "ducati", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("두카티 오토바이"), "keyword_filter": None},
    {"sourceGroup": "ducati", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("두카티 코리아 신차"), "keyword_filter": None},
    {"sourceGroup": "ducati", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("두카티 코리아 이벤트"), "keyword_filter": None},

    {"sourceGroup": "triumph", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("트라이엄프 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "triumph", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("트라이엄프 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "triumph", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("트라이엄프 코리아 신차"), "keyword_filter": None},
    {"sourceGroup": "triumph", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("트라이엄프 코리아 이벤트"), "keyword_filter": None},

    {"sourceGroup": "harley", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("할리데이비슨 오토바이"), "keyword_filter": None},
    {"sourceGroup": "harley", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("할리데이비슨 오토바이"), "keyword_filter": None},
    {"sourceGroup": "harley", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("할리데이비슨 코리아 신차"), "keyword_filter": None},
    {"sourceGroup": "harley", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("할리데이비슨 코리아 이벤트"), "keyword_filter": None},

    {"sourceGroup": "honda", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("혼다 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "honda", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("혼다 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "honda", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("혼다코리아 오토바이 출시"), "keyword_filter": None},
    {"sourceGroup": "honda", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("혼다코리아 모터사이클 이벤트"), "keyword_filter": None},

    {"sourceGroup": "yamaha", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("야마하 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "yamaha", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("야마하 모터사이클"), "keyword_filter": None},
    {"sourceGroup": "yamaha", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("야마하코리아 오토바이 출시"), "keyword_filter": None},
    {"sourceGroup": "yamaha", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("야마하코리아 모터사이클 이벤트"), "keyword_filter": None},

    # ---- NAVER 그룹: 이륜차 업계 일반 뉴스 중 네이버 노출 우선 ----
    {"sourceGroup": "naver", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("이륜차 신제품"), "keyword_filter": None},
    {"sourceGroup": "naver", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("오토바이 신모델"), "keyword_filter": None},
    {"sourceGroup": "naver", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("모터사이클 행사"), "keyword_filter": None},
    {"sourceGroup": "naver", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("이륜차 시장 판매"), "keyword_filter": None},
    {"sourceGroup": "naver", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("모터사이클 프로모션"), "keyword_filter": None},
    {"sourceGroup": "naver", "source": "Naver", "sourceType": "media", "url": naver_news_search_url("이륜차 딜러십"), "keyword_filter": None},

    # ---- GOOGLE 그룹: 이륜차 업계 일반 뉴스 (구글 전체 검색) ----
    {"sourceGroup": "google", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("이륜차 업계"), "keyword_filter": None},
    {"sourceGroup": "google", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("오토바이 신제품"), "keyword_filter": None},
    {"sourceGroup": "google", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("모터사이클 마케팅 프로모션"), "keyword_filter": None},
    {"sourceGroup": "google", "source": "Google", "sourceType": "market_report", "url": google_news_rss_kr("이륜차 시장 전망"), "keyword_filter": None},
    {"sourceGroup": "google", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("모터사이클 신모델 출시"), "keyword_filter": None},
    {"sourceGroup": "google", "source": "Google", "sourceType": "media", "url": google_news_rss_kr("이륜차 브랜드 캠페인"), "keyword_filter": None},

    # ---- 한국경제 전체뉴스 RSS — 이륜차 키워드로 필터링해서 보조 소스로 활용, GOOGLE 그룹에 포함 ----
    # (진짜 direct RSS이므로 method를 명시적으로 "rss"로 표기한다 — 나머지 46개 항목은
    # 전부 google_news_rss_kr()/naver_news_search_url() 경유이므로 아래에서 일괄 "google_news"로 채운다.)
    {
        "sourceGroup": "google",
        "source": "한국경제",
        "sourceType": "media",
        "url": "https://www.hankyung.com/feed/all-news",
        "keyword_filter": ["이륜차", "오토바이", "모터사이클", "bmw 모토라드", "할리데이비슨", "두카티", "야마하", "혼다 모터사이클"],
        "method": "rss",
    },

    # ==========================================================
    # STEP 10.1 — Direct Source Pipeline (Official + Global Professional Media)
    # 요청서 원칙: RSS가 HTML보다 구조적으로 안정적이므로, 이번 STEP은 실제 접근/파싱을
    # WebFetch로 재검증한 4개의 안정적 RSS만 활성화한다. Ducati/Triumph/Honda(HTML 수집)와
    # Harley-Davidson(자동수집 이용약관 미확인)은 "발견됨"이지만 이번 STEP에서 자동수집을
    # 켜지 않는다(STEP 10.2 후보). SOURCES에 없다는 사실 자체가 "미활성화"의 증거다.
    # ==========================================================

    # ---- BMW Motorrad Official Press RSS — Tier 1 Official, sourceGroup은 기존 "bmw" 유지 ----
    # (news_policy.SOURCE_TIERS["official"]에 www.press.bmwgroup.com 등록됨 -> sourceQualityScore=100)
    {
        "sourceGroup": "bmw",
        "source": "BMW Motorrad Press",
        "sourceType": "media",
        "url": "https://www.press.bmwgroup.com/global/rss/topic/6629",
        "keyword_filter": None,
        "method": "rss",
        # STEP 10-KR: MOTORRAD PULSE는 "국내 시장 뉴스 대시보드"가 메인 목적이므로
        # 메인 자동수집에서 비활성화한다. 코드는 삭제하지 않는다 — 향후 GLOBAL WATCH
        # 기능에서 재사용할 수 있도록 SOURCES에 그대로 남겨두고 enabled만 끈다.
        "enabled": False,
    },

    # ---- BMW Motorrad Korea Official Press RSS — STEP 10.2 ----
    # BMW Group PressClub은 국가별 섹션(/global/, /korea/ 등)이 있고, 기존에 확보한 Global
    # Motorrad Topic ID(6629)가 Korea 섹션에도 동일하게 존재함을 실제 WebFetch로 검증했다
    # (STEP 10.2-A AUDIT). 도메인이 www.press.bmwgroup.com으로 위 Global 항목과 완전히
    # 동일하므로 news_policy.py의 SOURCE_TIERS["official"]에 이미 등록되어 있고, 별도
    # 도메인 추가가 필요 없다(요청 사항: 불필요한 중복 도메인 추가 금지).
    # 한국 시장 전용 콘텐츠(국내 한정판 수량, 딜러 행사 등)를 담고 있어 업무 관련성이 높다.
    {
        "sourceGroup": "bmw",
        "source": "BMW Motorrad Korea Press",
        "sourceType": "media",
        "url": "https://www.press.bmwgroup.com/korea/rss/topic/6629",
        "keyword_filter": None,
        "method": "rss",
    },

    # ---- Yamaha Motor Global News RSS — Tier 1 Official이지만 전사(선박/로봇/재무 등) 피드다.
    # sourceGroup="yamaha"로 들어오면 has_motorcycle_context()가 BRAND_SPECIFIC_CONTEXT_KEYWORDS
    # ["yamaha"] 서브키워드까지 요구하는 기존 Hard Gate를 그대로 통과해야 한다 — Official이라고
    # 예외를 두지 않는다(요청서 5번). 코드 변경 없이 sourceGroup만으로 기존 게이트가 자동 적용된다.
    {
        "sourceGroup": "yamaha",
        "source": "Yamaha Motor Global News",
        "sourceType": "media",
        "url": "https://global.yamaha-motor.com/rss/update.xml",
        "keyword_filter": None,
        "method": "rss",
        # STEP 10-KR: 국내 전용 정책으로 메인 자동수집에서 비활성화 (GLOBAL WATCH용으로 코드 보존)
        "enabled": False,
    },

    # ---- Visordown (영국 이륜차 전문매체) — Tier 2, sourceGroup은 신규 "global_media" ----
    # "google" sourceGroup에 넣지 않는다 — Google 검색 폴백과 Direct RSS를 통계적으로
    # 분리해서 집계하기 위함(요청서 2번, 데이터 의미 보존이 UI 편의보다 우선).
    {
        "sourceGroup": "global_media",
        "source": "Visordown",
        "sourceType": "media",
        "url": "https://www.visordown.com/rss",
        "keyword_filter": None,
        "method": "rss",
        # STEP 10-KR: 국내 전용 정책으로 메인 자동수집에서 비활성화 (GLOBAL WATCH용으로 코드 보존).
        # global_media sourceGroup 정의 자체(news_policy.py)는 그대로 유지한다.
        "enabled": False,
    },

    # ---- ADV Pulse (북미 어드벤처 이륜차 전문매체) — Tier 2, sourceGroup "global_media" ----
    {
        "sourceGroup": "global_media",
        "source": "ADV Pulse",
        "sourceType": "media",
        "url": "https://www.advpulse.com/feed/",
        "keyword_filter": None,
        "method": "rss",
        # STEP 10-KR: 국내 전용 정책으로 메인 자동수집에서 비활성화 (GLOBAL WATCH용으로 코드 보존)
        "enabled": False,
    },
]

# STEP 10.1: 나머지 항목(46개, 전부 Google News RSS 검색 경유)은 "method"가 없으므로
# 일괄 "google_news"로 채운다. 위에서 명시적으로 "rss"를 지정한 4개 신규 항목과
# 한국경제 항목만 "rss"로 남고 나머지는 전부 "google_news"가 된다.
for _src in SOURCES:
    _src.setdefault("method", "google_news")


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

def new_stage_counters() -> dict[str, int]:
    """STEP 9 수집 품질 로그용 단계별 카운터 (요청서 34번 형식).
    STEP 10.2: 기존 7개 키의 의미/계산법은 전혀 바꾸지 않는다(요청 사항 7번). "raw_entries"
    1개만 순수 추가한다 — Direct Source Health 판정(NO_ENTRIES 구분)에만 쓰이고,
    기존 [STEP 9 품질 필터 로그] 블록에는 노출하지 않는다."""
    return {
        "fetched": 0,
        "trusted_domain_pass": 0,
        "blocked_by_domain": 0,
        "motorcycle_context_pass": 0,
        "blocked_as_context": 0,
        "business_relevance_pass": 0,
        "blocked_as_low_relevance": 0,
        "raw_entries": 0,  # STEP 10.2 신규: feed 자체의 원본 entry 개수 (Source Health용)
    }


def determine_source_health_status(error: str | None, raw_entries: int, fresh: int, final_count: int) -> str:
    """STEP 10.2: Direct Source(method="rss") 1건의 상태를 5단계로 판정한다.
    "articles=0"이라는 사실 하나만으로 무조건 장애로 보지 않는다 — 어느 단계에서
    0이 됐는지에 따라 원인을 구분한다(final article count와 독립적으로 판정, 요청 사항 4번).

    - FAILED            : HTTP/feed parse 등 Source 자체 접근/파싱 실패
    - NO_ENTRIES        : Source 접근/파싱은 정상이나 feed entry 자체가 0
    - NO_FRESH_ARTICLES : entry는 존재하지만 LOOKBACK_HOURS(48h) 이내 기사가 0
    - FILTERED_OUT      : Fresh article은 있었지만 Motorcycle Context/Business Relevance 등
                          Quality Gate에서 전부 제외됨
    - OK                : 최종 Quality Gate를 통과한 article이 1건 이상 존재

    순수 함수로 분리해서(요청 사항과 별개로) 네트워크 없이 단위 테스트가 가능하게 한다."""
    if error:
        return "FAILED"
    if raw_entries == 0:
        return "NO_ENTRIES"
    if fresh == 0:
        return "NO_FRESH_ARTICLES"
    if final_count == 0:
        return "FILTERED_OUT"
    return "OK"


def collect_rss(source_config: dict) -> tuple[list[dict], str | None, dict[str, int]]:
    """단일 RSS 소스에서 기사를 수집한다.
    반환: (수집된 기사 리스트, 오류 메시지 또는 None, 단계별 카운터)
    이 함수 내부에서 예외가 발생해도 상위로 전파하지 않고 오류 메시지로 반환한다 (요청서 17번)."""

    url = source_config["url"]
    source_group = source_config["sourceGroup"]
    source_name = source_config["source"]
    source_type = source_config["sourceType"]
    keyword_filter = source_config.get("keyword_filter")
    # STEP 10.1: 이 소스가 실제로 수집을 시도한 방법(rss=direct RSS, google_news=Google
    # News RSS 검색 경유). 기존 프론트엔드는 이 필드를 읽지 않으므로 하위호환에 영향 없다.
    acquisition_method = source_config.get("method", "google_news")

    stats = new_stage_counters()

    try:
        headers = {"User-Agent": USER_AGENT}
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)

        # STEP 10.2: Source Health의 NO_ENTRIES 판정에 쓰기 위해 feed 자체의 원본 entry
        # 개수를 기록한다(키워드 필터/신선도 필터 적용 전 숫자). bozo로 조기 반환되는 경우는
        # 어차피 error가 채워져 FAILED로 판정되므로 이 값은 그 경우 참고용일 뿐이다.
        stats["raw_entries"] = len(feed.entries)

        if feed.bozo and not feed.entries:
            return [], f"피드 파싱 실패 (bozo): {feed.bozo_exception}", stats

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

            stats["fetched"] += 1

            # ---- Google News 리다이렉트 링크를 실제 원문 기사 URL로 먼저 변환한다 ----
            # (팀 공유 시 news.google.com/rss/articles/... 같은 매우 긴 비원문 링크가 나가던 문제 해결)
            # 이 최종 URL을 확정한 다음에야 도메인 기준의 외국매체/차단 판단이 정확해진다.
            # 순서가 바뀌면(제목 텍스트만 보고 먼저 판단하면) 제목에 출처 표식이 없는 외국 기사가
            # 그대로 통과해버리는 문제가 있었다.
            # (STEP 9 확인: resolve 실패 시 반환되는 news.google.com 리다이렉트 링크는
            #  TRUSTED_DOMAINS에 없으므로 아래 is_trusted_domain에서 반드시 차단된다 —
            #  과거 AUDIT에서 발견된 "미해제 링크의 화이트리스트 우회" 재발을 막는 지점.)
            source_href = None
            if hasattr(entry, "source") and isinstance(entry.source, dict):
                source_href = entry.source.get("href")
            real_link = resolve_real_article_url(link, source_href)
            clean_url = normalize_url(real_link)

            # 신뢰 화이트리스트에 있는 도메인만 채택한다 (요청서: 신뢰할 수 있는 국내 언론사만).
            # 목록에 없으면 국내처럼 보여도 확인이 안 된 것이므로 기본적으로 제외한다.
            if not is_trusted_domain(clean_url):
                stats["blocked_by_domain"] += 1
                continue

            # 저품질 도메인(블로그/카페 등) 최종(원문) URL 기준 차단 — 화이트리스트에 실수로
            # 등록될 가능성에 대비한 이중 안전장치
            if is_blocked_domain(clean_url):
                stats["blocked_by_domain"] += 1
                continue

            stats["trusted_domain_pass"] += 1

            resolved_group = classify_source_group(clean_title, summary, source_group)

            # 모든 기사에 대해 실제 이륜차/오토바이 관련 기사인지 검증한다.
            # (특정 그룹에만 적용하면, 분류 로직이 실수로 다른 그룹에 넣었을 때
            # 이 검증을 피해갈 수 있어 전체 기사에 항상 적용하는 것이 더 안전하다)
            # STEP 9: Honda/Yamaha는 브랜드 전용 서브키워드까지 함께 검증한다.
            if not has_motorcycle_context(clean_title, summary, resolved_group):
                stats["blocked_as_context"] += 1
                continue

            stats["motorcycle_context_pass"] += 1

            # STEP 9: 업무 가치 필터 — 이륜차 기사여도 마케팅/사업 관점 가치가
            # 전혀 없으면 제외한다(요청서 15~19번). Soft Relevance(레이스 등)는
            # 점수만 낮게 기록되고 게이트에서는 통과한다.
            relevance_score = compute_business_relevance_score(clean_title, summary)
            if not passes_business_relevance_gate(clean_title, summary):
                stats["blocked_as_low_relevance"] += 1
                continue

            stats["business_relevance_pass"] += 1

            articles.append({
                "title": clean_title,
                "url": clean_url,
                "source": resolved_source_name,
                "sourceType": source_type,
                "sourceGroup": resolved_group,
                "publishedAt": published_at,
                "sourceTier": get_source_tier(clean_url),
                "sourceQualityScore": get_source_quality_score(clean_url),
                "businessRelevanceScore": relevance_score,
                "brandGroups": detect_brand_groups(clean_title, summary),
                "acquisitionMethod": acquisition_method,
                "summary_raw": summary[:300],  # 중복판단 참고용, 최종 저장 시 제거
            })

        return articles, None, stats

    except requests.exceptions.RequestException as e:
        return [], f"네트워크 오류: {e}", stats
    except Exception as e:
        return [], f"알 수 없는 오류: {e}", stats


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


# ==========================================================
# Duplicate Cluster — STEP 9 설계안 20~23번
# URL/제목 정확일치 dedupe(remove_duplicates)로 못 잡는, 서로 다른 매체가
# 같은 이슈를 다르게 쓴 기사들을 묶어 대표기사 1건 + relatedCoverageCount로 정리한다.
# ==========================================================

# 제목 유사도 비교 시 무시할 범용 단어(요청서 15번: 그렇지 않으면 무관한 기사가 잘못 묶임).
# 브랜드명 자체도 포함한다 — 클러스터링은 이미 같은 sourceGroup(같은 브랜드) 안에서만
# 비교하므로, 브랜드명 토큰이 남아 있으면 "같은 브랜드"라는 사실만으로 서로 다른
# 이슈(예: 신모델 출시 기사 vs 코리아 이벤트 기사)가 잘못 묶이는 문제가 실제로
# 재현되어(예: "BMW 모토라드, 신모델 국내 출시" vs "BMW 모토라드, 코리아 이벤트 성료")
# 브랜드명도 불용어에 포함시켰다.
TITLE_DEDUPE_STOPWORDS = {
    "공개", "출시", "신제품", "신모델", "모델", "오토바이", "이륜차", "모터사이클",
    "코리아", "국내", "한국", "new", "model", "launch", "launches", "unveil",
    "unveils", "announce", "announces", "출시한다", "공개한다",
    # 브랜드명 (같은 sourceGroup 내 비교이므로 중복 신호라 제거)
    "bmw", "모토라드", "motorrad", "두카티", "ducati", "트라이엄프", "triumph",
    "할리데이비슨", "harley", "harleydavidson", "혼다", "honda", "혼다코리아",
    "야마하", "yamaha", "야마하코리아", "카와사키", "kawasaki",
}


def _dedupe_tokens(title: str) -> set[str]:
    """중복 클러스터링용 제목 토큰화. 정규화 + 불용어 제거."""
    normalized = normalize_title(title)
    return {t for t in normalized.split() if t and t not in TITLE_DEDUPE_STOPWORDS and len(t) >= 2}


def _title_similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    """자카드 유사도 기반. 둘 중 하나라도 비교 가능한 토큰이 2개 미만이면
    신뢰할 수 없는 비교이므로 0으로 처리해 잘못 묶이는 것을 방지한다.

    한국어 기사 제목은 매체마다 "두카티 코리아"/"두카티코리아"처럼 띄어쓰기가
    달라 단순 공백 분리 토큰이 정확히 일치하지 않는 경우가 실제로 확인됐다.
    완전 일치뿐 아니라 한쪽 토큰이 다른 쪽 토큰에 포함되는 경우(부분 일치)도
    같은 단어로 취급해서 이런 표기 차이를 흡수한다."""
    if len(tokens_a) < 2 or len(tokens_b) < 2:
        return 0.0
    matched_a: set[str] = set()
    matched_b: set[str] = set()
    for ta in tokens_a:
        for tb in tokens_b:
            if ta == tb or ta in tb or tb in ta:
                matched_a.add(ta)
                matched_b.add(tb)
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return len(matched_a | matched_b) / len(union)


def _hours_between(iso_a: str | None, iso_b: str | None) -> float:
    """두 ISO 8601 시각 사이 시간(시간 단위, 절대값). 파싱 실패 시 무한대(비교 불가로 취급)."""
    if not iso_a or not iso_b:
        return float("inf")
    try:
        dt_a = datetime.fromisoformat(iso_a)
        dt_b = datetime.fromisoformat(iso_b)
        return abs((dt_a - dt_b).total_seconds()) / 3600
    except Exception:
        return float("inf")


def find_duplicate_clusters(
    articles: list[dict],
    similarity_threshold: float = 0.5,
    time_window_hours: float = 24,
) -> list[dict]:
    """같은 브랜드(sourceGroup) + 제목 키워드 유사도 + publishedAt 시간 근접,
    이 세 조건을 모두 만족할 때만 같은 이슈로 묶는다(요청서 21번 — 조건을 엄격히
    두어, 기존 Market Intelligence Group 묶기 로직과 같은 철학을 따른다).

    각 클러스터에서 대표기사 1건만 남기고(요청서 22번 우선순위: Tier 점수 ->
    최신성 -> 제목 길이), relatedCoverageCount에 클러스터 크기를 기록한다.
    원본 기사를 삭제하는 게 아니라 이번 실행 결과 리스트에서 대표만 앞으로
    보내는 것이므로, raw_news.json의 다른 필드 스펙은 전혀 바뀌지 않는다."""
    n = len(articles)
    token_cache = [_dedupe_tokens(a.get("title", "")) for a in articles]
    used = [False] * n
    clusters: list[list[dict]] = []

    for i in range(n):
        if used[i]:
            continue
        group = [articles[i]]
        used[i] = True
        for j in range(i + 1, n):
            if used[j]:
                continue
            if articles[i].get("sourceGroup") != articles[j].get("sourceGroup"):
                continue
            if _hours_between(articles[i].get("publishedAt"), articles[j].get("publishedAt")) > time_window_hours:
                continue
            if _title_similarity(token_cache[i], token_cache[j]) < similarity_threshold:
                continue
            group.append(articles[j])
            used[j] = True
        clusters.append(group)

    def _rep_priority(a: dict):
        return (a.get("sourceQualityScore", 0) or 0, a.get("publishedAt") or "", len(a.get("title", "")))

    representatives = []
    for group in clusters:
        rep = max(group, key=_rep_priority)
        rep["relatedCoverageCount"] = len(group)
        representatives.append(rep)

    return representatives


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
    "기존 데이터 보존" 로직 때문에 계속 화면에 남아있는 문제가 있었기 때문이다.

    STEP 9.1: 신선도(Freshness) 정책을 성공/실패 그룹에 다르게 적용한다.
    - 정상 수집 성공 그룹: 기존 기사든 신규 기사든 예외 없이 LOOKBACK_HOURS(48h) 기준을 적용한다.
      (신규 기사는 collect_rss 단계에서 이미 48h로 걸러져 있고, 여기서는 살아남는 기존 기사에
      동일 기준을 다시 적용한다.)
    - 수집 실패 그룹: "그 소스에 일시적으로 접근하지 못했다"는 이유만으로 화면이 비어버리는
      것을 막기 위한 안전장치이므로, 최대 RETENTION_HOURS(72h)까지는 기존 데이터를 유지한다.
      72h를 넘긴 기사는 실패 그룹이라도 더 이상 "현재 뉴스"로 유지하지 않는다.
    즉 정상 상태(성공 그룹)에서는 48~72시간짜리 기사가 남는 경우가 없다."""

    def passes_current_policy(item: dict) -> bool:
        url = item.get("url", "")
        title = item.get("title", "")
        description = item.get("description", "") or ""
        if not is_trusted_domain(url):
            return False
        if is_blocked_domain(url):
            return False
        # STEP 9: 브랜드별 강화 컨텍스트 + Business Relevance Threshold로도 기존 데이터를
        # 재검증한다(요청서 38번: 정책이 강화되면 예전에 통과했던 데이터도 새 정책으로 다시 걸러야 함).
        if not has_motorcycle_context(title, description, item.get("sourceGroup")):
            return False
        if not passes_business_relevance_gate(title, description):
            return False
        return True

    existing_by_group: dict[str, list[dict]] = {}
    for item in existing_news:
        if not passes_current_policy(item):
            continue
        # STEP 9.1: brandGroups는 정책(브랜드 판정 로직)이 나중에 강화될 수 있으므로,
        # 재검증 시점마다 항상 다시 계산해 최신 상태로 유지한다(기존 raw_news.json에
        # 이 필드가 아예 없던 데이터도 이 과정에서 자연스럽게 채워진다 = migration).
        item["brandGroups"] = detect_brand_groups(item.get("title", ""), item.get("description", "") or "")
        # STEP 10.1: acquisitionMethod가 없는 과거 raw_news.json 데이터(이번 STEP 이전 수집분)는
        # 전부 Google News RSS 검색 경유였으므로 "google_news"로 마이그레이션한다.
        item.setdefault("acquisitionMethod", "google_news")
        g = item.get("sourceGroup", "unknown")
        existing_by_group.setdefault(g, []).append(item)

    merged: list[dict] = []

    for group in SOURCE_GROUP_LABELS.keys():
        if group in failed_groups:
            # STEP 9.1: 수집 실패 -> 기존 데이터 유지하되 RETENTION_HOURS(72h) Grace Period를
            # 넘긴 기사는 제외한다(위에서 이미 현재 정책으로 한 번 더 걸러진 상태).
            merged.extend([
                item for item in existing_by_group.get(group, [])
                if is_within_lookback(item.get("publishedAt"), hours=RETENTION_HOURS)
            ])
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

        # STEP 9.1: 정상 수집 성공 그룹은 신규로 대체되지 않고 남은 기존 항목이라도
        # LOOKBACK_HOURS(48h)를 넘기면 제외한다 — 성공 그룹에서는 새/기존 관계없이 48h가 기준.
        remaining_old = [
            item for item in old_items
            if item["url"] not in new_urls
            and normalize_title(item["title"]) not in new_titles
            and is_within_lookback(item.get("publishedAt"), hours=LOOKBACK_HOURS)
        ]

        combined = new_items + remaining_old
        # STEP 9: 그룹 내에서 같은 이슈를 다르게 보도한 기사를 대표기사로 묶는다
        # (요청서 20~23번). MAX_PER_GROUP으로 자르기 전에 적용해야 중복 때문에
        # 실제로는 서로 다른 이슈인 기사가 밀려나는 것을 막을 수 있다.
        combined = find_duplicate_clusters(combined)
        # 최신순 정렬 후 그룹당 최대 5개 (요청서 11번: 억지로 채우지 않음, 있는 만큼만)
        combined.sort(key=lambda x: x.get("publishedAt") or "", reverse=True)
        merged.extend(combined[:MAX_PER_GROUP])

    return merged


# ==========================================================
# STEP 12-G.1 — Cross-Source Exact Duplicate 제거
# ==========================================================
# STEP 12-G AUDIT 결론: 같은 URL(=같은 id) 기사가 서로 다른 sourceGroup 검색 쿼리를
# 통해 서로 다른 수집 주기에 각각 "새 기사"로 잡히면, merge_news()가 그룹별로 완전히
# 독립적으로 병합하기 때문에(위 for group in SOURCE_GROUP_LABELS.keys() 루프 — 그룹을
# 넘나드는 비교가 전혀 없음) 두 사본이 영구히 따로 저장된다. remove_duplicates()는
# "이번 실행에서 새로 수집한 것"끼리만 비교해서 범위가 좁고, find_duplicate_clusters()는
# "URL은 다르지만 같은 이슈"를 묶는 별개의 용도(제목 유사도 기반)라 이 문제의 해결책이
# 아니다. 그래서 merge_news()가 반환하는 "최종" 리스트에 대해 전역 exact-duplicate
# 제거를 별도로, 딱 한 번 추가한다.
#
# Source of Truth는 id 하나다(id = generate_id(정규화 URL)이므로 id가 같다는 것은
# 정규화 URL이 같다는 것과 동일하다 — AUDIT 4번). 제목 유사도/fuzzy matching은 여기서
# 절대 쓰지 않는다 — 그건 find_duplicate_clusters()의 역할이고, 이 단계는 "완전히
# 같은 URL"만 다룬다.

# 브랜드 전용 수집 채널(공식 소스일 수도, 브랜드명 검색 쿼리일 수도 있음 — AUDIT 5번:
# "bmw"라고 해서 무조건 공식/고품질이라는 뜻은 아니므로 우선순위 1~3번에는 쓰지 않고
# 마지막 보조 tie-breaker로만 쓴다).
GENERIC_SOURCE_GROUPS = {"kmnews", "naver", "google", "global_media"}


def _collected_at_seconds(item: dict) -> float:
    """collectedAt을 비교 가능한 epoch 초로 변환한다. 파싱 실패/누락 시 +inf를 반환해서
    "가장 나중에 수집된 것"으로 취급한다 — 대표 선정 우선순위 3번(collectedAt이 더
    이른 레코드 우선)에서 이 레코드가 절대 유리해지지 않도록 하는 안전한 fallback이다."""
    raw = item.get("collectedAt")
    if not raw:
        return float("inf")
    try:
        return datetime.fromisoformat(raw).timestamp()
    except Exception:
        return float("inf")


def _dedupe_rep_priority(item: dict) -> tuple:
    """동일 id(=동일 정규화 URL)를 가진 여러 레코드 중 대표 1건을 고르는 우선순위.
    튜플이 클수록(=max() 기준) 더 우선한다.

    1. sourceQualityScore 높은 쪽
    2. description(실제 콘텐츠)이 더 풍부한(긴) 쪽
    3. collectedAt이 더 이른(먼저 발견된) 쪽 — epoch 초를 음수로 넣어 "작을수록(이를수록)
       크게" 되도록 뒤집는다.
    4. 그래도 전부 같으면, 브랜드 전용 sourceGroup(bmw/ducati/triumph/harley/honda/yamaha
       등)을 일반 채널(kmnews/naver/google/global_media)보다 마지막 보조 기준으로만
       우선한다(AUDIT 5번 — sourceGroup 자체를 1순위로 쓰지 않는다. 브랜드 sourceGroup이
       공식 소스가 아니라 단순 검색 쿼리 결과일 수도 있기 때문이다. 다만 다른 모든 조건이
       완전히 동일하다면, 화면(SOURCE MONITOR 등)에 더 의미 있는 라벨이자 실제 기사
       내용과 더 부합하는 브랜드 채널 쪽을 마지막으로 골라주는 것이 합리적이다).

    이 네 기준으로도 완전히 동점이면 Python max()가 "원본 리스트에서 먼저 나온 항목"을
    그대로 반환하므로(파이썬 max()의 표준 동작), 입력 순서가 곧 마지막 deterministic
    tie-breaker가 된다 — 별도 코드 없이도 항상 같은 입력에는 항상 같은 결과가 나온다."""
    is_brand_specific = 1 if item.get("sourceGroup") not in GENERIC_SOURCE_GROUPS else 0
    return (
        item.get("sourceQualityScore", 0) or 0,
        len(item.get("description", "") or ""),
        -_collected_at_seconds(item),
        is_brand_specific,
    )


def dedupe_cross_group(items: list[dict]) -> list[dict]:
    """merge_news()가 그룹별 병합을 모두 마친 "최종" 리스트에 대해, id가 같은
    레코드(=사실상 동일 URL 기사)가 서로 다른 sourceGroup에 중복으로 남아있으면
    대표 1건만 남긴다(요청서 2, 3번).

    같은 URL이 여러 수집 경로로 발견된 것은 "여러 매체의 다중 보도"가 아니라 "기사
    1건"이므로, 이 과정에서 relatedCoverageCount를 인위적으로 올리지 않는다 — 대표로
    남는 레코드가 원래 갖고 있던 값을 그대로 유지한다(요청서 5번 핵심 원칙). 이 함수는
    id가 다른 기사는 절대 건드리지 않으므로(요청서 3번: 다른 id는 이번 단계에서
    서로 다른 기사로 유지), 서로 다른 URL의 유사 기사를 묶는 find_duplicate_clusters()의
    역할을 침범하지 않는다."""
    by_id: dict = {}
    order: list = []
    for item in items:
        # id가 없는(비정상) 레코드는 그룹핑 기준이 없으므로 서로 다른 기사로 취급한다
        # (여러 개를 하나의 None 키로 잘못 묶어 대표 1건으로 줄여버리는 것을 방지).
        key = item.get("id") or id(item)
        if key not in by_id:
            by_id[key] = []
            order.append(key)
        by_id[key].append(item)

    result = []
    for key in order:
        group = by_id[key]
        rep = max(group, key=_dedupe_rep_priority) if len(group) > 1 else group[0]
        result.append(rep)

    return result


# ==========================================================
# STEP 12-J.4 — Canonical Dedup / Persistent ID Preservation
# ==========================================================
# STEP 12-J.3 AUDIT 결론: query parameter "순서"만 다른 동일 기사(Type C, 예: F900GS
# "?idx=X&bmode=view" vs "?bmode=view&idx=X")는 normalize_url()이 순서를 정규화하지
# 않아 서로 다른 id로 갈라진다. normalize_url()/generate_id() 자체를 바꾸면 기존 URL의
# 35%, LIVE 데이터의 67%가 즉시 id를 잃어 History/Brand Pulse 연속성이 깨진다(AUDIT
# 2번 섹션 실측). 그래서 "canonical identity(중복 판정)"와 "persistent id(연속성
# 식별자)"를 분리한다 — 이 섹션의 함수들은 id 생성 규칙 자체를 바꾸지 않고, 이미
# generate_id()로 만들어진 id를 "덮어쓸지"만 판단한다.

def canonical_dedupe_key(url: str) -> str:
    """중복 판정 전용 canonical key. normalize_url()과 동일하게 tracking parameter를
    제거하되, 남은 query parameter를 key 기준으로 "정렬"만 추가한다(파라미터 삭제 확대
    금지, 값 변경 금지, network 호출 없음, 항상 결정적). JSON에 저장하지 않고 매 실행마다
    런타임에 재계산한다(STEP 12-J.3 AUDIT 7번 섹션: 저장 없이 재계산해도 충분함을 확인).
    normalize_url()/generate_id() 자체는 이 함수가 호출하지 않으며 전혀 수정하지 않는다."""
    if not url:
        return url
    try:
        parts = urlsplit(url)
        query_pairs = parse_qsl(parts.query, keep_blank_values=True)
        cleaned_pairs = [(k, v) for k, v in query_pairs if k.lower() not in TRACKING_PARAMS]
        sorted_pairs = sorted(cleaned_pairs, key=lambda kv: (kv[0], kv[1]))
        cleaned_query = urlencode(sorted_pairs)
        canonical_url = urlunsplit((parts.scheme, parts.netloc, parts.path, cleaned_query, ""))
        return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
    except Exception:
        # 실패해도 전체 수집이 중단되지 않아야 한다(요청서 17번 원칙과 동일) — 이 URL만
        # canonical 매칭에서 제외되고(자기 자신과도 매칭되지 않을 만큼 원본 URL 기반의
        # 값을 그대로 반환), 이후 파이프라인은 평소대로 진행된다.
        return f"_unresolved_{url}"


def _existing_canonical_id_map(existing_news: list[dict]) -> dict[str, str]:
    """existing_news(직전까지 저장돼 있던 raw_news.json)를 canonical key 기준으로
    인덱싱해서 "이미 추적 중인 id"를 조회할 수 있게 한다. 같은 canonical key를 가진
    기존 레코드가 여러 개(과거 STEP 12-G.1 이전 데이터 등, 드문 경우) 있으면
    _dedupe_rep_priority()와 동일한 대표 선정 기준으로 하나만 고른다 — 이 함수는
    "어느 id를 승계할지" 결정에만 쓰이고 existing_news 자체는 건드리지 않는다."""
    groups: dict[str, list[dict]] = {}
    for item in existing_news:
        key = canonical_dedupe_key(item.get("url", ""))
        if not key:
            continue
        groups.setdefault(key, []).append(item)

    result: dict[str, str] = {}
    for key, items in groups.items():
        rep = max(items, key=_dedupe_rep_priority) if len(items) > 1 else items[0]
        result[key] = rep.get("id")
    return result


def reconcile_canonical_duplicates(new_articles: list[dict], existing_news: list[dict]) -> list[dict]:
    """STEP 12-J.4 핵심 로직. 이번 실행에서 새로 수집한 기사(new_articles, 이미 id/
    description/collectedAt까지 채워진 상태) 중 query parameter 순서만 달라 canonical
    key가 같은 것들을 하나로 합치고, 그 대표에 "이미 existing_news가 추적 중이던 id"가
    있으면 그 id를 그대로 승계한다.

    반드시 merge_news() 호출 "이전"에 실행해야 한다 — merge_news()는 그룹별로 제목이
    같은 기존 레코드를 remaining_old에서 걸러내므로(요청서 4번: F900GS 실사례처럼 old
    variant가 title match로 조용히 사라지고 new id만 남는 문제), 그 시점에는 이미 새
    레코드의 id가 확정되어 있어야 기존 id를 되살릴 수 없다.

    - 같은 실행 내 canonical 그룹: 콘텐츠 대표는 기존 _dedupe_rep_priority() 철학
      그대로(sourceQualityScore -> description 풍부함 -> collectedAt -> sourceGroup
      보조) 선택한다 — identity 대표(id)와 content 대표(제목/URL/description)를
      분리해도 된다는 요청서 5번 원칙에 따라, id 승계와 콘텐츠 선택은 서로 다른 기준을
      쓴다.
    - existing_news에 이미 같은 canonical key를 가진 레코드가 있으면, 그 id를 최우선
      승계한다(요청서 5번 1순위 원칙). 없으면(완전히 새로운 기사) generate_id()가 만든
      id를 그대로 쓴다(요청서 8번 — canonical key 때문에 신규 기사 id 생성 규칙 자체가
      바뀌지는 않는다).
    - normalize_url()/generate_id()는 이 함수에서 전혀 호출하지 않는다(이미 만들어진
      a["id"]를 필요할 때만 덮어쓸 뿐, 생성 규칙 자체는 그대로)."""
    existing_canonical_to_id = _existing_canonical_id_map(existing_news)

    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for a in new_articles:
        key = canonical_dedupe_key(a.get("url", ""))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(a)

    result: list[dict] = []
    for key in order:
        group = groups[key]
        rep = max(group, key=_dedupe_rep_priority) if len(group) > 1 else group[0]

        existing_id = existing_canonical_to_id.get(key)
        if existing_id and existing_id != rep.get("id"):
            rep["id"] = existing_id

        result.append(rep)

    return result


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

    # STEP 9: 수집 품질을 운영자가 로그에서 직접 확인할 수 있도록 단계별 합계를 누적한다 (요청서 34번).
    total_stats = new_stage_counters()

    # STEP 10.2: Direct Source(method="rss")에 한해서만 개별 소스 단위 Health를 기록한다.
    # 기존 total_stats(전체 파이프라인 합계, 47개 Google 검색 쿼리 포함)와는 완전히 분리된
    # 별도 구조다 — 의미도 계산법도 섞지 않는다(요청 사항 7번). 여기서 "final"은 이 소스
    # 하나의 collect_rss() 결과(중복 클러스터/그룹별 5건 제한 적용 전)이며, 뒤에서 나오는
    # 기존 [Direct Source] 블록의 최종 저장(merged_news, 전체 파이프라인 통과 후) 건수와는
    # 다른 숫자이므로 혼동하지 않도록 로그 라벨을 명확히 구분한다.
    direct_source_health: dict[str, dict] = {}

    # STEP 10-KR: enabled=False로 꺼진 소스 목록(수집 실패와 절대 혼동되지 않도록
    # 별도 리스트로 관리 — "시도했으나 실패"가 아니라 "애초에 시도하지 않음"이다).
    disabled_sources: list[str] = []

    all_new_articles: list[dict] = []

    for src in SOURCES:
        label = src["sourceGroup"] or "(키워드 자동분류)"

        # STEP 10-KR: MOTORRAD PULSE 메인 대시보드는 국내 시장 뉴스 전용 정책으로
        # 전환한다. enabled=False인 소스는 collect_rss() 자체를 호출하지 않는다 —
        # "수집 시도 후 실패"가 아니라 "애초에 수집 대상이 아님"이므로 failed_groups/
        # warnings에도 넣지 않고, 별도의 [Disabled Direct Sources] 로그로만 표시한다.
        # 코드는 삭제하지 않으므로 enabled를 다시 True로 바꾸면 즉시 원복된다(GLOBAL WATCH 후보).
        if not src.get("enabled", True):
            disabled_sources.append(src["source"])
            log(f"\n[비활성화됨] {src['source']} ({label}) — 국내 전용 정책으로 메인 수집에서 제외 (DISABLED, 실패 아님)")
            continue

        log(f"\n[수집 중] {src['source']} ({label}) — {src['url'][:80]}")

        articles, error, stage_stats = collect_rss(src)

        for key in total_stats:
            total_stats[key] += stage_stats.get(key, 0)

        if src.get("method") == "rss":
            raw_entries = stage_stats.get("raw_entries", 0)
            fresh = stage_stats.get("fetched", 0)  # 기존 "fetched" 키 = 키워드필터+48h 신선도 통과 수
            context_pass = stage_stats.get("motorcycle_context_pass", 0)
            business_pass = stage_stats.get("business_relevance_pass", 0)
            final_count = len(articles)

            status = determine_source_health_status(error, raw_entries, fresh, final_count)

            direct_source_health[src["source"]] = {
                "status": status,
                "fetched": raw_entries,
                "fresh": fresh,
                "context": context_pass,
                "business": business_pass,
                "final": final_count,
                "error": error,
            }

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

    # STEP 12-J.4: id/description까지 채운 직후, merge_news() 호출 "이전"에 canonical
    # dedup + persistent id 승계를 실행한다(요청서 4번 — merge_news()가 그룹별로 제목이
    # 같은 기존 레코드를 remaining_old에서 걸러내기 전에 새 레코드의 id를 먼저 바로잡아야,
    # F900GS 실사례처럼 old variant가 조용히 사라지고 new id만 남는 문제를 막을 수 있다).
    # (1) 같은 실행 내에서 query parameter 순서만 다른 variant가 여러 건 들어왔으면 대표
    #     1건으로 합치고, (2) 그 대표의 canonical key가 existing_news(직전 raw_news.json)에
    #     이미 있으면 그 기존 id를 승계한다. normalize_url()/generate_id() 생성 규칙 자체는
    #     바꾸지 않는다 — 이미 만들어진 id를 필요할 때만 덮어쓸 뿐이다.
    duplicates_removed_canonical = len(deduped)
    deduped = reconcile_canonical_duplicates(deduped, existing_news)
    duplicates_removed_canonical -= len(deduped)
    if duplicates_removed_canonical:
        log(f"\n[STEP 12-J.4] Canonical(query parameter 순서) 중복 통합: "
            f"{duplicates_removed_canonical}건 (같은 실행 내 query 순서 variant를 대표 1건으로 통합)")

    for a in deduped:
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

    # STEP 12-G.1: 그룹별 병합이 끝난 최종 리스트에서, 서로 다른 sourceGroup으로 들어온
    # 같은 URL(=같은 id) 중복을 한 번에 정리한다(요청서 2번 — merge_news() 반환 직후,
    # 그룹별 MAX_PER_GROUP 캡이 이미 적용된 뒤. 이 위치를 고른 이유는 merge_news()
    # 내부의 그룹별 루프 구조를 전혀 건드리지 않는 것이 가장 변경 범위가 작기 때문이다
    # — AUDIT 8번에서 이미 이 트레이드오프[캡 적용 후 dedup이라 빈 슬롯이 이번 실행에는
    # 즉시 보충되지 않을 수 있음]를 검토했고, 이번 STEP은 그 구조를 바꾸지 않는다).
    before_cross_dedup = len(merged_news)
    merged_news = dedupe_cross_group(merged_news)
    cross_group_duplicates_removed = before_cross_dedup - len(merged_news)
    if cross_group_duplicates_removed:
        log(f"\n[STEP 12-G.1] Cross-Source 중복 제거: {cross_group_duplicates_removed}건 "
            f"(서로 다른 sourceGroup으로 중복 수집된 동일 URL 기사)")

    # STEP 10-KR: KR-Only Monitor — 저장 직전, 해외 Direct Source와 같은 도메인의 기사가
    # (우연히 국내 검색 쿼리를 통해) 섞여 들어왔는지 관찰한다. 자동 DROP하지 않고 로그만 남긴다.
    foreign_domain_articles = [item for item in merged_news if is_foreign_direct_domain(item.get("url", ""))]

    result = {
        "lastCollectedAt": now_kst,
        "news": merged_news,
    }

    save_json(result, RAW_NEWS_PATH)

    # ---- 로그 요약 (요청서 19, 34~36번 형식) ----
    log("\n" + "=" * 60)
    log("[수집 결과 요약]")
    log("=" * 60)
    for group, label in SOURCE_GROUP_LABELS.items():
        status = " (실패, 기존 데이터 유지)" if group in failed_groups else ""
        log(f"{label}: {counts[group]} collected{status}")

    log(f"\nDuplicates removed: {duplicates_removed_total}")
    log(f"Total saved: {len(merged_news)}")

    # STEP 10-KR: 비활성화된 소스 목록 — 수집 실패(WARNING)와 절대 혼동되지 않도록 별도 블록.
    log("\n" + "-" * 60)
    log("[Disabled Direct Sources]")
    log("-" * 60)
    if disabled_sources:
        for name in disabled_sources:
            log(f"{name}: DISABLED")
    else:
        log("(없음)")

    # STEP 10-KR: KR-Only Monitor — 해외 Direct Source와 같은 도메인의 기사가 국내 검색
    # 쿼리를 통해 우연히 섞여 들어왔는지 관찰한다(자동 DROP 없음, 요청 사항 5번).
    log("\n" + "-" * 60)
    log("[KR-Only Monitor]")
    log("-" * 60)
    log(f"Foreign direct-domain articles saved: {len(foreign_domain_articles)}")
    if foreign_domain_articles:
        for item in foreign_domain_articles:
            domain = urlsplit(item.get("url", "")).netloc
            log(
                f"  - title=\"{item.get('title', '')[:60]}\" "
                f"source=\"{item.get('source', '')}\" domain={domain} sourceGroup={item.get('sourceGroup', '')}"
            )

    # STEP 9: 품질 필터 단계별 결과 (요청서 34번)
    log("\n" + "-" * 60)
    log("[STEP 9 품질 필터 로그]")
    log("-" * 60)
    log(f"Fetched: {total_stats['fetched']}")
    log(f"Trusted Domain PASS: {total_stats['trusted_domain_pass']}")
    log(f"Motorcycle Context PASS: {total_stats['motorcycle_context_pass']}")
    log(f"Business Relevance PASS: {total_stats['business_relevance_pass']}")
    log(f"Duplicate Removed (exact url/title): {duplicates_removed_total}")
    log(f"Final Saved: {len(merged_news)}")
    log("")
    log(f"Blocked by domain: {total_stats['blocked_by_domain']}")
    log(f"Blocked as low/auto-only motorcycle context: {total_stats['blocked_as_context']}")
    log(f"Blocked as low business relevance: {total_stats['blocked_as_low_relevance']}")

    # STEP 10.1: 수집 방법(Direct RSS vs Google News 검색 폴백)별 최종 저장 건수.
    # Official RSS/Global Motorcycle RSS/Google News fallback/Domestic direct RSS를
    # 구분해서 집계한다(요청서 9번 로그 형식).
    official_rss_count = sum(
        1 for item in merged_news
        if item.get("acquisitionMethod") == "rss" and get_source_tier(item.get("url", "")) == "official"
    )
    global_motorcycle_rss_count = sum(
        1 for item in merged_news
        if item.get("sourceGroup") == "global_media" and item.get("acquisitionMethod") == "rss"
    )
    google_fallback_count = sum(
        1 for item in merged_news if item.get("acquisitionMethod") == "google_news"
    )
    domestic_direct_rss_count = sum(
        1 for item in merged_news
        if item.get("acquisitionMethod") == "rss" and get_source_tier(item.get("url", "")) == "business_media"
    )
    log("\n" + "-" * 60)
    log("[Acquisition Method]")
    log("-" * 60)
    log(f"Official RSS: {official_rss_count}")
    log(f"Global Motorcycle RSS: {global_motorcycle_rss_count}")
    log(f"Google News fallback: {google_fallback_count}")
    log(f"Domestic direct RSS: {domestic_direct_rss_count}")

    # STEP 10.1/10.2: 이번 STEP까지 활성화한 Direct Source 각각의 "최종 저장(merged_news,
    # 전체 파이프라인/중복 클러스터/그룹당 5건 제한까지 통과한 후)" 건수. 아래
    # [Direct Source Health] 블록의 "final"과는 다른 숫자다(이 블록은 병합 이후,
    # Health 블록은 병합 이전의 소스 1건 단독 결과) — 혼동 방지를 위해 라벨을 분리해 둔다.
    log("\n[Direct Source] (최종 저장 기준, 병합/중복제거 이후)")
    for direct_source_name in (
        "BMW Motorrad Press", "BMW Motorrad Korea Press",
        "Yamaha Motor Global News", "Visordown", "ADV Pulse",
    ):
        cnt = sum(1 for item in merged_news if item.get("source") == direct_source_name)
        log(f"{direct_source_name}: {cnt}")

    # STEP 10.2: Direct Source Health — "0건"과 "Source 장애"를 구분한다(요청 사항 4~6번).
    # 47개 Google 검색 쿼리에는 적용하지 않고, method="rss"인 실제 Direct Source에만 적용한다.
    # 한국경제(기존 STEP 9 direct RSS)도 method="rss"라 자동으로 포함된다.
    log("\n" + "-" * 60)
    log("[Direct Source Health] (이 소스 1건의 collect_rss() 결과, 병합/중복제거 이전)")
    log("-" * 60)
    for source_name, health in direct_source_health.items():
        log(f"{source_name}")
        log(f"  status={health['status']}")
        if health["status"] == "FAILED":
            log(f"  error=\"{health['error']}\"")
        else:
            log(
                f"  fetched={health['fetched']} fresh={health['fresh']} "
                f"context={health['context']} business={health['business']} final={health['final']}"
            )

    # STEP 9: 최종 저장 데이터 기준 Source Tier 통계 (요청서 35번)
    tier_counts: dict[str, int] = {"official": 0, "motorcycle_media": 0, "business_media": 0, "unknown": 0}
    for item in merged_news:
        tier = item.get("sourceTier") or get_source_tier(item.get("url", "")) or "unknown"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    log("\n[Source Tier 통계 — 최종 저장 기준]")
    log(f"Tier 1 Official: {tier_counts['official']}")
    log(f"Tier 2 Motorcycle Media: {tier_counts['motorcycle_media']}")
    log(f"Tier 3 Business Media: {tier_counts['business_media']}")
    if tier_counts.get("unknown"):
        log(f"Tier 미상(과거 데이터 등): {tier_counts['unknown']}")

    # STEP 9: 최종 저장 데이터 기준 브랜드별 통계 (요청서 36번)
    log("\n[브랜드별 결과 로그 — 최종 저장 기준]")
    for group, label in SOURCE_GROUP_LABELS.items():
        cnt = sum(1 for item in merged_news if item.get("sourceGroup") == group)
        log(f"{label}: {cnt}")

    if warnings:
        log("\n[WARNING 목록]")
        for w in warnings:
            log(f"- {w}")

    # STEP 9.1: TOP NEWS는 이번 STEP에서 sourceGroup 기준 자사/타사 판정을 그대로 유지하지만,
    # brandGroups(신규)와 sourceGroup(기존)이 실제로 얼마나 어긋나 있는지는 로그로 남겨서
    # STEP 9.2에서 TOP NEWS 판정 기준 전환 필요 여부를 판단할 근거로 쓴다.
    bmw_brand_but_not_source = [
        item for item in merged_news
        if "bmw" in (item.get("brandGroups") or []) and item.get("sourceGroup") != "bmw"
    ]
    log("\n[STEP 9.1 — brandGroups vs sourceGroup 불일치 로그]")
    log(f"brandGroups에 'bmw'가 포함되지만 sourceGroup!='bmw'인 기사: {len(bmw_brand_but_not_source)}건")
    if bmw_brand_but_not_source:
        log("  (STEP 9.2 후보 — TOP NEWS 자사/타사 판정 기준을 sourceGroup에서 brandGroups로 전환할지 검토 필요)")
        for item in bmw_brand_but_not_source:
            log(f"  - [{item.get('sourceGroup')}] {item.get('title', '')[:60]}")

    log("\n완료: data/raw_news.json 저장됨")


if __name__ == "__main__":
    main()
