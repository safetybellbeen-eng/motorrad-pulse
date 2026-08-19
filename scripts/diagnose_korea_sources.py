#!/usr/bin/env python3
"""
MOTORRAD PULSE — diagnose_korea_sources.py (STEP 10-KR.2, 진단 전용 독립 도구)

목적: Harley-Davidson Korea 공식 뉴스 페이지(https://harley-korea.com/latest-news)의
"실제" HTML 원본 태그/클래스 구조를 사람이 눈으로 확인할 수 있게 출력한다.

STEP 10-KR.2-A AUDIT에서 WebFetch(HTML->마크다운 변환)로는 원본 태그를 확인할 수
없었고, 이 세션의 샌드박스는 외부 네트워크 자체가 프록시 단계에서 막혀 있어(curl 403)
Harley Korea 페이지의 실제 반복 구조(article container 태그, class 이름, href/date
패턴이 machine-readable한지 등)를 검증할 방법이 없었다(Honda/STEP 10.2와 동일한
샌드박스 제약). 이 스크립트는 실제 네트워크가 열린 환경(사용자 로컬 PC, GitHub
Actions 등)에서 1회 실행해서 그 결과를 사람이 직접 읽고, HarleyKoreaParser를 실제로
구현해도 안전한지(반복 구조 명확 / title·date·URL 안정 추출 / JS 불필요 / 로그인
불필요 / robots 문제 없음) 재판단하기 위한 것이다.

Ducati Korea는 이번 스크립트의 대상이 아니다(STEP 10-KR.2-A AUDIT 결론에 따라
개별 기사 상세 URL 구조를 확인하지 못해 이번 STEP에서 자동화 대상에서 제외했다).

====================== 매우 중요: 이 스크립트의 위상 ======================
- collect_news.py / analyze_news_free.py / news_policy.py 중 어느 것도 이 파일을
  import하지 않는다. 이 파일도 그 셋을 import하지 않는다 (완전히 독립).
- SOURCES 목록에 등록되어 있지 않다 — 자동 수집 파이프라인에 전혀 관여하지 않는다.
- GitHub Actions workflow(.yml)에서 자동 실행되지 않는다 — 사람이 손으로 실행해야 한다.
- data/raw_news.json, data/history/, data/news.json, data/insights.json 등 운영
  데이터는 어느 것도 읽거나 쓰지 않는다. 저장 파일은 이 스크립트와 같은 위치에
  별도 진단 전용 파일(기본: harley_korea_diagnostic_output.html)로만 남긴다.
============================================================================

사용법:
    python3 scripts/diagnose_korea_sources.py
    python3 scripts/diagnose_korea_sources.py --save
    python3 scripts/diagnose_korea_sources.py --save-path out.html
    python3 scripts/diagnose_korea_sources.py --search "웨이크업 투어"
"""

import argparse
import re
import sys

import requests

TARGET_URL = "https://harley-korea.com/latest-news"
USER_AGENT = "MotorradPulseNewsCollector/1.0 (+https://github.com/) [DIAGNOSTIC ONLY - NOT USED IN PRODUCTION PIPELINE]"
REQUEST_TIMEOUT = 15

# STEP 10-KR.2-A AUDIT 조사에서 확인된(WebFetch 기준) 최근 게시물 제목 후보들.
# 이 문자열들이 원본 HTML 안에서 실제로 어떤 태그에 감싸여 있는지 확인하는 것이
# 이 스크립트의 핵심 목적이다. 시간이 지나면 페이지 내용이 바뀔 수 있으므로
# --search 옵션으로 다른 검색어를 추가할 수 있다.
KNOWN_TITLE_FRAGMENTS = [
    "웨이크업",
    "Wake-Up",
    "할리데이비슨",
    "H.O.G",
    "HOG",
]

# 날짜처럼 보이는 패턴(예: "2026.07.21", "2026-07-21") — 제목 근처에 이런 패턴이
# 있는지 확인해서 machine-readable한 날짜 속성이 있는지 가늠하는 용도.
DATE_LIKE_PATTERN = re.compile(
    r"(\b\d{4}[.\-]\d{2}[.\-]\d{2}\b)"
    r"|(\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b)"
)

# href/link 패턴 — 제목 근처의 <a href="..."> 후보를 찾는 용도.
HREF_PATTERN = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)

# STEP 10-KR.2-A AUDIT에서 확인된 예상 상세 URL 패턴(/news-article/{id}/{slug})이
# 실제로 href 후보에 등장하는지 별도로 확인한다.
ARTICLE_URL_PATTERN = re.compile(r'/news-article/\d+/[^"\'\s]+', re.IGNORECASE)

# time/date 관련 태그 후보 (원본 HTML에서 <time>, class="date" 등 machine-readable 여부 확인용)
TIME_TAG_PATTERN = re.compile(r'<time[^>]*>.*?</time>', re.IGNORECASE | re.DOTALL)
DATE_CLASS_PATTERN = re.compile(r'class\s*=\s*["\'][^"\']*date[^"\']*["\']', re.IGNORECASE)

# 반복되는 article container 후보(class에 "news"/"article"/"list"/"item" 등이 포함된 태그)를
# 대략적으로 찾아 개수를 세어본다 — 반복 구조가 실제로 있는지 가늠하는 용도.
CONTAINER_CLASS_PATTERN = re.compile(
    r'class\s*=\s*["\'][^"\']*(?:news|article|list-item|board-item|item)[^"\']*["\']',
    re.IGNORECASE,
)


def fetch(url: str) -> requests.Response:
    print(f"[요청] GET {url}")
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    return resp


def print_basic_info(resp: requests.Response):
    print("\n" + "=" * 70)
    print("[기본 정보]")
    print("=" * 70)
    print(f"HTTP status       : {resp.status_code}")
    print(f"Final URL (리다이렉트 후) : {resp.url}")
    print(f"Content-Type      : {resp.headers.get('Content-Type')}")
    print(f"HTML 전체 길이(bytes) : {len(resp.content)}")
    print(f"HTML 전체 길이(chars) : {len(resp.text)}")


def print_container_summary(html: str):
    print("\n" + "=" * 70)
    print("[반복 article container 후보 요약]")
    print("=" * 70)
    containers = CONTAINER_CLASS_PATTERN.findall(html)
    print(f"class에 news/article/list-item/board-item/item 포함된 속성: {len(containers)}개 발견")
    if containers:
        uniq = sorted(set(containers))[:10]
        for c in uniq:
            print(f"  {c}")

    article_urls = ARTICLE_URL_PATTERN.findall(html)
    print(f"\n예상 상세 URL 패턴(/news-article/{{id}}/{{slug}}) 매칭: {len(article_urls)}개 발견")
    for u in sorted(set(article_urls))[:10]:
        print(f"  {u}")
    if not article_urls:
        print("  -> 이 패턴이 전혀 없으면 STEP 10-KR.2-A AUDIT에서 추정한 상세 URL 구조가 실제와")
        print("     다를 수 있다는 뜻이므로, Collector 구현 전 반드시 재확인이 필요하다.")


def print_title_context(html: str, fragment: str, context_chars: int = 400):
    print("\n" + "-" * 70)
    print(f"[검색어: \"{fragment}\"]")
    print("-" * 70)

    idx = html.find(fragment)
    if idx == -1:
        print("  -> 이 문자열을 원본 HTML에서 찾지 못함 (페이지 내용이 바뀌었거나, JS 렌더링으로만 존재할 가능성)")
        return

    occurrences = 0
    search_from = 0
    while True:
        idx = html.find(fragment, search_from)
        if idx == -1:
            break
        occurrences += 1
        start = max(0, idx - context_chars // 2)
        end = min(len(html), idx + len(fragment) + context_chars // 2)
        snippet = html[start:end]

        print(f"\n  [occurrence #{occurrences}] 원본 HTML 위치 offset={idx}")
        print("  --- 제목 전후 raw HTML context ---")
        print("  " + snippet.replace("\n", "\n  "))

        hrefs = HREF_PATTERN.findall(snippet)
        if hrefs:
            print(f"  --- 주변 href 후보 ({len(hrefs)}개) ---")
            for h in hrefs[:5]:
                print(f"    href={h}")
        else:
            print("  --- 주변 href 후보: 없음 ---")

        dates = DATE_LIKE_PATTERN.findall(snippet)
        flat_dates = [d for pair in dates for d in pair if d]
        if flat_dates:
            print(f"  --- 주변 날짜형 문자열 후보 ({len(flat_dates)}개) ---")
            for d in flat_dates[:5]:
                print(f"    date-like={d}")
        else:
            print("  --- 주변 날짜형 문자열 후보: 없음 ---")

        time_tags = TIME_TAG_PATTERN.findall(snippet)
        if time_tags:
            print(f"  --- <time> 태그 발견 ({len(time_tags)}개, machine-readable 가능성 높음) ---")
            for t in time_tags[:3]:
                print(f"    {t}")
        else:
            print("  --- <time> 태그: 발견되지 않음 ---")

        date_class = DATE_CLASS_PATTERN.findall(snippet)
        if date_class:
            print(f"  --- class에 'date' 포함된 속성 발견 ({len(date_class)}개) ---")
            for dc in date_class[:3]:
                print(f"    {dc}")

        search_from = idx + len(fragment)
        if occurrences >= 5:
            print("  (occurrence 5건 이상 — 이하 생략)")
            break

    print(f"\n  총 {occurrences}회 발견")


def main():
    parser = argparse.ArgumentParser(
        description="Harley-Davidson Korea 원본 HTML 구조 진단 도구 (운영 파이프라인과 완전히 분리된 독립 스크립트)"
    )
    parser.add_argument("--save", action="store_true", help="원본 HTML을 파일로 저장한다 (data/ 폴더는 절대 건드리지 않음)")
    parser.add_argument("--save-path", default="harley_korea_diagnostic_output.html", help="저장할 파일 경로")
    parser.add_argument("--search", action="append", default=[], help="검색할 제목 문자열 추가 (여러 번 지정 가능)")
    parser.add_argument("--url", default=TARGET_URL, help=f"진단할 URL (기본: {TARGET_URL})")
    args = parser.parse_args()

    print("=" * 70)
    print("[Harley-Davidson Korea HTML 구조 진단 — STEP 10-KR.2 진단 전용 독립 도구]")
    print("이 스크립트는 운영 파이프라인(collect_news.py 등)에 전혀 연결되어 있지 않습니다.")
    print("Ducati Korea는 이번 진단 대상이 아닙니다 (STEP 10-KR.2-A AUDIT 결론에 따라 제외).")
    print("=" * 70)

    try:
        resp = fetch(args.url)
    except requests.exceptions.RequestException as e:
        print(f"\n[FAILED] 네트워크 오류로 진단할 수 없음: {e}")
        print("[결론] 이 환경에서는 raw HTML을 확인할 수 없으므로, 이 스크립트만으로는")
        print("       Collector 구현 여부를 판단할 수 없습니다. 네트워크가 열린 환경에서")
        print("       재실행한 뒤 사람이 직접 결과를 확인해야 합니다.")
        sys.exit(1)

    print_basic_info(resp)

    if resp.status_code != 200:
        print(f"\n[중단] HTTP {resp.status_code} — 200이 아니므로 본문 구조 분석을 진행하지 않습니다.")
        sys.exit(1)

    html = resp.text

    print_container_summary(html)

    search_terms = KNOWN_TITLE_FRAGMENTS + args.search
    print("\n" + "=" * 70)
    print(f"[제목 문자열 검색 — {len(search_terms)}개 검색어]")
    print("=" * 70)
    for fragment in search_terms:
        print_title_context(html, fragment)

    if args.save:
        with open(args.save_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n[저장 완료] 원본 HTML을 다음 경로에 저장했습니다: {args.save_path}")
        print("(주의: data/ 폴더의 운영 데이터가 아닌, 이 진단 스크립트 전용 결과물입니다.)")

    print("\n" + "=" * 70)
    print("[다음 단계 안내]")
    print("=" * 70)
    print("위 출력에서 각 제목 주변에 반복되는 태그/class 패턴이 있는지, <time> 태그나")
    print("class=\"...date...\" 같은 machine-readable 날짜 속성이 있는지, /news-article/{id}/{slug}")
    print("형태의 상세 URL이 실제로 존재하는지 사람이 직접 확인하세요.")
    print("아래 조건을 모두 만족해야만 HarleyKoreaParser(HTMLParser) 구현을 진행할 수 있습니다:")
    print("  - 반복 article container 구조가 명확함")
    print("  - title이 안정적으로 추출됨")
    print("  - date가 안정적으로 추출됨(machine-readable, 파싱 실패시 DROP 원칙 유지 가능)")
    print("  - article 상세 URL이 안정적으로 추출됨(추측/조작 없이)")
    print("  - JS 렌더링 없이 정적 HTML만으로 위 조건이 충족됨")
    print("  - 로그인/세션 없이 접근 가능함")
    print("  - robots.txt상 문제 없음(이미 STEP 10-KR.2-A AUDIT에서 확인됨: 전체 허용, BLEXBot만 차단)")
    print("하나라도 불안정하면 NOT SAFE TO AUTOMATE로 결론짓고, Collector/SOURCES/Tier 등록을")
    print("진행하지 않아야 합니다(억지 구현 금지 — STEP 10-KR.2 요청사항 14번).")


if __name__ == "__main__":
    main()
