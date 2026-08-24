/* ==========================================================
   MOTORRAD PULSE — 홈 화면 추가(PWA) 서비스워커
   목적: "앱처럼" 느껴지도록 정적 셸(shell)만 캐싱해서 재실행이 빠르게 뜨도록 한다.
   data/*.json(뉴스 데이터)은 항상 네트워크 최신본을 우선하고, 실패할 때만
   캐시로 폴백한다 — 오래된 뉴스가 캐시에 남아 보이는 걸 방지하기 위함이다.
   ========================================================== */

const SHELL_CACHE = "motorrad-pulse-shell-v1";

const SHELL_FILES = [
  "./index.html",
  "./login.html",
  "./style.css",
  "./auth.css",
  "./script.js",
  "./auth-client.js",
  "./auth-guard.js",
  "./auth-ui.js",
  "./login.js",
  "./manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) =>
      cache.addAll(SHELL_FILES).catch(() => {
        /* 일부 파일이 없어도(예: auth-config.js는 사용자가 직접 채워넣는 파일이라
           경로가 다를 수 있음) 설치 자체가 실패하지 않도록 한다. */
      })
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // 외부 폰트/CDN/Supabase 요청은 그대로 통과

  const isDataFile = url.pathname.includes("/data/");

  if (isDataFile) {
    // 뉴스 데이터: 네트워크 우선, 실패 시에만 캐시 폴백
    event.respondWith(
      fetch(req)
        .then((res) => {
          const clone = res.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(req, clone));
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // 앱 셸(정적 파일): 캐시 우선, 백그라운드로 최신본 갱신
  event.respondWith(
    caches.match(req).then((cached) => {
      const network = fetch(req)
        .then((res) => {
          const clone = res.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(req, clone));
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
