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
      renderMarketIntelligence(data.marketIntelligence || {});
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
function updateRelativeTimeDisplay() {
  const timeEl = document.getElementById("topbar-time");
  if (!timeEl) return;

  if (!LAST_UPDATED_ISO) {
    timeEl.textContent = "업데이트 정보 없음";
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
}

/* ---------- 새로고침 버튼: 브라우저 전체 새로고침 없이 news.json/raw_news.json만 다시 불러온다 ---------- */
function setupRefreshButton() {
  const btn = document.getElementById("refresh-btn");
  if (!btn) return;

  btn.addEventListener("click", () => {
    btn.classList.add("is-loading");
    Promise.all([loadNewsData(), loadRawNewsData()])
      .then(() => showToast("최신 데이터로 업데이트되었습니다."))
      .catch(() => showToast("업데이트에 실패했습니다. 잠시 후 다시 시도해 주세요."))
      .finally(() => btn.classList.remove("is-loading"));
  });
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

/* ---------- MARKET INTELLIGENCE ---------- */
function renderMarketIntelligence(intel) {
  renderIntelColumn("intel-market", intel.market);
  renderIntelColumn("intel-competitor", intel.competitor);
  renderIntelColumn("intel-productTech", intel.productTech);
  renderIntelColumn("intel-customerTrend", intel.customerTrend);
}

function renderIntelColumn(containerId, items) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";

  (items || []).forEach((item) => {
    const card = document.createElement("div");
    card.className = "intel-card";
    card.innerHTML = `
      <h5 class="intel-card__title">${escapeHtml(item.title)}</h5>
      ${item.description ? `<p class="intel-card__desc">${escapeHtml(item.description)}</p>` : ""}
      <div class="intel-card__meta">
        <span>관련 뉴스 ${item.relatedNewsCount}건</span>
        <span class="impact-badge impact-${item.impact}">${item.impact}</span>
      </div>
      <p class="intel-card__bmw">
        <strong>BMW MOTORRAD</strong>
        ${escapeHtml(item.bmwNote)}
      </p>
    `;
    container.appendChild(card);
  });
}

/* ---------- SOURCE MONITOR ---------- */
function renderSourceMonitor(newsList) {
  const container = document.getElementById("source-monitor-grid");
  container.innerHTML = "";

  let filtered = currentFilter === "all"
    ? [...newsList]
    : newsList.filter((item) => item.sourceGroup === currentFilter);

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
}

/* ---------- TEAM BRIEF ---------- */
function renderTeamBrief(data) {
  const briefBody = document.getElementById("brief-body");
  document.getElementById("brief-date").textContent = data.meta.date
    ? data.meta.date.replaceAll("-", ".")
    : "-";

  // 팀 공유용 요약에는 BMW 자사 뉴스는 포함하지 않는다 (자사 모니터링은 하되 외부 공유는 X)
  const topNews = (data.news || [])
    .filter((n) => n.topNewsGroup === "others")
    .sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999));

  if (topNews.length === 0) {
    briefBody.innerHTML = `<div class="brief-item"><p>아직 분석된 뉴스가 없습니다.</p></div>`;
  } else {
    briefBody.innerHTML = topNews.map((n, idx) => `
      <div class="brief-item">
        <span class="brief-item__label">(${idx + 1})</span>
        <p>
          ${escapeHtml(n.title)}<br>
          <a href="${escapeHtml(n.url)}" target="_blank" rel="noopener noreferrer" class="brief-item__link">${escapeHtml(shortenUrlForShare(n.url))}</a>
        </p>
      </div>
    `).join("");
  }
}

function setupCopyBriefButton() {
  const btn = document.getElementById("copy-brief-btn");
  btn.addEventListener("click", () => {
    if (!NEWS_DATA) return;

    const meta = NEWS_DATA.meta;
    // 팀 공유용 요약에는 BMW 자사 뉴스는 포함하지 않는다 (자사 모니터링은 하되 외부 공유는 X)
    const topNews = (NEWS_DATA.news || [])
      .filter((n) => n.topNewsGroup === "others")
      .sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999));

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
function setupMobileTabbar() {
  const tabs = document.querySelectorAll(".mobile-tab");
  if (!tabs.length) return;

  function activateTab(targetId) {
    tabs.forEach((t) => t.classList.toggle("is-active", t.dataset.tab === targetId));
    document.querySelectorAll(".section:not(.signal-section)").forEach((sec) => {
      sec.classList.toggle("is-tab-active", sec.id === targetId);
    });
  }

  tabs.forEach((tab) => {
    tab.addEventListener("click", (e) => {
      e.preventDefault();
      activateTab(tab.dataset.tab);
      document.getElementById(tab.dataset.tab)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  // 초기 상태: 첫 번째 탭(TOP NEWS)을 기본으로 노출
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
