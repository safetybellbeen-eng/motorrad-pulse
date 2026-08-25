#!/usr/bin/env python3
"""
MOTORRAD PULSE — collect_youtube.py

브랜드별 한국 공식 유튜브 채널의 최신 영상을 RSS로 수집하여
data/youtube_videos.json에 저장한다 (VIDEO WATCH 섹션용).

핵심 원칙 (collect_news.py와 동일한 기준을 그대로 따른다):
- title, url, thumbnail, channelTitle, publishedAt은 절대 AI가 만들지 않는다.
  유튜브 RSS 피드에 실제로 들어있는 값만 그대로 사용한다.
- 존재하지 않는 videoId/URL을 임의로 만들지 않는다.
- 채널별 최대 MAX_PER_CHANNEL개만 채택한다. 억지로 채우지 않는다(0개도 정상).
- 한 채널이 실패해도(비공개 전환, 채널 삭제, 일시적 네트워크 오류 등) 다른 채널
  수집은 계속 진행한다.
- 실패한 채널은 새로 덮어쓰지 않고 기존 data/youtube_videos.json에 있던
  해당 채널의 영상 목록을 그대로 보존한다(삭제하지 않음).

채널 ID 확인 방법 — 새 브랜드를 추가하거나 기존 채널이 바뀐 경우:
  해당 채널 페이지에서 "정보"(About) 탭을 열어 페이지 소스에서
  canonical link(<link rel="canonical" href="https://www.youtube.com/channel/UC...">)
  또는 "channelId"/"externalId" 값을 확인한다. 반드시 실제 브랜드 공식 채널인지
  (설명란에 "공식"/"official" 문구, 브랜드 공식 홈페이지의 SNS 링크 등으로) 확인 후 사용할 것.
  RSS 피드 URL 형식: https://www.youtube.com/feeds/videos.xml?channel_id=<CHANNEL_ID>
"""

import json
import os
from datetime import datetime, timezone

import feedparser
import requests
from dateutil import parser as dateutil_parser

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
YOUTUBE_VIDEOS_PATH = os.path.join(DATA_DIR, "youtube_videos.json")

MAX_PER_CHANNEL = 5
REQUEST_TIMEOUT = 15
USER_AGENT = "MotorradPulseYouTubeCollector/1.0 (+https://github.com/)"

# 2026-08-25 리서치 기준으로 확인한 브랜드별 한국 공식 유튜브 채널.
# label은 기존 SOURCE_GROUP_LABELS(news_policy.py)와 동일한 브랜드 표기를 그대로 쓴다.
#
# 확인 상태(confidence) 메모 — 채널이 바뀌었거나 확실하지 않으면 반드시 재확인할 것:
#   bmw, triumph, honda: About 설명에 "공식"이라는 문구가 명시되어 있어 확인됨(High)
#   ducati, harley, yamaha: 브랜드명/설명/콘텐츠가 일치하지만 "공식" 문구가 명시적으로
#     확인되지는 않았음(Medium) — 운영 중 이상 콘텐츠가 섞여 나오면 채널을 재검증할 것
CHANNELS = {
    "bmw": {
        "label": "BMW",
        "channelId": "UCiRbUGYLu0P-q1Kv5kTOR-A",  # BMW Motorrad Korea
    },
    "ducati": {
        "label": "Ducati",
        "channelId": "UCJTNF1bn9qQ_g2CLTsikKiQ",  # 두카티코리아
    },
    "triumph": {
        "label": "Triumph",
        "channelId": "UCE3-Y1RG9Uu4Qob85_KQV6A",  # Triumph Korea
    },
    "harley": {
        "label": "Harley-Davidson",
        "channelId": "UCJs2Asltby49ZJcGnwFvtGQ",  # 할리데이비슨 코리아
    },
    "honda": {
        "label": "Honda",
        "channelId": "UCd_Iw4TQqnmMzwMD-BsYkcA",  # Honda Motorcycle Korea
    },
    "yamaha": {
        "label": "Yamaha",
        "channelId": "UC4GvVeyt0ITmUg174soDYjQ",  # 야마하 모터사이클
    },
}


def load_existing():
    """기존 파일을 읽어 {brandGroup: [video, ...]} 형태로 되돌린다.
    채널 수집이 실패했을 때 이전 데이터를 보존하기 위한 폴백 용도."""
    if not os.path.exists(YOUTUBE_VIDEOS_PATH):
        return {}
    try:
        with open(YOUTUBE_VIDEOS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    by_group = {}
    for v in data.get("videos", []):
        group = v.get("brandGroup")
        if not group:
            continue
        by_group.setdefault(group, []).append(v)
    return by_group


def collect_channel(brand_group, channel_id):
    """채널 하나의 RSS를 수집해 정규화된 영상 리스트를 반환한다.
    실패 시 None을 반환한다(빈 리스트[]와 구분 — []는 '정상 수집됐는데 영상이 0개',
    None은 '수집 자체가 실패해서 기존 데이터를 보존해야 함'을 의미)."""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [FAIL] {brand_group}: RSS 요청 실패 ({e})")
        return None

    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        print(f"  [FAIL] {brand_group}: RSS 파싱 실패 (bozo={feed.bozo_exception})")
        return None

    videos = []
    for entry in feed.entries[:MAX_PER_CHANNEL]:
        video_id = getattr(entry, "yt_videoid", None)
        title = getattr(entry, "title", None)
        link = getattr(entry, "link", None)
        published_raw = getattr(entry, "published", None)
        channel_title = getattr(feed.feed, "title", CHANNELS[brand_group]["label"])

        if not video_id or not title or not link:
            # 필수 필드가 없는 항목은 조용히 건너뛴다 — 가공해서 채우지 않는다.
            continue

        thumbnail = None
        media_thumb = getattr(entry, "media_thumbnail", None)
        if media_thumb and isinstance(media_thumb, list) and media_thumb:
            thumbnail = media_thumb[0].get("url")
        if not thumbnail:
            # RSS에 썸네일이 없는 예외적인 경우에 한해 표준 유튜브 썸네일 URL 패턴으로 대체.
            # (이건 "가공"이 아니라 videoId 기반의 결정적 공개 URL이라 사실 왜곡이 없음)
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        published_iso = None
        if published_raw:
            try:
                published_iso = dateutil_parser.parse(published_raw).isoformat()
            except (ValueError, TypeError):
                published_iso = None

        videos.append(
            {
                "id": f"yt_{video_id}",
                "videoId": video_id,
                "title": title,
                "url": link,
                "thumbnail": thumbnail,
                "channelTitle": channel_title,
                "brandGroup": brand_group,
                "publishedAt": published_iso,
            }
        )

    return videos


def main():
    print("=== MOTORRAD PULSE YouTube 수집 시작 ===")
    existing_by_group = load_existing()

    all_videos = []
    success_count = 0
    fail_count = 0

    for brand_group, cfg in CHANNELS.items():
        print(f"[{cfg['label']}] 채널 수집 중... ({cfg['channelId']})")
        result = collect_channel(brand_group, cfg["channelId"])

        if result is None:
            # 수집 실패 — 기존에 저장돼 있던 이 채널의 영상을 그대로 보존한다.
            fail_count += 1
            fallback = existing_by_group.get(brand_group, [])
            if fallback:
                print(f"  [KEEP] {cfg['label']}: 기존 영상 {len(fallback)}건 유지")
            all_videos.extend(fallback)
        else:
            success_count += 1
            print(f"  [OK] {cfg['label']}: {len(result)}건 수집")
            all_videos.extend(result)

    # 최신순 정렬 (publishedAt이 없는 항목은 맨 뒤로)
    all_videos.sort(key=lambda v: v.get("publishedAt") or "", reverse=True)

    now = datetime.now(timezone.utc).astimezone()
    output = {
        "meta": {
            "lastUpdatedISO": now.isoformat(),
            "channelCount": len(CHANNELS),
            "successCount": success_count,
            "failCount": fail_count,
        },
        "videos": all_videos,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(YOUTUBE_VIDEOS_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(
        f"=== 완료: 총 {len(all_videos)}건 저장 "
        f"(성공 {success_count}/실패 {fail_count} 채널) ==="
    )


if __name__ == "__main__":
    main()
