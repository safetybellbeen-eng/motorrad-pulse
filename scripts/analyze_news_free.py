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

# ==========================================================
# 설정
# ==========================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
RAW_NEWS_PATH = os.path.join(DATA_DIR, "raw_news.json")
NEWS_PATH = os.path.join(DATA_DIR, "news.json")
INSIGHTS_PATH = os.path.join(DATA_DIR, "insights.json")

TOP_NEWS_MAX = 5
TOP_NEWS_MAX_PER_GROUP = 2          # 요청서 22번: 동일 sourceGroup 최대 2개
TOP_NEWS_TITLE_SIMILARITY_THRESHOLD = 0.75  # 요청서 23번: 유사 제목 중복 제한

VALID_CATEGORIES = {"MARKET", "COMPETITOR", "PRODUCT_TECH", "CUSTOMER_TREND"}

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


def log(msg: str):
    print(msg, flush=True)


# ==========================================================
# 1. 점수 체계 (요청서 5~14번)
# ==========================================================

BRAND_SCORES = {
    "bmw motorrad": 25, "bmw": 25, "비엠더블유 모토라드": 25, "bmw 모토라드": 25,
    "ducati": 16, "두카티": 16,
    "triumph": 16, "트라이엄프": 16,
    "harley-davidson": 14, "harley davidson": 14, "할리데이비슨": 14, "할리 데이비슨": 14,
    "honda": 12, "혼다": 12,
    "yamaha": 12, "야마하": 12,
    "ktm": 12,
    "kawasaki": 10, "가와사키": 10,
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
    "launch": 10, "unveil": 10, "new model": 10, "gs": 10,
    "new motorcycle": 9, "model year": 7, "update": 6, "facelift": 6,
    "concept": 7, "prototype": 7, "adventure": 8, "touring": 6,
    "roadster": 5, "sport": 5, "scooter": 5,
    "출시": 10, "공개": 10, "신모델": 10, "신형": 9,
    "새로운": 6, "부분변경": 6, "컨셉": 7, "프로토타입": 7,
    "어드벤처": 8, "투어링": 6, "스쿠터": 5, "신차": 9,
}

TECH_KEYWORD_SCORES = {
    "electric": 10, "ev": 9, "adas": 10, "battery": 8, "radar": 8,
    "connectivity": 8, "abs": 6, "software": 6, "navigation": 5,
    "charging": 6, "hybrid": 6, "safety": 6, "ai": 5, "sensor": 5,
    "engine": 5,
    "전기": 10, "전동": 10, "배터리": 8, "레이더": 8,
    "커넥티비티": 8, "소프트웨어": 6, "내비게이션": 5,
    "충전": 6, "하이브리드": 6, "안전": 6, "센서": 5,
    "엔진": 5, "자율주행": 8,
}

CUSTOMER_KEYWORD_SCORES = {
    "rider": 6, "community": 6, "lifestyle": 6, "experience": 6,
    "customer": 6, "generation": 5, "adventure travel": 7,
    "social media": 5, "women riders": 6, "young riders": 6,
    "urban mobility": 6,
    "라이더": 6, "커뮤니티": 6, "라이프스타일": 6, "경험": 5,
    "고객": 6, "세대": 5, "여성 라이더": 6, "젊은 라이더": 6,
    "도심형 모빌리티": 6, "동호회": 6, "투어": 5,
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


def match_keywords(text: str, keyword_scores: dict[str, int]) -> tuple[int, list[str]]:
    """요청서 13번: 같은 키워드가 여러 번 나와도 한 번만 가산. 존재 여부 기준."""
    text_lower = text.lower()
    total = 0
    matched = []
    for kw, score in keyword_scores.items():
        if kw in text_lower:
            total += score
            matched.append(kw)
    return total, matched


def compute_score(article: dict) -> tuple[int, list[str], dict[str, int]]:
    """
    기사 하나에 대한 총점, 매칭된 키워드 전체 목록, 카테고리별 점수를 계산한다.
    반환: (총점 0~100, matchedKeywords, category별 점수 dict)
    """
    text = f"{article.get('title', '')} {article.get('description', '')}"

    brand_score, brand_kw = match_keywords(text, BRAND_SCORES)
    market_score, market_kw = match_keywords(text, MARKET_KEYWORD_SCORES)
    product_score, product_kw = match_keywords(text, PRODUCT_KEYWORD_SCORES)
    tech_score, tech_kw = match_keywords(text, TECH_KEYWORD_SCORES)
    customer_score, customer_kw = match_keywords(text, CUSTOMER_KEYWORD_SCORES)
    event_score, event_kw = match_keywords(text, EVENT_KEYWORD_SCORES)

    fresh_score = freshness_score(article.get("publishedAt"))
    source_score = SOURCE_TYPE_SCORES.get(article.get("sourceType", ""), 5)

    total = brand_score + market_score + product_score + tech_score + customer_score + event_score + fresh_score + source_score
    total = max(0, min(100, total))  # 요청서 14번: 0~100 clamp

    all_matched = list(dict.fromkeys(brand_kw + market_kw + product_kw + tech_kw + customer_kw + event_kw))

    category_scores = {
        "MARKET": market_score,
        "COMPETITOR": brand_score,  # 브랜드(경쟁사) 키워드가 곧 COMPETITOR 신호
        "PRODUCT_TECH": product_score + tech_score,
        "CUSTOMER_TREND": customer_score,
    }

    return total, all_matched, category_scores


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


def determine_category(category_scores: dict[str, int]) -> str:
    max_score = max(category_scores.values())

    if max_score == 0:
        # 아무 카테고리 키워드도 안 걸렸으면 기본값으로 MARKET (가장 일반적인 산업 뉴스로 취급)
        return "MARKET"

    top_categories = [cat for cat, score in category_scores.items() if score == max_score]

    if len(top_categories) == 1:
        return top_categories[0]

    # 동점일 경우 우선순위 tie-breaker (요청서 21번)
    for cat in CATEGORY_TIE_BREAK_ORDER:
        if cat in top_categories:
            return cat

    return top_categories[0]


# ==========================================================
# 3. Summary 생성 — 창작 금지, 원문 정리 또는 고정 문구 (요청서 27번)
# ==========================================================

def strip_html(text: str) -> str:
    """HTML 태그와 흔한 HTML 엔티티를 제거한다.
    Google News RSS의 description은 실제 요약이 아니라
    "<a>제목</a>&nbsp;&nbsp;<font>출처</font>" 형태로 제목을 재포장한 값이라,
    태그만 지우면 &nbsp; 같은 엔티티가 그대로 남는다."""
    text = re.sub(r"<[^>]+>", "", text or "")
    entities = {
        "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&quot;": '"', "&#39;": "'", "&apos;": "'",
    }
    for entity, replacement in entities.items():
        text = text.replace(entity, replacement)
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


def build_summary(article: dict) -> str:
    title = article.get("title", "")
    description = strip_html(article.get("description", ""))

    # Google News RSS의 description은 대부분 "제목 + 출처명" 재포장이라
    # 실제 요약으로 볼 수 없다. 제목과 사실상 같은 내용이면 가짜 요약으로 판단해
    # 정직한 고정 문구로 대체한다 (요청서 27번: 사실을 지어내지 않는다).
    if description and not _titles_are_essentially_same(title, description):
        if len(description) > 200:
            truncated = description[:200]
            last_period = truncated.rfind(".")
            description = truncated[:last_period + 1] if last_period > 50 else truncated + "..."
        return description

    return "원문 기사 제목을 기준으로 확인이 필요한 뉴스입니다. 자세한 내용은 원문에서 확인하세요."


# ==========================================================
# 4. Why It Matters / BMW Watch Point 템플릿 (요청서 24~26번)
# ==========================================================

WHY_IT_MATTERS_TEMPLATES = {
    "MARKET": "Motorcycle 시장의 판매 및 수요 흐름과 관련된 기사입니다. 주요 시장과 Premium Segment의 변화 여부를 확인할 필요가 있습니다.",
    "COMPETITOR": "주요 경쟁 브랜드의 제품 또는 시장 움직임과 관련된 뉴스입니다. 경쟁사 Positioning과 제품 전략 변화를 확인할 필요가 있습니다.",
    "PRODUCT_TECH": "Motorcycle 제품 및 기술 변화와 관련된 기사입니다. 향후 Premium Motorcycle 고객의 제품 기대수준에 영향을 줄 수 있는 요소인지 확인할 필요가 있습니다.",
    "CUSTOMER_TREND": "Motorcycle 고객 및 라이딩 Trend와 관련된 기사입니다. 고객 Experience와 Lifestyle 변화 관점에서 지속적으로 확인할 필요가 있습니다.",
}

# 요청서 26번: 키워드 우선순위 (구체적인 것부터). 리스트 순서 = 우선순위.
WATCH_POINT_PRIORITY = [
    ("recall", "리콜 등 품질/안전 이슈와 관련된 사안입니다. 해당 브랜드의 품질 관리 이슈가 시장 신뢰도에 미치는 영향을 확인할 필요가 있습니다."),
    ("리콜", "리콜 등 품질/안전 이슈와 관련된 사안입니다. 해당 브랜드의 품질 관리 이슈가 시장 신뢰도에 미치는 영향을 확인할 필요가 있습니다."),
    ("acquisition", "인수·합병 등 산업 구조 변화와 관련된 뉴스입니다. Motorcycle 산업 내 경쟁 구도 변화 가능성을 확인할 필요가 있습니다."),
    ("merger", "인수·합병 등 산업 구조 변화와 관련된 뉴스입니다. Motorcycle 산업 내 경쟁 구도 변화 가능성을 확인할 필요가 있습니다."),
    ("인수", "인수·합병 등 산업 구조 변화와 관련된 뉴스입니다. Motorcycle 산업 내 경쟁 구도 변화 가능성을 확인할 필요가 있습니다."),
    ("합병", "인수·합병 등 산업 구조 변화와 관련된 뉴스입니다. Motorcycle 산업 내 경쟁 구도 변화 가능성을 확인할 필요가 있습니다."),
    ("electric", "Motorcycle 전동화 기술의 적용 범위와 시장 반응, 경쟁 브랜드의 출시 속도를 지속적으로 확인할 필요가 있습니다."),
    ("ev", "Motorcycle 전동화 기술의 적용 범위와 시장 반응, 경쟁 브랜드의 출시 속도를 지속적으로 확인할 필요가 있습니다."),
    ("전기", "Motorcycle 전동화 기술의 적용 범위와 시장 반응, 경쟁 브랜드의 출시 속도를 지속적으로 확인할 필요가 있습니다."),
    ("전동", "Motorcycle 전동화 기술의 적용 범위와 시장 반응, 경쟁 브랜드의 출시 속도를 지속적으로 확인할 필요가 있습니다."),
    ("adventure", "Premium Adventure Segment의 신제품 구성과 경쟁 브랜드의 Line-up 확대 움직임을 확인할 필요가 있습니다."),
    ("어드벤처", "Premium Adventure Segment의 신제품 구성과 경쟁 브랜드의 Line-up 확대 움직임을 확인할 필요가 있습니다."),
    ("connectivity", "Connectivity 및 Digital Feature가 Premium Motorcycle 고객의 제품 기대수준에 미치는 영향을 확인할 필요가 있습니다."),
    ("커넥티비티", "Connectivity 및 Digital Feature가 Premium Motorcycle 고객의 제품 기대수준에 미치는 영향을 확인할 필요가 있습니다."),
    ("new model", "경쟁사의 신규 모델 출시와 제품 Positioning, 가격대 및 고객 반응을 지속적으로 비교할 필요가 있습니다."),
    ("launch", "경쟁사의 신규 모델 출시와 제품 Positioning, 가격대 및 고객 반응을 지속적으로 비교할 필요가 있습니다."),
    ("unveil", "경쟁사의 신규 모델 출시와 제품 Positioning, 가격대 및 고객 반응을 지속적으로 비교할 필요가 있습니다."),
    ("신모델", "경쟁사의 신규 모델 출시와 제품 Positioning, 가격대 및 고객 반응을 지속적으로 비교할 필요가 있습니다."),
    ("출시", "경쟁사의 신규 모델 출시와 제품 Positioning, 가격대 및 고객 반응을 지속적으로 비교할 필요가 있습니다."),
    ("공개", "경쟁사의 신규 모델 출시와 제품 Positioning, 가격대 및 고객 반응을 지속적으로 비교할 필요가 있습니다."),
    ("market", "주요 Motorcycle 시장의 수요 변화와 Premium Segment 판매 흐름을 함께 확인할 필요가 있습니다."),
    ("sales", "주요 Motorcycle 시장의 수요 변화와 Premium Segment 판매 흐름을 함께 확인할 필요가 있습니다."),
    ("시장", "주요 Motorcycle 시장의 수요 변화와 Premium Segment 판매 흐름을 함께 확인할 필요가 있습니다."),
    ("판매", "주요 Motorcycle 시장의 수요 변화와 Premium Segment 판매 흐름을 함께 확인할 필요가 있습니다."),
]

CATEGORY_DEFAULT_WATCH_POINT = {
    "MARKET": "시장 동향을 지속적으로 모니터링할 필요가 있습니다.",
    "COMPETITOR": "경쟁 브랜드의 움직임을 지속적으로 관찰할 필요가 있습니다.",
    "PRODUCT_TECH": "제품 및 기술 트렌드를 지속적으로 확인할 필요가 있습니다.",
    "CUSTOMER_TREND": "고객 트렌드 변화를 지속적으로 관찰할 필요가 있습니다.",
}


def build_why_it_matters(category: str) -> str:
    return WHY_IT_MATTERS_TEMPLATES.get(category, WHY_IT_MATTERS_TEMPLATES["MARKET"])


def build_watch_point(matched_keywords: list[str], category: str) -> str:
    """요청서 26번: 우선순위 리스트를 순서대로 확인해서 가장 구체적인 템플릿을 선택"""
    matched_set = set(matched_keywords)
    for keyword, template in WATCH_POINT_PRIORITY:
        if keyword in matched_set:
            return template
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
        score, matched_keywords, category_scores = compute_score(article)
        category = determine_category(category_scores)
        importance = score_to_importance(score)
        summary = build_summary(article)
        why_it_matters = build_why_it_matters(category)
        bmw_insight = build_watch_point(matched_keywords, category)

        analyzed.append({
            # ---- 원본 필드 그대로 (요청서 1번) ----
            "id": article["id"],
            "title": article["title"],
            "url": article["url"],
            "source": article.get("source", ""),
            "sourceType": article.get("sourceType", ""),
            "sourceGroup": article.get("sourceGroup", ""),
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
        })

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

def build_market_intelligence(analyzed_articles: list[dict]) -> dict[str, list[dict]]:
    by_category: dict[str, list[dict]] = defaultdict(list)
    for a in analyzed_articles:
        by_category[a["category"]].append(a)

    result = {}
    key_map = {"MARKET": "market", "COMPETITOR": "competitor", "PRODUCT_TECH": "productTech", "CUSTOMER_TREND": "customerTrend"}

    for category, key in key_map.items():
        articles = sorted(by_category.get(category, []), key=lambda x: x["score"], reverse=True)[:4]
        cards = []
        for a in articles:
            cards.append({
                "title": a["title"],
                "summary": a["summary"],
                "relatedNewsIds": [a["id"]],
                "impact": a["importance"],
                "bmwView": a["bmwInsight"],
            })
        result[key] = cards

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

    top_news_count = sum(1 for a in analyzed_articles if a.get("isTopNews"))
    if top_news_count > TOP_NEWS_MAX:
        log(f"[검증 실패] TOP NEWS가 {TOP_NEWS_MAX}개를 초과합니다: {top_news_count}개")
        return False

    return True


# ==========================================================
# 12. news.json / insights.json 생성
# ==========================================================

def build_news_json(analyzed_articles: list[dict], top_ids: set[str], existing_news: dict | None) -> dict:
    now_kst = datetime.now(timezone(timedelta(hours=9)))

    final_news = []
    top_rank = {tid: i + 1 for i, tid in enumerate(top_ids)}

    for a in analyzed_articles:
        item = {
            "id": a["id"],
            "title": a["title"],
            "url": a["url"],
            "source": a["source"],
            "sourceType": a["sourceType"],
            "sourceGroup": a["sourceGroup"],
            "publishedAt": a["publishedAt"],
            "category": a["category"],
            "summary": a["summary"],
            "importance": a["importance"],
            "whyItMatters": a["whyItMatters"],
            "bmwInsight": a["bmwInsight"],
            "isTopNews": a["id"] in top_ids,
        }
        if a["id"] in top_rank:
            item["rank"] = top_rank[a["id"]]
        final_news.append(item)

    meta = {}
    if existing_news and "meta" in existing_news:
        meta = dict(existing_news["meta"])

    meta["date"] = now_kst.strftime("%Y-%m-%d")
    meta["dayLabel"] = now_kst.strftime("%A").upper()
    meta["lastUpdated"] = now_kst.strftime("%I:%M %p")
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
            "description": it.get("summary", ""),
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
# 메인 실행
# ==========================================================

def main():
    log("=" * 60)
    log("[MOTORRAD PULSE FREE INTELLIGENCE]")
    log("=" * 60)

    raw_articles = load_raw_news()
    log(f"\nRaw News: {len(raw_articles)}")

    if not raw_articles:
        log("\n[안내] 분석할 뉴스가 없습니다. 기존 news.json / insights.json은 변경하지 않습니다.")
        return

    analyzed_articles = analyze_all_articles(raw_articles)
    log(f"Analyzed: {len(analyzed_articles)}")

    top_ids = select_top_news(analyzed_articles)
    for a in analyzed_articles:
        a["isTopNews"] = a["id"] in top_ids

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
    log("\nTOP NEWS")
    top_articles_sorted = [a for a in analyzed_articles if a["id"] in top_ids]
    top_articles_sorted.sort(key=lambda x: x["score"], reverse=True)
    for i, a in enumerate(top_articles_sorted, 1):
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

    # ---- 저장 ----
    existing_news = load_existing_json(NEWS_PATH)

    news_json = build_news_json(analyzed_articles, top_ids, existing_news)
    insights_json = build_insights_json(daily_signal, market_intel, team_brief)
    news_json = apply_insights_to_news_meta(news_json, insights_json)

    save_json_atomic(news_json, NEWS_PATH)
    log("\nnews.json: UPDATED")

    save_json_atomic(insights_json, INSIGHTS_PATH)
    log("insights.json: UPDATED")

    log("\nAI API COST: $0")
    log("External AI API: NOT USED")
    log("\n완료.")


if __name__ == "__main__":
    main()
