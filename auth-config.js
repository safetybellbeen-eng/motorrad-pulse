// ==========================================================
// MOTORRAD PULSE — Supabase 연결 설정
// ==========================================================
// Supabase 프로젝트를 새로 만든 뒤, 프로젝트 설정(Project Settings → API)에서
// 아래 두 값을 그대로 복사해서 붙여넣으세요.
//
//   SUPABASE_URL       -> "Project URL" (예: https://xxxxxxxxxxxx.supabase.co)
//   SUPABASE_ANON_KEY  -> "anon public" API key (긴 문자열)
//
// anon key는 "공개해도 되는" 키입니다(RLS 정책이 실제 접근 제어를 담당하므로
// 브라우저 코드에 그대로 들어가도 안전합니다). service_role 키는 여기 절대
// 넣지 마세요 — 그 키는 모든 RLS를 무시하는 관리자 전용 키입니다.
// ==========================================================

const SUPABASE_URL = "https://kuphyemtyamglvyjpvwh.supabase.co";
const SUPABASE_ANON_KEY = "sb_publishable_eOUPEa1xtZP5FbOoEuiWUw_2M1_Q5c5";

// Supabase Auth는 이메일 기반 로그인만 지원합니다. 이 사이트는 "아이디(ID)" 방식
// 로그인을 쓰므로, 내부적으로 "{아이디}@motorrad-pulse.local" 형태의 가짜 이메일로
// 변환해서 Supabase Auth에 전달합니다(실제 메일함이 아니므로 이메일 발송/확인 없이
// 동작해야 합니다 — setup 안내의 "Confirm email 끄기" 단계 참고).
const AUTH_EMAIL_DOMAIN = "@motorrad-pulse.local";

function usernameToEmail(username) {
  return `${username.trim().toLowerCase()}${AUTH_EMAIL_DOMAIN}`;
}
