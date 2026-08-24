/* ==========================================================
   MOTORRAD PULSE — script.js
   순수 Vanilla JS. data/news.json을 fetch하여 화면을 그린다.
   ========================================================== */

const SOURCE_GROUP_LABELS = {
  bmw: "BMW",
  ducati: "Ducati",
  triumph: "Triumph",
  harley: "Harley-Davidson",
  honda: "Honda",
  yamaha: "Yamaha",
  naver: "Naver",
  google: "Google",
  kmnews: "KMNEWS"
};

/* category 코드값(MARKET/COMPETITOR/PRODUCT_TECH/CUSTOMER_TREND) -> 화면 표시 라벨 */
const CATEGORY_LABELS = {
  MARKET: "MARKET",
  COMPETITOR: "COMPETITOR",
  PRODUCT_TECH: "PRODUCT & TECH",
  CUSTOMER_TREND: "CUSTOMER & TREND"
};

let NEWS_DATA = null;
let RAW_NEWS_DATA = null;
let currentFilter = "all";

/* ---------- 초기 로드 ----------
   news.json: AI 분석이 완료된 데이터 (TOP NEWS, MARKET INTELLIGENCE, TEAM BRIEF)
   raw_news.json: 자동수집된 원본 전체 (SOURCE MONITOR 전용, 요청서 42/43/44번)
   두 파일은 서로 독립적으로 로드한다 — 한쪽이 없거나 실패해도 다른 한쪽은 정상 표시되어야 한다.

   cache: "no-store"와 타임스탬프 쿼리를 붙이는 이유: GitHub Pages는 정적 파일이라
   브라우저/중간 캐시가 예전 JSON을 계속 보여줄 수 있다. 새로고침 버튼을 눌렀을 때
   진짜 최신 데이터를 받아오려면 캐시를 우회해야 한다. */
function loadNewsData() {
  return fetch(`./data/news.json?t=${Date.now()}`, { cache: "no-store" })
    .then((res) => {
      if (!res.ok) throw new Error("데이터를 불러오지 못했습니다.");
      return res.json();
    })
    .then((data) => {
      NEWS_DATA = data;
      renderHeader(data.meta);
      renderSignal(data.meta.todaySignal);
      renderTopNews(data.news || []);
      renderBrandSummary(data.meta.brandSummary || {});
      // STEP 12-D: marketIntelligenceV2(SHADOW MODE로 생성된 brandRole/insightType 포함 데이터)를
      // 우선 사용하고, 없는 과거 데이터(marketIntelligenceV2 없이 marketIntelligence만 있는 경우)는
      // 기존 marketIntelligence로 자연스럽게 fallback한다 — 데이터 계산 로직은 전혀 건드리지 않고,
      // 어느 데이터를 화면에 연결할지만 여기서 결정한다.
      renderMarketIntelligence(data.marketIntelligenceV2 || data.marketIntelligence || {});
      renderTeamBrief(data);
    })
    .catch((err) => {
      console.error(err);
      showToast("news.json을 불러오지 못했습니다. 파일 위치를 확인해 주세요.");
      throw err;
    });
}

function loadRawNewsData() {
  return fetch(`./data/raw_news.json?t=${Date.now()}`, { cache: "no-store" })
    .then((res) => {
      if (!res.ok) throw new Error("원본 뉴스 데이터를 불러오지 못했습니다.");
      return res.json();
    })
    .then((data) => {
      RAW_NEWS_DATA = data;
      renderSourceMonitor(data.news || []);
    })
    .catch((err) => {
      console.error(err);
      showToast("raw_news.json을 불러오지 못했습니다. 파일 위치를 확인해 주세요.");
      throw err;
    });
}

document.addEventListener("DOMContentLoaded", () => {
  loadNewsData();
  loadRawNewsData();

  setupNavHighlight();
  setupMobileTabbar();
  setupIntelTabbar();
  setupFilterBar();
  setupCopyBriefButton();
  setupRefreshButton();
  setupPullToRefresh();

  // "N분/N시간 전 업데이트" 표시를 1분마다 갱신 (페이지를 오래 열어둬도 흘러가도록)
  setInterval(updateRelativeTimeDisplay, 60 * 1000);

  // 최상단 실시간 시계 — 뉴스 정보의 신뢰도를 위해 "지금 이 순간"을 항상 보여준다.
  updateLiveClock();
  setInterval(updateLiveClock, 1000);
});

/* ---------- 최상단 실시간 시계 ---------- */
function updateLiveClock() {
  const el = document.getElementById("topbar-live-clock");
  if (!el) return;

  const now = new Date();
  const dayNames = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
  const pad = (n) => String(n).padStart(2, "0");

  const dateStr = `${now.getFullYear()}.${pad(now.getMonth() + 1)}.${pad(now.getDate())} (${dayNames[now.getDay()]})`;
  const timeStr = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;

  el.textContent = `${dateStr} ${timeStr}`;
}

/* ---------- HEADER ---------- */
let LAST_UPDATED_ISO = null;

function renderHeader(meta) {
  const dateEl = document.getElementById("topbar-date");
  const timeEl = document.getElementById("topbar-time");
  if (dateEl) {
    dateEl.textContent = meta.date
      ? `${meta.date.replaceAll("-", ".")}  ${meta.dayLabel || ""}`
      : "날짜 정보 없음";
  }
  LAST_UPDATED_ISO = meta.lastUpdatedISO || null;
  updateRelativeTimeDisplay();
}

/* 1분마다 "N분 전 / N시간 전" 표시를 최신으로 갱신 (페이지를 오래 열어둬도 시간이 흘러가도록) */

/* ---------- 데이터 신선도 경고 ----------
   뉴스 수집은 하루 1회(GitHub Actions) 갱신되는 게 정상 흐름이다. LAST UPDATED가
   너무 오래된 채로 방치되면(수집 파이프라인 실패 등) 화면만 봐서는 알아채기 어려우므로,
   텍스트 옆에 색상 점 + 텍스트 색상으로 단계적 경고를 준다.
     - 12시간 미만: 정상 (기본 회색)
     - 12시간 이상 24시간 미만: 주의 (signal-amber) — 오늘자 수집이 아직 안 들어왔을 수 있음
     - 24시간 이상: 경고 (signal-red) — 수집 파이프라인 확인 필요 */
const FRESHNESS_WARN_HOURS = 12;
const FRESHNESS_ALERT_HOURS = 24;

function applyFreshnessState(diffHours) {
  const wrap = document.getElementById("topbar-updated");
  if (!wrap) return;

  wrap.classList.remove("is-stale-warn", "is-stale-alert");

  if (diffHours === null) {
    wrap.title = "";
    return;
  }

  if (diffHours >= FRESHNESS_ALERT_HOURS) {
    wrap.classList.add("is-stale-alert");
    wrap.title = "마지막 수집이 24시간 넘게 갱신되지 않았습니다 — 수집 파이프라인을 확인해 주세요.";
  } else if (diffHours >= FRESHNESS_WARN_HOURS) {
    wrap.classList.add("is-stale-warn");
    wrap.title = "마지막 수집이 12시간 넘게 지났습니다 — 오늘자 데이터가 아직 반영되지 않았을 수 있습니다.";
  } else {
    wrap.title = "최근 12시간 이내에 수집된 데이터입니다.";
  }
}

function updateRelativeTimeDisplay() {
  const timeEl = document.getElementById("topbar-time");
  if (!timeEl) return;

  if (!LAST_UPDATED_ISO) {
    timeEl.textContent = "업데이트 정보 없음";
    applyFreshnessState(null);
    return;
  }

  const updated = new Date(LAST_UPDATED_ISO);
  const now = new Date();
  const diffMinutes = Math.floor((now - updated) / 60000);

  if (diffMinutes < 1) {
    timeEl.textContent = "방금 업데이트됨";
  } else if (diffMinutes < 60) {
    timeEl.textContent = `${diffMinutes}분 전 업데이트`;
  } else {
    const diffHours = Math.floor(diffMinutes / 60);
    timeEl.textContent = `${diffHours}시간 전 업데이트`;
  }

  applyFreshnessState(diffMinutes / 60);
}

/* ---------- 데이터 새로고침 (새로고침 버튼 + 아래로 당겨서 새로고침이 공유) ---------- */
function refreshDashboardData() {
  return Promise.all([loadNewsData(), loadRawNewsData()])
    .then(() => showToast("최신 데이터로 업데이트되었습니다."))
    .catch(() => showToast("업데이트에 실패했습니다. 잠시 후 다시 시도해 주세요."));
}

/* ---------- 새로고침 버튼: 브라우저 전체 새로고침 없이 news.json/raw_news.json만 다시 불러온다 ---------- */
function setupRefreshButton() {
  const btn = document.getElementById("refresh-btn");
  if (!btn) return;

  btn.addEventListener("click", () => {
    btn.classList.add("is-loading");
    refreshDashboardData().finally(() => btn.classList.remove("is-loading"));
  });
}

/* ---------- 아래로 당겨서 새로고침 (모바일 홈 화면 추가 시 "앱스러움"용) ----------
   PWA를 standalone으로 실행하면 브라우저 기본 pull-to-refresh가 꺼지는 경우가 많아서
   같은 동작을 직접 구현한다. 문서 맨 위(scrollY === 0)에서 아래로 당길 때만 개입하고,
   그 외에는 손을 대지 않아 필터바/브랜드펄스 등 기존 가로 스크롤과 절대 충돌하지 않는다. */
function setupPullToRefresh() {
  if (!("ontouchstart" in window)) return; // 터치 디바이스에서만 등록

  const indicator = document.getElementById("ptr-indicator");
  const label = document.getElementById("ptr-indicator-label");
  if (!indicator || !label) return;

  const READY_DISTANCE = 32; // 저항 적용 후 이만큼 당겨지면 "놓으면 새로고침"
  const MAX_PULL = 100;

  let startY = 0;
  let pulling = false;
  let ready = false;
  let refreshing = false;

  function reset() {
    indicator.classList.remove("is-visible", "is-ready");
    indicator.style.transform = "";
    label.textContent = "당겨서 새로고침";
  }

  function onTouchStart(e) {
    if (refreshing || window.scrollY > 0) {
      pulling = false;
      return;
    }
    startY = e.touches[0].clientY;
    pulling = true;
    ready = false;
  }

  function onTouchMove(e) {
    if (!pulling || refreshing) return;
    if (window.scrollY > 0) { pulling = false; reset(); return; } // 스크롤이 이미 내려갔으면 개입 중단

    const delta = e.touches[0].clientY - startY;
    if (delta <= 0) { reset(); return; }

    const pull = Math.min(delta * 0.5, MAX_PULL); // 저항감(1:1로 안 딸려오게)
    indicator.style.transform = `translate(-50%, ${pull - 36}px)`;
    indicator.classList.add("is-visible");

    const nowReady = pull >= READY_DISTANCE;
    if (nowReady !== ready) {
      ready = nowReady;
      indicator.classList.toggle("is-ready", ready);
      label.textContent = ready ? "놓으면 새로고침" : "당겨서 새로고침";
    }

    if (delta > 10 && e.cancelable) e.preventDefault(); // 당기는 동안 브라우저 바운스 억제
  }

  function onTouchEnd() {
    if (!pulling) return;
    pulling = false;

    if (ready && !refreshing) {
      refreshing = true;
      indicator.classList.add("is-loading");
      label.textContent = "새로고침 중…";
      indicator.style.transform = "translate(-50%, 16px)";

      refreshDashboardData().finally(() => {
        refreshing = false;
        indicator.classList.remove("is-loading");
        reset();
      });
    } else {
      reset();
    }
  }

  document.addEventListener("touchstart", onTouchStart, { passive: true });
  document.addEventListener("touchmove", onTouchMove, { passive: false });
  document.addEventListener("touchend", onTouchEnd);
  document.addEventListener("touchcancel", onTouchEnd);
}

/* ---------- TODAY'S SIGNAL ---------- */
function renderSignal(signal) {
  const headlineEl = document.getElementById("signal-headline");
  const descEl = document.getElementById("signal-description");

  if (!signal || !signal.headline) {
    headlineEl.textContent = "아직 분석된 시그널이 없습니다.";
    descEl.textContent = "뉴스 자동수집 후 AI 분석을 실행하면 이곳에 오늘의 시장 흐름이 표시됩니다.";
    return;
  }

  headlineEl.textContent = signal.headline;
  descEl.textContent = signal.description || "";
}

/* ---------- TOP NEWS ---------- */
function renderTopNews(newsList) {
  const bmwColumn = document.getElementById("top-news-own");
  const othersColumn = document.getElementById("top-news-others");
  if (!bmwColumn || !othersColumn) return;

  const bmwNews = newsList
    .filter((item) => item.topNewsGroup === "own")
    .sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999));

  const othersNews = newsList
    .filter((item) => item.topNewsGroup === "others")
    .sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999));

  renderTopNewsColumn(bmwColumn, bmwNews, "아직 BMW Motorrad 관련 분석 뉴스가 없습니다.");
  renderTopNewsColumn(othersColumn, othersNews, "아직 분석된 타사/업계 뉴스가 없습니다.");
}

function renderTopNewsColumn(container, newsArray, emptyMessage) {
  container.innerHTML = "";

  if (newsArray.length === 0) {
    container.innerHTML = `<div class="empty-state">${emptyMessage}</div>`;
    return;
  }

  newsArray.forEach((news, idx) => {
    const rank = news.rank ?? idx + 1;
    const card = document.createElement("article");
    card.className = "news-card";

    const tagClass = news.category === "COMPETITOR" ? "tag tag--competitor" : "tag";
    const categoryLabel = CATEGORY_LABELS[news.category] || escapeHtml(news.category);
    const importanceText = typeof news.importance === "number" ? news.importance.toFixed(1) : null;
    const hasInsight = news.whyItMatters || news.bmwInsight;

    card.innerHTML = `
      <div class="news-card__rank">${String(rank).padStart(2, "0")}</div>
      <div class="news-card__body">
        <div class="news-card__meta">
          <span class="${tagClass}">${categoryLabel}</span>
          ${importanceText ? `<span class="news-card__importance">${importanceText} / 5</span>` : ""}
          <span class="news-card__source">${escapeHtml(news.source)}</span>
          <span class="news-card__date">${formatDisplayDate(news.publishedAt)}</span>
        </div>
        <h4 class="news-card__title">${createNewsTitleLink(news)}</h4>
        ${news.summary ? `<p class="news-card__summary">${escapeHtml(news.summary)}</p>` : ""}
        ${hasInsight ? `
          <button class="insight-toggle" type="button" aria-expanded="false">
            VIEW INSIGHT <span class="insight-toggle__icon">▾</span>
          </button>
          <div class="news-card__insight" hidden>
            ${news.whyItMatters ? `
              <div class="news-card__insight-block">
                <span class="news-card__insight-label">WHY IT MATTERS</span>
                <p>${escapeHtml(news.whyItMatters)}</p>
              </div>` : ""}
            ${news.bmwInsight ? `
              <div class="news-card__insight-block">
                <span class="news-card__insight-label">BMW MOTORRAD WATCH POINT</span>
                <p>${escapeHtml(news.bmwInsight)}</p>
              </div>` : ""}
          </div>
        ` : ""}
      </div>
    `;

    container.appendChild(card);
  });

  container.querySelectorAll(".insight-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const panel = btn.nextElementSibling;
      const isOpen = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", String(!isOpen));
      panel.hidden = isOpen;
      btn.classList.toggle("is-open", !isOpen);
    });
  });
}

/* ---------- MARKET INTELLIGENCE (STEP 12-D: marketIntelligenceV2 UI Integration) ---------- */
// brandRole 코드값 -> 화면에 보여줄 작은 배지 표현. STEP 12-C에서 계산된 값을 그대로 표시만
// 한다(UI에서 재분류하지 않음). 알 수 없는/누락된 값은 배지 자체를 그리지 않는다(Legacy
// fallback 데이터나 STEP 12-D 이전 카드에는 brandRole이 없을 수 있음 — 요청서 15번 Case D).
const BRAND_ROLE_BADGE = {
  OWN: { label: "OWN", cls: "role-badge--own" },
  COMPETITOR: { label: "COMPETITOR", cls: "role-badge--competitor" },
  INDUSTRY: { label: "INDUSTRY", cls: "role-badge--industry" },
  MIXED: { label: "MIXED", cls: "role-badge--mixed" },
};

// insightType 코드값을 개발자 코드값 그대로 노출하지 않고, 사용자가 이해하기 쉬운 짧은
// 한국어 표현으로 바꾼다(요청서 6번). NEEDS_CONFIRMATION만 시각적으로 살짝 구분하되(색상만),
// 경고 아이콘/배경색 채우기 같은 과도한 경고 디자인은 쓰지 않는다.
const INSIGHT_TYPE_LABEL = {
  GROUPED: { label: "복수 기사", cls: "" },
  SINGLE_SIGNAL: { label: "단일 신호", cls: "" },
  NEEDS_CONFIRMATION: { label: "추가 확인 필요", cls: "is-needs-confirmation" },
};

function roleBadgeHtml(brandRole) {
  const badge = BRAND_ROLE_BADGE[brandRole];
  if (!badge) return ""; // brandRole 없음/알 수 없음 -> 조용히 생략 (요청서 15번 Case D)
  return `<span class="role-badge ${badge.cls}">${badge.label}</span>`;
}

function insightTypeHtml(insightType) {
  const info = INSIGHT_TYPE_LABEL[insightType];
  if (!info) return ""; // insightType 없음/알 수 없음 -> 조용히 생략 (요청서 15번 Case E)
  return `<span class="insight-type ${info.cls}">${info.label}</span>`;
}

function renderMarketIntelligence(intel) {
  // OWN WATCH는 기존 4개 컬럼과 별개로, 상단 full-width 강조 영역에 렌더링한다(요청서 1번).
  // marketIntelligence(v1, Legacy fallback)에는 ownWatch 키 자체가 없으므로 빈 배열로 처리된다
  // (요청서 15번 Case B) — renderOwnWatch()가 빈 배열을 compact empty state로 자연스럽게 처리한다.
  renderOwnWatch(intel.ownWatch || []);
  renderIntelColumn("intel-market", intel.market);
  renderIntelColumn("intel-competitor", intel.competitor);
  renderIntelColumn("intel-productTech", intel.productTech);
  renderIntelColumn("intel-customerTrend", intel.customerTrend);
}

function renderOwnWatch(items) {
  const container = document.getElementById("own-watch-cards");
  if (!container) return; // index.html에 own-watch 마크업이 없는 예외적 상황에서도 페이지가 죽지 않도록
  container.innerHTML = "";

  if (!items || items.length === 0) {
    // 요청서 4번: 영역 자체는 유지하되, compact한 empty state로 표시한다(큰 여백을 만들지 않음).
    container.innerHTML = `<div class="own-watch__empty">오늘 확인된 주요 BMW 전략 신호가 없습니다.</div>`;
    return;
  }

  items.forEach((item) => {
    container.appendChild(buildIntelCard(item));
  });
}

function renderIntelColumn(containerId, items) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";

  if (!items || items.length === 0) {
    container.innerHTML = `<div class="empty-state empty-state--intel">오늘 수집된 Insight가 없습니다.</div>`;
    return;
  }

  items.forEach((item) => {
    container.appendChild(buildIntelCard(item));
  });
}

// OWN WATCH 카드와 기존 4개 컬럼 카드가 동일한 마크업/스타일을 공유한다(요청서 9번: TOP NEWS와는
// 시각적으로 다르지만, Market Intelligence 내부에서는 하나의 일관된 카드 디자인을 유지).
function buildIntelCard(item) {
  const card = document.createElement("div");
  card.className = "intel-card";

  // 요청서 8번: brandRole/insightType을 한 줄(compact meta row)로 묶어 정보 위계 1번 자리에 둔다.
  // 둘 다 없는(Legacy) 카드는 이 줄 자체를 만들지 않는다(빈 줄 노출 방지).
  const roleHtml = roleBadgeHtml(item.brandRole);
  const insightHtml = insightTypeHtml(item.insightType);
  const topMetaHtml = (roleHtml || insightHtml)
    ? `<div class="intel-card__topmeta">${roleHtml}${insightHtml}</div>`
    : "";

  const relatedCount = typeof item.relatedNewsCount === "number" ? item.relatedNewsCount : 1;

  card.innerHTML = `
    ${topMetaHtml}
    <h5 class="intel-card__title">${escapeHtml(item.title || "")}</h5>
    ${item.description ? `<p class="intel-card__desc">${escapeHtml(item.description)}</p>` : ""}
    ${item.bmwNote ? `
    <p class="intel-card__bmw">
      <strong>WATCH POINT</strong>
      ${escapeHtml(item.bmwNote)}
    </p>` : ""}
    <div class="intel-card__meta">
      <span>관련 뉴스 ${relatedCount}건</span>
      <span class="impact-badge impact-${item.impact}">${item.impact}</span>
    </div>
  `;
  return card;
}

/* ---------- BRAND PULSE (STEP 8: 브랜드별 Intelligence Summary) ---------- */
const BRAND_DISPLAY_NAMES = {
  bmw: "BMW MOTORRAD",
  ducati: "DUCATI",
  triumph: "TRIUMPH",
  harley: "HARLEY-DAVIDSON",
  honda: "HONDA",
  yamaha: "YAMAHA",
};

const SIGNAL_INDICATOR = {
  RISING: { icon: "↑", label: "RISING", cls: "is-rising" },
  NEW: { icon: "●", label: "NEW", cls: "is-new" },
  CONTINUING: { icon: "→", label: "CONTINUING", cls: "is-continuing" },
  NORMAL: { icon: "—", label: "NORMAL", cls: "" },
  BASELINE: { icon: "—", label: "BASELINE", cls: "" },
  NO_SIGNIFICANT_UPDATE: { icon: "—", label: "NO UPDATE", cls: "" },
};

function renderBrandSummary(brandSummary) {
  const container = document.getElementById("brand-pulse-grid");
  if (!container) return;
  container.innerHTML = "";

  const brandOrder = ["bmw", "ducati", "triumph", "harley", "honda", "yamaha"];
  const hasData = brandOrder.some((b) => brandSummary[b]);

  if (!hasData) {
    container.innerHTML = `<div class="empty-state">브랜드 요약 데이터가 없습니다.</div>`;
    return;
  }

  brandOrder.forEach((brand) => {
    const info = brandSummary[brand];
    if (!info) return;

    const card = document.createElement("div");
    card.className = `brand-pulse-card${brand === "bmw" ? " is-own-brand" : ""}`;

    const signal = SIGNAL_INDICATOR[info.signal] || SIGNAL_INDICATOR.NORMAL;
    const isEmpty = info.newsCount === 0;
    const themeText = [info.primaryTheme, info.secondaryTheme].filter(Boolean).join(" · ");

    card.innerHTML = `
      <div class="brand-pulse-card__name">${BRAND_DISPLAY_NAMES[brand] || brand.toUpperCase()}</div>
      <div class="brand-pulse-card__count">${info.newsCount} NEWS</div>
      ${isEmpty
        ? `<div class="brand-pulse-card__empty">NO SIGNIFICANT UPDATE</div>`
        : `
          <div class="brand-pulse-card__topic" title="${escapeHtml(themeText)}">${escapeHtml(themeText || "-")}</div>
          <div class="brand-pulse-card__signal ${signal.cls}">${signal.icon} ${signal.label}</div>
        `}
    `;
    container.appendChild(card);
  });
}

/* ---------- SOURCE MONITOR ---------- */
/* STEP 9.2: 필터 칩은 의미가 다른 두 그룹이 섞여 있다.
   - 브랜드 필터(BMW/Ducati/Triumph/Harley/Honda/Yamaha): "이 브랜드를 실제로 다룬 기사"
     → brandGroups(STEP 9.1에서 raw_news.json에 추가된 필드) 기준으로 판정한다.
   - 매체 필터(MOTORCYCLE=kmnews/GENERAL=naver/MARKET=google)와 ALL: "어느 채널로 들어왔는지"
     → 기존 sourceGroup 기준을 그대로 유지한다(요청 사항: 두 필터의 의미를 분리, 매체 필터는 변경 금지). */
const BRAND_FILTER_KEYS = ["bmw", "ducati", "triumph", "harley", "honda", "yamaha"];

function renderSourceMonitor(newsList) {
  const container = document.getElementById("source-monitor-grid");
  container.innerHTML = "";

  let filtered;
  if (currentFilter === "all") {
    filtered = [...newsList];
  } else if (BRAND_FILTER_KEYS.includes(currentFilter)) {
    filtered = newsList.filter((item) => (item.brandGroups || []).includes(currentFilter));
  } else {
    filtered = newsList.filter((item) => item.sourceGroup === currentFilter);
  }

  // SOURCE MONITOR는 raw_news.json(분석 전 원본)을 사용하므로 importance 값이
  // 항상 비어있어 중요도순 정렬이 무의미했다. 항상 최신순으로 고정한다.
  filtered.sort((a, b) => (b.publishedAt || "").localeCompare(a.publishedAt || ""));

  // 요청서 12번: 그룹별 최대 5개. "ALL" 필터일 때는 그룹별로 5개씩 골라 합친다.
  if (currentFilter === "all") {
    const perGroupCount = {};
    filtered = filtered.filter((item) => {
      const g = item.sourceGroup;
      perGroupCount[g] = (perGroupCount[g] || 0) + 1;
      return perGroupCount[g] <= 5;
    });
  } else {
    filtered = filtered.slice(0, 5);
  }

  if (filtered.length === 0) {
    container.innerHTML = `<div class="empty-state">해당 조건의 기사가 없습니다.</div>`;
    return;
  }

  filtered.forEach((item) => {
    const row = document.createElement("article");
    row.className = "source-row";

    row.innerHTML = `
      <span class="source-row__date">${formatDisplayDate(item.publishedAt)}</span>
      <h6 class="source-row__title">${createNewsTitleLink(item)}</h6>
      <span class="source-row__meta">
        ${escapeHtml(item.source)}${item.sourceType ? ` · ${escapeHtml(item.sourceType.toUpperCase())}` : ""}
      </span>
    `;
    container.appendChild(row);
  });
}

/* ---------- 필터 / 정렬 바 ---------- */
function setupFilterBar() {
  const bar = document.getElementById("filter-bar");

  bar.addEventListener("click", (e) => {
    const target = e.target.closest(".filter-chip");
    if (!target) return;

    if (target.dataset.filter) {
      bar.querySelectorAll("[data-filter]").forEach((el) => el.classList.remove("is-active"));
      target.classList.add("is-active");
      currentFilter = target.dataset.filter;
    }

    if (RAW_NEWS_DATA) renderSourceMonitor(RAW_NEWS_DATA.news || []);
  });

  setupFilterBarScrollHint(bar);
}

/* ---------- 필터바 가로 스크롤 힌트 ----------
   모바일에서 필터 칩이 화면 밖으로 넘어갈 때, "더 있다"는 걸 좌우 그라데이션 페이드로
   알려준다. 데스크톱처럼 flex-wrap으로 줄바꿈되어 스크롤이 아예 없는 경우엔
   scrollWidth와 clientWidth가 같아서 두 클래스 모두 자연히 꺼진 채로 유지된다. */
function setupFilterBarScrollHint(bar) {
  const wrap = document.getElementById("filter-bar-wrap");
  if (!wrap || !bar) return;

  function update() {
    const maxScroll = bar.scrollWidth - bar.clientWidth;
    const scrolled = bar.scrollLeft;
    wrap.classList.toggle("has-scroll-left", scrolled > 4);
    wrap.classList.toggle("has-scroll-right", scrolled < maxScroll - 4);
  }

  bar.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update);

  // SOURCE MONITOR는 모바일 탭 전환으로 부모가 display:none <-> block 되는 섹션이라,
  // 처음 로드 시점엔 filter-bar의 폭이 0이라 스크롤 가능 여부를 제대로 계산할 수 없다.
  // ResizeObserver로 실제 크기가 잡히는 순간(탭이 열리는 순간)마다 다시 계산한다.
  if (window.ResizeObserver) {
    new ResizeObserver(update).observe(bar);
  }

  update();
}

/* ---------- TEAM BRIEF ---------- */
// 팀 공유용 요약에는 BMW 자사 뉴스는 포함하지 않는다 (자사 모니터링은 하되 외부 공유는 X)
function getTeamBriefItems(data) {
  return (data.news || [])
    .filter((n) => n.topNewsGroup === "others")
    .sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999));
}

/* ---------- 전일 대비 비교 (요청: "어제 공유한 뉴스와 오늘 공유할 뉴스가 겹치는지 미리 보고 싶다") ----------
   Supabase(team_brief_archive 테이블)에 날짜별 브리핑 목록을 저장해두고, 오늘 화면을
   그릴 때 가장 최근에 저장된 이전 날짜와 비교한다. 로그인 기능에서 이미 쓰던 것과 같은
   Supabase 프로젝트를 그대로 사용하므로(_supabase는 auth-client.js가 만든 전역),
   어느 브라우저/기기에서 열어도 팀 전체가 같은 "어제" 기록을 본다.
   supabase_brief_archive_schema.sql을 Supabase SQL Editor에서 먼저 실행해야 동작한다. */
const BRIEF_ARCHIVE_TABLE = "team_brief_archive";

// 오늘보다 이전 날짜 중 가장 최근 1건을 가져온다. 실패(미설정/네트워크 오류/RLS 거부)해도
// 화면이 깨지지 않도록 항상 { yesterdayKey: null, yesterdayItems: null } 형태로 조용히 fallback한다.
async function fetchYesterdayBrief(todayKey) {
  const empty = { yesterdayKey: null, yesterdayItems: null };
  if (typeof _supabase === "undefined" || !_supabase || !todayKey) return empty;

  try {
    const { data, error } = await _supabase
      .from(BRIEF_ARCHIVE_TABLE)
      .select("date, items")
      .lt("date", todayKey)
      .order("date", { ascending: false })
      .limit(1)
      .maybeSingle();

    if (error || !data) return empty;
    return { yesterdayKey: data.date, yesterdayItems: data.items || [] };
  } catch (e) {
    console.error("전일 브리핑 조회 실패:", e);
    return empty;
  }
}

// 오늘 브리핑을 upsert한다 — 같은 날짜(primary key)로 여러 번 새로고침해도 한 행만 계속 갱신된다.
async function saveTodayBrief(todayKey, items) {
  if (typeof _supabase === "undefined" || !_supabase || !todayKey) return;

  try {
    const { data: userData } = await _supabase.auth.getUser();
    const { error } = await _supabase.from(BRIEF_ARCHIVE_TABLE).upsert({
      date: todayKey,
      items,
      updated_at: new Date().toISOString(),
      updated_by: userData?.user?.id || null,
    });
    if (error) console.error("오늘 브리핑 저장 실패:", error);
  } catch (e) {
    console.error("오늘 브리핑 저장 실패:", e);
    /* 저장 실패해도 화면 표시 자체는 계속 동작해야 하므로 조용히 무시한다 */
  }
}

function formatBriefDateLabel(dateStr) {
  if (!dateStr) return "-";
  const parts = dateStr.split("-");
  return parts.length === 3 ? `${parts[1]}.${parts[2]}` : dateStr;
}

function renderBriefCompare(todayKey, todayItems, yesterdayKey, yesterdayItems, yesterdayIds) {
  const summaryEl = document.getElementById("brief-compare-summary");
  const gridEl = document.getElementById("brief-compare-grid");
  if (!summaryEl || !gridEl) return;

  if (!yesterdayKey || !yesterdayItems) {
    summaryEl.textContent = "비교할 이전 브리핑 기록이 없습니다. 오늘 저장하면 내일부터 비교가 표시됩니다.";
    gridEl.innerHTML = "";
    return;
  }

  const todayIds = new Set(todayItems.map((n) => n.id));
  const newCount = todayItems.filter((n) => !yesterdayIds.has(n.id)).length;
  const dupCount = todayItems.length - newCount;
  const todayLabel = formatBriefDateLabel(todayKey);
  const yesterdayLabel = formatBriefDateLabel(yesterdayKey);

  summaryEl.innerHTML = `${yesterdayLabel} 대비 신규 <strong>${newCount}건</strong> · 중복 <strong class="${dupCount > 0 ? "is-dup" : ""}">${dupCount}건</strong>`;

  const renderCol = (label, items, highlightIds) => `
    <div class="brief-compare__col">
      <div class="brief-compare__col-label">${escapeHtml(label)}</div>
      ${items.length === 0
        ? `<div class="brief-compare__empty">공유된 뉴스 없음</div>`
        : items.map((n) => `
          <div class="brief-compare__row${highlightIds.has(n.id) ? " is-dup" : ""}">${escapeHtml(n.title)}</div>
        `).join("")}
    </div>`;

  gridEl.innerHTML =
    renderCol(`${yesterdayLabel} 공유함`, yesterdayItems, todayIds) +
    renderCol(`${todayLabel} 공유 예정`, todayItems, yesterdayIds);
}

async function renderTeamBrief(data) {
  const briefBody = document.getElementById("brief-body");
  document.getElementById("brief-date").textContent = data.meta.date
    ? data.meta.date.replaceAll("-", ".")
    : "-";

  const topNews = getTeamBriefItems(data);

  // 오늘 브리핑을 Supabase에 저장하고(같은 날 재저장해도 upsert로 덮어쓸 뿐이라 안전),
  // 오늘보다 이전인 가장 최근 저장 날짜를 "어제"로 삼아 비교한다.
  const todayKey = data.meta.date || null;
  const { yesterdayKey, yesterdayItems } = await fetchYesterdayBrief(todayKey);
  const yesterdayIds = new Set((yesterdayItems || []).map((n) => n.id));

  if (todayKey) {
    const todayItemsForArchive = topNews.map((n) => ({
      id: n.id,
      title: n.title,
      url: n.url,
      source: n.source,
      publishedAt: n.publishedAt,
    }));
    saveTodayBrief(todayKey, todayItemsForArchive); // 화면 렌더링을 막지 않도록 저장은 기다리지 않는다
  }

  renderBriefCompare(todayKey, topNews, yesterdayKey, yesterdayItems, yesterdayIds);

  if (topNews.length === 0) {
    briefBody.innerHTML = `<div class="brief-item"><p>아직 분석된 뉴스가 없습니다.</p></div>`;
  } else {
    briefBody.innerHTML = topNews.map((n, idx) => {
      const isDup = yesterdayIds.has(n.id);
      return `
      <div class="brief-item${isDup ? " brief-item--dup" : ""}">
        <span class="brief-item__label">(${idx + 1})</span>
        <p>
          ${isDup ? `<span class="brief-dup-badge" title="${escapeHtml(formatBriefDateLabel(yesterdayKey))} 브리핑에도 포함됐던 뉴스입니다">🔁 어제도 공유</span> ` : ""}${escapeHtml(n.title)}<br>
          <a href="${escapeHtml(n.url)}" target="_blank" rel="noopener noreferrer" class="brief-item__link">${escapeHtml(shortenUrlForShare(n.url))}</a>
        </p>
      </div>
    `;
    }).join("");
  }
}

function setupCopyBriefButton() {
  const btn = document.getElementById("copy-brief-btn");
  btn.addEventListener("click", () => {
    if (!NEWS_DATA) return;

    const meta = NEWS_DATA.meta;
    const topNews = getTeamBriefItems(NEWS_DATA);

    const dateObj = meta.date ? new Date(meta.date) : null;
    const dayNames = ["일요일", "월요일", "화요일", "수요일", "목요일", "금요일", "토요일"];
    const dateLine = dateObj
      ? `${dateObj.getFullYear()}년 ${dateObj.getMonth() + 1}월 ${dateObj.getDate()}일 ${dayNames[dateObj.getDay()]}`
      : "-";

    const lines = [
      `[${dateLine} 데일리뉴스]`,
      `좋은 아침입니다!`,
      ``,
      `📍뉴스`,
    ];

    if (topNews.length === 0) {
      lines.push(`아직 분석된 뉴스가 없습니다.`);
    } else {
      topNews.forEach((n, idx) => {
        lines.push(`(${idx + 1}) ${n.title}`);
        lines.push(shortenUrlForShare(n.url));
      });
    }

    const text = lines.join("\n");

    navigator.clipboard.writeText(text)
      .then(() => {
        showToast("Team Brief가 클립보드에 복사되었습니다.");
        const originalLabel = btn.textContent;
        btn.textContent = "COPIED";
        btn.classList.add("is-copied");
        setTimeout(() => {
          btn.textContent = originalLabel;
          btn.classList.remove("is-copied");
        }, 1800);
      })
      .catch(() => showToast("복사에 실패했습니다. 브라우저 권한을 확인해 주세요."));
  });
}

/* ---------- 원문 보기 (샘플 데이터: example.com URL) ---------- */
/* ---------- 뉴스 제목 -> 원문 링크 생성 (요청서 14번: URL 없으면 안전하게 일반 텍스트) ---------- */
function createNewsTitleLink(item) {
  const safeTitle = escapeHtml(item.title);
  if (item.url && item.url !== "#") {
    return `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer" class="news-title-link">${safeTitle}</a>`;
  }
  return `<span>${safeTitle}</span>`;
}

/* ---------- ISO 8601 날짜 -> 화면 표시용 가공 (요청서 15번: 데이터 원본은 유지, 화면만 가공) ---------- */
function formatDisplayDate(isoString) {
  if (!isoString) return "";
  const d = new Date(isoString);
  if (Number.isNaN(d.getTime())) return escapeHtml(isoString);

  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${mm}.${dd}`;
}

/* ---------- Sidebar Nav 하이라이트 ---------- */
/* ---------- Mobile Bottom Tab Bar ----------
   900px 이하에서만 의미가 있다. 탭 클릭 시 해당 섹션에 is-tab-active를 부여하고
   나머지는 CSS(.section:not(.signal-section){display:none})로 숨긴다.
   TODAY'S SIGNAL(#overview)은 signal-section 클래스로 항상 노출된다. */
/* TOP NEWS 섹션 하나를 "TOP 5"(업계 전체) / "OWN"(자사) 두 개의 모바일 탭이
   공유한다 — 탭 id는 가상의 id(top-news-competitor / top-news-own)이고 실제
   섹션 id는 둘 다 top-news이다. 이 매핑을 통해 섹션 표시 여부와 그 안의
   top-news-split__col 중 어느 쪽을 보여줄지를 함께 결정한다. */
const TOPNEWS_TAB_TO_COL = {
  "top-news-competitor": "competitor",
  "top-news-own": "own",
};

function setupMobileTabbar() {
  const tabs = document.querySelectorAll(".mobile-tab");
  if (!tabs.length) return;

  function resolveSectionId(tabId) {
    return TOPNEWS_TAB_TO_COL[tabId] ? "top-news" : tabId;
  }

  function activateTab(tabId) {
    const sectionId = resolveSectionId(tabId);
    tabs.forEach((t) => t.classList.toggle("is-active", t.dataset.tab === tabId));
    document.querySelectorAll(".section:not(.signal-section)").forEach((sec) => {
      sec.classList.toggle("is-tab-active", sec.id === sectionId);
    });

    const col = TOPNEWS_TAB_TO_COL[tabId];
    if (col) {
      document.querySelectorAll(".top-news-split__col").forEach((c) => {
        c.classList.toggle("is-topnews-active", c.dataset.topnewsCol === col);
      });
    }
  }

  tabs.forEach((tab) => {
    tab.addEventListener("click", (e) => {
      e.preventDefault();
      activateTab(tab.dataset.tab);
      document.getElementById(resolveSectionId(tab.dataset.tab))?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  // 초기 상태: 첫 번째 탭(TOP 5 = 업계 전체 뉴스)을 기본으로 노출
  activateTab(tabs[0].dataset.tab);
}

/* ---------- MARKET INTELLIGENCE 모바일 Sub Tab ----------
   Desktop에서는 CSS로 탭바 자체가 숨겨지고 4개 컬럼이 그대로 보인다.
   Mobile에서는 탭을 눌러 선택된 카테고리 컬럼만 보이게 전환한다. */
function setupIntelTabbar() {
  const tabbar = document.getElementById("intel-tabbar");
  if (!tabbar) return;

  const tabs = tabbar.querySelectorAll(".intel-tab");
  const panels = document.querySelectorAll("[data-intel-panel]");

  function activate(key) {
    tabs.forEach((t) => t.classList.toggle("is-active", t.dataset.intelTab === key));
    panels.forEach((p) => p.classList.toggle("is-intel-active", p.dataset.intelPanel === key));
  }

  tabbar.addEventListener("click", (e) => {
    const target = e.target.closest(".intel-tab");
    if (!target) return;
    activate(target.dataset.intelTab);
  });

  activate(tabs[0].dataset.intelTab);
}

function setupNavHighlight() {
  const navItems = document.querySelectorAll(".side-nav__item");
  const sections = Array.from(navItems).map((item) =>
    document.getElementById(item.dataset.nav)
  );

  navItems.forEach((item) => {
    item.addEventListener("click", () => {
      navItems.forEach((el) => el.classList.remove("is-active"));
      item.classList.add("is-active");
    });
  });

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const id = entry.target.id;
          navItems.forEach((el) => {
            el.classList.toggle("is-active", el.dataset.nav === id);
          });
        }
      });
    },
    { rootMargin: "-20% 0px -70% 0px" }
  );

  sections.forEach((section) => {
    if (section) observer.observe(section);
  });
}

/* ---------- Toast ---------- */
let toastTimer = null;
function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.add("is-visible");

  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.classList.remove("is-visible");
  }, 2600);
}

/* ---------- HTML escape (XSS 방지) ---------- */
/* ---------- 공유용 URL 표시 축약 (링크 자체는 그대로, 보이는 글자만 축약) ---------- */
function shortenUrlForShare(url, maxLength = 70) {
  if (!url || url.length <= maxLength) return url;
  try {
    const u = new URL(url);
    const short = `${u.origin}${u.pathname}`;
    return short.length <= maxLength ? short : short.slice(0, maxLength - 1) + "…";
  } catch {
    return url.slice(0, maxLength - 1) + "…";
  }
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
