// ==========================================================
// MOTORRAD PULSE — login.html 동작 로직
// auth-config.js / auth-client.js 다음에 로드된다(둘 다 defer이므로 이 스크립트가
// 실행되는 시점엔 이미 로드가 끝나 있다 — 일반 <script>도 defer 스크립트들 뒤에
// 실행 순서가 보장된다).
// ==========================================================

(async function redirectIfAlreadyApproved() {
  try {
    const { data } = await _supabase.auth.getSession();
    if (!data || !data.session) return;
    const profile = await fetchOwnProfile();
    if (profile && profile.status === "approved") {
      window.location.replace("./index.html");
    } else if (profile) {
      // pending/rejected 상태로 세션만 남아있는 경우 — 접근 못하게 정리한다.
      await _supabase.auth.signOut();
    }
  } catch (e) {
    // 무시 — 로그인 폼을 그냥 보여주면 된다.
  }
})();

const tabs = document.querySelectorAll(".auth-tab");
const forms = { login: document.getElementById("login-form"), signup: document.getElementById("signup-form") };
const messageBox = document.getElementById("auth-message");

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.remove("is-active"));
    tab.classList.add("is-active");
    Object.values(forms).forEach((f) => f.classList.remove("is-active"));
    forms[tab.dataset.authTab].classList.add("is-active");
    hideMessage();
  });
});

function showMessage(text, type) {
  messageBox.textContent = text;
  messageBox.className = `auth-message is-visible is-${type}`;
}
function hideMessage() {
  messageBox.className = "auth-message";
}

function setBusy(button, busy) {
  button.disabled = busy;
}

document.getElementById("login-form").addEventListener("submit", async function (ev) {
  ev.preventDefault();
  hideMessage();
  const username = document.getElementById("login-username").value;
  const password = document.getElementById("login-password").value;
  const btn = document.getElementById("login-submit");
  setBusy(btn, true);

  const result = await attemptLogin(username, password);
  setBusy(btn, false);

  if (result.error) {
    showMessage(result.error, "error");
    return;
  }
  window.location.href = "./index.html";
});

document.getElementById("signup-form").addEventListener("submit", async function (ev) {
  ev.preventDefault();
  hideMessage();
  const username = document.getElementById("signup-username").value;
  const password = document.getElementById("signup-password").value;
  const password2 = document.getElementById("signup-password2").value;
  const btn = document.getElementById("signup-submit");

  if (password !== password2) {
    showMessage("비밀번호가 서로 일치하지 않습니다.", "error");
    return;
  }

  setBusy(btn, true);
  const result = await requestSignup(username, password);
  setBusy(btn, false);

  if (result.error) {
    showMessage(result.error, "error");
    return;
  }
  showMessage("가입 신청이 완료되었습니다. 관리자 승인 후 로그인할 수 있습니다.", "success");
  document.getElementById("signup-form").reset();
});
