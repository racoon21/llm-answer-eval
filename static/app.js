"use strict";

const POLL_DEFAULT = 5;
let cfg = { max_submissions: 4, judge_count: 3, poll_seconds: POLL_DEFAULT };
let pollTimer = null;

const $ = (id) => document.getElementById(id);

function initials(name) {
  const s = (name || "").trim();
  if (!s) return "?";
  const parts = s.split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return s.slice(0, 2).toUpperCase();
}

function levelFor(score) {
  if (score >= 90) return "Gold 🥇";
  if (score >= 75) return "Silver 🥈";
  if (score >= 50) return "Bronze 🥉";
  return "Rookie";
}

function currentEmployeeId() {
  return $("employee_id").value.trim();
}

// ---------------- Leaderboard rendering ----------------
async function loadLeaderboard() {
  let data;
  try {
    const res = await fetch("/api/leaderboard");
    data = await res.json();
  } catch (e) {
    return;
  }
  renderBoard(data.leaderboard || []);
}

function renderBoard(board) {
  const podium = $("podium");
  const list = $("rank-list");
  const empty = $("empty-state");
  podium.innerHTML = "";
  list.innerHTML = "";

  if (board.length === 0) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  const me = currentEmployeeId();
  // Podium order: rank2 (left), rank1 (center), rank3 (right)
  const order = [board[1], board[0], board[2]];
  const rankClass = ["rank2", "rank1", "rank3"];
  const barNum = ["2", "1", "3"];
  order.forEach((entry, i) => {
    const slot = document.createElement("div");
    slot.className = "slot " + rankClass[i] + (entry ? "" : " empty");
    if (entry) {
      slot.innerHTML = `
        <div class="avatar">${initials(entry.display_name)}</div>
        <div class="p-name">${escapeHtml(entry.display_name)}</div>
        <div class="p-points">${entry.score} points</div>
        <div class="bar">${barNum[i]}</div>`;
    } else {
      slot.innerHTML = `<div class="avatar">–</div>
        <div class="p-name">&nbsp;</div>
        <div class="p-points">&nbsp;</div>
        <div class="bar">${barNum[i]}</div>`;
    }
    podium.appendChild(slot);
  });

  // Rank 4+
  board.slice(3).forEach((entry) => {
    const li = document.createElement("li");
    if (entry.employee_id === me) li.className = "is-me";
    const crown = entry.rank <= 6 ? "👑" : "👑";
    const crownColor = entry.rank === 7 ? "color:var(--crown-gold)" : "color:var(--crown-pink)";
    li.innerHTML = `
      <span class="num">${String(entry.rank).padStart(2, "0")}</span>
      <div class="info">
        <div class="nm">${escapeHtml(entry.display_name)}</div>
        <div class="pts">${entry.score} points</div>
      </div>
      <span class="crown" style="${crownColor}">${crown}</span>`;
    list.appendChild(li);
  });

  refreshMeCard(board);
}

function refreshMeCard(board) {
  const me = currentEmployeeId();
  const card = $("me-card");
  if (!me) { card.hidden = true; return; }
  const entry = board.find((e) => e.employee_id === me);
  if (!entry) { card.hidden = true; return; }
  card.hidden = false;
  $("me-avatar").textContent = initials(entry.display_name);
  $("me-points").textContent = entry.score;
  $("me-level").textContent = levelFor(entry.score);
  $("me-position").textContent = entry.rank;
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---------------- Quota hint ----------------
async function refreshQuota() {
  const id = currentEmployeeId();
  if (!id) { $("quota-hint").textContent = ""; return; }
  try {
    const res = await fetch("/api/me/" + encodeURIComponent(id));
    const data = await res.json();
    const used = data.summary ? data.summary.submission_count : 0;
    const left = Math.max(0, cfg.max_submissions - used);
    $("quota-hint").textContent =
      `${id} 님 제출 ${used}/${cfg.max_submissions}회 (남은 횟수 ${left})`;
  } catch (e) { /* ignore */ }
}

// ---------------- Submit ----------------
async function onSubmit(ev) {
  ev.preventDefault();
  const btn = $("submit-btn");
  const resultBox = $("submit-result");
  const file = $("file").files[0];
  if (!file) return;

  const form = new FormData();
  form.append("employee_id", currentEmployeeId());
  form.append("display_name", $("display_name").value.trim());
  form.append("file", file);

  btn.disabled = true;
  btn.textContent = `채점 중… (${cfg.judge_count}명 심사)`;
  resultBox.hidden = true;

  try {
    const res = await fetch("/api/submit", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "제출 실패");

    let html = `<strong>이번 제출 점수: ${data.submission_score}점</strong><br/>` +
      `최고 점수: ${data.best_score}점 · 현재 순위: ${data.rank ?? "-"}위 · ` +
      `남은 제출: ${data.remaining_submissions}회`;
    if (data.items && data.items.length) {
      html += "<ul>" + data.items.map((it) =>
        `<li>문제 ${it.problem_no}: ${it.score}점` +
        (it.matched_reference ? "" : " (모범답안 없음)") + "</li>").join("") + "</ul>";
    }
    if (data.warnings && data.warnings.length) {
      html += "<ul>" + data.warnings.map((w) => `<li>⚠ ${escapeHtml(w)}</li>`).join("") + "</ul>";
    }
    resultBox.className = "submit-result ok";
    resultBox.innerHTML = html;
    resultBox.hidden = false;
    $("file").value = "";
    $("file-name").textContent = "CSV 파일 선택 (문제번호, 답변)";
    $("file-label").classList.remove("has-file");
    await loadLeaderboard();
    await refreshQuota();
  } catch (e) {
    resultBox.className = "submit-result err";
    resultBox.textContent = "오류: " + e.message;
    resultBox.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "채점 제출";
  }
}

// ---------------- Init ----------------
async function init() {
  try {
    const res = await fetch("/api/config");
    cfg = await res.json();
  } catch (e) { /* defaults */ }

  $("file").addEventListener("change", () => {
    const f = $("file").files[0];
    if (f) {
      $("file-name").textContent = f.name;
      $("file-label").classList.add("has-file");
    }
  });
  $("employee_id").addEventListener("input", () => { refreshQuota(); refreshMeCardFromCache(); });
  $("submit-form").addEventListener("submit", onSubmit);

  await loadLeaderboard();
  const seconds = (cfg.poll_seconds || POLL_DEFAULT) * 1000;
  pollTimer = setInterval(loadLeaderboard, seconds);
}

// re-highlight me-card when typing without refetching the board
let lastBoard = [];
const _origRender = renderBoard;
renderBoard = function (board) { lastBoard = board; _origRender(board); };
function refreshMeCardFromCache() { refreshMeCard(lastBoard); }

init();
