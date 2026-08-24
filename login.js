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

/* ---------- 아이디 저장 / 자동 로그인 ----------
   "아이디 저장"은 이 브라우저에 아이디만 남겨서 다음 방문 때 입력칸에 미리 채워주는
   용도(비밀번호는 저장하지 않는다). "자동 로그인"은 auth-client.js의 isAutoLoginOn()/
   setAutoLoginPref()가 관리하는 설정으로, 브라우저를 껐다 켜도 로그인이 유지될지를
   결정한다 — 실제 세션 저장 위치(localStorage vs sessionStorage) 전환은 그쪽에서 처리한다. */
const SAVED_USERNAME_KEY = "motorradPulseSavedUsername";
const usernameInput = document.getElementById("login-username");
const rememberCheckbox = document.getElementById("login-remember-username");
const autoLoginCheckbox = document.getElementById("login-auto-login");

(function restoreLoginPrefs() {
  try {
    const saved = localStorage.getItem(SAVED_USERNAME_KEY);
    if (saved) {
      usernameInput.value = saved;
      rememberCheckbox.checked = true;
    }
  } catch (e) {
    /* localStorage 접근 불가 — 그냥 빈 칸으로 둔다 */
  }
  autoLoginCheckbox.checked = isAutoLoginOn();
})();

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

  // 로그인 시도 전에 두 체크박스 값을 먼저 반영해둔다 — attemptLogin() 내부에서
  // Supabase가 세션을 저장하는 순간, auth-client.js의 저장소 어댑터가 바로 이 값을
  // 참조해서 localStorage/sessionStorage 중 어디에 쓸지 정하기 때문에 순서가 중요하다.
  setAutoLoginPref(autoLoginCheckbox.checked);

  setBusy(btn, true);
  const result = await attemptLogin(username, password);
  setBusy(btn, false);

  if (result.error) {
    showMessage(result.error, "error");
    return;
  }

  try {
    if (rememberCheckbox.checked) {
      localStorage.setItem(SAVED_USERNAME_KEY, username);
    } else {
      localStorage.removeItem(SAVED_USERNAME_KEY);
    }
  } catch (e) {
    /* 저장 실패해도 로그인 자체는 이미 성공했으므로 무시하고 계속 진행 */
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
