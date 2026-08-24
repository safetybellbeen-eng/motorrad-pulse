// ==========================================================
// MOTORRAD PULSE — 로그인 상태 표시 UI
// auth-guard.js가 인증 확인을 끝내고 쏘는 "auth-ready" 이벤트를 받아서
// 상단 topbar에 "로그인한 아이디 · (관리자면) 승인 관리 · 로그아웃"을 붙인다.
// 기존 topbar 마크업/스타일은 건드리지 않고 DOM에 한 블록만 추가한다.
// ==========================================================

document.addEventListener("auth-ready", function (e) {
  const profile = e.detail;
  const meta = document.querySelector(".topbar__meta");
  if (!meta || !profile) return;

  const box = document.createElement("div");
  box.className = "topbar__account";

  const who = document.createElement("span");
  who.className = "topbar__account-name";
  who.textContent = profile.role === "admin" ? `${profile.username} (관리자)` : profile.username;
  box.appendChild(who);

  if (profile.role === "admin") {
    const adminLink = document.createElement("a");
    adminLink.href = "./admin.html";
    adminLink.className = "topbar__account-link";
    adminLink.textContent = "승인 관리";
    box.appendChild(adminLink);
  }

  const logoutBtn = document.createElement("button");
  logoutBtn.type = "button";
  logoutBtn.className = "topbar__account-link topbar__account-logout";
  logoutBtn.textContent = "로그아웃";
  logoutBtn.addEventListener("click", function () {
    signOutAndRedirect();
  });
  box.appendChild(logoutBtn);

  meta.appendChild(box);
});
