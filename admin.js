// ==========================================================
// MOTORRAD PULSE — admin.html 동작 로직
// auth-guard.js가 이 페이지 접근을 role='admin'인 승인된 사용자로 이미 제한했다
// (window.__REQUIRE_ADMIN__ = true). RLS의 is_admin() 정책 덕분에, 여기서 하는
// profiles 테이블 select/update는 관리자 계정일 때만 실제로 허용된다.
// ==========================================================

function formatDate(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toLocaleString("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

async function loadUsers() {
  const { data, error } = await _supabase
    .from("profiles")
    .select("id, username, role, status, created_at, approved_at")
    .order("created_at", { ascending: false });

  if (error) {
    console.error("[admin] 사용자 목록 조회 실패:", error);
    return;
  }

  const pending = data.filter((u) => u.status === "pending");
  const approved = data.filter((u) => u.status === "approved");
  const rejected = data.filter((u) => u.status === "rejected");

  renderPending(pending);
  renderApproved(approved);
  renderRejected(rejected);
}

function renderPending(rows) {
  const body = document.getElementById("pending-body");
  const empty = document.getElementById("pending-empty");
  body.innerHTML = "";
  empty.style.display = rows.length ? "none" : "block";

  rows.forEach((u) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td data-label="아이디">${escapeHtml(u.username)}</td>
      <td data-label="신청일시">${formatDate(u.created_at)}</td>
      <td class="admin-actions" data-label="처리">
        <button class="admin-btn admin-btn--approve" data-action="approve" data-id="${u.id}">승인</button>
        <button class="admin-btn admin-btn--reject" data-action="reject" data-id="${u.id}">거절</button>
      </td>`;
    body.appendChild(tr);
  });
}

function renderApproved(rows) {
  const body = document.getElementById("approved-body");
  const empty = document.getElementById("approved-empty");
  body.innerHTML = "";
  empty.style.display = rows.length ? "none" : "block";

  rows.forEach((u) => {
    const isSelf = window.__CURRENT_PROFILE__ && window.__CURRENT_PROFILE__.id === u.id;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td data-label="아이디">${escapeHtml(u.username)}</td>
      <td data-label="권한">${u.role === "admin" ? '<span class="badge badge--admin">관리자</span>' : '<span class="badge badge--approved">일반</span>'}</td>
      <td data-label="승인일시">${formatDate(u.approved_at)}</td>
      <td class="admin-actions" data-label="처리">
        <button class="admin-btn admin-btn--reject" data-action="revoke" data-id="${u.id}" ${isSelf ? "disabled title=\"본인 계정은 여기서 해제할 수 없습니다\"" : ""}>승인 해제</button>
      </td>`;
    body.appendChild(tr);
  });
}

function renderRejected(rows) {
  const body = document.getElementById("rejected-body");
  const empty = document.getElementById("rejected-empty");
  body.innerHTML = "";
  empty.style.display = rows.length ? "none" : "block";

  rows.forEach((u) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td data-label="아이디">${escapeHtml(u.username)}</td>
      <td class="admin-actions" data-label="처리">
        <button class="admin-btn admin-btn--approve" data-action="approve" data-id="${u.id}">재승인</button>
      </td>`;
    body.appendChild(tr);
  });
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s || "";
  return div.innerHTML;
}

async function updateStatus(id, status) {
  const patch = { status };
  if (status === "approved") {
    patch.approved_at = new Date().toISOString();
    const { data: userData } = await _supabase.auth.getUser();
    patch.approved_by = userData && userData.user ? userData.user.id : null;
  }
  const { error } = await _supabase.from("profiles").update(patch).eq("id", id);
  if (error) {
    alert(`처리 실패: ${error.message}`);
    return;
  }
  await loadUsers();
}

document.addEventListener("click", function (ev) {
  const btn = ev.target.closest("button[data-action]");
  if (!btn) return;
  const id = btn.dataset.id;
  const action = btn.dataset.action;

  if (action === "approve") {
    updateStatus(id, "approved");
  } else if (action === "reject") {
    if (confirm("이 신청을 거절하시겠습니까?")) updateStatus(id, "rejected");
  } else if (action === "revoke") {
    if (confirm("이 사용자의 승인을 해제하시겠습니까? 다음 로그인부터 다시 승인 대기 상태가 됩니다.")) {
      updateStatus(id, "pending");
    }
  }
});

document.addEventListener("auth-ready", loadUsers);
