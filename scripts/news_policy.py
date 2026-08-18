#!/usr/bin/env python3
"""
MOTORRAD PULSE — news_policy.py

STEP 9.3: collect_news.py / analyze_news_free.py 양쪽에서 "실제로 공통으로 쓰는"
수집/유효성 판정 정책만 이 파일 한 곳에 모은다(Single Source of Truth).

이 파일은 리팩터링 전용이다 — 정책의 값(도메인 목록, 키워드, 임계값 등)이나
동작을 하나도 바꾸지 않았다. STEP 9.1/9.2까지 collect_news.py와
analyze_news_free.py에 각각 복제되어 있던 다음 9개 항목을 그대로(값 변경 없이)
옮겨왔다.

  1. SOURCE_TIERS / SOURCE_TIER_SCORES / TRUSTED_DOMAINS
  2. get_source_tier() / get_source_quality_score()
  3. is_trusted_domain()
  4. MOTORCYCLE_CONTEXT_KEYWORDS
  5. AUTOMOTIVE_ONLY_KEYWORDS
  6. INCIDENT_ONLY_KEYWORDS
  7. BRAND_SPECIFIC_CONTEXT_KEYWORDS
  8. has_motorcycle_context()
  9. BRAND_NAME_KEYWORDS / detect_brand_groups() / SOURCE_GROUP_LABELS

이 파일에 포함되지 않은 것(의도적으로 옮기지 않음):
  - collect 전용: BLOCKED_DOMAINS/is_blocked_domain, TRACKING_PARAMS/normalize_url,
    Business Relevance 일체, Freshness(LOOKBACK_HOURS/RETENTION_HOURS/is_within_lookback),
    classify_source_group()/BRAND_KEYWORDS(구 phrase 매칭), find_duplicate_clusters 계열.
    이 정책들은 analyze_news_free.py에 대응 항목이 없어(=중복이 아니어서) 공통화 대상이
    아니다. Business Relevance/Freshness는 원칙적으로 collect_news.py가 Source of Truth이고
    analyze_news_free.py에서 다시 하드 필터하지 않는다(STEP 9.1/9.3 AUDIT에서 확정).
  - analyze 전용: Category Scoring/TOP NEWS/Market Intelligence/Brand Pulse/History/
    TEAM BRIEF 관련 함수 전체, freshness_score()(TOP NEWS 랭킹용 소프트 가산점 —
    이름은 비슷하지만 collect의 Hard Freshness Gate와는 목적이 다르다).

IMPORT 방향(요청 사항: 순환 import 금지): 이 파일은 collect_news.py나
analyze_news_free.py를 절대 import하지 않는다. 표준 라이브러리(urllib.parse)만
사용하며, requests/feedparser/dateutil 등 외부 패키지도 필요 없다.

  news_policy.py
        ^                 ^
  collect_news.py   analyze_news_free.py   (단방향)
"""

from urllib.parse import urlsplit

# ==========================================================
# 1. Trusted Domain / Source Tier
# ==========================================================
# 이전 버전은 "알려진 외국 도메인을 차단"하는 블랙리스트 방식이었는데,
# .com/.net 도메인은 무조건 통과시키는 구멍이 있어 실제로
# fortunebusinessinsights.com(미국 시장조사 업체), vietnam.vn 등이 계속 새어 들어왔다.
# 새로운 외국 사이트가 나올 때마다 목록을 추가해야 하는 블랙리스트는 구조적으로
# 뚫릴 수밖에 없으므로, 반대로 "이 목록에 있는 도메인만 허용한다"는
# 화이트리스트 방식으로 바꾼다. 목록에 없는 도메인은 국내든 해외든 기본적으로 차단된다.
#
# STEP 9: 도메인을 "허용/차단"만 하지 않고 내부적으로 신뢰도 Tier를 부여한다
# (JSON Schema나 화면에는 영향 없음 — 내부 로깅/정렬용). AUDIT 결과 실제로 검증된
# 공식 브랜드 프레스룸 도메인이 아직 없어 Tier 1은 비워둔다(없는 RSS를 지어내지 않음).
SOURCE_TIERS = {
    # Tier 1 — Official/Primary. 실제 접근 가능한 공식 소스가 확인되면 여기에만 추가한다.
    # STEP 10.1: "발견됨"과 "실제 자동수집에 활성화됨"을 구분한다 — STEP 10-A/B/C에서
    # 조사한 6개 브랜드 중 실제로 이번 STEP에서 SOURCES에 등록해 자동수집을 켜는
    # BMW/Yamaha 공식 RSS 도메인만 여기에 추가한다. Ducati/Triumph/Honda/Harley는
    # STEP 10-A/B/C에서 (HTML 수집 후보 또는 자동화 보류)로 "발견"만 되었을 뿐
    # 이번 STEP에서 활성화되는 collector가 없으므로 추가하지 않는다.
    "official": {
        "www.press.bmwgroup.com", "press.bmwgroup.com",  # BMW Motorrad Press (RSS 검증 완료)
        "global.yamaha-motor.com",  # Yamaha Motor Global News (RSS 검증 완료, 전사 피드 — Context Gate 필수)
    },

    # Tier 2 — Trusted Motorcycle/Automotive Media (국내 이륜차·자동차 전문지 + STEP 10.1 해외 전문매체)
    "motorcycle_media": {
        "kmnews.net", "www.kmnews.net",
        "mbzine.com", "www.mbzine.com",
        "carguy.kr", "www.carguy.kr",
        "dailycar.co.kr", "www.dailycar.co.kr",
        # STEP 10.1: 해외 이륜차 전문매체 RSS (STEP 10-B에서 검증 완료된 것만 등록)
        "visordown.com", "www.visordown.com",
        "advpulse.com", "www.advpulse.com",
    },

    # Tier 3 — Trusted Business/General Media (국내 경제/종합지)
    "business_media": {
        "hankyung.com", "www.hankyung.com",
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
    },
}

# Tier별 내부 점수 (정렬/대표기사 선택/로깅용 — collect_news.py에서만 사용)
SOURCE_TIER_SCORES = {"official": 100, "motorcycle_media": 90, "business_media": 75}

# TRUSTED_DOMAINS는 SOURCE_TIERS에서 자동 파생한다(한 곳에서만 정의 — STEP 9.3 핵심).
TRUSTED_DOMAINS = set().union(*SOURCE_TIERS.values())


def get_source_tier(url: str) -> str | None:
    """최종 원문 URL의 도메인이 속한 Tier 이름을 반환. 화이트리스트 밖이면 None.
    (collect_news.py 전용 — analyze_news_free.py는 이 함수를 쓰지 않는다.)"""
    try:
        netloc = urlsplit(url).netloc.lower()
    except Exception:
        return None
    for tier_name, domains in SOURCE_TIERS.items():
        if netloc in domains:
            return tier_name
    return None


def get_source_quality_score(url: str) -> int:
    """Tier 기반 내부 점수(대표기사 선택/정렬용). 화이트리스트 밖이면 0.
    (collect_news.py 전용 — analyze_news_free.py는 이 함수를 쓰지 않는다.)"""
    return SOURCE_TIER_SCORES.get(get_source_tier(url), 0)


def is_trusted_domain(url: str) -> bool:
    """최종 원문 URL의 도메인이 신뢰 화이트리스트에 있는지 확인한다.
    목록에 없으면 국내 사이트로 보이더라도 기본적으로 차단한다
    (요청서 원칙: 신뢰할 수 있는 뉴스가 부족하면 빈 자리를 허용한다,
    낮은 품질/미확인 Source로 억지로 채우지 않는다). collect/analyze 공통 사용."""
    try:
        netloc = urlsplit(url).netloc.lower()
    except Exception:
        return False
    return netloc in TRUSTED_DOMAINS


# ==========================================================
# 2. Motorcycle Context 판정
# ==========================================================
# kmnews(한국이륜차신문 등), naver, google 검색 결과에는 이륜차 전문지라도
# 자동차/전기차 기사가 섞여 나오는 경우가 있다 (예: 카가이는 자동차 종합 매체).
# 그래서 sourceGroup과 무관하게 모든 수집 기사에 대해 실제로 이륜차/오토바이
# 관련 기사인지 검증한다.

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

# MOTORRAD PULSE는 BMW Motorrad 브랜드 마케터를 위한 업무용 Market Intelligence
# Dashboard다. 이륜차 키워드가 있어도 교통사고/범죄/단속처럼 마케팅 업무와
# 무관한 사회면 사건사고 기사는 제외한다 (예: "헬멧 없이 오토바이 몰던 10대... 사고 사망").
INCIDENT_ONLY_KEYWORDS = [
    "사고", "사망", "숨져", "숨진", "부상", "중상", "치사", "치상",
    "음주운전", "무면허", "뺑소니", "도주", "체포", "검거", "구속", "입건",
    "단속", "적발", "위반", "범칙금", "과태료", "절도", "훔쳐", "절취",
    "폭행", "사기", "고소", "고발", "재판", "실형", "징역", "벌금형",
]

# STEP 9 AUDIT 결과: Honda/Yamaha는 이륜차 외 사업(자동차/로봇/항공/발전기,
# 악기/음향기기/선박엔진 등)이 커서 브랜드명만으로는 오탐 위험이 구조적으로 높다.
BRAND_SPECIFIC_CONTEXT_KEYWORDS = {
    "honda": [
        "motorcycle", "motorbike", "bike", "cb", "cbr", "africa twin", "gold wing",
        "rebel", "forza", "pcx", "adv", "super cub", "two-wheeler", "scooter",
        "이륜차", "오토바이", "모터사이클", "스쿠터",
    ],
    "yamaha": [
        "motorcycle", "motorbike", "bike", "mt", "yzf", "xsr", "tracer", "tenere",
        "ténéré", "nmax", "xmax", "scooter", "two-wheeler",
        "이륜차", "오토바이", "모터사이클", "스쿠터",
    ],
}


def has_motorcycle_context(title: str, summary: str = "", brand_group: str | None = None) -> bool:
    """이륜차 관련 키워드가 있으면 True.
    단, 아래 경우는 예외적으로 False로 판단한다.
    1) 자동차 전용 키워드만 있고 이륜차 키워드가 없는 경우 (순수 자동차 기사)
    2) 사건사고/범죄/단속 키워드가 있는 경우 (마케팅 인텔리전스와 무관한 사회면 뉴스)
    3) brand_group이 honda/yamaha인데 브랜드 전용 이륜차 서브키워드가 전혀 없는 경우
       — 두 브랜드는 자동차/로봇/항공/악기/선박 등 이륜차 외 사업이 커서, 전역
       키워드만으로는 다른 사업 뉴스가 섞여 들어올 위험이 있기 때문이다.
    collect_news.py(수집 시 Hard Gate)와 analyze_news_free.py(이중 안전장치) 양쪽에서
    동일하게 사용한다."""
    text = f"{title or ''} {summary or ''}".lower()

    has_bike_keyword = any(kw.lower() in text for kw in MOTORCYCLE_CONTEXT_KEYWORDS)
    has_auto_only_keyword = any(kw.lower() in text for kw in AUTOMOTIVE_ONLY_KEYWORDS)
    has_incident_keyword = any(kw.lower() in text for kw in INCIDENT_ONLY_KEYWORDS)

    if has_auto_only_keyword and not has_bike_keyword:
        return False

    if has_incident_keyword:
        return False

    if not has_bike_keyword:
        return False

    brand_keywords = BRAND_SPECIFIC_CONTEXT_KEYWORDS.get(brand_group)
    if brand_keywords and not any(kw.lower() in text for kw in brand_keywords):
        return False

    return True


# ==========================================================
# 3. Brand Attribution — brandGroups
# ==========================================================
# AUDIT 결과, 기존 sourceGroup은 "어느 수집 채널에서 들어왔는가"(kmnews/naver/google 등)와
# "어느 브랜드 기사인가"라는 서로 다른 두 의미가 섞여 있었다. sourceGroup의 기존 동작
# (SOURCE MONITOR 매체 필터 등)은 건드리지 않고, 이 함수는 "이 기사가 실제로 어느
# 브랜드를 다루는가"만 독립적으로 판단해 brandGroups(배열)라는 별도 필드를 채운다.

# sourceGroup 표시명 (기존 UI와 동일하게 유지 — collect/analyze 공통 참조용)
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
    # STEP 10.1: Google 검색 폴백(google)과 통계적으로 섞이지 않도록 별도 sourceGroup으로 분리한다.
    # (Direct RSS vs Google 검색 폴백 비율을 나중에 정확히 집계하기 위한 의도적 분리 — UI 필터
    # 칩은 이번 STEP에서 추가하지 않으며, index.html/script.js/style.css는 수정하지 않는다.)
    "global_media": "Global Motorcycle Media",
}

BRAND_NAME_KEYWORDS = {
    "bmw": ["bmw", "비엠더블유", "모토라드", "motorrad"],
    "ducati": ["ducati", "두카티"],
    "triumph": ["triumph", "트라이엄프"],
    "harley": ["harley-davidson", "harley davidson", "harley", "할리데이비슨", "할리 데이비슨"],
    "honda": ["honda", "혼다"],
    "yamaha": ["yamaha", "야마하"],
}


def detect_brand_groups(title: str, summary: str) -> list[str]:
    """제목/요약에서 실제로 언급된 브랜드를 모두 찾아 배열로 반환한다(brandGroups).

    브랜드명이 텍스트 어디에 있든(문장 부호/따옴표로 분리되어 있어도) 잡아내기 위해
    고정된 phrase를 요구하지 않고, 브랜드명 자체 + 문맥 조건을 분리해서 판단한다.

    - Honda/Yamaha: 브랜드명이 있어도 이륜차 외 사업(자동차/로봇/항공/악기 등)이 커서
      브랜드명만으로는 오탐 위험이 크다. 그래서 BRAND_SPECIFIC_CONTEXT_KEYWORDS(이미
      has_motorcycle_context에서 쓰는 것과 동일한 기준)를 반드시 함께 만족해야 한다.
    - BMW/Ducati/Triumph/Harley: 브랜드명 매칭에 더해 has_motorcycle_context() 게이트
      (자동차 전용/사건사고 배제)를 그대로 재사용해서 오탐을 막는다.

    STEP 9.3: collect_news.py의 수집 시점 계산과 analyze_news_free.py의 Legacy
    Fallback 계산이 이 함수 하나를 공통으로 사용한다(더 이상 두 파일에 복제하지 않음)."""
    text = f"{title} {summary}".lower()
    general_context_ok = has_motorcycle_context(title, summary, None)

    detected: list[str] = []
    for brand, names in BRAND_NAME_KEYWORDS.items():
        if not any(name in text for name in names):
            continue

        brand_specific_kws = BRAND_SPECIFIC_CONTEXT_KEYWORDS.get(brand)
        if brand_specific_kws:
            if not any(kw.lower() in text for kw in brand_specific_kws):
                continue
        else:
            if not general_context_ok:
                continue

        detected.append(brand)

    return detected
