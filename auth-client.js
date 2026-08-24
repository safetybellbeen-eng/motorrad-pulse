// ==========================================================
// MOTORRAD PULSE — Auth 공통 클라이언트
// login.html / auth-guard.js / admin.js가 공통으로 사용하는 함수 모음.
// 이 파일은 auth-config.js(SUPABASE_URL/ANON_KEY) 다음, supabase-js CDN 스크립트
// 다음 순서로 로드되어야 한다.
// ==========================================================

/* ---------- 자동 로그인 설정 ----------
   로그인 화면의 "자동 로그인" 체크박스 상태를 저장해두고, Supabase 세션을 어디에
   저장할지(브라우저를 닫아도 남는 localStorage / 탭을 닫으면 사라지는 sessionStorage)를
   그 값에 따라 결정한다. 이 값 자체(켜짐/꺼짐 여부)는 민감정보가 아니므로 항상
   localStorage에 남겨두고, 모든 페이지(login.html/index.html/admin.html)가 이 값을
   똑같이 참조해서 세션을 어디서 읽을지 판단한다.
   기본값은 true(자동 로그인 켜짐) — 이 기능이 생기기 전까지는 항상 로그인이 유지되던
   것과 동일하게 동작하도록 하여, 기존 사용자가 갑자기 로그아웃되지 않게 한다. */
const AUTO_LOGIN_PREF_KEY = "motorradPulseAutoLogin";

function isAutoLoginOn() {
  try {
    const v = localStorage.getItem(AUTO_LOGIN_PREF_KEY);
    return v === null ? true : v === "1";
  } catch (e) {
    return true; // localStorage 접근 불가(프라이빗 모드 등) — 기본 동작 유지
  }
}

function setAutoLoginPref(on) {
  try {
    localStorage.setItem(AUTO_LOGIN_PREF_KEY, on ? "1" : "0");
  } catch (e) {
    /* 저장 실패해도 로그인 자체는 계속 진행되어야 하므로 조용히 무시 */
  }
}

/* Supabase가 세션 토큰을 읽고/쓸 때마다 그 순간의 "자동 로그인" 설정을 다시 확인해서
   localStorage(브라우저 재시작 후에도 유지) 또는 sessionStorage(탭을 닫으면 로그아웃)
   중 하나로 위임하는 어댑터. 클라이언트 생성 시점에 저장소를 한 번만 고정해버리면
   "로그인 버튼을 누르는 순간 체크박스 값"이 반영되지 않기 때문에, 매 호출마다
   isAutoLoginOn()을 다시 읽는 방식으로 만든다. */
const _authStorageAdapter = {
  getItem(key) {
    try {
      return (isAutoLoginOn() ? window.localStorage : window.sessionStorage).getItem(key);
    } catch (e) {
      return null;
    }
  },
  setItem(key, value) {
    try {
      (isAutoLoginOn() ? window.localStorage : window.sessionStorage).setItem(key, value);
    } catch (e) {
      /* 무시 */
    }
  },
  removeItem(key) {
    // 로그아웃 시엔 설정과 무관하게 양쪽 다 정리해서 이전에 저장된 세션이 남지 않게 한다.
    try { window.localStorage.removeItem(key); } catch (e) { /* 무시 */ }
    try { window.sessionStorage.removeItem(key); } catch (e) { /* 무시 */ }
  },
};

const _supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    storage: _authStorageAdapter,
  },
});

const USERNAME_PATTERN = /^[a-zA-Z0-9_.-]{3,32}$/;

function validateUsername(username) {
  if (!USERNAME_PATTERN.test(username)) {
    return "아이디는 영문/숫자/._- 만 사용해 3~32자로 입력해주세요.";
  }
  return null;
}

function validatePassword(password) {
  if (!password || password.length < 6) {
    return "비밀번호는 6자 이상이어야 합니다.";
  }
  return null;
}

/** 가입 신청. 성공 시 auth.users에 계정이 생기고, DB 트리거가 profiles에
 * status='pending' 행을 자동으로 만든다(관리자 승인 전까지 로그인 불가). */
async function requestSignup(username, password) {
  const usernameErr = validateUsername(username);
  if (usernameErr) return { error: usernameErr };
  const passwordErr = validatePassword(password);
  if (passwordErr) return { error: passwordErr };

  const email = usernameToEmail(username);
  const { data, error } = await _supabase.auth.signUp({
    email,
    password,
    options: { data: { username: username.trim().toLowerCase() } },
  });

  if (error) {
    if (/already registered|already exists|duplicate/i.test(error.message)) {
      return { error: "이미 사용 중인 아이디입니다." };
    }
    return { error: `가입 신청 실패: ${error.message}` };
  }

  // 이메일 확인이 켜져 있으면 session이 없는 상태로 signUp이 끝난다(정상).
  // 승인 전이므로 어느 쪽이든 즉시 로그아웃 상태로 되돌린다 — 미승인 세션이
  // 브라우저에 남아있지 않게 한다.
  await _supabase.auth.signOut();
  return { data };
}

/** 로그인. 성공 + 승인완료(approved) + 정지 아님 상태일 때만 세션을 유지한다.
 * 그 외의 경우 즉시 signOut하고 사유를 반환한다(미승인 세션이 남지 않도록). */
async function attemptLogin(username, password) {
  const email = usernameToEmail(username);
  const { data: authData, error: authError } = await _supabase.auth.signInWithPassword({
    email,
    password,
  });

  if (authError) {
    return { error: "아이디 또는 비밀번호가 올바르지 않습니다." };
  }

  const profile = await fetchOwnProfile();
  if (!profile) {
    await _supabase.auth.signOut();
    return { error: "계정 정보를 확인할 수 없습니다. 관리자에게 문의하세요." };
  }

  if (profile.status === "pending") {
    await _supabase.auth.signOut();
    return { error: "아직 관리자 승인 대기 중입니다. 승인 후 다시 로그인해주세요." };
  }
  if (profile.status === "rejected") {
    await _supabase.auth.signOut();
    return { error: "가입 신청이 승인되지 않았습니다. 관리자에게 문의하세요." };
  }

  return { data: authData, profile };
}

async function fetchOwnProfile() {
  const { data: userData } = await _supabase.auth.getUser();
  if (!userData || !userData.user) return null;
  const { data, error } = await _supabase
    .from("profiles")
    .select("id, username, role, status")
    .eq("id", userData.user.id)
    .maybeSingle();
  if (error || !data) return null;
  return data;
}

async function signOutAndRedirect() {
  await _supabase.auth.signOut();
  window.location.href = "./login.html";
}
