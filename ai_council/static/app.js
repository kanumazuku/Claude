"use strict";

// ---- 定数 ---------------------------------------------------------------

const PROVIDER_COLORS = {
  claude: "var(--p-claude)",
  openai: "var(--p-openai)",
  gemini: "var(--p-gemini)",
  ollama: "var(--p-ollama)",
  mock: "var(--p-mock)",
};
const REAL_PROVIDERS = ["claude", "openai", "gemini", "ollama"];

const PROMPT_EXAMPLES = [
  "新入社員向けに、生成AIを安全に使うためのガイドラインを作ってください",
  "小学生にもわかるように、地球温暖化のしくみを説明してください",
  "地方の商店街を活性化するアイデアを、実行手順つきで5つ提案してください",
  "リモートワークのメリットとデメリットを、経営者向けに800字でまとめてください",
];

const PRESET_PARTICIPANTS = [
  { name: "正確さ重視", stance: "事実関係と論理の正しさを重視する。誤りや根拠の弱い記述を見逃さない。" },
  { name: "わかりやすさ重視", stance: "読み手の立場に立ち、構成と表現のわかりやすさを重視する。" },
  { name: "実用性重視", stance: "読み手がすぐに使える具体性と、実行しやすさを重視する。" },
  { name: "批判的視点", stance: "見落とされている観点、反対意見、リスクを指摘する。" },
  { name: "簡潔さ重視", stance: "冗長な部分を削り、要点を短く伝えることを重視する。" },
  { name: "独創性重視", stance: "ありきたりでない視点やアイデアを加えることを重視する。" },
];
const DEFAULT_COUNT = 3;

const MIN_PARTICIPANTS = 2;
const MAX_PARTICIPANTS = 6;
const STORAGE_KEY = "ai-council-settings-v3";

// ---- 状態 ---------------------------------------------------------------

const $ = (id) => document.getElementById(id);
let providers = [];
let controller = null;
let session = null; // 実行中・実行済みの合議の記録
let seatCounter = 0;

// ---- 起動 ---------------------------------------------------------------

init();

async function init() {
  renderPromptExamples();
  try {
    const res = await fetch("/api/providers");
    providers = await res.json();
  } catch {
    providers = [{ id: "mock", label: "デモ", available: true, reason: "", default_model: "demo", models: ["demo"] }];
  }
  renderProviderStatus();
  restoreOrDefault();

  $("councilForm").addEventListener("submit", (e) => {
    e.preventDefault();
    startCouncil();
  });
  $("councilForm").addEventListener("input", saveSettings);
  $("councilForm").addEventListener("change", saveSettings);
  $("addParticipant").addEventListener("click", addParticipant);
  $("stopBtn").addEventListener("click", () => controller?.abort());
  $("sessionStopBtn").addEventListener("click", () => controller?.abort());
  $("toggleSettings").addEventListener("click", () => setFocus(!$("layout").classList.contains("focus")));
  $("copyBtn").addEventListener("click", () => copyText($("copyBtn"), buildMinutes(), "議事録をコピー"));
  $("downloadBtn").addEventListener("click", () => download(buildMinutes(), "議事録"));
  $("copyAnswerBtn").addEventListener("click", () => copyText($("copyAnswerBtn"), finalText(), "回答をコピー"));
  $("saveAnswerBtn").addEventListener("click", () => download(finalText() + "\n", "回答"));
}

function renderPromptExamples() {
  const box = $("promptExamples");
  for (const text of PROMPT_EXAMPLES) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = text;
    chip.addEventListener("click", () => {
      $("prompt").value = text;
      saveSettings();
    });
    box.append(chip);
  }
}

function renderProviderStatus() {
  const list = $("providerStatus");
  list.replaceChildren();
  for (const p of providers) {
    const li = document.createElement("li");
    li.title = p.available ? `${p.label}：使えます` : `${p.label}：${p.reason}`;
    const dot = document.createElement("span");
    dot.className = "dot" + (p.available ? " on" : "");
    li.append(dot, document.createTextNode(p.label));
    list.append(li);
  }
}

// 合議中は設定欄をたたみ、議論と原稿を広く表示する
function setFocus(on) {
  $("layout").classList.toggle("focus", on);
  $("toggleSettings").textContent = on ? "設定を表示" : "設定を隠す";
}

// ---- 参加者の席 -----------------------------------------------------------

function providerById(id) {
  return providers.find((p) => p.id === id);
}

function availableReal() {
  return REAL_PROVIDERS.filter((id) => providerById(id)?.available);
}

// 「さまざまなAI」が混ざるように、使えるAIを順番に割り当てる。
function defaultProvider(index) {
  const avail = availableReal();
  if (avail.length === 0) return "mock";
  return avail[index % avail.length];
}

function createSeat(data) {
  const node = $("seatTemplate").content.firstElementChild.cloneNode(true);
  const name = node.querySelector(".seat-name");
  const select = node.querySelector(".seat-provider");
  const model = node.querySelector(".seat-model");
  const stance = node.querySelector(".seat-stance");
  const remove = node.querySelector(".seat-remove");

  const listId = `models-${++seatCounter}`;
  const datalist = document.createElement("datalist");
  datalist.id = listId;
  model.setAttribute("list", listId);
  node.append(datalist);

  for (const p of providers) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.available ? p.label : `${p.label}（使えません）`;
    opt.disabled = !p.available;
    if (!p.available) opt.title = p.reason;
    select.append(opt);
  }

  const applyProvider = () => {
    const p = providerById(select.value);
    node.style.setProperty("--seat-color", PROVIDER_COLORS[select.value] || "var(--muted)");
    model.placeholder = p ? `モデル（既定：${p.default_model}）` : "モデル";
    datalist.replaceChildren(
      ...(p?.models || []).map((m) => Object.assign(document.createElement("option"), { value: m }))
    );
  };

  name.value = data.name || "";
  select.value = data.provider;
  model.value = data.model || "";
  stance.value = data.stance || "";
  applyProvider();

  select.addEventListener("change", () => {
    model.value = "";
    applyProvider();
  });
  remove.addEventListener("click", () => {
    node.remove();
    refreshSeatButtons();
    saveSettings();
  });
  return node;
}

function refreshSeatButtons() {
  const seats = $("participantSlots").children;
  for (const seat of seats) {
    seat.querySelector(".seat-remove").disabled = seats.length <= MIN_PARTICIPANTS || Boolean(controller);
  }
  $("addParticipant").disabled = seats.length >= MAX_PARTICIPANTS || Boolean(controller);
}

function addParticipant() {
  const slots = $("participantSlots");
  const count = slots.children.length;
  if (count >= MAX_PARTICIPANTS) return;
  const used = new Set(readSeats().map((s) => s.name));
  const preset = PRESET_PARTICIPANTS.find((d) => !used.has(d.name)) || { name: `参加者${count + 1}`, stance: "" };
  slots.append(createSeat({ ...preset, provider: defaultProvider(count) }));
  refreshSeatButtons();
  saveSettings();
}

function readSeat(node) {
  return {
    name: node.querySelector(".seat-name").value.trim(),
    provider: node.querySelector(".seat-provider").value,
    model: node.querySelector(".seat-model").value.trim(),
    stance: node.querySelector(".seat-stance").value.trim(),
  };
}

function readSeats() {
  return [...$("participantSlots").children].map(readSeat);
}

// ---- 設定の保存と復元 -------------------------------------------------------

function readForm() {
  return {
    prompt: $("prompt").value.trim(),
    rounds: Number($("rounds").value),
    max_chars: Number($("maxChars").value),
    participants: readSeats(),
  };
}

function saveSettings() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(readForm()));
  } catch {
    // 保存できない環境では何もしない
  }
}

function loadSaved() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch {
    return null;
  }
}

// 保存時に使えたAIが今は使えない場合は、使えるAIに置き換える。
function usableSeat(seat, index) {
  if (providerById(seat.provider)?.available) return seat;
  return { ...seat, provider: defaultProvider(index), model: "" };
}

function restoreOrDefault() {
  const saved = loadSaved();
  const valid = saved && Array.isArray(saved.participants) && saved.participants.length >= MIN_PARTICIPANTS;
  const participants = valid
    ? saved.participants.slice(0, MAX_PARTICIPANTS).map(usableSeat)
    : PRESET_PARTICIPANTS.slice(0, DEFAULT_COUNT).map((d, i) => ({ ...d, provider: defaultProvider(i) }));

  if (valid) {
    $("prompt").value = saved.prompt || "";
    $("rounds").value = String(saved.rounds || 2);
    $("maxChars").value = String(saved.max_chars || 300);
  }
  $("participantSlots").replaceChildren(...participants.map(createSeat));
  refreshSeatButtons();
}

// ---- 合議の実行 -------------------------------------------------------------

function validate(cfg) {
  if (!cfg.prompt) return "プロンプトを入力してください。";
  const names = cfg.participants.map((p) => p.name);
  if (names.some((n) => !n)) return "参加者の名前を入力してください。";
  if (new Set(names).size !== names.length) return "参加者の名前が重複しています。";
  return "";
}

function showFormError(message) {
  const el = $("formError");
  el.textContent = message;
  el.hidden = !message;
}

function setRunning(running) {
  for (const el of $("councilForm").elements) {
    if (el.id !== "stopBtn") el.disabled = running;
  }
  $("startBtn").hidden = running;
  $("stopBtn").hidden = !running;
  $("stopBtn").disabled = false;
  $("sessionStopBtn").hidden = !running; // 設定欄をたたんでいても止められるように
  if (!running) {
    // 選べないAIの選択肢は無効のままにする
    for (const opt of $("councilForm").querySelectorAll(".seat-provider option")) {
      opt.disabled = !providerById(opt.value)?.available;
    }
    refreshSeatButtons();
  }
}

async function startCouncil() {
  const cfg = readForm();
  const problem = validate(cfg);
  showFormError(problem);
  if (problem) return;

  controller = new AbortController();
  setRunning(true);

  let res;
  try {
    res = await fetch("/api/council", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
      signal: controller.signal,
    });
  } catch {
    showFormError("サーバーに接続できませんでした。");
    finish();
    return;
  }

  if (!res.ok) {
    showFormError(await errorMessage(res));
    finish();
    return;
  }

  resetSessionView(cfg);
  setFocus(true);
  $("sessionView").scrollIntoView({ behavior: "smooth" });
  try {
    await readEvents(res.body, handleEvent);
    if (!session.ended) endSession("error", "通信が途中で切れました。");
  } catch (err) {
    if (err.name === "AbortError") {
      endSession("stopped", "合議を途中で止めました。");
    } else {
      endSession("error", "通信が途中で切れました。");
    }
  }
  finish();
}

function finish() {
  controller = null;
  setRunning(false);
}

async function errorMessage(res) {
  try {
    const data = await res.json();
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail.map((d) => String(d.msg).replace(/^Value error, /, "")).join(" / ");
    }
  } catch {
    // 本文がJSONでない場合は下の既定メッセージにする
  }
  return `エラーが発生しました（${res.status}）。`;
}

// SSE（data: {...}\n\n の連続）を1件ずつ取り出す。
async function readEvents(body, onEvent) {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let index;
    while ((index = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
      }
    }
  }
}

// ---- 合議の表示 -------------------------------------------------------------

function resetSessionView(cfg) {
  session = {
    cfg,
    seats: {},
    turns: {},
    lastTurnOf: {},
    versions: {}, // 版番号 → { no, author_id, text, diff, added, removed, approvals:Set, objections:Set }
    latest: 0,
    shown: 0, // 共同原稿欄に表示している版
    drafting: null, // 初稿を書いている発言
    final: null,
    log: [],
    startedAt: new Date(),
    ended: false,
  };
  $("emptyState").hidden = true;
  $("sessionView").hidden = false;
  $("sessionPrompt").textContent = cfg.prompt;
  $("roster").replaceChildren();
  $("timeline").replaceChildren();
  $("resultBar").hidden = true;
  $("docActions").hidden = true;
  renderDoc();
}

function handleEvent(ev) {
  switch (ev.type) {
    case "session_start":
      for (const p of ev.participants) session.seats[p.id] = p;
      renderRoster(ev.participants);
      renderDoc();
      break;
    case "phase":
      appendPhase(ev);
      break;
    case "lottery": {
      const name = session.seats[ev.drafter_id]?.name;
      appendNotice(
        ev.retry
          ? `初稿を書けなかったため、くじ引きで選び直しました。${name}が初稿を書きます。`
          : `くじ引きの結果、${name}が初稿を書きます。`
      );
      break;
    }
    case "turn_start":
      startTurn(ev);
      break;
    case "delta":
      appendDelta(ev.turn_id, ev.text);
      break;
    case "turn_end":
      endTurn(ev);
      break;
    case "version":
      addVersion(ev);
      break;
    case "review":
      showReview(ev);
      break;
    case "skip":
      appendNotice(`${session.seats[ev.speaker_id]?.name}は第${ev.version}版をすでに承認しているため、番を省略しました。`);
      break;
    case "consensus":
      appendNotice(`全員が第${ev.version}版を承認しました。`);
      break;
    case "final":
      showFinal(ev);
      break;
    case "session_end":
      endSession(ev.status, ev.message);
      break;
  }
}

function avatar(seat) {
  const el = document.createElement("span");
  el.className = "avatar";
  el.textContent = Array.from(seat.name)[0] || "?";
  el.style.setProperty("--seat-color", PROVIDER_COLORS[seat.provider] || "var(--muted)");
  return el;
}

function renderRoster(participants) {
  const list = $("roster");
  for (const p of participants) {
    const li = document.createElement("li");
    const small = document.createElement("small");
    small.textContent = p.provider_label;
    small.title = p.model;
    li.append(avatar(p), document.createTextNode(p.name), small);
    list.append(li);
  }
}

function appendPhase(ev) {
  const block = document.createElement("div");
  const line = document.createElement("div");
  line.className = "phase";
  line.textContent = ev.title;
  block.append(line);
  let detail = "";
  if (ev.order) {
    detail = `発言順：${ev.order.map((id) => session.seats[id]?.name || id).join("→")}`;
    const sub = document.createElement("p");
    sub.className = "phase-order";
    sub.textContent = detail;
    block.append(sub);
  }
  appendToTimeline(block);
  session.log.push({ type: "phase", title: ev.title, detail });
}

function appendNotice(text) {
  const el = document.createElement("p");
  el.className = "notice";
  el.textContent = text;
  appendToTimeline(el);
  session.log.push({ type: "notice", text });
}

function startTurn(ev) {
  const seat = session.seats[ev.speaker_id];
  const el = document.createElement("article");
  el.className = `msg thinking kind-${ev.kind}`;
  el.style.setProperty("--seat-color", PROVIDER_COLORS[seat.provider] || "var(--accent)");

  const main = document.createElement("div");
  const head = document.createElement("div");
  head.className = "msg-head";
  const name = document.createElement("span");
  name.className = "msg-name";
  name.textContent = seat.name;
  const meta = document.createElement("span");
  meta.className = "msg-meta";
  const what = ev.version ? `${ev.label}・第${ev.version}版を確認` : ev.label;
  meta.textContent = `${what}・${seat.provider_label}（${seat.model}）`;
  head.append(name, meta);

  const body = document.createElement("div");
  body.className = "msg-body";
  main.append(head, body);
  el.append(avatar(seat), main);
  appendToTimeline(el);

  const entry = { type: "turn", seat, label: ev.label, kind: ev.kind, text: "", error: "", note: "" };
  const turn = { el, body, main, entry, pending: false, done: false };
  session.turns[ev.turn_id] = turn;
  session.lastTurnOf[ev.speaker_id] = ev.turn_id;
  if (ev.kind === "draft") {
    session.drafting = turn;
    renderDoc();
  }
  session.log.push(entry);
}

function appendDelta(turnId, text) {
  const turn = session.turns[turnId];
  if (!turn) return;
  turn.entry.text += text;
  turn.el.classList.remove("thinking");
  turn.el.classList.add("streaming");
  // 描画は1フレームに1回にまとめる
  if (!turn.pending) {
    turn.pending = true;
    requestAnimationFrame(() => {
      turn.pending = false;
      // 発言が終わったあとは、意見や判断の表示を上書きしないよう描画しない
      if (turn.done) return;
      renderBody(turn);
      if (session.drafting === turn) renderDoc();
    });
  }
}

function renderBody(turn) {
  const stick = nearBottom();
  let html = renderMarkdown(turn.entry.text);
  if (turn.el.classList.contains("streaming")) html = withCaret(html);
  turn.body.innerHTML = html;
  if (stick) scrollToBottom();
}

// 入力中を示すカーソルを、最後の段落の末尾に置く
function withCaret(html) {
  return html.replace(/<\/(p|li|h4)>(?![\s\S]*<\/(?:p|li|h4)>)/, '<span class="caret"></span></$1>');
}

function endTurn(ev) {
  const turn = session.turns[ev.turn_id];
  if (!turn) return;
  turn.done = true;
  turn.el.classList.remove("thinking", "streaming");
  if (ev.ok) {
    turn.entry.text = ev.text;
    renderBody(turn);
  } else {
    turn.entry.error = ev.error;
    turn.el.classList.add("failed");
    turn.body.textContent = `発言できませんでした：${ev.error}`;
  }
  if (session.drafting === turn) {
    session.drafting = null;
    renderDoc();
  }
}

// 新しい版ができたとき
function addVersion(ev) {
  const v = { ...ev, no: ev.version, approvals: new Set(ev.approvals), objections: new Set() };
  session.versions[ev.version] = v;
  session.latest = ev.version;
  session.shown = ev.version;
  renderDoc();

  if (ev.version === 1) {
    // 初稿の発言欄は、全文を折りたたんで表示する（原稿は共同原稿欄で読める）
    const turn = session.turns[session.lastTurnOf[ev.author_id]];
    if (turn) {
      turn.body.replaceChildren(textP("初稿（第1版）を書きました。共同原稿欄に表示しています。"), details("初稿の全文を見る", markdownBox(ev.text)));
      turn.entry.note = "第1版を作成";
    }
  }
}

function showReview(ev) {
  const turn = session.turns[session.lastTurnOf[ev.speaker_id]];
  const reviewed = session.versions[ev.reviewed_version];
  const current = session.versions[ev.current_version];
  if (ev.decision === "approve") {
    reviewed.approvals.add(ev.speaker_id);
    reviewed.objections.delete(ev.speaker_id);
  } else if (ev.decision === "object") {
    reviewed.approvals.delete(ev.speaker_id);
    reviewed.objections.add(ev.speaker_id);
  }
  current.approvals = new Set(ev.approvals);
  renderDoc();
  if (!turn) return;

  // 発言欄は、意見だけを表示し直す（修正版の全文は共同原稿欄と差分で見られる）
  const parts = [markdownBox(ev.opinion)];
  const chip = document.createElement("span");
  if (ev.decision === "approve") {
    chip.className = "vote v-agree";
    chip.textContent = `第${ev.reviewed_version}版を承認`;
  } else if (ev.decision === "revise") {
    chip.className = "vote v-accent";
    chip.textContent = `修正して第${ev.current_version}版を作成（＋${current.added}行／−${current.removed}行）`;
  } else {
    chip.className = "vote v-cond";
    chip.textContent = `第${ev.reviewed_version}版に修正を求める`;
  }
  const row = document.createElement("div");
  row.className = "chip-row";
  row.append(chip);
  parts.push(row);
  if (ev.decision === "revise") parts.push(details("変更点を見る", diffBox(current.diff)));
  turn.body.replaceChildren(...parts);
  turn.entry.text = ev.opinion;
  turn.entry.note = chip.textContent;
}

function showFinal(ev) {
  session.final = ev;
  session.shown = ev.version;
  const total = Object.keys(session.seats).length;
  if (ev.unanimous) {
    appendNotice(`完成しました。全員が承認した第${ev.version}版が回答です。`);
  } else if (ev.version === ev.latest_version) {
    appendNotice(`最大ラウンドに達しました。全員の承認には至りませんでしたが、最も多くの承認（${ev.approvals.length}／${total}）を得た第${ev.version}版を回答とします。`);
  } else {
    appendNotice(
      `最大ラウンドに達しました。全員の承認には至らなかったため、最新の第${ev.latest_version}版ではなく、最も多くの承認（${ev.approvals.length}／${total}）を得た第${ev.version}版を回答とします。`
    );
  }
  $("docActions").hidden = false;
  renderDoc();
}

// ---- 共同原稿欄 -------------------------------------------------------------

function renderDoc() {
  if (!session) return;
  const total = Object.keys(session.seats).length;
  const v = session.versions[session.shown];
  const final = session.final;

  // 見出し
  const versionLabel = $("docVersion");
  const status = $("docStatus");
  status.className = "doc-status";
  if (session.drafting && !v) {
    versionLabel.textContent = "第1版";
    status.textContent = `${session.drafting.entry.seat.name}が執筆中…`;
  } else if (!v) {
    versionLabel.textContent = "";
    status.textContent = "初稿を待っています";
  } else {
    versionLabel.textContent = `第${v.no}版`;
    if (final && final.version === v.no) {
      status.textContent = final.unanimous ? "完成（全員が承認）" : `採用（承認 ${v.approvals.size}／${total}）`;
      status.classList.add("done");
    } else {
      const latest = v.no === session.latest ? "" : `（最新は第${session.latest}版）`;
      status.textContent = `承認 ${v.approvals.size}／${total}${latest}`;
    }
  }

  // 承認状況
  const approvals = $("docApprovals");
  approvals.replaceChildren();
  if (v) {
    for (const seat of Object.values(session.seats)) {
      const li = document.createElement("li");
      let state = "未確認";
      if (v.approvals.has(seat.id)) state = v.author_id === seat.id ? "作成・承認" : "承認";
      else if (v.objections.has(seat.id)) state = "修正を求める";
      li.className = state.includes("承認") ? "ok" : state === "修正を求める" ? "ng" : "";
      li.append(avatar(seat), document.createTextNode(`${seat.name}：${state}`));
      approvals.append(li);
    }
  }

  // 版の切り替え
  const versions = $("docVersions");
  versions.replaceChildren();
  const nos = Object.keys(session.versions).map(Number).sort((a, b) => a - b);
  if (nos.length > 1) {
    for (const no of nos) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "version-btn" + (no === session.shown ? " active" : "");
      b.textContent = final && final.version === no ? `第${no}版（回答）` : `第${no}版`;
      b.title = `${session.seats[session.versions[no].author_id]?.name}が作成`;
      b.addEventListener("click", () => {
        session.shown = no;
        renderDoc();
      });
      versions.append(b);
    }
  }

  // 本文
  const body = $("docBody");
  if (v) {
    body.innerHTML = renderMarkdown(v.text);
  } else if (session.drafting) {
    body.innerHTML = withCaret(renderMarkdown(session.drafting.entry.text)) || '<p class="muted">考え中…</p>';
  } else {
    body.innerHTML = '<p class="muted">方針の議論のあと、くじ引きで選ばれたAIが初稿を書きます。</p>';
  }
}

function finalText() {
  if (!session?.final) return "";
  return session.final.text;
}

// ---- 部品 -------------------------------------------------------------------

function textP(text) {
  const p = document.createElement("p");
  p.textContent = text;
  return p;
}

function markdownBox(text) {
  const div = document.createElement("div");
  div.innerHTML = renderMarkdown(text);
  return div;
}

function details(summaryText, content) {
  const d = document.createElement("details");
  const s = document.createElement("summary");
  s.textContent = summaryText;
  d.append(s, content);
  return d;
}

// 差分表示。変更のない行が続くところは前後2行だけ残して省略する
function diffBox(diff) {
  const box = document.createElement("div");
  box.className = "diff";
  const keep = new Set();
  diff.forEach((d, i) => {
    if (d.op !== "same") for (let k = i - 2; k <= i + 2; k++) keep.add(k);
  });
  let skipped = 0;
  const flush = () => {
    if (!skipped) return;
    const gap = document.createElement("div");
    gap.className = "diff-gap";
    gap.textContent = `（変更のない${skipped}行を省略）`;
    box.append(gap);
    skipped = 0;
  };
  diff.forEach((d, i) => {
    if (d.op === "same" && !keep.has(i)) {
      skipped++;
      return;
    }
    flush();
    const line = document.createElement("div");
    line.className = `diff-line diff-${d.op}`;
    line.textContent = (d.op === "add" ? "＋ " : d.op === "del" ? "− " : "　 ") + d.text;
    box.append(line);
  });
  flush();
  return box;
}

function endSession(status, message) {
  if (!session || session.ended) return;
  session.ended = true;
  for (const turn of Object.values(session.turns)) {
    if (turn.el.classList.contains("thinking") || turn.el.classList.contains("streaming")) {
      turn.el.classList.remove("thinking", "streaming");
      if (!turn.entry.text) turn.body.textContent = "（中断されました）";
    }
  }
  session.drafting = null;
  renderDoc();
  const text = status === "completed" ? "合議が終わりました。" : message || "合議が中断されました。";
  const p = $("resultMessage");
  p.textContent = text;
  p.className = status === "error" ? "error" : "";
  $("resultBar").hidden = false;
  session.result = text;
}

function appendToTimeline(el) {
  const stick = nearBottom();
  $("timeline").append(el);
  if (stick) scrollToBottom();
}

function nearBottom() {
  return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160;
}

function scrollToBottom() {
  window.scrollTo({ top: document.documentElement.scrollHeight });
}

// ---- 議事録と保存 -----------------------------------------------------------

function buildMinutes() {
  if (!session) return "";
  const lines = [
    "# AI合議室 議事録",
    "",
    `- 日時：${session.startedAt.toLocaleString("ja-JP")}`,
    "- 参加者（全員が対等）：",
  ];
  for (const seat of Object.values(session.seats)) {
    const stance = seat.stance ? `、${seat.stance}` : "";
    lines.push(`  - ${seat.name}（${seat.provider_label}：${seat.model}${stance}）`);
  }
  lines.push("", "## プロンプト", "", session.cfg.prompt);
  for (const item of session.log) {
    if (item.type === "phase") {
      lines.push("", `## ${item.title}`);
      if (item.detail) lines.push("", item.detail);
    }
    if (item.type === "notice") lines.push("", `> ${item.text}`);
    if (item.type === "turn") {
      lines.push("", `### ${item.seat.name}`, "");
      if (item.error) lines.push(`（発言できませんでした：${item.error}）`);
      else if (item.kind === "draft") lines.push("（初稿の全文は、下の「版の履歴」にあります）");
      else lines.push(item.text || "（中断されました）");
      if (item.note && item.kind !== "draft") lines.push("", `→ ${item.note}`);
    }
  }
  const nos = Object.keys(session.versions).map(Number).sort((a, b) => a - b);
  if (nos.length) {
    lines.push("", "## 版の履歴");
    for (const no of nos) {
      const v = session.versions[no];
      const author = session.seats[v.author_id]?.name;
      lines.push("", `### 第${no}版（${author}が作成、承認：${[...v.approvals].map((id) => session.seats[id]?.name).join("、") || "なし"}）`, "", v.text);
    }
  }
  if (session.final) {
    lines.push("", `## 回答（第${session.final.version}版${session.final.unanimous ? "、全員が承認" : ""}）`, "", session.final.text);
  }
  if (session.result) lines.push("", "---", "", session.result);
  return lines.join("\n") + "\n";
}

async function copyText(btn, text, label) {
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = "コピーしました";
  } catch {
    btn.textContent = "コピーできませんでした";
  }
  setTimeout(() => (btn.textContent = label), 2000);
}

function download(text, prefix) {
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const d = session.startedAt;
  const pad = (n) => String(n).padStart(2, "0");
  a.href = url;
  a.download = `${prefix}_${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.md`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---- 簡易Markdown（AIの発言を安全に表示する） ---------------------------------------

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function inline(s) {
  return escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function renderMarkdown(src) {
  let html = "";
  let para = [];
  let list = null;
  let code = null; // コードブロックの中身
  const flushPara = () => {
    if (para.length) html += `<p>${para.map(inline).join("<br>")}</p>`;
    para = [];
  };
  const closeList = () => {
    if (list) html += `</${list}>`;
    list = null;
  };

  for (const raw of src.replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    let m;
    if (code !== null) {
      if (/^\s*```/.test(line)) {
        html += `<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`;
        code = null;
      } else {
        code.push(raw);
      }
    } else if (/^\s*```/.test(line)) {
      flushPara();
      closeList();
      code = [];
    } else if (!line.trim()) {
      flushPara();
      closeList();
    } else if ((m = line.match(/^#{1,6}\s+(.*)$/))) {
      flushPara();
      closeList();
      html += `<h4>${inline(m[1])}</h4>`;
    } else if ((m = line.match(/^\s*(?:[-*+]\s+|・\s*)(.*)$/))) {
      flushPara();
      if (list !== "ul") {
        closeList();
        html += "<ul>";
        list = "ul";
      }
      html += `<li>${inline(m[1])}</li>`;
    } else if ((m = line.match(/^\s*(\d+)(?:[.)]\s+|．\s*)(.*)$/))) {
      flushPara();
      if (list !== "ol") {
        closeList();
        html += `<ol start="${Number(m[1])}">`;
        list = "ol";
      }
      html += `<li>${inline(m[2])}</li>`;
    } else {
      closeList();
      para.push(line);
    }
  }
  if (code !== null) html += `<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`;
  flushPara();
  closeList();
  return html;
}
