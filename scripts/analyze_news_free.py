#!/usr/bin/env python3
"""
MOTORRAD PULSE — analyze_news_free.py

AI API를 전혀 사용하지 않는 규칙 기반(Rule-Based) 뉴스 분석 엔진.
data/raw_news.json의 실제 수집 기사를 점수 계산과 키워드 매칭으로 분류하고,
data/news.json 과 data/insights.json 을 갱신한다.

핵심 원칙 (요청서 STEP 4-FREE 기준):
- 외부 AI API(Claude/OpenAI/Gemini 등)를 전혀 호출하지 않는다. 비용 $0.
- id, title, url, source, sourceType, sourceGroup, publishedAt, collectedAt은
  raw_news.json의 원본값을 그대로 사용한다.
- summary는 새로운 문장을 창작하지 않는다: RSS description이 있으면 정리해서 쓰고,
  없으면 "원문 기사 제목을 기준으로 확인이 필요한 뉴스입니다." 같은 고정 문구를 쓴다.
- whyItMatters / bmwInsight(Watch Point)는 Category와 매칭된 키워드 기반 고정 템플릿만 사용한다.
- Today's Signal은 오늘 수집된 뉴스의 키워드/카테고리 "빈도"를 근거로 하며,
  실제 시장 변화를 단정하는 과장된 문장을 쓰지 않는다.
- 분석 성공 + 검증 통과 시에만 기존 news.json/insights.json을 교체한다 (Atomic Update).
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import requests

# STEP 9.3: Trusted Domain, Motorcycle Context, Brand Attribution 정책은
# collect_news.py와 공통으로 쓰는 단일 모듈(news_policy.py)에서 가져온다.
# news_policy.py는 이 파일이나 collect_news.py를 import하지 않는 단방향 구조다.
from news_policy import (
    TRUSTED_DOMAINS,
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
NEWS_PATH = os.path.join(DATA_DIR, "news.json")
INSIGHTS_PATH = os.path.join(DATA_DIR, "insights.json")
HISTORY_DIR = os.path.join(DATA_DIR, "history")

# STEP 8: Yesterday/Today 변화 감지를 위한 History 설정
HISTORY_RETENTION_DAYS = 30     # 요청서 STEP8-5번, 8-9번: 30일 초과분만 정리
HISTORY_CURRENT_WINDOW_HOURS = 24   # Current = 최근 24시간 (STEP8-B 승인사항)
HISTORY_PREVIOUS_WINDOW_HOURS = 48  # Previous = 그 이전 24시간 (24~48시간 전)

TOP_NEWS_MAX = 5
TOP_NEWS_MAX_PER_GROUP = 2          # 요청서 22번: 동일 sourceGroup 최대 2개
TOP_NEWS_TITLE_SIMILARITY_THRESHOLD = 0.75  # 요청서 23번: 유사 제목 중복 제한

VALID_CATEGORIES = {"MARKET", "COMPETITOR", "PRODUCT_TECH", "CUSTOMER_TREND"}



def log(msg: str):
    print(msg, flush=True)


# ==========================================================
# 1. 점수 체계 (요청서 5~14번)
# ==========================================================

# STEP 11-C1 (버그 수정): 브랜드별로 여러 동의어 키워드가 등록돼 있으면(예: BMW의
# "bmw"와 "bmw 모토라드"), 기존에는 match_keywords()가 동의어마다 각각 점수를
# 가산해서 같은 브랜드 언급인데도 2배 가까이 점수가 부풀려지는 문제가 있었다
# (STEP 11-A AUDIT 2-2번에서 실측 확인: BMW 기사 brand_score가 25+25=50으로 계산됨).
# BMW만 예외 처리하지 않고, "브랜드 하나 = 동의어가 몇 개 매칭되든 점수는 1회만"이라는
# 원칙을 모든 브랜드에 동일하게 적용하기 위해 브랜드 단위 그룹 구조를 Source of Truth로
# 둔다. BRAND_SCORES(동의어 -> 점수 flat dict)는 이 그룹에서 자동으로 파생시켜서,
# 값이 두 군데서 따로 관리되다 어긋나는 일이 없게 한다(team_brief 등 기존 코드가
# BRAND_SCORES의 키 목록을 그대로 참조하므로 flat dict 자체는 계속 유지해야 한다).
BRAND_SYNONYM_GROUPS = [
    {"canonical": "bmw", "score": 25, "keywords": ["bmw motorrad", "bmw", "비엠더블유 모토라드", "bmw 모토라드"]},
    {"canonical": "ducati", "score": 16, "keywords": ["ducati", "두카티"]},
    {"canonical": "triumph", "score": 16, "keywords": ["triumph", "트라이엄프"]},
    {"canonical": "harley", "score": 14, "keywords": ["harley-davidson", "harley davidson", "할리데이비슨", "할리 데이비슨"]},
    {"canonical": "honda", "score": 12, "keywords": ["honda", "혼다"]},
    {"canonical": "yamaha", "score": 12, "keywords": ["yamaha", "야마하"]},
    {"canonical": "ktm", "score": 12, "keywords": ["ktm"]},
    {"canonical": "kawasaki", "score": 10, "keywords": ["kawasaki", "가와사키"]},
]

BRAND_SCORES = {
    kw: group["score"]
    for group in BRAND_SYNONYM_GROUPS
    for kw in group["keywords"]
}

MARKET_KEYWORD_SCORES = {
    "market share": 10, "market": 10, "sales": 10, "growth": 8, "decline": 8,
    "registration": 8, "forecast": 8, "industry": 6, "demand": 6,
    "revenue": 6, "segment": 6, "premium": 5, "global": 4,
    "europe": 4, "asia": 4, "china": 4, "india": 4, "us": 3,
    "시장": 10, "판매": 10, "점유율": 10, "성장": 8, "감소": 8,
    "등록대수": 8, "전망": 8, "업계": 6, "수요": 6,
    "매출": 6, "세그먼트": 6, "프리미엄": 5, "글로벌": 4,
    "유럽": 4, "아시아": 4, "중국": 4, "국내": 3, "수출": 5,
}

PRODUCT_KEYWORD_SCORES = {
    "launch": 12, "unveil": 12, "new model": 12, "gs": 10,
    "new motorcycle": 11, "model year": 8, "update": 6, "facelift": 6,
    "concept": 7, "prototype": 7, "adventure": 8, "touring": 6,
    "roadster": 5, "sport": 5, "scooter": 5,
    "출시": 12, "공개": 12, "신모델": 12, "신형": 10,
    "새로운": 6, "부분변경": 6, "컨셉": 7, "프로토타입": 7,
    "어드벤처": 8, "투어링": 6, "스쿠터": 5, "신차": 10,
}

TECH_KEYWORD_SCORES = {
    "electric": 12, "ev": 11, "adas": 12, "battery": 9, "radar": 9,
    "connectivity": 9, "abs": 6, "software": 6, "navigation": 5,
    "charging": 6, "hybrid": 6, "safety": 6, "ai": 5, "sensor": 5,
    "engine": 5,
    "전기": 12, "전동": 12, "배터리": 9, "레이더": 9,
    "커넥티비티": 9, "소프트웨어": 6, "내비게이션": 5,
    "충전": 6, "하이브리드": 6, "안전": 6, "센서": 5,
    "엔진": 5, "자율주행": 9,
}

CUSTOMER_KEYWORD_SCORES = {
    "rider": 6, "community": 8, "lifestyle": 6, "experience": 6,
    "customer": 6, "generation": 5, "adventure travel": 7,
    "social media": 5, "women riders": 6, "young riders": 6,
    "urban mobility": 6, "event": 8, "festival": 8, "customization": 6,
    "라이더": 6, "커뮤니티": 8, "라이프스타일": 6, "경험": 5,
    "고객": 6, "세대": 5, "여성 라이더": 6, "젊은 라이더": 6,
    "도심형 모빌리티": 6, "동호회": 8, "투어": 5, "행사": 8, "축제": 8, "커스터마이징": 6,
}

EVENT_KEYWORD_SCORES = {
    "recall": 15, "acquisition": 15, "merger": 15, "partnership": 10,
    "investment": 10, "factory": 10, "production": 8, "shutdown": 12,
    "tariff": 12, "regulation": 12, "ban": 12, "lawsuit": 10,
    "ceo": 8, "strategy": 10, "record sales": 12,
    "리콜": 15, "인수": 15, "합병": 15, "제휴": 10, "파트너십": 10,
    "투자": 10, "공장": 10, "생산": 8, "가동중단": 12,
    "관세": 12, "규제": 12, "판매금지": 12, "소송": 10,
    "대표이사": 8, "전략": 10, "역대 최대 판매": 12,
}

SOURCE_TYPE_SCORES = {
    "official": 10,
    "industry": 8,
    "market_report": 8,
    "media": 6,
    "automotive_media": 6,
}

# COMPETITOR Category 재정의 (STEP 7): 브랜드명이 있다는 이유만으로 COMPETITOR가
# 되지 않도록, 실제 "경쟁 전략" 성격의 키워드에만 점수를 준다.
# 신제품/기술 자체는 PRODUCT_TECH가 담당하고, COMPETITOR는 가격/딜러/파트너십/
# 조직변화 등 전략적 움직임에 집중한다.
COMPETITOR_STRATEGY_KEYWORD_SCORES = {
    "pricing": 10, "price cut": 10, "price increase": 10, "dealer": 10,
    "dealership": 10, "partnership": 9, "campaign": 8, "positioning": 8,
    "promotion": 8, "market entry": 9, "expansion": 7, "ceo": 7,
    "management": 6, "reorganization": 8, "acquisition": 12, "merger": 12,
    "가격": 10, "가격 인하": 10, "가격 인상": 10, "딜러": 10, "대리점": 9,
    "제휴": 9, "협업": 7, "캠페인": 8, "포지셔닝": 8, "프로모션": 8,
    "시장 진출": 9, "확장": 7, "대표이사": 7, "경영": 6, "조직개편": 8,
    "인수": 12, "합병": 12,
}


def freshness_score(published_at: str | None) -> int:
    """요청서 11번: 최신 기사일수록 높은 점수"""
    if not published_at:
        return 0
    try:
        dt = datetime.fromisoformat(published_at)
        now = datetime.now(dt.tzinfo)
        hours = (now - dt).total_seconds() / 3600
    except Exception:
        return 0

    if hours <= 12:
        return 15
    if hours <= 24:
        return 12
    if hours <= 36:
        return 8
    if hours <= 48:
        return 5
    return 0


_HANGUL_RANGE = re.compile(r"[가-힣]")


def _is_hangul_char(ch: str) -> bool:
    return bool(ch) and bool(_HANGUL_RANGE.match(ch))


def _keyword_matches(kw_lower: str, text_lower: str) -> bool:
    """키워드 하나가 text_lower 안에 "유효하게" 존재하는지 판단하는 공통 규칙.

    1) 3글자 이하 영문 키워드(ev/ai/us 등): 단어 경계(\\b) 검사 — "event" 안의
       "ev"처럼 다른 단어의 일부로 잘못 걸리는 것을 막는다(기존 규칙, 변경 없음).
    2) 한글이 포함된 키워드(예: "시장"): STEP 11-C1 버그 수정 — 매칭된 위치
       바로 앞 글자가 한글 음절이면(예: "전시장"의 "전"+"시장") 그 앞 글자와
       합쳐져 전혀 다른 단어(전시장=Exhibition Hall)를 이루는 것으로 보고
       매칭에서 제외한다. 앞 글자가 없거나(문자열 시작) 공백/한글이 아닌
       문자(문장부호 등)면 정상 매칭으로 인정한다.
       뒤쪽 글자는 검사하지 않는다 — "판매량"(판매+량), "시장 점유율"/"시장
       성장"(시장+공백+접미어)처럼 한글 조사/접미어가 뒤에 붙는 것은 기존과
       동일하게 정상적으로 허용해야 하기 때문이다(요청 2, 6번 — "국내 시장",
       "시장 점유율", "시장 성장" 매칭은 그대로 유지).
    3) 그 외(영문 4글자 이상, 혼합 등): 기존과 동일하게 단순 부분 문자열 매칭.
    """
    if len(kw_lower) <= 3 and kw_lower.isascii() and kw_lower.isalpha():
        return bool(re.search(r"\b" + re.escape(kw_lower) + r"\b", text_lower))

    if _HANGUL_RANGE.search(kw_lower):
        start = text_lower.find(kw_lower)
        while start != -1:
            prev_char = text_lower[start - 1] if start > 0 else ""
            if not _is_hangul_char(prev_char):
                return True
            start = text_lower.find(kw_lower, start + 1)
        return False

    return kw_lower in text_lower


def match_keywords(text: str, keyword_scores: dict[str, int]) -> tuple[int, list[str]]:
    """요청서 13번: 같은 키워드가 여러 번 나와도 한 번만 가산. 존재 여부 기준.

    실제 매칭 판정은 _keyword_matches()가 담당한다(짧은 영문 단어 경계 규칙 +
    STEP 11-C1의 한글 앞글자 경계 규칙)."""
    text_lower = text.lower()
    total = 0
    matched = []
    for kw, score in keyword_scores.items():
        if _keyword_matches(kw.lower(), text_lower):
            total += score
            matched.append(kw)
    return total, matched


def match_brand_keywords(text: str) -> tuple[int, list[str]]:
    """STEP 11-C1 버그 수정: 브랜드 점수는 match_keywords()처럼 동의어별로 각각
    가산하지 않고, BRAND_SYNONYM_GROUPS 기준으로 "브랜드 하나당 점수 1회만"
    가산한다. 같은 브랜드의 동의어가 여러 개 매칭돼도(예: BMW의 "bmw"와
    "bmw 모토라드") 점수는 해당 브랜드의 score 값 1번만 더해진다. matchedKeywords
    표시용으로는 실제로 매칭된 동의어를 전부 남긴다(team_brief 등 기존 코드가
    matchedKeywords 안의 브랜드 키워드 문자열을 그대로 참조하기 때문)."""
    text_lower = text.lower()
    total = 0
    matched: list[str] = []
    for group in BRAND_SYNONYM_GROUPS:
        group_matched = [kw for kw in group["keywords"] if _keyword_matches(kw.lower(), text_lower)]
        if group_matched:
            total += group["score"]
            matched.extend(group_matched)
    return total, matched


def compute_score(article: dict) -> tuple[int, list[str], dict[str, int], dict[str, int]]:
    """
    기사 하나에 대한 총점, 매칭된 키워드 전체 목록, 카테고리별 점수, 카테고리별 매칭 키워드 개수를 계산한다.
    반환: (총점 0~100, matchedKeywords, category별 점수 dict, category별 키워드 개수 dict)

    STEP 7 개선: COMPETITOR는 더 이상 브랜드명 언급 자체(brand_score)로 판정하지 않는다.
    "Ducati unveils new electric motorcycle"처럼 브랜드명이 있어도 실제로는 신제품/기술
    소식인 기사가 전부 COMPETITOR로 분류되던 문제를 해결하기 위해, COMPETITOR 판정은
    전략 키워드(가격/딜러/파트너십/조직변화 등, COMPETITOR_STRATEGY_KEYWORD_SCORES)를
    중심으로 하고 브랜드 언급은 아주 약한 보조 신호로만 반영한다.
    """
    text = f"{article.get('title', '')} {article.get('description', '')}"

    brand_score, brand_kw = match_brand_keywords(text)
    market_score, market_kw = match_keywords(text, MARKET_KEYWORD_SCORES)
    product_score, product_kw = match_keywords(text, PRODUCT_KEYWORD_SCORES)
    tech_score, tech_kw = match_keywords(text, TECH_KEYWORD_SCORES)
    customer_score, customer_kw = match_keywords(text, CUSTOMER_KEYWORD_SCORES)
    event_score, event_kw = match_keywords(text, EVENT_KEYWORD_SCORES)
    competitor_strategy_score, competitor_strategy_kw = match_keywords(text, COMPETITOR_STRATEGY_KEYWORD_SCORES)

    fresh_score = freshness_score(article.get("publishedAt"))
    source_score = SOURCE_TYPE_SCORES.get(article.get("sourceType", ""), 5)

    total = brand_score + market_score + product_score + tech_score + customer_score + event_score + fresh_score + source_score
    total = max(0, min(100, total))  # 요청서 14번: 0~100 clamp

    all_matched = list(dict.fromkeys(
        brand_kw + market_kw + product_kw + tech_kw + customer_kw + event_kw + competitor_strategy_kw
    ))

    # COMPETITOR 판정 점수: 전략 신호(강한 가중치) + 리콜/인수합병 등 이벤트 신호 + 브랜드 언급(약한 보조 신호, 0.3배)
    # -> 브랜드명만 있고 전략/이벤트 신호가 전혀 없으면 COMPETITOR 점수가 낮아져
    #    PRODUCT_TECH/MARKET 등 실제 주제 카테고리가 더 쉽게 이긴다.
    competitor_score = competitor_strategy_score + event_score + int(brand_score * 0.15)

    category_scores = {
        "MARKET": market_score,
        "COMPETITOR": competitor_score,
        "PRODUCT_TECH": product_score + tech_score,
        "CUSTOMER_TREND": customer_score,
    }

    # tie-breaker에서 "더 구체적인 주제"를 판단하기 위한 카테고리별 매칭 키워드 개수
    category_keyword_counts = {
        "MARKET": len(market_kw),
        "COMPETITOR": len(competitor_strategy_kw) + len(event_kw),
        "PRODUCT_TECH": len(product_kw) + len(tech_kw),
        "CUSTOMER_TREND": len(customer_kw),
    }

    return total, all_matched, category_scores, category_keyword_counts


def score_to_importance(score: int) -> float:
    """요청서 15번: 0~100 점수를 1.0~5.0 importance로 변환"""
    if score >= 90:
        return 5.0
    if score >= 80:
        return 4.5
    if score >= 70:
        return 4.0
    if score >= 60:
        return 3.5
    if score >= 50:
        return 3.0
    if score >= 40:
        return 2.5
    if score >= 30:
        return 2.0
    return 1.5


# ==========================================================
# 2. Category 자동분류 (요청서 16~21번)
# ==========================================================

CATEGORY_TIE_BREAK_ORDER = ["PRODUCT_TECH", "COMPETITOR", "MARKET", "CUSTOMER_TREND"]


def determine_category(category_scores: dict[str, int], category_keyword_counts: dict[str, int] | None = None) -> str:
    """카테고리를 결정한다.

    STEP 7 개선: 동점일 때 무조건 고정된 우선순위로 정하지 않고,
    1) 더 구체적인 주제 키워드가 많이 매칭된 카테고리 우선
    2) 그래도 같으면 가중치 합(원래 점수) 우선 -- 사실 여기 오면 이미 max_score로 동점이므로 사실상 생략됨
    3) 그래도 같으면 고정 우선순위(CATEGORY_TIE_BREAK_ORDER)
    순서로 판단한다."""
    max_score = max(category_scores.values())

    if max_score == 0:
        # 아무 카테고리 키워드도 안 걸렸으면 기본값으로 MARKET (가장 일반적인 산업 뉴스로 취급)
        return "MARKET"

    top_categories = [cat for cat, score in category_scores.items() if score == max_score]

    if len(top_categories) == 1:
        return top_categories[0]

    # 1단계: 더 구체적인(매칭 키워드 개수가 많은) 카테고리 우선
    if category_keyword_counts:
        max_kw_count = max(category_keyword_counts.get(cat, 0) for cat in top_categories)
        most_specific = [cat for cat in top_categories if category_keyword_counts.get(cat, 0) == max_kw_count]
        if len(most_specific) == 1:
            return most_specific[0]
        top_categories = most_specific

    # 2, 3단계: 고정 우선순위 tie-breaker (요청서 21번)
    for cat in CATEGORY_TIE_BREAK_ORDER:
        if cat in top_categories:
            return cat

    return top_categories[0]


# ==========================================================
# 3. Summary 생성 — 창작 금지, 원문 정리 또는 고정 문구 (요청서 27번)
# ==========================================================

def strip_html(text: str) -> str:
    """HTML 태그와 모든 HTML 엔티티(&nbsp; &middot; &amp; 등)를 제거한다.
    Google News RSS의 description은 실제 요약이 아니라
    "<a>제목</a>&nbsp;&nbsp;<font>출처</font>" 형태로 제목을 재포장한 값이고,
    원문 페이지에서 가져온 요약에도 &middot; 같은 엔티티가 섞여 나오는 경우가 있어
    표준 라이브러리(html.unescape)로 빠짐없이 처리한다."""
    import html as html_module
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _titles_are_essentially_same(a: str, b: str) -> bool:
    """정규화 후 비교해서 두 문자열이 사실상 같은 내용인지 판단.
    Google News description이 제목을 그대로 재포장한 경우를 걸러내기 위함."""
    norm_a = re.sub(r"[^\w가-힣]", "", a.lower())
    norm_b = re.sub(r"[^\w가-힣]", "", b.lower())
    if not norm_a or not norm_b:
        return False
    shorter, longer = sorted([norm_a, norm_b], key=len)
    return shorter in longer


def enrich_summary_from_article_page(url: str) -> str | None:
    """TOP NEWS로 선정된 소수(최대 5개) 기사에 한해, 원문 페이지에 직접 접속해서
    og:description 메타 태그(대부분의 국내 언론사가 제공하는 실제 기사 요약)를 가져온다.
    RSS의 description은 비어있거나 제목 재포장인 경우가 많아 요약이 자주 빈 화면으로
    나오던 문제를 개선하기 위함이다.

    소수 기사에만 적용하는 이유는 성능/시간 때문이다 — 전체 기사(수십 건)에 이 작업을
    하면 GitHub Actions 실행 시간이 크게 늘어난다. 실패해도 None을 반환해 안전하게
    기존 방식(build_summary)으로 폴백한다 (요청서 17번 원칙과 동일)."""
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        resp.raise_for_status()
        match = re.search(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
            resp.text, re.IGNORECASE,
        )
        if not match:
            match = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
                resp.text, re.IGNORECASE,
            )
        if match:
            description = strip_html(match.group(1)).strip()
            if len(description) > 200:
                truncated = description[:200]
                last_period = truncated.rfind(".")
                description = truncated[:last_period + 1] if last_period > 50 else truncated + "..."
            return description or None
        return None
    except Exception:
        return None


def build_summary(article: dict) -> str | None:
    title = article.get("title", "")
    description = strip_html(article.get("description", ""))

    # Google News RSS의 description은 대부분 "제목 + 출처명" 재포장이라
    # 실제 요약으로 볼 수 없다. 제목과 사실상 같은 내용이면 가짜 요약으로 판단한다.
    # 예전에는 이 경우 "원문 확인이 필요한 뉴스입니다" 같은 고정 문구를 넣었지만,
    # 실제 사용해보니 거의 모든 카드에 똑같은 문구가 반복되어 오히려 지저분해 보였다.
    # 그래서 요약이 없으면 None을 반환해 화면에서 그 줄 자체를 아예 그리지 않는다
    # (요청서 27번: 사실을 지어내지 않는다 — 없으면 없는 대로 보여주는 게 가장 정직하다).
    if description and not _titles_are_essentially_same(title, description):
        if len(description) > 200:
            truncated = description[:200]
            last_period = truncated.rfind(".")
            description = truncated[:last_period + 1] if last_period > 50 else truncated + "..."
        return description

    return None


# ==========================================================
# 3-1. Topic Tags / Segment / Technology Theme (STEP 7 신규 — 내부 계산용)
# ==========================================================
# Intelligence Group 묶기에 사용할 주제 신호를 세 종류로 나눠서 계산한다.
# - TOPIC_TAGS: 넓은 범주(NEW_MODEL, MARKET, CUSTOMER 등) — 이것만으로는 그룹핑하지 않는다.
# - SEGMENT_TAGS: 제품 세그먼트(ADVENTURE/TOURING/SPORT/ROADSTER/SCOOTER/CRUISER) — 좁고 구체적.
# - TECH_THEME_TAGS: 기술 테마(ELECTRIFICATION/ADAS/CONNECTIVITY/SAFETY) — 좁고 구체적.
# 최종 JSON(news.json/insights.json)에는 노출하지 않고 분석 파이프라인 내부에서만 사용한다.

TOPIC_TAG_KEYWORDS = {
    "NEW_MODEL": ["launch", "unveil", "new model", "new motorcycle", "model year", "출시", "공개", "신모델", "신형", "신차"],
    "ELECTRIFICATION": ["electric", "ev", "battery", "charging", "hybrid", "전기", "전동", "배터리", "충전", "하이브리드"],
    "TECH": ["adas", "radar", "connectivity", "abs", "software", "navigation", "ai", "sensor",
             "레이더", "커넥티비티", "소프트웨어", "내비게이션", "센서", "자율주행"],
    "MARKET": ["market", "sales", "registration", "growth", "decline", "forecast", "revenue", "demand",
               "시장", "판매", "점유율", "성장", "감소", "등록대수", "전망", "수요", "매출"],
    "CUSTOMER": ["rider", "community", "lifestyle", "experience", "customer", "event", "festival",
                 "라이더", "커뮤니티", "라이프스타일", "경험", "고객", "동호회"],
}

SEGMENT_TAG_KEYWORDS = {
    "ADVENTURE": ["adventure", "gs", "africa twin", "multistrada", "tiger", "tenere", "어드벤처"],
    "TOURING": ["touring", "gold wing", "투어링"],
    "SPORT": ["sport", "cbr", "ninja", "r1", "r7"],
    "ROADSTER": ["roadster", "mt-", "streetfighter"],
    "SCOOTER": ["scooter", "스쿠터"],
    "CRUISER": ["cruiser", "rebel", "크루저"],
}

TECH_THEME_TAG_KEYWORDS = {
    "ELECTRIFICATION": ["electric", "ev", "battery", "charging", "hybrid", "전기", "전동", "배터리", "충전", "하이브리드"],
    "ADAS": ["adas", "radar", "레이더", "자율주행"],
    "CONNECTIVITY": ["connectivity", "software", "navigation", "커넥티비티", "소프트웨어", "내비게이션"],
    "SAFETY": ["safety", "abs", "안전"],
}


def _extract_tags(text: str, tag_keyword_map: dict[str, list[str]]) -> list[str]:
    """짧은 영문 키워드(예: ev)는 단어 경계를 검사해서 오탐을 막는다
    (예: "ev"가 "event"의 부분 문자열로 잘못 매칭되어 이벤트 기사가
    ELECTRIFICATION으로 잘못 태깅되는 문제가 실제 테스트에서 발견되어 추가함.
    match_keywords()와 동일한 원리)."""
    text_lower = text.lower()
    tags = []
    for tag, keywords in tag_keyword_map.items():
        matched = False
        for kw in keywords:
            kw_lower = kw.lower()
            if len(kw_lower) <= 3 and kw_lower.isascii() and kw_lower.isalpha():
                if re.search(r"\b" + re.escape(kw_lower) + r"\b", text_lower):
                    matched = True
                    break
            elif kw_lower in text_lower:
                matched = True
                break
        if matched:
            tags.append(tag)
    return tags


def compute_topic_signals(title: str, description: str) -> dict[str, list[str]]:
    """기사 하나에 대해 topicTags(넓은 주제), segmentTags(제품 세그먼트),
    techThemeTags(기술 테마) 세 종류의 내부 태그를 계산한다."""
    text = f"{title} {description or ''}"
    return {
        "topicTags": _extract_tags(text, TOPIC_TAG_KEYWORDS),
        "segmentTags": _extract_tags(text, SEGMENT_TAG_KEYWORDS),
        "techThemeTags": _extract_tags(text, TECH_THEME_TAG_KEYWORDS),
    }


# ==========================================================
# 4. Why It Matters / BMW Watch Point 템플릿 (요청서 24~26번)
# ==========================================================

WHY_IT_MATTERS_TEMPLATES = {
    "MARKET": "Motorcycle 시장의 판매 및 수요 흐름과 관련된 기사입니다. 주요 시장과 Premium Segment의 변화 여부를 확인할 필요가 있습니다.",
    "COMPETITOR": "주요 경쟁 브랜드의 제품 또는 시장 움직임과 관련된 뉴스입니다. 경쟁사 Positioning과 제품 전략 변화를 확인할 필요가 있습니다.",
    "PRODUCT_TECH": "Motorcycle 제품 및 기술 변화와 관련된 기사입니다. 향후 Premium Motorcycle 고객의 제품 기대수준에 영향을 줄 수 있는 요소인지 확인할 필요가 있습니다.",
    "CUSTOMER_TREND": "Motorcycle 고객 및 라이딩 Trend와 관련된 기사입니다. 고객 Experience와 Lifestyle 변화 관점에서 지속적으로 확인할 필요가 있습니다.",
}

# ==========================================================
# STEP 7: Watch Point / Why It Matters 13개 주제 세분화
# ==========================================================
# 카테고리(4종)만으로는 같은 카테고리 기사가 전부 동일 문구를 받는 문제가 있었다.
# 실제 기사 주제(리콜/전동화/어드벤처 등)에 맞는 13개 세부 템플릿을 우선 적용하고,
# 어디에도 안 걸리면 기존 카테고리 기본 문구로 폴백한다.

# 요청서 13번: 우선순위(구체적인 것부터). 이 순서대로 첫 매칭되는 주제를 채택한다.
WATCH_POINT_TOPIC_PRIORITY = [
    "RECALL_SAFETY", "ELECTRIFICATION", "ADAS_TECH", "CONNECTIVITY",
    "ADVENTURE", "TOURING", "NEW_MODEL", "CUSTOMIZATION", "COMMUNITY_EVENT",
    "CUSTOMER_EXPERIENCE", "PRICING", "REGULATION", "PREMIUM_POSITIONING",
    "MARKET_SALES",
]

# 각 주제를 판별하는 키워드 (한/영 병기)
WATCH_POINT_TOPIC_KEYWORDS = {
    "RECALL_SAFETY": ["recall", "리콜", "safety", "안전"],
    "ELECTRIFICATION": ["electric", "ev", "battery", "charging", "hybrid", "전기", "전동", "배터리", "충전", "하이브리드"],
    "ADAS_TECH": ["adas", "radar", "레이더", "자율주행"],
    "CONNECTIVITY": ["connectivity", "software", "navigation", "커넥티비티", "소프트웨어", "내비게이션"],
    "ADVENTURE": ["adventure", "gs", "africa twin", "multistrada", "tiger", "tenere", "어드벤처"],
    "TOURING": ["touring", "gold wing", "투어링"],
    "NEW_MODEL": ["launch", "unveil", "new model", "new motorcycle", "model year", "출시", "공개", "신모델", "신형", "신차"],
    "CUSTOMIZATION": ["customization", "accessory", "커스터마이징", "액세서리", "개인화"],
    "COMMUNITY_EVENT": ["community", "event", "festival", "커뮤니티", "동호회", "행사", "축제"],
    "CUSTOMER_EXPERIENCE": ["experience", "lifestyle", "경험", "라이프스타일"],
    "PRICING": ["pricing", "price cut", "price increase", "가격", "가격 인하", "가격 인상"],
    "REGULATION": ["regulation", "tariff", "ban", "규제", "관세", "판매금지"],
    "PREMIUM_POSITIONING": ["positioning", "premium", "포지셔닝", "프리미엄"],
    "MARKET_SALES": ["market", "sales", "registration", "market share", "시장", "판매", "점유율", "등록대수"],
}

WATCH_POINT_TOPIC_TEMPLATES = {
    "NEW_MODEL": "경쟁 브랜드의 신제품 출시 시점과 제품 Positioning, 세그먼트 내 차별화 요소를 지속적으로 비교할 필요가 있습니다.",
    "ADVENTURE": "Premium Adventure Segment의 라인업 확대와 중형급 제품 경쟁 강도를 지속적으로 확인할 필요가 있습니다.",
    "TOURING": "Touring 편의성과 장거리 Experience 강화 요소가 Premium 고객 선택에 미치는 영향을 확인할 필요가 있습니다.",
    "ELECTRIFICATION": "Motorcycle 전동화 기술의 적용 속도와 경쟁 브랜드의 출시 전략, 실제 시장 반응을 지속적으로 확인할 필요가 있습니다.",
    "ADAS_TECH": "ADAS·Radar·Safety 기술이 Premium Motorcycle의 제품 기대수준을 어떻게 변화시키는지 비교할 필요가 있습니다.",
    "CONNECTIVITY": "Connectivity와 Digital Feature가 제품 차별화 및 고객 Experience에 미치는 영향을 확인할 필요가 있습니다.",
    "MARKET_SALES": "주요 Motorcycle 시장의 판매·등록 흐름과 Premium Segment 수요 변화를 함께 확인할 필요가 있습니다.",
    "PREMIUM_POSITIONING": "경쟁 브랜드의 가격·제품 구성·브랜드 Positioning 변화가 Premium Segment 경쟁구도에 미치는 영향을 관찰할 필요가 있습니다.",
    "CUSTOMER_EXPERIENCE": "제품 외 Experience 요소가 고객 유입과 브랜드 충성도에 미치는 영향을 지속적으로 확인할 필요가 있습니다.",
    "COMMUNITY_EVENT": "브랜드 Community 및 오프라인 Experience 활동이 고객 Engagement와 재구매 관계 형성에 미치는 영향을 확인할 필요가 있습니다.",
    "CUSTOMIZATION": "Customization 수요와 액세서리·개인화 전략이 Premium Motorcycle 고객 경험에 미치는 영향을 비교할 필요가 있습니다.",
    "PRICING": "경쟁사의 가격 전략과 금융·프로모션 변화가 고객 선택과 Segment Positioning에 미치는 영향을 확인할 필요가 있습니다.",
    "REGULATION": "규제 변화가 제품 구성, 판매 환경 및 고객 접근성에 미치는 영향을 지속적으로 확인할 필요가 있습니다.",
    "RECALL_SAFETY": "안전·Recall 이슈가 브랜드 신뢰도와 고객 커뮤니케이션에 미치는 영향을 확인할 필요가 있습니다.",
}

# Why It Matters도 같은 주제 체계로 세분화한다. 단 요청서 15번 원칙(과도하게 늘려
# 관리하기 어렵게 만들지 않는다)에 따라, Watch Point만큼 세밀하지 않고 대표성 있는
# 주제 몇 개만 별도 문구를 두고 나머지는 카테고리 기본 문구로 자연스럽게 폴백한다.
WHY_IT_MATTERS_TOPIC_TEMPLATES = {
    "NEW_MODEL": "주요 브랜드의 신규 모델 출시와 관련된 기사입니다. Segment 내 제품 경쟁과 Positioning 변화를 확인할 필요가 있습니다.",
    "MARKET_SALES": "Motorcycle 시장의 판매 및 수요 흐름과 관련된 기사입니다. 주요 시장과 Premium Segment의 변화 여부를 확인할 필요가 있습니다.",
    "ELECTRIFICATION": "Motorcycle 전동화 및 관련 기술 변화와 관련된 기사입니다. 향후 제품 전략과 고객 기대수준 변화 여부를 확인할 필요가 있습니다.",
    "RECALL_SAFETY": "안전 또는 품질 관리 이슈와 관련된 기사입니다. 브랜드 신뢰도에 미치는 영향을 확인할 필요가 있습니다.",
    "COMMUNITY_EVENT": "브랜드 Community 및 오프라인 Experience 활동과 관련된 기사입니다. 고객 Engagement 효과를 확인할 필요가 있습니다.",
}


def _detect_watch_point_topic(matched_keywords: list[str], title: str) -> str | None:
    """제목/매칭 키워드에서 13개 Watch Point 주제 중 우선순위가 가장 높은 것 하나를 찾는다."""
    text = title.lower()
    matched_set = set(kw.lower() for kw in matched_keywords)
    for topic in WATCH_POINT_TOPIC_PRIORITY:
        keywords = WATCH_POINT_TOPIC_KEYWORDS[topic]
        if any(kw.lower() in matched_set or kw.lower() in text for kw in keywords):
            return topic
    return None


def build_why_it_matters(category: str, matched_keywords: list[str] | None = None, title: str = "") -> str:
    if matched_keywords is not None:
        topic = _detect_watch_point_topic(matched_keywords, title)
        if topic and topic in WHY_IT_MATTERS_TOPIC_TEMPLATES:
            return WHY_IT_MATTERS_TOPIC_TEMPLATES[topic]
    return WHY_IT_MATTERS_TEMPLATES.get(category, WHY_IT_MATTERS_TEMPLATES["MARKET"])


CATEGORY_DEFAULT_WATCH_POINT = {
    "MARKET": "시장 동향을 지속적으로 모니터링할 필요가 있습니다.",
    "COMPETITOR": "경쟁 브랜드의 움직임을 지속적으로 관찰할 필요가 있습니다.",
    "PRODUCT_TECH": "제품 및 기술 트렌드를 지속적으로 확인할 필요가 있습니다.",
    "CUSTOMER_TREND": "고객 트렌드 변화를 지속적으로 관찰할 필요가 있습니다.",
}


def build_watch_point(matched_keywords: list[str], category: str, title: str = "") -> str:
    """13개 세부 주제 우선순위 리스트를 순서대로 확인해서 가장 구체적인 템플릿을 선택.
    어디에도 안 걸리면 카테고리 기본 문구로 폴백한다."""
    topic = _detect_watch_point_topic(matched_keywords, title)
    if topic and topic in WATCH_POINT_TOPIC_TEMPLATES:
        return WATCH_POINT_TOPIC_TEMPLATES[topic]
    return CATEGORY_DEFAULT_WATCH_POINT.get(category, CATEGORY_DEFAULT_WATCH_POINT["MARKET"])


# ==========================================================
# 5. 데이터 로드
# ==========================================================

def load_raw_news() -> list[dict]:
    if not os.path.exists(RAW_NEWS_PATH):
        log("[ERROR] data/raw_news.json 파일이 없습니다. 먼저 collect_news.py를 실행하세요.")
        sys.exit(1)
    with open(RAW_NEWS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("news", [])


def load_existing_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ==========================================================
# 6. 전체 기사 분석
# ==========================================================

def analyze_all_articles(raw_articles: list[dict]) -> list[dict]:
    """모든 raw 기사에 점수/카테고리/요약/템플릿을 부여한 분석 결과 리스트 반환"""
    analyzed = []

    for article in raw_articles:
        title = article.get("title", "")
        description = article.get("description", "")

        score, matched_keywords, category_scores, category_keyword_counts = compute_score(article)
        category = determine_category(category_scores, category_keyword_counts)
        importance = score_to_importance(score)
        summary = build_summary(article)
        why_it_matters = build_why_it_matters(category, matched_keywords, title)
        bmw_insight = build_watch_point(matched_keywords, category, title)
        topic_signals = compute_topic_signals(title, description)

        # brandGroups는 정상적으로는 collect_news.py가 이미 채워서 넘겨준다.
        # 없거나 빈 배열인 경우(legacy 데이터)에 한해서만 즉석으로 계산한다 — collect가
        # 채운 값이 있으면 절대 덮어쓰지 않는다(Source of Truth는 collect_news.py).
        # STEP 9.3: 이 fallback 계산도 news_policy.py의 공통 detect_brand_groups()를
        # 그대로 쓴다 — 더 이상 이 파일에 복제된 별도 함수가 없다.
        brand_groups = article.get("brandGroups")
        if not brand_groups:
            brand_groups = detect_brand_groups(title, description)

        analyzed.append({
            # ---- 원본 필드 그대로 (요청서 1번) ----
            "id": article["id"],
            "title": article["title"],
            "url": article["url"],
            "source": article.get("source", ""),
            "sourceType": article.get("sourceType", ""),
            "sourceGroup": article.get("sourceGroup", ""),
            # STEP 9.1: 브랜드 귀속(brandGroups)은 sourceGroup과 별개로 그대로 통과시킨다.
            # collect_news.py가 채워 넣은 값을 여기서 새로 계산하지 않고 원본을 그대로 보존한다
            # (요청서 원칙: title/url/source/publishedAt과 마찬가지로 collect 단계 데이터를 신뢰).
            "brandGroups": brand_groups,
            "publishedAt": article.get("publishedAt", ""),
            "collectedAt": article.get("collectedAt", ""),
            # ---- 규칙 기반 생성 필드 (요청서 2번) ----
            "category": category,
            "summary": summary,
            "importance": importance,
            "whyItMatters": why_it_matters,
            "bmwInsight": bmw_insight,
            "isTopNews": False,
            "score": score,
            "matchedKeywords": matched_keywords,
            # ---- STEP 7 신규: Intelligence Group 묶기용 내부 계산 필드 (최종 JSON에는 노출 안 함) ----
            "topicTags": topic_signals["topicTags"],
            "segmentTags": topic_signals["segmentTags"],
            "techThemeTags": topic_signals["techThemeTags"],
        })

    # STEP 8: signalThemes(topicTags+segmentTags+techThemeTags 통합, 중복 제거)를
    # 별도로 부여한다. compute_signal_themes()는 딕셔너리가 이미 완성된 뒤
    # 호출해야 하므로 analyzed 리스트가 다 만들어진 다음 한 번 더 순회한다.
    for a in analyzed:
        a["signalThemes"] = compute_signal_themes(a)

    return analyzed


# ==========================================================
# 7. TOP NEWS 선정 (요청서 22, 23번: Diversity + 유사 제목 제거)
# ==========================================================

def normalize_title_for_similarity(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    return t


def is_similar_title(a: str, b: str) -> bool:
    ratio = SequenceMatcher(None, normalize_title_for_similarity(a), normalize_title_for_similarity(b)).ratio()
    return ratio >= TOP_NEWS_TITLE_SIMILARITY_THRESHOLD


def select_top_news(analyzed_articles: list[dict], max_count: int = TOP_NEWS_MAX) -> list[str]:
    """점수 내림차순으로 정렬하며, 그룹당 최대 2개 + 유사 제목 중복 제거"""
    sorted_articles = sorted(analyzed_articles, key=lambda x: x["score"], reverse=True)

    selected: list[dict] = []
    group_counts: dict[str, int] = defaultdict(int)

    for article in sorted_articles:
        if len(selected) >= max_count:
            break

        group = article["sourceGroup"]
        if group_counts[group] >= TOP_NEWS_MAX_PER_GROUP:
            continue

        # 유사 제목 검사 (요청서 23번)
        is_duplicate_topic = any(
            is_similar_title(article["title"], s["title"]) for s in selected
        )
        if is_duplicate_topic:
            continue

        selected.append(article)
        group_counts[group] += 1

    return [a["id"] for a in selected]


def _is_bmw_own_article(article: dict) -> bool:
    """STEP 9.2: BMW 자사(OWN) 판정. brandGroups가 Source of Truth다.

    - brandGroups가 있으면(정상 경로): brandGroups == {"bmw"}(BMW 단독 언급)일 때만 OWN.
      BMW + 다른 브랜드가 함께 언급된 비교/경쟁 기사는 OTHERS로 분류한다(요청 사항: BMW
      단독 포함은 OWN, BMW+타 브랜드 동시 포함은 OTHERS).
    - brandGroups가 아예 없거나 빈 배열인 legacy/비정상 데이터에서는 STEP 9.1 이전과 동일하게
      sourceGroup == "bmw" 기준으로 폴백한다(요청 사항: 기존 동작을 완전히 잃지 않도록 하는
      Legacy Safety Fallback). 즉 brandGroups가 채워져 있으면 항상 그 값을 우선한다."""
    brand_groups = article.get("brandGroups") or []
    if brand_groups:
        return set(brand_groups) == {"bmw"}
    return article.get("sourceGroup") == "bmw"


def select_top_news_split(analyzed_articles: list[dict]) -> tuple[list[str], list[str]]:
    """BMW Motorrad 소속 사용자를 위한 대시보드이므로 TOP NEWS를 둘로 나눈다.

    - 자사(BMW): _is_bmw_own_article()이 OWN으로 판정한 기사 중 점수 상위 5개
    - 타사: 그 외 전체 기사 중 점수 상위 5개 (그룹당 최대 2개, 유사 제목 중복 제거는
      select_top_news의 로직을 그대로 재사용 — 이 다양성 규칙은 요청 사항대로 sourceGroup
      기준을 그대로 유지한다)

    자사 뉴스가 5개보다 적으면 있는 만큼만 반환한다 (요청서 원칙: 억지로 채우지 않음)."""
    bmw_articles = [a for a in analyzed_articles if _is_bmw_own_article(a)]
    bmw_sorted = sorted(bmw_articles, key=lambda x: x["score"], reverse=True)[:TOP_NEWS_MAX]
    bmw_ids = [a["id"] for a in bmw_sorted]

    non_bmw_articles = [a for a in analyzed_articles if not _is_bmw_own_article(a)]
    others_ids = select_top_news(non_bmw_articles, max_count=TOP_NEWS_MAX)

    return bmw_ids, others_ids


# ==========================================================
# 8. Today's Signal (요청서 28, 29번: 빈도 기반, 과장 금지)
# ==========================================================

SIGNAL_KEYWORD_LABELS = {
    "adventure": "Adventure",
    "어드벤처": "Adventure",
    "electric": "전동화",
    "ev": "전동화",
    "전기": "전동화",
    "전동": "전동화",
    "launch": "신규 모델",
    "new model": "신규 모델",
    "unveil": "신규 모델",
    "신모델": "신규 모델",
    "출시": "신규 모델",
    "공개": "신규 모델",
    "market": "시장/판매",
    "sales": "시장/판매",
    "시장": "시장/판매",
    "판매": "시장/판매",
    "connectivity": "Connectivity",
    "커넥티비티": "Connectivity",
    "recall": "리콜/품질 이슈",
    "리콜": "리콜/품질 이슈",
}


def build_daily_signal(analyzed_articles: list[dict]) -> dict | None:
    if not analyzed_articles:
        return None

    label_counter = Counter()
    for article in analyzed_articles:
        matched = set(article.get("matchedKeywords", []))
        for kw, label in SIGNAL_KEYWORD_LABELS.items():
            if kw in matched:
                label_counter[label] += 1

    if not label_counter:
        return None

    top_labels = label_counter.most_common(2)
    if not top_labels:
        return None

    if len(top_labels) == 1:
        title = f"{top_labels[0][0]} 관련 뉴스 증가"
    else:
        title = f"{top_labels[0][0]} 및 {top_labels[1][0]} 관련 뉴스 증가"

    label_summary = ", ".join(f"{label} {count}건" for label, count in top_labels)
    summary = f"오늘 수집된 주요 Motorcycle 뉴스에서는 {label_summary} 등 관련 보도가 상대적으로 많이 확인됩니다."

    return {"title": title, "summary": summary}


# ==========================================================
# 9. Market Intelligence 카드 생성 (요청서 31, 32번)
# ==========================================================

# ==========================================================
# STEP 7: Intelligence Group 묶기
# ==========================================================
# "기사 1건 = Insight 1개"였던 기존 구조를, 같은 시장 흐름으로 볼 수 있는
# 기사들을 하나의 그룹으로 묶어 보여주는 구조로 개선한다.
#
# 그룹핑 조건 (사용자 결정사항 — 요청서 초안보다 엄격화):
#   필수: 같은 Category + 핵심 Topic(topicTags) 1개 이상 일치
#   추가(하나 이상 충족): 같은 Segment / 같은 Technology Theme / 제목 유사도 기준 이상
#   -> NEW_MODEL, MARKET, CUSTOMER처럼 아주 넓은 태그 하나만 겹친다고
#      자동으로 묶이지 않는다 (예: "두카티 스포츠 신모델"과 "혼다 스쿠터 신모델"은
#      둘 다 NEW_MODEL이지만 Segment가 SPORT vs SCOOTER로 다르므로 묶이지 않음).

GROUP_TITLE_SIMILARITY_THRESHOLD = 0.55  # 제목 유사도만으로 묶을 때 기준 (그룹핑용이라 TOP NEWS 중복판정보다 완화)

# Topic 조합 -> Group 제목 템플릿 (요청서 7번 예시 그대로)
GROUP_TITLE_TEMPLATES = [
    (("ADVENTURE", "NEW_MODEL"), "Adventure 신모델 경쟁 확대"),
    (("ELECTRIFICATION", "TECH"), "전동화·기술 적용 확대"),
    (("MARKET", "NEW_MODEL"), "Motorcycle 시장 판매 흐름 변화"),
    (("CUSTOMER", "NEW_MODEL"), "브랜드 Experience·Community 활동 확대"),
]
# 단일 세그먼트/테마 기반 폴백 제목
SEGMENT_GROUP_TITLES = {
    "ADVENTURE": "Adventure Segment 신제품 움직임",
    "TOURING": "Touring·Adventure Experience 관련 움직임",
    "SPORT": "Sport Segment 신제품 움직임",
    "ROADSTER": "Roadster Segment 신제품 움직임",
    "SCOOTER": "Scooter Segment 신제품 움직임",
    "CRUISER": "Cruiser Segment 신제품 움직임",
}
TECH_THEME_GROUP_TITLES = {
    "ELECTRIFICATION": "전동화 기술 적용 확대",
    "ADAS": "ADAS·Safety 기술 적용 확대",
    "CONNECTIVITY": "Connectivity 기술 적용 확대",
    "SAFETY": "Safety 기술 적용 확대",
}


# Intelligence Group 제목 유사도 계산 시 제외할 흔한 단어.
# "new", "launches", "unveils" 같은 범용 동사/형용사가 겹치는 것만으로
# 서로 무관한 기사(예: Sport 신모델 vs Scooter 신모델)가 묶이는 것을 방지한다.
GROUP_SIMILARITY_STOPWORDS = {
    "new", "launches", "launch", "unveils", "unveil", "model", "models",
    "motorcycle", "motorcycles", "reveals", "reveal", "announces", "announce",
    "출시", "공개", "신형", "모델", "새로운", "발표",
}


def _normalize_for_group_similarity(title: str) -> str:
    """그룹핑 전용 정규화: 소문자화 + 특수문자 제거 + 흔한 단어 제외."""
    t = title.lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    words = [w for w in t.split() if w not in GROUP_SIMILARITY_STOPWORDS]
    return " ".join(words)


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize_for_group_similarity(a), _normalize_for_group_similarity(b)).ratio()


def _can_group(article_a: dict, article_b: dict) -> bool:
    """두 기사가 하나의 Intelligence Group으로 묶일 수 있는지 판단.

    필수: 같은 category + topicTags 1개 이상 겹침
    추가(하나 이상): 같은 segmentTags 존재 / 같은 techThemeTags 존재 / 제목 유사도 기준 이상
    """
    if article_a["category"] != article_b["category"]:
        return False

    shared_topics = set(article_a["topicTags"]) & set(article_b["topicTags"])
    if not shared_topics:
        return False

    shared_segments = set(article_a["segmentTags"]) & set(article_b["segmentTags"])
    shared_tech_themes = set(article_a["techThemeTags"]) & set(article_b["techThemeTags"])
    title_sim = _title_similarity(article_a["title"], article_b["title"])

    return bool(shared_segments) or bool(shared_tech_themes) or title_sim >= GROUP_TITLE_SIMILARITY_THRESHOLD


def _group_articles(articles: list[dict]) -> list[list[dict]]:
    """같은 카테고리 기사 리스트를 조건에 맞는 그룹들로 묶는다 (union-find 방식).
    2건 이상 묶인 것만 그룹으로 취급하고, 나머지는 1건짜리 그룹(개별 Insight)으로 유지."""
    n = len(articles)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for i in range(n):
        for j in range(i + 1, n):
            if _can_group(articles[i], articles[j]):
                union(i, j)

    groups_by_root: dict[int, list[dict]] = defaultdict(list)
    for i in range(n):
        groups_by_root[find(i)].append(articles[i])

    return list(groups_by_root.values())


def _build_group_title(articles: list[dict]) -> str:
    """그룹 내 기사들의 공통 topicTags/segmentTags/techThemeTags 조합으로 템플릿 제목을 만든다."""
    if len(articles) == 1:
        return articles[0]["title"]

    common_topics = set(articles[0]["topicTags"])
    common_segments = set(articles[0]["segmentTags"])
    common_tech = set(articles[0]["techThemeTags"])
    for a in articles[1:]:
        common_topics &= set(a["topicTags"])
        common_segments &= set(a["segmentTags"])
        common_tech &= set(a["techThemeTags"])

    for topic_combo, title in GROUP_TITLE_TEMPLATES:
        if all(t in common_topics for t in topic_combo):
            return title

    if common_segments:
        seg = sorted(common_segments)[0]
        if seg in SEGMENT_GROUP_TITLES:
            return SEGMENT_GROUP_TITLES[seg]

    if common_tech:
        tech = sorted(common_tech)[0]
        if tech in TECH_THEME_GROUP_TITLES:
            return TECH_THEME_GROUP_TITLES[tech]

    # 폴백: 카테고리 이름 기반 일반 제목
    category_fallback = {
        "MARKET": "Motorcycle 시장 관련 뉴스 확대",
        "COMPETITOR": "경쟁 브랜드 전략 관련 뉴스 확대",
        "PRODUCT_TECH": "신제품·기술 관련 뉴스 확대",
        "CUSTOMER_TREND": "고객·Trend 관련 뉴스 확대",
    }
    return category_fallback.get(articles[0]["category"], articles[0]["title"])


def _build_group_summary(articles: list[dict]) -> str:
    """실제 기사 수와 브랜드명을 데이터에서 계산해서 간결한 템플릿 문장을 만든다 (AI 생성 금지)."""
    if len(articles) == 1:
        return articles[0]["summary"] or "원문 기사 제목을 기준으로 확인이 필요한 뉴스입니다."

    brand_names = []
    seen = set()
    for a in articles:
        group = a.get("sourceGroup", "")
        # STEP 10.1: global_media(Visordown/ADV Pulse 등 해외 전문매체 수집 채널)도
        # naver/google/kmnews와 동일하게 "매체 채널"이지 "브랜드"가 아니므로 제외한다.
        # 넣지 않으면 "Global Motorcycle Media 등 주요 브랜드에서..." 처럼 매체명이
        # 브랜드명인 것처럼 표시되는 오류가 생긴다.
        if group in SOURCE_GROUP_LABELS and group not in seen and group not in ("naver", "google", "kmnews", "global_media"):
            seen.add(group)
            brand_names.append(SOURCE_GROUP_LABELS[group])

    count = len(articles)
    if brand_names:
        brand_str = ", ".join(brand_names[:3])
        return f"{brand_str} 등 주요 브랜드에서 관련 뉴스 {count}건이 확인되었습니다."
    return f"오늘 수집된 주요 뉴스에서 관련 보도가 {count}건 확인되었습니다."


def _compute_group_impact(articles: list[dict]) -> float:
    """Group의 impact = 최고 importance + 기사 수 보정 (5.0 상한)."""
    max_importance = max(a["importance"] for a in articles)
    count_bonus = min((len(articles) - 1) * 0.1, 0.4)
    return round(min(max_importance + count_bonus, 5.0), 1)


def _build_group_bmw_view(articles: list[dict]) -> str:
    """그룹의 BMW Watch Point는 그룹 내에서 importance가 가장 높은 기사의 Watch Point를 대표로 사용한다."""
    top_article = max(articles, key=lambda a: a["importance"])
    return top_article["bmwInsight"]


def build_market_intelligence(analyzed_articles: list[dict]) -> dict[str, list[dict]]:
    """카테고리별로 관련 기사를 그룹으로 묶어 Intelligence Card를 만든다.
    카테고리당 최대 4개 그룹, importance(그룹 impact) 높은 순으로 정렬한다."""
    by_category: dict[str, list[dict]] = defaultdict(list)
    for a in analyzed_articles:
        by_category[a["category"]].append(a)

    result = {}
    key_map = {"MARKET": "market", "COMPETITOR": "competitor", "PRODUCT_TECH": "productTech", "CUSTOMER_TREND": "customerTrend"}

    for category, key in key_map.items():
        category_articles = by_category.get(category, [])
        groups = _group_articles(category_articles)

        cards = []
        for group in groups:
            cards.append({
                "title": _build_group_title(group),
                "summary": _build_group_summary(group),
                "relatedNewsIds": [a["id"] for a in group],
                "impact": _compute_group_impact(group),
                "bmwView": _build_group_bmw_view(group),
            })

        # impact(그룹 대표 중요도) 높은 순으로 정렬 후 카테고리당 최대 4개 (요청서 11번)
        cards.sort(key=lambda c: c["impact"], reverse=True)
        result[key] = cards[:4]

    return result


# ==========================================================
# 10. Team Brief 생성 (요청서 33, 34번: 템플릿 + 실제 빈도)
# ==========================================================

def build_team_brief(analyzed_articles: list[dict], market_intel: dict) -> dict:
    by_category: dict[str, list[dict]] = defaultdict(list)
    for a in analyzed_articles:
        by_category[a["category"]].append(a)

    market_count = len(by_category.get("MARKET", []))
    competitor_articles = by_category.get("COMPETITOR", [])
    competitor_count = len(competitor_articles)
    product_count = len(by_category.get("PRODUCT_TECH", []))
    customer_count = len(by_category.get("CUSTOMER_TREND", []))

    # 경쟁 브랜드명 추출 (COMPETITOR 카테고리 기사에서 matchedKeywords 중 브랜드만)
    # 브랜드 사전에 영어/한글 키워드가 함께 있으므로, 한글 표기를 우선 채택해 중복 표시를 방지한다.
    BRAND_DISPLAY_NAMES = {
        "ducati": "두카티", "두카티": "두카티",
        "triumph": "트라이엄프", "트라이엄프": "트라이엄프",
        "harley-davidson": "할리데이비슨", "harley davidson": "할리데이비슨", "할리데이비슨": "할리데이비슨", "할리 데이비슨": "할리데이비슨",
        "honda": "혼다", "혼다": "혼다",
        "yamaha": "야마하", "야마하": "야마하",
        "ktm": "KTM",
        "kawasaki": "가와사키", "가와사키": "가와사키",
    }
    brand_names = set()
    for a in competitor_articles:
        for kw in a.get("matchedKeywords", []):
            if kw in BRAND_SCORES and kw not in ("bmw", "bmw motorrad", "비엠더블유 모토라드", "bmw 모토라드"):
                brand_names.add(BRAND_DISPLAY_NAMES.get(kw, kw))
    brand_str = ", ".join(sorted(brand_names)[:3])

    market_text = (
        f"오늘 수집된 시장 관련 뉴스 중 주요 기사 {market_count}건이 확인되었습니다. "
        "주요 Motorcycle 시장의 판매 및 수요 흐름을 확인할 필요가 있습니다."
        if market_count > 0 else "오늘 수집된 뉴스 중 시장 관련 주요 기사는 확인되지 않았습니다."
    )

    if competitor_count > 0 and brand_names:
        competitor_text = (
            f"{brand_str} 등 경쟁 브랜드 관련 뉴스가 {competitor_count}건 확인되었습니다. "
            "신제품 및 Positioning 변화를 중심으로 확인할 필요가 있습니다."
        )
    elif competitor_count > 0:
        competitor_text = (
            f"경쟁 브랜드 관련 뉴스가 {competitor_count}건 확인되었습니다. "
            "신제품 및 Positioning 변화를 중심으로 확인할 필요가 있습니다."
        )
    else:
        competitor_text = "오늘 수집된 뉴스 중 경쟁 브랜드 관련 주요 기사는 확인되지 않았습니다."

    product_text = (
        f"제품 및 기술 관련 뉴스 {product_count}건이 확인되었습니다. "
        "신모델 및 기술 트렌드 변화를 지속적으로 확인할 필요가 있습니다."
        if product_count > 0 else "오늘 수집된 뉴스 중 제품/기술 관련 주요 기사는 확인되지 않았습니다."
    )

    customer_text = (
        f"고객 및 라이딩 트렌드 관련 뉴스 {customer_count}건이 확인되었습니다. "
        "고객 Experience와 Lifestyle 변화를 지속적으로 확인할 필요가 있습니다."
        if customer_count > 0 else "오늘 수집된 뉴스 중 고객 트렌드 관련 주요 기사는 확인되지 않았습니다."
    )

    label_counter = Counter()
    for article in analyzed_articles:
        matched = set(article.get("matchedKeywords", []))
        for kw, label in SIGNAL_KEYWORD_LABELS.items():
            if kw in matched:
                label_counter[label] += 1

    top_labels = [label for label, _ in label_counter.most_common(3)]
    if top_labels:
        today_insight = f"오늘은 {', '.join(top_labels)} 관련 뉴스 비중이 상대적으로 높게 나타났습니다."
    else:
        today_insight = "오늘 수집된 뉴스에서는 특별히 두드러진 주제 편중은 확인되지 않았습니다."

    return {
        "market": market_text,
        "competitor": competitor_text,
        "productTech": product_text,
        "customerTrend": customer_text,
        "todayInsight": today_insight,
    }


# ==========================================================
# 11. Validation (요청서 45번)
# ==========================================================

def validate_analyzed_articles(analyzed_articles: list[dict], raw_by_id: dict[str, dict]) -> bool:
    for a in analyzed_articles:
        if not a.get("url"):
            log(f"[검증 실패] URL 없음: {a.get('id')}")
            return False
        if not a.get("title"):
            log(f"[검증 실패] title 없음: {a.get('id')}")
            return False
        if a.get("sourceGroup") not in SOURCE_GROUP_LABELS:
            log(f"[검증 실패] 허용되지 않는 sourceGroup: {a.get('sourceGroup')}")
            return False
        if a.get("category") not in VALID_CATEGORIES:
            log(f"[검증 실패] 허용되지 않는 category: {a.get('category')}")
            return False
        if not (1.0 <= a.get("importance", 0) <= 5.0):
            log(f"[검증 실패] importance 범위 초과: {a.get('importance')}")
            return False

        original = raw_by_id.get(a["id"])
        if original is None:
            log(f"[검증 실패] raw_news.json에 없는 ID: {a['id']}")
            return False
        if a["url"] != original["url"]:
            log(f"[검증 실패] URL이 원본과 다름: {a['id']}")
            return False

    bmw_top_count = sum(1 for a in analyzed_articles if a.get("topNewsGroup") == "own")
    others_top_count = sum(1 for a in analyzed_articles if a.get("topNewsGroup") == "others")
    if bmw_top_count > TOP_NEWS_MAX:
        log(f"[검증 실패] BMW 자사 TOP NEWS가 {TOP_NEWS_MAX}개를 초과합니다: {bmw_top_count}개")
        return False
    if others_top_count > TOP_NEWS_MAX:
        log(f"[검증 실패] 타사 TOP NEWS가 {TOP_NEWS_MAX}개를 초과합니다: {others_top_count}개")
        return False

    return True


# ==========================================================
# 12. news.json / insights.json 생성
# ==========================================================

def build_news_json(analyzed_articles: list[dict], bmw_top_ids: list[str], others_top_ids: list[str], existing_news: dict | None) -> dict:
    now_kst = datetime.now(timezone(timedelta(hours=9)))

    final_news = []
    bmw_rank = {tid: i + 1 for i, tid in enumerate(bmw_top_ids)}
    others_rank = {tid: i + 1 for i, tid in enumerate(others_top_ids)}
    top_ids = set(bmw_top_ids) | set(others_top_ids)

    for a in analyzed_articles:
        item = {
            "id": a["id"],
            "title": a["title"],
            "url": a["url"],
            "source": a["source"],
            "sourceType": a["sourceType"],
            "sourceGroup": a["sourceGroup"],
            # STEP 9.1: news.json에도 brandGroups를 노출한다(신규 필드 추가만, 기존 키는 무변경).
            # SOURCE MONITOR(script.js)는 여전히 sourceGroup만 읽으므로 화면 동작에는 영향 없다.
            "brandGroups": a.get("brandGroups", []),
            "publishedAt": a["publishedAt"],
            "category": a["category"],
            "summary": a["summary"],
            "importance": a["importance"],
            "whyItMatters": a["whyItMatters"],
            "bmwInsight": a["bmwInsight"],
            "isTopNews": a["id"] in top_ids,
            "topNewsGroup": a.get("topNewsGroup"),
        }
        if a["id"] in bmw_rank:
            item["rank"] = bmw_rank[a["id"]]
        elif a["id"] in others_rank:
            item["rank"] = others_rank[a["id"]]
        final_news.append(item)

    meta = {}
    if existing_news and "meta" in existing_news:
        meta = dict(existing_news["meta"])

    meta["date"] = now_kst.strftime("%Y-%m-%d")
    meta["dayLabel"] = now_kst.strftime("%A").upper()
    meta["lastUpdated"] = now_kst.strftime("%I:%M %p")
    meta["lastUpdatedISO"] = now_kst.isoformat()
    meta["isSampleData"] = False

    return {
        "meta": meta,
        "marketIntelligence": existing_news.get("marketIntelligence", {}) if existing_news else {},
        "news": final_news,
    }


def build_insights_json(daily_signal: dict | None, market_intel: dict, team_brief: dict) -> dict:
    now_kst = datetime.now(timezone(timedelta(hours=9)))

    return {
        "date": now_kst.strftime("%Y-%m-%d"),
        "lastAnalyzedAt": now_kst.isoformat(),
        "dailySignal": daily_signal,
        "market": market_intel.get("market", []),
        "competitor": market_intel.get("competitor", []),
        "productTech": market_intel.get("productTech", []),
        "customerTrend": market_intel.get("customerTrend", []),
        "teamBrief": team_brief,
    }


def apply_insights_to_news_meta(news_json: dict, insights: dict) -> dict:
    if insights.get("dailySignal"):
        news_json["meta"]["todaySignal"] = {
            "headline": insights["dailySignal"].get("title", ""),
            "description": insights["dailySignal"].get("summary", ""),
        }

    news_json["marketIntelligence"] = {
        "market": _to_intel_cards(insights.get("market", [])),
        "competitor": _to_intel_cards(insights.get("competitor", [])),
        "productTech": _to_intel_cards(insights.get("productTech", [])),
        "customerTrend": _to_intel_cards(insights.get("customerTrend", [])),
    }

    return news_json


def _to_intel_cards(items: list[dict]) -> list[dict]:
    cards = []
    for it in items:
        cards.append({
            "title": it.get("title", ""),
            "description": it.get("summary") or "",
            "relatedNewsCount": len(it.get("relatedNewsIds", [])),
            "impact": _impact_label(it.get("impact", 3.0)),
            "bmwNote": it.get("bmwView", ""),
        })
    return cards


def _impact_label(importance: float) -> str:
    if importance >= 4.0:
        return "High"
    if importance >= 2.5:
        return "Medium"
    return "Low"


def save_json_atomic(data: dict, path: str):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


# ==========================================================
# STEP 8: Yesterday/Today 변화 감지 + Brand Intelligence Summary
# ==========================================================
# 설계 승인사항(STEP8-8) 반영:
# 1) History는 날짜 1개 파일이 아니라 "실행별 Snapshot"으로 저장한다
#    (하루 여러 번 실행돼도 각 실행 시점의 기록이 덮어써지지 않도록).
# 2) Snapshot을 합칠 때는 article id 기준으로 반드시 중복 제거한다.
# 3) topicTags/segmentTags/techThemeTags를 변화 감지에서는 "signalThemes"라는
#    하나의 통합 개념으로 합쳐서 다룬다 (중복 제거).
# 4) Signal 비교는 태그 "조합 전체"가 아니라 "개별 Theme" 단위로 한다.
#    (예: 어제 ADVENTURE만 있었고 오늘 ADVENTURE+NEW_MODEL이면
#     ADVENTURE=CONTINUING, NEW_MODEL=NEW로 각각 판정)
# 5) History가 전혀 없는 최초 실행은 NORMAL이 아니라 BASELINE으로 표시한다.
# 6) Global(전체 시장) Signal과 Brand별 Signal은 서로 다른 지표를 쓴다.
#    - Global: 기사 수 + 관련 브랜드 수 + 실제 Source 다양성
#    - Brand:  기사 수 + 실제 Source 다양성 + Importance 변화
#    ("관련 브랜드 수"는 브랜드 하나만 보는 단위에서는 의미가 없어 제외)
# 7) Activity 계산도 실제 sourceGroup이 아니라 실제 source(언론사)/domain
#    다양성을 사용한다 (sourceGroup은 브랜드 단위에서는 항상 1종류이므로 무의미).


def _extract_domain(url: str) -> str:
    """History 저장 및 실제 Source 다양성 계산용. 전체 URL은 저장하지 않고
    도메인만 남긴다 (요청서 STEP8-8 8번: 필요하지 않은 값은 저장하지 않음)."""
    try:
        from urllib.parse import urlsplit
        return urlsplit(url).netloc.lower()
    except Exception:
        return ""


def compute_signal_themes(article: dict) -> list[str]:
    """topicTags + segmentTags + techThemeTags를 하나로 합치고 중복을 제거한
    signalThemes를 계산한다 (요청서 STEP8-8 3번). ELECTRIFICATION처럼 여러
    태그 그룹에 동시에 존재하는 값은 한 번만 남는다."""
    combined = (
        article.get("topicTags", [])
        + article.get("segmentTags", [])
        + article.get("techThemeTags", [])
    )
    return list(dict.fromkeys(combined))  # 순서를 보존하며 중복 제거


def build_history_snapshot(analyzed_articles: list[dict]) -> dict:
    """History Snapshot에 저장할 최소 데이터를 구성한다 (요청서 STEP8-8 8번).
    기사 본문/summary/whyItMatters/bmwInsight/전체 URL은 저장하지 않는다.
    비교에 필요한 것만: id, sourceGroup, source(언론사명), domain, category,
    signalThemes, importance, publishedAt.

    STEP 9.1: brandGroups도 함께 저장한다. build_brand_summary()가 이제 Previous(직전
    History Snapshot) 비교에도 sourceGroup이 아닌 brandGroups를 쓰므로, 이 필드가
    Snapshot에 없으면 previous 집계가 항상 빈 값이 되어버린다."""
    now_kst = datetime.now(timezone(timedelta(hours=9)))
    articles = []
    for a in analyzed_articles:
        articles.append({
            "id": a["id"],
            "sourceGroup": a["sourceGroup"],
            "brandGroups": a.get("brandGroups", []),
            "source": a.get("source", ""),
            "domain": _extract_domain(a.get("url", "")),
            "category": a["category"],
            "signalThemes": compute_signal_themes(a),
            "importance": a["importance"],
            "publishedAt": a.get("publishedAt", ""),
        })
    return {
        "savedAtISO": now_kst.isoformat(),
        "articles": articles,
    }


def save_history_snapshot(snapshot: dict) -> str | None:
    """실행별 Snapshot 파일로 저장한다 (요청서 STEP8-8 1번: 날짜 1개 파일이 아니라
    실행별 파일, 예: 2026-08-14_0930.json). 저장 실패해도 전체 파이프라인은
    계속 진행되어야 하므로 예외를 삼키고 None을 반환한다."""
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        now_kst = datetime.now(timezone(timedelta(hours=9)))
        filename = now_kst.strftime("%Y-%m-%d_%H%M") + ".json"
        path = os.path.join(HISTORY_DIR, filename)
        save_json_atomic(snapshot, path)
        return path
    except Exception as e:
        log(f"[History 경고] Snapshot 저장 실패 (분석 결과에는 영향 없음): {e}")
        return None


def load_history_snapshots_in_window(hours_ago_start: float, hours_ago_end: float) -> list[dict]:
    """HISTORY_DIR의 모든 Snapshot 파일 중, 저장 시각이
    [now - hours_ago_end, now - hours_ago_start) 구간에 속하는 것만 골라 로드한다.
    파일이 없거나 폴더 자체가 없으면 빈 리스트를 반환한다 (최초 실행 안전 처리)."""
    if not os.path.isdir(HISTORY_DIR):
        return []

    now_kst = datetime.now(timezone(timedelta(hours=9)))
    window_start = now_kst - timedelta(hours=hours_ago_end)
    window_end = now_kst - timedelta(hours=hours_ago_start)

    snapshots = []
    for filename in os.listdir(HISTORY_DIR):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(HISTORY_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved_at = datetime.fromisoformat(data["savedAtISO"])
            if window_start <= saved_at < window_end:
                snapshots.append(data)
        except Exception:
            continue  # 손상된/형식이 다른 파일은 조용히 건너뜀

    return snapshots


def merge_snapshot_articles(snapshots: list[dict]) -> list[dict]:
    """여러 Snapshot의 articles를 합치되, 같은 기사가 여러 Snapshot(08시/13시/18시 등)에
    중복으로 들어있을 수 있으므로 반드시 article id 기준으로 중복 제거한다
    (요청서 STEP8-8 1번 핵심 요구사항). 가장 최근 Snapshot에 있는 버전을 우선한다."""
    by_id: dict[str, dict] = {}
    # savedAtISO 오름차순으로 처리해서, 나중 것(최신)이 먼저 것을 덮어쓰게 한다
    sorted_snapshots = sorted(snapshots, key=lambda s: s.get("savedAtISO", ""))
    for snap in sorted_snapshots:
        for article in snap.get("articles", []):
            by_id[article["id"]] = article
    return list(by_id.values())


def cleanup_old_history(retention_days: int = HISTORY_RETENTION_DAYS) -> int:
    """retention_days를 초과한 History Snapshot만 삭제한다 (요청서 STEP8-8 9번:
    현재 생성 중인/오늘 필요한 Snapshot을 실수로 삭제하지 않도록 파일의 실제
    savedAtISO 시각을 기준으로 판단한다. 파일명 파싱이 아니라 내용을 읽어서
    판단하므로 더 안전하다). 삭제된 파일 개수를 반환한다."""
    if not os.path.isdir(HISTORY_DIR):
        return 0

    now_kst = datetime.now(timezone(timedelta(hours=9)))
    cutoff = now_kst - timedelta(days=retention_days)
    deleted = 0

    for filename in os.listdir(HISTORY_DIR):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(HISTORY_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved_at = datetime.fromisoformat(data["savedAtISO"])
            if saved_at < cutoff:
                os.remove(path)
                deleted += 1
        except Exception:
            continue  # 읽을 수 없는 파일은 안전하게 건드리지 않고 넘어감

    return deleted


# ---- Theme 단위 상태 판정 (요청서 STEP8-8 4번) ----

SIGNAL_STATE_PRIORITY = {"RISING": 3, "NEW": 2, "CONTINUING": 1, "NORMAL": 0}


def _determine_theme_state(theme: str, current_themes_count: dict, previous_themes_count: dict,
                            current_total_articles: int, previous_total_articles: int) -> str:
    """개별 Theme 하나의 상태(NEW/CONTINUING/RISING/NORMAL)를 판정한다.
    RISING 조건(요청서 STEP8-8 6번 Global 기준 응용, Theme 단위로 적용):
      해당 Theme 기사 수가 Previous 대비 2배 이상 증가 + Current 최소 2건 이상
      (기사 1건->2건 정도의 작은 변화만으로는 RISING 처리하지 않음, 원 요청서 3-3번)
    """
    cur_count = current_themes_count.get(theme, 0)
    prev_count = previous_themes_count.get(theme, 0)

    if prev_count == 0 and cur_count > 0:
        return "NEW"

    if prev_count > 0 and cur_count > 0:
        if cur_count >= max(prev_count * 2, 1) and cur_count >= 2 and cur_count > prev_count:
            return "RISING"
        return "CONTINUING"

    return "NORMAL"


def compute_global_topic_signals(current_articles: list[dict], previous_articles: list[dict],
                                  has_history: bool) -> dict[str, dict]:
    """전체 시장 기준(Global) Theme별 상태를 계산한다. TODAY'S SIGNAL 고도화(요청서 14번)에
    사용할 수 있도록 별도 함수로 분리한다."""
    if not has_history:
        return {}

    current_theme_articles: dict[str, list[dict]] = defaultdict(list)
    for a in current_articles:
        for theme in a.get("signalThemes", []):
            current_theme_articles[theme].append(a)

    previous_theme_articles: dict[str, list[dict]] = defaultdict(list)
    for a in previous_articles:
        for theme in a.get("signalThemes", []):
            previous_theme_articles[theme].append(a)

    current_counts = {t: len(v) for t, v in current_theme_articles.items()}
    previous_counts = {t: len(v) for t, v in previous_theme_articles.items()}

    result = {}
    all_themes = set(current_counts) | set(previous_counts)
    for theme in all_themes:
        state = _determine_theme_state(theme, current_counts, previous_counts, len(current_articles), len(previous_articles))
        cur_arts = current_theme_articles.get(theme, [])
        brands = set(a.get("sourceGroup") for a in cur_arts if a.get("sourceGroup") in BRAND_SOURCE_GROUPS)
        result[theme] = {
            "state": state,
            "currentCount": current_counts.get(theme, 0),
            "previousCount": previous_counts.get(theme, 0),
            "brands": sorted(brands),
        }
    return result


# ---- Brand Summary (요청서 6~10번 + STEP8-8 보완) ----

BRAND_SOURCE_GROUPS = ["bmw", "ducati", "triumph", "harley", "honda", "yamaha"]


def _brand_signal_from_themes(theme_states: dict[str, str]) -> str:
    """개별 Theme 상태들 중 우선순위가 가장 높은 것을 Brand 대표 Signal로 채택한다
    (요청서 STEP8-8 4번: RISING > NEW > CONTINUING > NORMAL)."""
    if not theme_states:
        return "NORMAL"
    best = max(theme_states.values(), key=lambda s: SIGNAL_STATE_PRIORITY.get(s, 0))
    return best


def _compute_brand_activity(article_count: int, source_diversity: int, theme_diversity: int,
                             has_new_theme: bool, has_rising_theme: bool) -> str:
    """Brand Activity를 계산한다 (요청서 STEP8-8 7번: sourceGroup이 아니라 실제
    source/domain 다양성, 기사 1건 차이로 등급이 자주 안 바뀌도록 계단식 설계)."""
    if article_count == 0:
        return "LOW"

    score = article_count * 2 + theme_diversity * 3 + source_diversity * 2
    if has_new_theme:
        score += 5
    if has_rising_theme:
        score += 8

    if score >= 15:
        return "HIGH"
    if score >= 6:
        return "MEDIUM"
    return "LOW"


def build_brand_summary(analyzed_articles: list[dict]) -> dict:
    """브랜드 6개(BMW/Ducati/Triumph/Harley/Honda/Yamaha)에 대해 오늘의 활동
    요약을 계산한다. History가 없으면(최초 실행) 모든 브랜드가 BASELINE으로
    표시된다 (요청서 STEP8-8 5번). 설명 문장은 절대 생성하지 않고, 실제
    데이터에서 계산 가능한 값만 담는다 (요청서 8번)."""

    current_history_snapshots = load_history_snapshots_in_window(0, HISTORY_CURRENT_WINDOW_HOURS)
    previous_history_snapshots = load_history_snapshots_in_window(
        HISTORY_CURRENT_WINDOW_HOURS, HISTORY_PREVIOUS_WINDOW_HOURS
    )
    has_history = bool(current_history_snapshots or previous_history_snapshots)

    # Current는 "이번 실행 결과"를 그대로 쓴다 (History에 방금 저장한 것과 동일 데이터라
    # 별도로 다시 로드할 필요가 없다). Previous는 History Snapshot에서만 가져온다.
    current_by_id = {a["id"]: a for a in analyzed_articles}
    # 혹시 같은 Current Window 안에 이전 실행(같은 날 이전 시각) Snapshot이 있으면
    # 함께 합쳐서(중복 제거) 더 완전한 Current 집합을 만든다 (요청서 STEP8-8 1번).
    if current_history_snapshots:
        merged_current_history = merge_snapshot_articles(current_history_snapshots)
        for a in merged_current_history:
            current_by_id.setdefault(a["id"], a)
    current_articles = list(current_by_id.values())

    previous_articles = merge_snapshot_articles(previous_history_snapshots)

    summary = {}
    for brand in BRAND_SOURCE_GROUPS:
        # STEP 9.1: sourceGroup(수집 채널) 대신 brandGroups(실제 브랜드 귀속)로 판정한다.
        # sourceGroup은 SOURCE MONITOR 등 기존 화면에서 계속 쓰이므로 절대 값 자체를 바꾸지
        # 않고, 여기 Brand Pulse 집계 로직만 brandGroups 기준으로 전환한다. brandGroups가
        # 없는(과거) 데이터는 collect_news.py의 merge_news()가 재실행 시 채워 넣지만(migration),
        # 혹시 아직 채워지지 않은 값이 있어도 .get(...) or []로 안전하게 빈 배열 처리한다.
        brand_current = [a for a in current_articles if brand in (a.get("brandGroups") or [])]
        brand_previous = [a for a in previous_articles if brand in (a.get("brandGroups") or [])]

        news_count = len(brand_current)

        if news_count == 0:
            summary[brand] = {
                "newsCount": 0,
                "primaryTheme": None,
                "secondaryTheme": None,
                "signal": "NO_SIGNIFICANT_UPDATE",
                "activity": "LOW",
                "topArticleTitle": None,
                "topArticleUrl": None,
                "signalDetails": {},
            }
            continue

        # Primary/Secondary Theme: 가장 자주 등장한 signalThemes 상위 2개 (요청서 8번 예시)
        theme_counter = Counter()
        for a in brand_current:
            for theme in a.get("signalThemes", []):
                theme_counter[theme] += 1
        top_themes = [t for t, _ in theme_counter.most_common(2)]
        primary_theme = top_themes[0] if len(top_themes) >= 1 else None
        secondary_theme = top_themes[1] if len(top_themes) >= 2 else None

        # 대표 기사: importance가 가장 높은 것 (실제 analyzed_articles에만 title/url이 있음)
        top_article = max(brand_current, key=lambda a: a.get("importance", 0)) if brand_current else None
        top_title = top_article.get("title") if top_article else None
        top_url = top_article.get("url") if top_article else None

        # 실제 Source 다양성: sourceGroup이 아니라 실제 source(언론사)명 종류수 (요청서 STEP8-8 2번)
        source_diversity = len(set(a.get("source", "") for a in brand_current if a.get("source")))

        if not has_history:
            # 최초 실행: 비교할 Previous가 전혀 없으므로 BASELINE (요청서 STEP8-8 5번)
            signal = "BASELINE"
            signal_details = {}
        else:
            cur_theme_counts = Counter()
            for a in brand_current:
                for t in a.get("signalThemes", []):
                    cur_theme_counts[t] += 1
            prev_theme_counts = Counter()
            for a in brand_previous:
                for t in a.get("signalThemes", []):
                    prev_theme_counts[t] += 1

            signal_details = {}
            for theme in set(cur_theme_counts) | set(prev_theme_counts):
                signal_details[theme] = _determine_theme_state(
                    theme, cur_theme_counts, prev_theme_counts, len(brand_current), len(brand_previous)
                )
            signal = _brand_signal_from_themes(signal_details)

        has_new = any(s == "NEW" for s in (signal_details or {}).values())
        has_rising = any(s == "RISING" for s in (signal_details or {}).values())
        activity = _compute_brand_activity(
            article_count=news_count,
            source_diversity=source_diversity,
            theme_diversity=len(theme_counter),
            has_new_theme=has_new,
            has_rising_theme=has_rising,
        )

        summary[brand] = {
            "newsCount": news_count,
            "primaryTheme": primary_theme,
            "secondaryTheme": secondary_theme,
            "signal": signal,
            "activity": activity,
            "topArticleTitle": top_title,
            "topArticleUrl": top_url,
            "signalDetails": signal_details,
        }

    return summary


def main():
    log("=" * 60)
    log("[MOTORRAD PULSE FREE INTELLIGENCE]")
    log("=" * 60)

    raw_articles = load_raw_news()
    log(f"\nRaw News: {len(raw_articles)}")

    # 이중 안전장치: collect_news.py에서 걸러진 것으로 예상되지만, 혹시 남아있으면 여기서 한 번 더 제거
    before_filter = len(raw_articles)
    raw_articles = [a for a in raw_articles if is_trusted_domain(a.get("url", ""))]
    if len(raw_articles) < before_filter:
        log(f"[안전장치] 신뢰 화이트리스트에 없는 기사 {before_filter - len(raw_articles)}건 추가 제거")

    before_context_filter = len(raw_articles)
    raw_articles = [
        a for a in raw_articles
        if has_motorcycle_context(a.get("title", ""), a.get("description", ""), a.get("sourceGroup"))
    ]
    if len(raw_articles) < before_context_filter:
        log(f"[안전장치] 이륜차와 무관한(자동차 등) 기사 {before_context_filter - len(raw_articles)}건 추가 제거")

    if not raw_articles:
        log("\n[안내] 분석할 뉴스가 없습니다. 기존 news.json / insights.json은 변경하지 않습니다.")
        return

    analyzed_articles = analyze_all_articles(raw_articles)
    log(f"Analyzed: {len(analyzed_articles)}")

    bmw_top_ids, others_top_ids = select_top_news_split(analyzed_articles)
    top_ids = set(bmw_top_ids) | set(others_top_ids)
    for a in analyzed_articles:
        a["isTopNews"] = a["id"] in top_ids
        if a["id"] in bmw_top_ids:
            a["topNewsGroup"] = "own"
        elif a["id"] in others_top_ids:
            a["topNewsGroup"] = "others"
        else:
            a["topNewsGroup"] = None

    # TOP NEWS로 선정된 기사 중 요약이 비어있는 것만 원문 페이지에서 보강 시도.
    # 최대 10개(자사5+타사5)뿐이라 전체 실행 시간에 미치는 영향이 적고, 실패해도 그냥 요약 없이 진행된다.
    top_news_without_summary = [a for a in analyzed_articles if a["id"] in top_ids and not a["summary"]]
    if top_news_without_summary:
        log(f"\nEnriching {len(top_news_without_summary)} TOP NEWS summaries from article pages...")
        for a in top_news_without_summary:
            enriched = enrich_summary_from_article_page(a["url"])
            if enriched:
                a["summary"] = enriched
                log(f"  [보강 성공] {a['title'][:40]}")
            else:
                log(f"  [보강 실패, 요약 없이 진행] {a['title'][:40]}")

    raw_by_id = {a["id"]: a for a in raw_articles}
    if not validate_analyzed_articles(analyzed_articles, raw_by_id):
        log("\n[안전장치 작동] 검증 실패로 기존 news.json / insights.json을 유지합니다.")
        sys.exit(1)

    # ---- 카테고리 집계 로그 ----
    cat_counts = Counter(a["category"] for a in analyzed_articles)
    log("\nCategories")
    log(f"MARKET: {cat_counts.get('MARKET', 0)}")
    log(f"COMPETITOR: {cat_counts.get('COMPETITOR', 0)}")
    log(f"PRODUCT & TECH: {cat_counts.get('PRODUCT_TECH', 0)}")
    log(f"CUSTOMER & TREND: {cat_counts.get('CUSTOMER_TREND', 0)}")

    # ---- TOP NEWS 로그 ----
    log("\nTOP NEWS (BMW 자사)")
    bmw_top_sorted = [a for a in analyzed_articles if a["id"] in bmw_top_ids]
    bmw_top_sorted.sort(key=lambda x: x["score"], reverse=True)
    for i, a in enumerate(bmw_top_sorted, 1):
        log(f"{i}. {a['title'][:60]}")

    log("\nTOP NEWS (타사/업계)")
    others_top_sorted = [a for a in analyzed_articles if a["id"] in others_top_ids]
    others_top_sorted.sort(key=lambda x: x["score"], reverse=True)
    for i, a in enumerate(others_top_sorted, 1):
        log(f"{i}. [{a['sourceGroup']}] {a['title'][:60]}")

    # ---- 키워드 빈도 로그 ----
    all_keywords = Counter()
    for a in analyzed_articles:
        for kw in a.get("matchedKeywords", []):
            all_keywords[kw] += 1
    top_keywords = all_keywords.most_common(5)
    if top_keywords:
        log("\nTop Keywords")
        for kw, cnt in top_keywords:
            log(f"{kw.title()}: {cnt}")

    # ---- insights 생성 ----
    daily_signal = build_daily_signal(analyzed_articles)
    market_intel = build_market_intelligence(analyzed_articles)
    team_brief = build_team_brief(analyzed_articles, market_intel)

    log("\nInsights")
    log(f"MARKET: {len(market_intel.get('market', []))}")
    log(f"COMPETITOR: {len(market_intel.get('competitor', []))}")
    log(f"PRODUCT & TECH: {len(market_intel.get('productTech', []))}")
    log(f"CUSTOMER & TREND: {len(market_intel.get('customerTrend', []))}")

    # ---- STEP 8: Brand Intelligence Summary ----
    # 중요: 반드시 History Snapshot을 "저장하기 전"에 계산해야 한다.
    # build_brand_summary()는 기존에 쌓여있던 History를 Previous/Current 비교에 사용하는데,
    # 만약 이번 실행 결과를 먼저 저장해버리면 그 Snapshot이 Current Window 안에서
    # 자기 자신과 또 겹쳐 집계될 위험이 있다. 저장은 항상 이 계산이 끝난 다음이다.
    brand_summary = build_brand_summary(analyzed_articles)
    log("\nBrand Summary")
    for brand, info in brand_summary.items():
        log(f"{brand.upper()}: {info['newsCount']} news, signal={info['signal']}, activity={info['activity']}")

    # ---- 저장 ----
    existing_news = load_existing_json(NEWS_PATH)

    news_json = build_news_json(analyzed_articles, bmw_top_ids, others_top_ids, existing_news)
    insights_json = build_insights_json(daily_signal, market_intel, team_brief)
    news_json = apply_insights_to_news_meta(news_json, insights_json)

    # brandSummary는 news.json의 meta 아래 새 필드로만 추가한다 (요청서 STEP8-A 확인사항 H:
    # 기존 meta/marketIntelligence/news 키는 전혀 건드리지 않아 Backward Compatible).
    news_json["meta"]["brandSummary"] = brand_summary

    save_json_atomic(news_json, NEWS_PATH)
    log("\nnews.json: UPDATED")

    save_json_atomic(insights_json, INSIGHTS_PATH)
    log("insights.json: UPDATED")

    # ---- STEP 8: History Snapshot 저장 + 30일 정리 (요청서 STEP8-8 1, 9번) ----
    # 이번 실행 결과를 실행별 Snapshot 파일로 저장한다 (날짜 1개 파일이 아니라
    # 2026-08-14_0930.json 형태). 저장 실패해도 news.json/insights.json은 이미
    # 저장이 끝난 뒤라 전체 파이프라인에 영향이 없다.
    snapshot = build_history_snapshot(analyzed_articles)
    snapshot_path = save_history_snapshot(snapshot)
    if snapshot_path:
        log(f"\nHistory snapshot saved: {os.path.basename(snapshot_path)}")

    deleted_count = cleanup_old_history()
    if deleted_count:
        log(f"History cleanup: removed {deleted_count} snapshot(s) older than {HISTORY_RETENTION_DAYS} days")

    log("\nAI API COST: $0")
    log("External AI API: NOT USED")
    log("\n완료.")


if __name__ == "__main__":
    main()
