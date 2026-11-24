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

const TOPIC_EXAMPLES = [
  "日本の企業は週休3日制を導入すべきか",
  "小学校の授業で生成AIを積極的に使うべきか",
  "都市部の自家用車は段階的に減らすべきか",
  "リモートワークを働き方の基本にすべきか",
];

const DEFAULT_MODERATOR = { name: "議長", stance: "落ち着いて公平に議論を整理する。" };
const DEFAULT_DEBATERS = [
  { name: "推進派", stance: "議題に前向きな立場。新しい挑戦がもたらす可能性や利点を重視する。" },
  { name: "慎重派", stance: "議題に慎重な立場。リスクや副作用、影響を受ける人への配慮を重視する。" },
  { name: "現実派", stance: "中立的な立場。費用、実現可能性、具体的な実行手順を重視する。" },
];
const EXTRA_DEBATERS = [
  { name: "当事者目線", stance: "議題によって最も影響を受ける人の目線で、生活の実感から意見を述べる。" },
  { name: "データ重視", stance: "統計や研究結果など、客観的な根拠を何より重視する。" },
];

const MIN_DEBATERS = 2;
const MAX_DEBATERS = 5;
const STORAGE_KEY = "ai-council-settings-v1";

const VOTE_CLASS = { 賛成: "agree", 条件付き賛成: "cond", 反対: "oppose", 不明: "unknown" };

// ---- 状態 ---------------------------------------------------------------

const $ = (id) => document.getElementById(id);
let providers = [];
let controller = null;
let debate = null; // 実行中・実行済みの討論の記録
let seatCounter = 0;

// ---- 起動 ---------------------------------------------------------------

init();

async function init() {
  renderTopicExamples();
  try {
    const res = await fetch("/api/providers");
    providers = await res.json();
  } catch {
    providers = [{ id: "mock", label: "デモ", available: true, reason: "", default_model: "demo", models: ["demo"] }];
  }
  renderProviderStatus();
  restoreOrDefault();

  $("debateForm").addEventListener("submit", (e) => {
    e.preventDefault();
    startDebate();
  });
  $("debateForm").addEventListener("input", saveSettings);
  $("debateForm").addEventListener("change", saveSettings);
  $("addDebater").addEventListener("click", addDebater);
  $("stopBtn").addEventListener("click", () => controller?.abort());
  $("copyBtn").addEventListener("click", copyMinutes);
  $("downloadBtn").addEventListener("click", downloadMinutes);
}

function renderTopicExamples() {
  const box = $("topicExamples");
  for (const text of TOPIC_EXAMPLES) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = text;
    chip.addEventListener("click", () => {
      $("topic").value = text;
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

function createSeat(data, role) {
  const node = $("seatTemplate").content.firstElementChild.cloneNode(true);
  node.dataset.role = role;
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
  stance.placeholder = role === "moderator" ? "司会の個性（例：落ち着いて公平に整理する）" : "立場・個性（例：慎重派。リスクを重視する）";
  applyProvider();

  select.addEventListener("change", () => {
    model.value = "";
    applyProvider();
  });

  if (role === "moderator") {
    remove.remove();
  } else {
    remove.addEventListener("click", () => {
      node.remove();
      refreshSeatButtons();
      saveSettings();
    });
  }
  return node;
}

function refreshSeatButtons() {
  const seats = $("debaterSlots").children;
  for (const seat of seats) {
    seat.querySelector(".seat-remove").disabled = seats.length <= MIN_DEBATERS;
  }
  $("addDebater").disabled = seats.length >= MAX_DEBATERS || Boolean(controller);
}

function addDebater() {
  const slots = $("debaterSlots");
  const count = slots.children.length;
  if (count >= MAX_DEBATERS) return;
  const used = new Set(readSeats(slots).map((s) => s.name));
  const preset = [...DEFAULT_DEBATERS, ...EXTRA_DEBATERS].find((d) => !used.has(d.name)) || {
    name: `討論者${count + 1}`,
    stance: "",
  };
  slots.append(createSeat({ ...preset, provider: defaultProvider(count + 1) }, "debater"));
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

function readSeats(container) {
  return [...container.children].map(readSeat);
}

// ---- 設定の保存と復元 -------------------------------------------------------

function readForm() {
  return {
    topic: $("topic").value.trim(),
    rounds: Number($("rounds").value),
    max_chars: Number($("maxChars").value),
    early_stop: $("earlyStop").checked,
    moderator: readSeat($("moderatorSlot").firstElementChild),
    debaters: readSeats($("debaterSlots")),
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
  const valid = saved && saved.moderator && Array.isArray(saved.debaters) && saved.debaters.length >= MIN_DEBATERS;

  const moderator = valid
    ? usableSeat(saved.moderator, 0)
    : { ...DEFAULT_MODERATOR, provider: defaultProvider(0) };
  const debaters = valid
    ? saved.debaters.slice(0, MAX_DEBATERS).map((d, i) => usableSeat(d, i + 1))
    : DEFAULT_DEBATERS.map((d, i) => ({ ...d, provider: defaultProvider(i + 1) }));

  if (valid) {
    $("topic").value = saved.topic || "";
    $("rounds").value = String(saved.rounds || 2);
    $("maxChars").value = String(saved.max_chars || 300);
    $("earlyStop").checked = saved.early_stop !== false;
  }

  $("moderatorSlot").replaceChildren(createSeat(moderator, "moderator"));
  $("debaterSlots").replaceChildren(...debaters.map((d) => createSeat(d, "debater")));
  refreshSeatButtons();
}

// ---- 討論の実行 -------------------------------------------------------------

function validate(cfg) {
  if (!cfg.topic) return "議題を入力してください。";
  const names = [cfg.moderator.name, ...cfg.debaters.map((d) => d.name)];
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
  for (const el of $("debateForm").elements) {
    if (el.id !== "stopBtn") el.disabled = running;
  }
  $("startBtn").hidden = running;
  $("stopBtn").hidden = !running;
  $("stopBtn").disabled = false;
  if (!running) {
    // 選べないAIの選択肢は無効のままにする
    for (const opt of $("debateForm").querySelectorAll(".seat-provider option")) {
      opt.disabled = !providerById(opt.value)?.available;
    }
    refreshSeatButtons();
  }
}

async function startDebate() {
  const cfg = readForm();
  const problem = validate(cfg);
  showFormError(problem);
  if (problem) return;

  controller = new AbortController();
  setRunning(true);

  let res;
  try {
    res = await fetch("/api/debate", {
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

  resetDebateView(cfg);
  if (window.matchMedia("(max-width: 860px)").matches) {
    // スマートフォンでは設定の下に討論が表示されるので、そこまで移動する
    $("debateView").scrollIntoView({ behavior: "smooth" });
  }
  try {
    await readEvents(res.body, handleEvent);
    if (!debate.ended) endDebate("error", "通信が途中で切れました。");
  } catch (err) {
    if (err.name === "AbortError") {
      endDebate("stopped", "討論を途中で止めました。");
    } else {
      endDebate("error", "通信が途中で切れました。");
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

// ---- 討論の表示 -------------------------------------------------------------

function resetDebateView(cfg) {
  debate = { cfg, seats: {}, turns: {}, lastTurnOf: {}, log: [], startedAt: new Date(), ended: false };
  $("emptyState").hidden = true;
  $("debateView").hidden = false;
  $("debateTopic").textContent = cfg.topic;
  $("roster").replaceChildren();
  $("timeline").replaceChildren();
  $("resultBar").hidden = true;
}

function handleEvent(ev) {
  switch (ev.type) {
    case "debate_start":
      for (const p of ev.participants) debate.seats[p.id] = p;
      renderRoster(ev.participants);
      break;
    case "phase":
      appendPhase(ev.title);
      debate.log.push({ type: "phase", title: ev.title });
      break;
    case "turn_start":
      startTurn(ev);
      break;
    case "delta":
      appendDelta(ev.turn_id, ev.text);
      break;
    case "turn_end":
      endTurn(ev);
      break;
    case "judgment":
      appendNotice(judgmentText(ev));
      break;
    case "vote":
      showVote(ev);
      break;
    case "tally":
      appendTally(ev.counts);
      break;
    case "debate_end":
      endDebate(ev.status, ev.message);
      break;
  }
}

function judgmentText(ev) {
  if (ev.decision === "continue") return "司会の判定：議論を続けます。";
  if (ev.early_stop) return "司会の判定：論点が出尽くしたため、残りのラウンドを省略して結論案に進みます。";
  return "司会は議論の終了を提案しましたが、設定に従ってラウンドを続けます。";
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
    const role = p.role === "moderator" ? "司会・" : "";
    const small = document.createElement("small");
    small.textContent = `${role}${p.provider_label}`;
    small.title = p.model;
    li.append(avatar(p), document.createTextNode(p.name), small);
    list.append(li);
  }
}

function appendPhase(title) {
  const el = document.createElement("div");
  el.className = "phase";
  el.textContent = title;
  appendToTimeline(el);
}

function appendNotice(text) {
  const el = document.createElement("p");
  el.className = "notice";
  el.textContent = text;
  appendToTimeline(el);
  debate.log.push({ type: "notice", text });
}

function startTurn(ev) {
  const seat = debate.seats[ev.speaker_id];
  const el = document.createElement("article");
  el.className = `msg thinking ${seat.role}`;
  if (ev.kind === "conclusion") el.classList.add("conclusion");
  el.style.setProperty("--seat-color", PROVIDER_COLORS[seat.provider] || "var(--accent)");

  const main = document.createElement("div");
  const head = document.createElement("div");
  head.className = "msg-head";
  const name = document.createElement("span");
  name.className = "msg-name";
  name.textContent = seat.name;
  head.append(name);
  if (seat.role === "moderator") {
    const badge = document.createElement("span");
    badge.className = "role-badge";
    badge.textContent = "司会";
    head.append(badge);
  }
  const meta = document.createElement("span");
  meta.className = "msg-meta";
  meta.textContent = `${ev.label}・${seat.provider_label}（${seat.model}）`;
  head.append(meta);

  const body = document.createElement("div");
  body.className = "msg-body";
  main.append(head, body);
  el.append(avatar(seat), main);
  appendToTimeline(el);

  const entry = { type: "turn", seat, label: ev.label, kind: ev.kind, text: "", error: "", vote: "" };
  debate.turns[ev.turn_id] = { el, body, main, entry, pending: false };
  debate.lastTurnOf[ev.speaker_id] = ev.turn_id;
  debate.log.push(entry);
}

function appendDelta(turnId, text) {
  const turn = debate.turns[turnId];
  if (!turn) return;
  turn.entry.text += text;
  turn.el.classList.remove("thinking");
  turn.el.classList.add("streaming");
  // 描画は1フレームに1回にまとめる
  if (!turn.pending) {
    turn.pending = true;
    requestAnimationFrame(() => {
      turn.pending = false;
      renderBody(turn);
    });
  }
}

function renderBody(turn) {
  const stick = nearBottom();
  let html = renderMarkdown(turn.entry.text);
  if (turn.el.classList.contains("streaming")) {
    // 入力中を示すカーソルを、最後の段落の末尾に置く
    html = html.replace(/<\/(p|li|h4)>(?![\s\S]*<\/(?:p|li|h4)>)/, '<span class="caret"></span></$1>');
  }
  turn.body.innerHTML = html;
  if (stick) scrollToBottom();
}

function endTurn(ev) {
  const turn = debate.turns[ev.turn_id];
  if (!turn) return;
  turn.el.classList.remove("thinking", "streaming");
  if (ev.ok) {
    turn.entry.text = ev.text;
    renderBody(turn);
  } else {
    turn.entry.error = ev.error;
    turn.el.classList.add("failed");
    turn.body.textContent = `発言できませんでした：${ev.error}`;
  }
}

function showVote(ev) {
  const turn = debate.turns[debate.lastTurnOf[ev.speaker_id]];
  if (!turn) return;
  turn.entry.vote = ev.vote;
  const chip = document.createElement("span");
  chip.className = `vote v-${VOTE_CLASS[ev.vote] || "unknown"}`;
  chip.textContent = ev.vote;
  turn.main.append(chip);
}

function appendTally(counts) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (!total) return;
  const wrap = document.createElement("div");
  wrap.className = "tally";
  const bar = document.createElement("div");
  bar.className = "tally-bar";
  const legend = document.createElement("div");
  legend.className = "tally-legend";
  for (const [label, n] of Object.entries(counts)) {
    if (!n) continue;
    const seg = document.createElement("span");
    seg.className = `seg-${VOTE_CLASS[label] || "unknown"}`;
    seg.style.width = `${(n / total) * 100}%`;
    seg.title = `${label}：${n}`;
    bar.append(seg);
    const item = document.createElement("span");
    item.innerHTML = `${escapeHtml(label)} <b>${n}</b>`;
    legend.append(item);
  }
  wrap.append(bar, legend);
  appendToTimeline(wrap);
  debate.log.push({ type: "tally", counts });
}

function endDebate(status, message) {
  if (!debate || debate.ended) return;
  debate.ended = true;
  for (const turn of Object.values(debate.turns)) {
    if (turn.el.classList.contains("thinking") || turn.el.classList.contains("streaming")) {
      turn.el.classList.remove("thinking", "streaming");
      if (!turn.entry.text) turn.body.textContent = "（中断されました）";
    }
  }
  const text = status === "completed" ? "討論が終わりました。" : message || "討論が中断されました。";
  const p = $("resultMessage");
  p.textContent = text;
  p.className = status === "error" ? "error" : "";
  $("resultBar").hidden = false;
  debate.result = text;
  if (nearBottom()) scrollToBottom();
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

// ---- 議事録 ---------------------------------------------------------------

function buildMinutes() {
  if (!debate) return "";
  const lines = ["# AI合議室 議事録", "", `- 議題：${debate.cfg.topic}`, `- 日時：${debate.startedAt.toLocaleString("ja-JP")}`, "- 参加者："];
  for (const seat of Object.values(debate.seats)) {
    const role = seat.role === "moderator" ? "司会" : "討論者";
    const stance = seat.stance ? `、${seat.stance}` : "";
    lines.push(`  - ${seat.name}（${role}、${seat.provider_label}：${seat.model}${stance}）`);
  }
  for (const item of debate.log) {
    if (item.type === "phase") lines.push("", `## ${item.title}`);
    if (item.type === "notice") lines.push("", `> ${item.text}`);
    if (item.type === "tally") {
      const summary = Object.entries(item.counts).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join("、");
      lines.push("", `**賛否の集計**：${summary}`);
    }
    if (item.type === "turn") {
      lines.push("", `### ${item.seat.name}${item.vote ? `【${item.vote}】` : ""}`, "");
      lines.push(item.error ? `（発言できませんでした：${item.error}）` : item.text || "（中断されました）");
    }
  }
  if (debate.result) lines.push("", "---", "", debate.result);
  return lines.join("\n") + "\n";
}

async function copyMinutes() {
  const btn = $("copyBtn");
  try {
    await navigator.clipboard.writeText(buildMinutes());
    btn.textContent = "コピーしました";
  } catch {
    btn.textContent = "コピーできませんでした";
  }
  setTimeout(() => (btn.textContent = "議事録をコピー"), 2000);
}

function downloadMinutes() {
  const blob = new Blob([buildMinutes()], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const d = debate.startedAt;
  const stamp = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}-${String(d.getHours()).padStart(2, "0")}${String(d.getMinutes()).padStart(2, "0")}`;
  a.href = url;
  a.download = `議事録_${stamp}.md`;
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
    if (!line.trim()) {
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
  flushPara();
  closeList();
  return html;
}
