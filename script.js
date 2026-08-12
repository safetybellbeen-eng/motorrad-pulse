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
let currentSort = "latest";

/* ---------- 초기 로드 ----------
   news.json: AI 분석이 완료된 데이터 (TOP NEWS, MARKET INTELLIGENCE, TEAM BRIEF)
   raw_news.json: 자동수집된 원본 전체 (SOURCE MONITOR 전용, 요청서 42/43/44번)
   두 파일은 서로 독립적으로 로드한다 — 한쪽이 없거나 실패해도 다른 한쪽은 정상 표시되어야 한다. */
document.addEventListener("DOMContentLoaded", () => {
  fetch("./data/news.json")
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
    });

  fetch("./data/raw_news.json")
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
    });

  setupNavHighlight();
  setupFilterBar();
  setupCopyBriefButton();
});

/* ---------- HEADER ---------- */
function renderHeader(meta) {
  const dateEl = document.getElementById("topbar-date");
  const timeEl = document.getElementById("topbar-time");
  if (dateEl) {
    dateEl.textContent = meta.date
      ? `${meta.date.replaceAll("-", ".")}  ${meta.dayLabel || ""}`
      : "날짜 정보 없음";
  }
  if (timeEl) timeEl.textContent = meta.lastUpdated || "-";
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
  const container = document.getElementById("top-news-list");
  container.innerHTML = "";

  // isTopNews === true 인 기사만, rank 오름차순으로 정렬 (요청서 22번 로직: rank가 없다면 importance 내림차순 폴백)
  const topOnly = newsList
    .filter((item) => item.isTopNews)
    .sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999) || b.importance - a.importance)
    .slice(0, 5);

  if (topOnly.length === 0) {
    container.innerHTML = `<div class="empty-state">아직 AI 분석이 완료된 뉴스가 없습니다.</div>`;
    return;
  }

  topOnly.forEach((news, idx) => {
    const rank = news.rank ?? idx + 1;
    const card = document.createElement("article");
    card.className = "news-card";

    const tagClass = news.category === "COMPETITOR" ? "tag tag--competitor" : "tag";
    const categoryLabel = CATEGORY_LABELS[news.category] || escapeHtml(news.category);

    card.innerHTML = `
      <div class="news-card__rank">${String(rank).padStart(2, "0")}</div>
      <div class="news-card__body">
        <div class="news-card__meta">
          <span class="${tagClass}">${categoryLabel}</span>
          <span class="news-card__source">${escapeHtml(news.source)}</span>
          <span class="news-card__date">${formatDisplayDate(news.publishedAt)}</span>
        </div>
        <h4 class="news-card__title">${createNewsTitleLink(news)}</h4>
        <p class="news-card__summary">${escapeHtml(news.summary)}</p>
        <div class="news-card__footer">
          <button class="read-btn" type="button" data-url="${escapeHtml(news.url)}">READ ORIGINAL</button>
        </div>
      </div>
    `;

    container.appendChild(card);
  });

  container.querySelectorAll(".read-btn").forEach((btn) => {
    btn.addEventListener("click", () => handleReadOriginal(btn.dataset.url));
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
      <p class="intel-card__desc">${escapeHtml(item.description)}</p>
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

  if (currentSort === "importance") {
    filtered.sort((a, b) => (b.importance ?? 0) - (a.importance ?? 0));
  } else {
    // LATEST: ISO 8601 문자열은 사전식 정렬이 곧 시간순 정렬과 동일 -> 내림차순으로 최신순
    filtered.sort((a, b) => b.publishedAt.localeCompare(a.publishedAt));
  }

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
    const card = document.createElement("article");
    card.className = "source-card";
    const importanceText = typeof item.importance === "number" ? item.importance.toFixed(1) : "-";
    const summaryText = item.summary || "원문 확인이 필요한 뉴스입니다.";
    card.innerHTML = `
      <div class="source-card__top">
        <span class="source-card__source">${escapeHtml(item.source)}</span>
        <span>${formatDisplayDate(item.publishedAt)}</span>
      </div>
      <h6 class="source-card__title">${createNewsTitleLink(item)}</h6>
      <p class="source-card__summary">${escapeHtml(summaryText)}</p>
      <div class="source-card__bottom">
        <span class="source-card__category">${CATEGORY_LABELS[item.category] || (item.category ? escapeHtml(item.category) : "미분류")}</span>
        <span class="source-card__importance">Importance ${importanceText}</span>
        <button class="source-card__link" type="button" data-url="${escapeHtml(item.url)}">→ Read</button>
      </div>
    `;
    container.appendChild(card);
  });

  container.querySelectorAll(".source-card__link").forEach((btn) => {
    btn.addEventListener("click", () => handleReadOriginal(btn.dataset.url));
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

    if (target.dataset.sort) {
      bar.querySelectorAll("[data-sort]").forEach((el) => el.classList.remove("is-active"));
      target.classList.add("is-active");
      currentSort = target.dataset.sort;
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

  const intel = data.marketIntelligence || {};

  const sections = [
    { label: "① MARKET", text: intel.market?.[0]?.description || "" },
    { label: "② COMPETITOR", text: intel.competitor?.[0]?.description || "" },
    { label: "③ PRODUCT & TECH", text: intel.productTech?.[0]?.description || "" },
    { label: "④ CUSTOMER", text: intel.customerTrend?.[0]?.description || "" }
  ];

  briefBody.innerHTML = sections.map((s) => `
    <div class="brief-item">
      <span class="brief-item__label">${s.label}</span>
      <p>${escapeHtml(s.text || "아직 분석된 내용이 없습니다.")}</p>
    </div>
  `).join("");

  document.getElementById("brief-insight-text").textContent =
    data.meta.todaySignal?.headline || "아직 분석된 시그널이 없습니다.";
}

function setupCopyBriefButton() {
  const btn = document.getElementById("copy-brief-btn");
  btn.addEventListener("click", () => {
    if (!NEWS_DATA) return;

    const intel = NEWS_DATA.marketIntelligence || {};
    const meta = NEWS_DATA.meta;

    const text = [
      `MOTORRAD DAILY BRIEF`,
      `${meta.date ? meta.date.replaceAll("-", ".") : "-"}`,
      ``,
      `① MARKET`,
      intel.market?.[0]?.description || "아직 분석된 내용이 없습니다.",
      ``,
      `② COMPETITOR`,
      intel.competitor?.[0]?.description || "아직 분석된 내용이 없습니다.",
      ``,
      `③ PRODUCT & TECH`,
      intel.productTech?.[0]?.description || "아직 분석된 내용이 없습니다.",
      ``,
      `④ CUSTOMER`,
      intel.customerTrend?.[0]?.description || "아직 분석된 내용이 없습니다.",
      ``,
      `TODAY'S INSIGHT`,
      meta.todaySignal?.headline || "아직 분석된 시그널이 없습니다."
    ].join("\n");

    navigator.clipboard.writeText(text)
      .then(() => showToast("Team Brief가 클립보드에 복사되었습니다."))
      .catch(() => showToast("복사에 실패했습니다. 브라우저 권한을 확인해 주세요."));
  });
}

/* ---------- 원문 보기 (샘플 데이터: example.com URL) ---------- */
function handleReadOriginal(url) {
  if (!url || url === "#") {
    showToast("원문 URL이 없는 기사입니다.");
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

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
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
