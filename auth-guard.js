// ==========================================================
// MOTORRAD PULSE — Auth Guard
// index.html / admin.html의 <head>에서 supabase-js + auth-config.js +
// auth-client.js 바로 다음에 로드한다. 로그인/승인 여부를 확인하기 전까지
// <html class="auth-checking">가 style.css 규칙으로 body를 숨겨서
// 로그인 안 된 화면이 잠깐이라도 노출되는 것을 막는다.
//
// admin.html에서만 관리자 권한까지 요구하려면, 이 스크립트를 불러오기 전에
// 다음을 먼저 선언한다: <script>window.__REQUIRE_ADMIN__ = true;</script>
// ==========================================================

(async function authGuard() {
  try {
    const { data: sessionData } = await _supabase.auth.getSession();
    const session = sessionData && sessionData.session;

    if (!session) {
      window.location.replace("./login.html");
      return;
    }

    const profile = await fetchOwnProfile();

    if (!profile || profile.status !== "approved") {
      await _supabase.auth.signOut();
      window.location.replace("./login.html");
      return;
    }

    if (window.__REQUIRE_ADMIN__ && profile.role !== "admin") {
      window.location.replace("./index.html");
      return;
    }

    window.__CURRENT_PROFILE__ = profile;
    document.documentElement.classList.remove("auth-checking");
    document.dispatchEvent(new CustomEvent("auth-ready", { detail: profile }));
  } catch (e) {
    // 네트워크 오류 등으로 인증 확인 자체가 실패하면, 안전한 쪽(로그인 화면)으로 보낸다.
    console.error("[auth-guard] 인증 확인 실패:", e);
    window.location.replace("./login.html");
  }
})();
