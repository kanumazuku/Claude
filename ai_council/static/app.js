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

const PRESET_PARTICIPANTS = [
  { name: "推進派", stance: "議題に前向きな立場。新しい挑戦がもたらす可能性や利点を重視する。" },
  { name: "慎重派", stance: "議題に慎重な立場。リスクや副作用、影響を受ける人への配慮を重視する。" },
  { name: "現実派", stance: "中立的な立場。費用、実現可能性、具体的な実行手順を重視する。" },
  { name: "当事者目線", stance: "議題によって最も影響を受ける人の目線で、生活の実感から意見を述べる。" },
  { name: "データ重視", stance: "統計や研究結果など、客観的な根拠を何より重視する。" },
  { name: "長期視点", stance: "10年、20年先の社会への影響を重視して考える。" },
];
const DEFAULT_COUNT = 3;

const MIN_PARTICIPANTS = 2;
const MAX_PARTICIPANTS = 6;
const STORAGE_KEY = "ai-council-settings-v2";
const OLD_STORAGE_KEY = "ai-council-settings-v1";

const RATING_CLASS = { 賛成: "agree", 条件付き賛成: "cond", 反対: "oppose", 不明: "unknown" };

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
  $("addParticipant").addEventListener("click", addParticipant);
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
    topic: $("topic").value.trim(),
    rounds: Number($("rounds").value),
    max_chars: Number($("maxChars").value),
    early_stop: $("earlyStop").checked,
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
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (saved) return saved;
    // 司会がいた以前の版の設定は、討論者だけを参加者として引き継ぐ
    const old = JSON.parse(localStorage.getItem(OLD_STORAGE_KEY) || "null");
    if (old && Array.isArray(old.debaters)) return { ...old, participants: old.debaters };
  } catch {
    // 読めない場合は既定の設定にする
  }
  return null;
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
    $("topic").value = saved.topic || "";
    $("rounds").value = String(saved.rounds || 2);
    $("maxChars").value = String(saved.max_chars || 300);
    $("earlyStop").checked = saved.early_stop !== false;
  }
  $("participantSlots").replaceChildren(...participants.map(createSeat));
  refreshSeatButtons();
}

// ---- 討論の実行 -------------------------------------------------------------

function validate(cfg) {
  if (!cfg.topic) return "議題を入力してください。";
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
  debate = { cfg, seats: {}, turns: {}, lastTurnOf: {}, proposals: {}, log: [], startedAt: new Date(), ended: false };
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
      appendPhase(ev);
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
    case "continue_vote":
      addChips(ev.speaker_id, [ev.decision === "stop" ? ["終了に1票", "oppose"] : ["継続に1票", "agree"]]);
      break;
    case "round_decision":
      appendNotice(decisionText(ev));
      break;
    case "evaluation":
      addChips(
        ev.speaker_id,
        ev.ratings.map((r) => [`案${r.proposal_no}：${r.rating}`, RATING_CLASS[r.rating] || "unknown"])
      );
      break;
    case "result":
      appendResult(ev);
      break;
    case "debate_end":
      endDebate(ev.status, ev.message);
      break;
  }
}

function decisionText(ev) {
  const count = `継続${ev.continue}票、終了${ev.stop}票`;
  if (ev.decision === "continue") return `多数決（${count}）：議論を続けます。`;
  if (ev.early_stop) return `多数決（${count}）：論点が出尽くしたと判断し、残りのラウンドを省略して結論案に進みます。`;
  return `多数決（${count}）では終了が上回りましたが、設定に従ってラウンドを続けます。`;
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
  const wrap = document.createElement("div");
  wrap.className = "phase";
  const title = document.createElement("span");
  title.textContent = ev.title;
  wrap.append(title);
  let detail = "";
  if (ev.order) {
    detail = `発言順：${ev.order.map((id) => debate.seats[id]?.name || id).join("→")}`;
    const sub = document.createElement("small");
    sub.textContent = detail;
    wrap.append(sub);
  }
  appendToTimeline(wrap);
  debate.log.push({ type: "phase", title: ev.title, detail });
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
  el.className = `msg thinking kind-${ev.kind}`;
  el.style.setProperty("--seat-color", PROVIDER_COLORS[seat.provider] || "var(--accent)");

  const main = document.createElement("div");
  const head = document.createElement("div");
  head.className = "msg-head";
  const name = document.createElement("span");
  name.className = "msg-name";
  name.textContent = seat.name;
  head.append(name);
  if (ev.proposal_no) {
    const tag = document.createElement("span");
    tag.className = "proposal-tag";
    tag.textContent = `案${ev.proposal_no}`;
    head.append(tag);
  }
  const meta = document.createElement("span");
  meta.className = "msg-meta";
  meta.textContent = `${ev.proposal_no ? "結論案" : ev.label}・${seat.provider_label}（${seat.model}）`;
  head.append(meta);

  const body = document.createElement("div");
  body.className = "msg-body";
  main.append(head, body);
  el.append(avatar(seat), main);
  appendToTimeline(el);

  const entry = { type: "turn", seat, label: ev.label, kind: ev.kind, text: "", error: "", chips: [] };
  debate.turns[ev.turn_id] = { el, body, main, entry, pending: false };
  debate.lastTurnOf[ev.speaker_id] = ev.turn_id;
  if (ev.proposal_no) debate.proposals[ev.proposal_no] = { seat, entry };
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

function addChips(speakerId, chips) {
  const turn = debate.turns[debate.lastTurnOf[speakerId]];
  if (!turn) return;
  const row = document.createElement("div");
  row.className = "chip-row";
  for (const [text, cls] of chips) {
    const chip = document.createElement("span");
    chip.className = `vote v-${cls}`;
    chip.textContent = text;
    row.append(chip);
  }
  turn.main.append(row);
  turn.entry.chips.push(...chips.map(([text]) => text));
}

function appendResult(ev) {
  const wrap = document.createElement("section");
  wrap.className = "result";

  for (const p of ev.proposals) {
    const author = debate.seats[p.author_id];
    const row = document.createElement("div");
    row.className = "score-row" + (p.no === ev.adopted ? " adopted" : "");
    const head = document.createElement("div");
    head.className = "score-head";
    const tag = document.createElement("span");
    tag.className = "proposal-tag";
    tag.textContent = `案${p.no}`;
    const who = document.createElement("span");
    who.textContent = `${author?.name || "?"}の案`;
    head.append(tag, who);
    if (p.no === ev.adopted) {
      const badge = document.createElement("span");
      badge.className = "adopted-badge";
      badge.textContent = "採択";
      head.append(badge);
    }
    const support = document.createElement("span");
    support.className = "support";
    support.textContent = `支持率 ${p.support}%`;
    head.append(support);
    row.append(head, tallyBar(p.counts));
    wrap.append(row);
  }

  const note = document.createElement("p");
  note.className = "muted";
  note.textContent =
    "支持率は、賛成を2点、条件付き賛成を1点、反対と不明を0点として、満点に対する割合です。自分の案は評価していません。";
  wrap.append(note);
  appendToTimeline(wrap);

  if (ev.tie_break) appendNotice("支持率が同じ案があったため、くじ引きで採択する案を決めました。");

  let conclusion = null;
  if (ev.adopted == null) {
    appendNotice("どの案も支持を得られなかったため、結論は採択されませんでした。");
  } else {
    const adopted = ev.proposals.find((p) => p.no === ev.adopted);
    const author = debate.seats[adopted.author_id];
    const card = document.createElement("article");
    card.className = "conclusion-card";
    const h = document.createElement("h3");
    h.textContent = "採択された結論";
    const meta = document.createElement("p");
    meta.className = "muted";
    meta.textContent = `案${adopted.no}（${author?.name || "?"}の案）・支持率 ${adopted.support}%`;
    const body = document.createElement("div");
    body.className = "msg-body";
    body.innerHTML = renderMarkdown(ev.text);
    card.append(h, meta, body);
    appendToTimeline(card);
    conclusion = { no: adopted.no, author: author?.name || "?", support: adopted.support, text: ev.text };
  }
  debate.log.push({ type: "result", proposals: ev.proposals, tieBreak: ev.tie_break, conclusion });
}

function tallyBar(counts) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const box = document.createElement("div");
  const bar = document.createElement("div");
  bar.className = "tally-bar";
  const legend = document.createElement("div");
  legend.className = "tally-legend";
  for (const [label, n] of Object.entries(counts)) {
    if (!n) continue;
    const seg = document.createElement("span");
    seg.className = `seg-${RATING_CLASS[label] || "unknown"}`;
    seg.style.width = `${(n / total) * 100}%`;
    seg.title = `${label}：${n}`;
    bar.append(seg);
    const item = document.createElement("span");
    item.append(document.createTextNode(`${label} `));
    const b = document.createElement("b");
    b.textContent = n;
    item.append(b);
    legend.append(item);
  }
  box.append(bar, legend);
  return box;
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
  const lines = [
    "# AI合議室 議事録",
    "",
    `- 議題：${debate.cfg.topic}`,
    `- 日時：${debate.startedAt.toLocaleString("ja-JP")}`,
    "- 参加者（全員が対等）：",
  ];
  for (const seat of Object.values(debate.seats)) {
    const stance = seat.stance ? `、${seat.stance}` : "";
    lines.push(`  - ${seat.name}（${seat.provider_label}：${seat.model}${stance}）`);
  }
  for (const item of debate.log) {
    if (item.type === "phase") {
      lines.push("", `## ${item.title}`);
      if (item.detail) lines.push("", item.detail);
    }
    if (item.type === "notice") lines.push("", `> ${item.text}`);
    if (item.type === "turn") {
      const tag = item.kind === "proposal" ? `（${item.label}）` : "";
      lines.push("", `### ${item.seat.name}${tag}`, "");
      lines.push(item.error ? `（発言できませんでした：${item.error}）` : item.text || "（中断されました）");
      if (item.chips.length) lines.push("", `→ ${item.chips.join("、")}`);
    }
    if (item.type === "result") {
      lines.push("", "| 案 | 作成者 | 支持率 | 賛成 | 条件付き賛成 | 反対 | 不明 |", "|---|---|---|---|---|---|---|");
      for (const p of item.proposals) {
        const c = p.counts;
        const name = debate.seats[p.author_id]?.name || "?";
        lines.push(`| 案${p.no} | ${name} | ${p.support}% | ${c["賛成"]} | ${c["条件付き賛成"]} | ${c["反対"]} | ${c["不明"]} |`);
      }
      if (item.conclusion) {
        const c = item.conclusion;
        lines.push("", `## 採択された結論（案${c.no}・${c.author}の案・支持率${c.support}%）`, "", c.text);
      }
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
  const pad = (n) => String(n).padStart(2, "0");
  a.href = url;
  a.download = `議事録_${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.md`;
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
