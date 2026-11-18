const byId = id => document.getElementById(id);
const statusLine = byId("status");
let state;
let registration;
let busy = false;
let selected;
let renderedStocks;
const number = value => value.toLocaleString("ko-KR", { maximumFractionDigits: 2 });

async function api(path, method = "GET", data) {
  const response = await fetch(path, {
    method, cache: "no-store",
    headers: data === undefined ? {} : { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "입력 또는 연결을 확인하세요.");
  return payload;
}

function element(tag, value, parent) {
  const item = document.createElement(tag);
  item.textContent = value;
  if (parent) parent.append(item);
  return item;
}

function chart(canvas) {
  let bars = [];
  const ctx = canvas.getContext("2d");
  const readout = canvas.previousElementSibling;
  let active = -1;
  const left = 5;
  let space;
  function draw() {
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (!width || !height || !bars.length) return;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const plotWidth = Math.max(20, width - 69);
  const priceHeight = height - 62;
  const min = Math.min(...bars.map(bar => bar.low ?? bar.close));
  const max = Math.max(...bars.map(bar => bar.high ?? bar.close));
  const span = max - min || 1;
  const y = price => 12 + (max - price) / span * (priceHeight - 16);
  space = plotWidth / bars.length;
  const maxVolume = Math.max(1, ...bars.map(bar => bar.volume));
  ctx.font = "11px system-ui";
  for (let i = 0; i <= 3; i++) {
    const price = max - span * i / 3;
    const lineY = y(price);
    ctx.fillStyle = "#27374e";
    ctx.fillRect(left, lineY, plotWidth, 1);
    ctx.fillStyle = "#a5b4c9";
    ctx.fillText(number(price), left + plotWidth + 5, lineY + 4, 56);
  }
  if (active >= 0) {
    ctx.fillStyle = "#273b56";
    ctx.fillRect(left + active * space, 5, space, height - 24);
  }
  bars.forEach((bar, index) => {
    const x = left + (index + .5) * space;
    const body = Math.max(1, space * .65);
    const rising = bar.close >= (bar.open ?? bars[Math.max(0, index - 1)].close);
    ctx.fillStyle = rising ? "#57dfb0" : "#ff91a0";
    const volume = 34 * bar.volume / maxVolume;
    ctx.globalAlpha = .55;
    ctx.fillRect(x - body / 2, height - 20 - volume, body, volume);
    ctx.globalAlpha = 1;
    if (bar.open !== undefined) {
      ctx.fillRect(x, y(bar.high), 1, Math.max(1, y(bar.low) - y(bar.high)));
      ctx.fillRect(x - body / 2, y(Math.max(bar.open, bar.close)), body,
        Math.max(1, Math.abs(y(bar.open) - y(bar.close))));
    }
  });
  if (bars[0].open === undefined) {
    ctx.strokeStyle = "#8acaff";
    ctx.beginPath();
    bars.forEach((bar, index) => {
      const x = left + (index + .5) * space;
      if (index === 0) ctx.moveTo(x, y(bar.close)); else ctx.lineTo(x, y(bar.close));
    });
    ctx.stroke();
  }
  ctx.fillStyle = "#a5b4c9";
  ctx.fillText(bars[0].date, left, height - 4);
  const lastDate = bars.at(-1).date;
  ctx.fillText(lastDate, left + plotWidth - ctx.measureText(lastDate).width, height - 4);
  const bar = bars[active];
  readout.textContent = bar.date + (bar.open === undefined ? "" :
    " · 시가 " + number(bar.open) + " · 고가 " + number(bar.high) + " · 저가 " + number(bar.low)) +
    " · 종가 " + number(bar.close) + " · 거래량 " + number(bar.volume);
  }
  canvas.addEventListener("pointermove", event => {
    if (event.pointerType !== "mouse") return;
    active = Math.max(0, Math.min(bars.length - 1, Math.floor((event.offsetX - left) / space)));
    draw();
  });
  canvas.addEventListener("click", event => {
    active = Math.max(0, Math.min(bars.length - 1, Math.floor((event.offsetX - left) / space)));
    draw();
  });
  canvas.addEventListener("keydown", event => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    active = Math.max(0, Math.min(bars.length - 1, active + (event.key === "ArrowLeft" ? -1 : 1)));
    draw();
  });
  window.addEventListener("resize", draw);
  return (next, reset) => {
    const inspecting = !reset && active >= 0 && active < bars.length - 1 ? bars[active].date : null;
    bars = next;
    active = inspecting ? bars.findIndex(bar => bar.date === inspecting) : -1;
    if (active < 0) active = bars.length - 1;
    canvas.hidden = !bars.length;
    if (bars.length) draw();
    else readout.textContent = "";
  };
}

const drawBars = chart(byId("price-chart"));
const interval = byId("interval"), cadence = byId("cadence"), range = byId("range");
const custom = byId("custom-minutes");
let chosenMinutes = 3;
let viewKey = "", timer, generation = 0, lastBars = [], lastInfo = "";
try {
  const prefs = JSON.parse(localStorage.getItem("stockai-chart") || "{}");
  for (const [field, value] of [[interval, prefs.interval], [cadence, prefs.cadence], [range, prefs.range]]) {
    if ([...field.options].some(o => o.value === value)) field.value = value;
  }
  if (Number.isInteger(prefs.custom) && prefs.custom >= 1 && prefs.custom <= 60) chosenMinutes = prefs.custom;
} catch {}
custom.value = chosenMinutes;
function savePreferences() {
  try { localStorage.setItem("stockai-chart", JSON.stringify({
    interval: interval.value, custom: chosenMinutes, cadence: cadence.value, range: range.value
  })); } catch {}
}
function displayBars(reset = false) {
  const visible = lastBars.slice(-Number(range.value));
  drawBars(visible, reset);
  byId("chart-note").textContent = [lastInfo, visible.length ? visible.length + "개 봉 표시" : ""].filter(Boolean).join(" · ");
}
async function pollChart(version, key, minutes) {
  if (version !== generation || document.hidden) return;
  try {
    const data = await api("/api/chart/" + encodeURIComponent(key) + "?minutes=" + minutes);
    if (version !== generation) return;
    lastBars = data.bars;
    const checked = data.asof ? "조회 " + new Date(data.asof * 1000).toLocaleTimeString("ko-KR") : "";
    lastInfo = [data.warning, checked, lastBars.length ? "" : "분봉 데이터 대기"].filter(Boolean).join(" · ");
  } catch (error) {
    if (version !== generation) return;
    lastInfo = "조회 실패 · " + error.message;
  }
  displayBars();
  timer = setTimeout(() => pollChart(version, key, minutes), Number(cadence.value) * 1000);
}
function syncChart(force = false) {
  const day = interval.value === "day";
  const minutes = interval.value === "custom" ? chosenMinutes : Number(interval.value);
  byId("custom-form").hidden = interval.value !== "custom";
  cadence.disabled = day;
  byId("price-panel").hidden = !selected;
  const unit = day ? "일봉" : minutes + "분봉";
  const nextKey = JSON.stringify([selected, unit]);
  const changed = nextKey !== viewKey;
  if (changed || force) {
    ++generation;
    clearTimeout(timer);
    viewKey = nextKey;
  }
  if (!selected || !state) return;
  const item = state.watch[selected];
  byId("plot-title").textContent = item.query + " · " + unit;
  byId("chart-zone").textContent = day ? "거래일 기준" : (item.market.startsWith("KOS") ? "서울 시간" : "뉴욕 시간");
  if (day) {
    lastBars = state.results[selected]?.market?.bars || [];
    lastInfo = lastBars.length ? "일봉 종가 · 거래량" : "일봉 데이터 대기";
    displayBars(changed);
  } else if (changed || force) {
    if (changed) { lastBars = []; lastInfo = "분봉 불러오는 중"; displayBars(true); }
    pollChart(generation, selected, minutes);
  }
}
interval.onchange = cadence.onchange = () => { savePreferences(); syncChart(true); };
range.onchange = () => { savePreferences(); displayBars(); };
byId("custom-form").onsubmit = event => {
  event.preventDefault();
  chosenMinutes = custom.valueAsNumber;
  savePreferences();
  syncChart(true);
};
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { ++generation; clearTimeout(timer); }
  else syncChart(true);
});

function metric(parent, label, value, note, tone = "") {
  const box = element("div", "", parent);
  box.className = "metric";
  element("small", label, box);
  element("strong", value, box).className = tone;
  if (note) element("small", note, box);
}

function render() {
  byId("mode").textContent = state.demo ? "모의 시세" : "토스증권 시세";
  const monitor = state.monitor;
  byId("server-status").textContent = monitor ? "서버 갱신 · " + monitor.status +
    (monitor.checked_at ? " · " + new Date(monitor.checked_at).toLocaleString("ko-KR") : "") : "";
  byId("next").hidden = !state.demo;
  byId("enable").disabled = !state.push_key || !registration;
  byId("test").disabled = !state.push_key || !registration;
  const stocks = byId("stocks");
  const entries = Object.entries(state.watch);
  if (!Object.hasOwn(state.watch, selected)) selected = entries[0]?.[0];
  syncChart();
  const content = JSON.stringify([state.watch, selected, state.results[selected], state.demo]);
  if (renderedStocks !== content) {
  renderedStocks = content;
  const focusId = document.activeElement?.id;
  const openDetails = new Set([...stocks.querySelectorAll("details[open]")].map(item => item.dataset.section));
  stocks.replaceChildren();
  const watchlist = byId("watchlist");
  watchlist.replaceChildren();
  for (const [key, item] of entries) {
    const button = element("button", "", watchlist);
    button.type = "button";
    button.id = "watch-" + key;
    button.className = "watch-button";
    button.setAttribute("aria-pressed", key === selected);
    element("span", item.query, button);
    element("small", item.symbol, button);
    button.onclick = () => { selected = key; render(); };
  }
  if (!selected) element("p", "관심 종목을 추가하세요.", stocks).className = "panel";
  for (const [key, item] of entries.filter(([key]) => key === selected)) {
    const card = element("article", "", stocks);
    card.className = "panel";
    const heading = element("div", "", card);
    heading.className = "card-heading";
    const identity = element("div", "", heading);
    element("h3", item.query + " · 일봉 분석", identity);
    element("p", item.market + " · " + item.symbol, identity).className = "ticker";
    const result = state.results[key];
    if (result?.market) {
      const market = result.market;
      const tone = market.signal === "매수 관심" ? "up" : market.signal === "매도 주의" ? "down" : "";
      element("span", market.signal + " · 기술 점수 " + market.score, heading).className = "signal " + tone;
      const rate = market.hit_rate;
      const bars = market.bars;
      const latest = bars.at(-1);
      const quote = element("p", number(latest.close) + (item.market.startsWith("KOS") ? "원 " : " USD "), card);
      quote.className = "quote";
      element("small", "종가 · " + market.asof.slice(0, 10), quote);
      const metrics = element("div", "", card);
      metrics.className = "metrics";
      const change = market.return_20d * 100;
      metric(metrics, "20봉 수익률", (change > 0 ? "+" : "") + change.toFixed(2) + "%", "", change >= 0 ? "up" : "down");
      metric(metrics, "RSI", market.rsi?.toFixed(1) ?? "—", "");
      metric(metrics, "거래량 배수", market.volume_ratio == null ? "—" : market.volume_ratio.toFixed(2) + "배", number(latest.volume) + "주");
      metric(metrics, state.demo ? "모의 과거 적중률" : "과거 적중률",
        rate?.percent != null ? rate.percent + "%" : rate ? "표본 부족" : "—",
        rate ? (rate.percent == null ? rate.samples + "/10건" : rate.hits + "/" + rate.samples + "건") + " · " + rate.horizon + "봉 기준" : "");
      const reasons = element("details", "", card);
      reasons.className = "detail";
      reasons.dataset.section = key + "-factors";
      reasons.open = openDetails.has(reasons.dataset.section);
      element("summary", "신호 근거", reasons);
      for (const [factor, score] of Object.entries(market.factors)) {
        const line = element("p", "", reasons);
        line.className = "factor";
        element("span", factor, line);
        element("strong", (score > 0 ? "+" : "") + score, line).className = score > 0 ? "up" : score < 0 ? "down" : "";
      }
    } else element("p", "시세 갱신을 기다리는 중", card);
    for (const error of Object.values(result?.errors || {})) element("p", error, card);
    for (const category of ["소식", "공시"]) {
      if (!result?.[category]?.length) continue;
      const details = element("details", "", card);
      details.className = "detail";
      details.dataset.section = key + category;
      details.open = openDetails.has(details.dataset.section);
      element("summary", category + " " + result[category].length + "건", details);
      for (const article of result[category]) {
        const line = element("p", "", details);
        line.className = "article-link";
        if (article.url.startsWith("https://")) {
          const link = element("a", article.title, line);
          link.href = article.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
        } else line.textContent = article.title;
        element("small", " · " + article.date, line);
      }
    }
    const remove = element("button", "관심 종목 해제", card);
    remove.className = "remove";
    remove.onclick = () => run(() => api("/api/watch/" + encodeURIComponent(key), "DELETE"));
  }
  if (focusId && (focusId.startsWith("watch-") || focusId.startsWith("range-"))) {
    byId(focusId)?.focus({ preventScroll: true });
  }
  }
  const events = byId("events");
  events.replaceChildren();
  for (const event of state.events) {
    const line = element("li", "", events);
    element("strong", event.title, line);
    element("small", event.body, line);
  }
  if (!state.events.length) element("li", "아직 새 알림이 없습니다.", events);
}

async function load() {
  state = await api("/api/state");
  render();
}

async function run(action, message = "") {
  if (busy) return;
  busy = true;
  try { await action(); await load(); statusLine.textContent = message; }
  catch (error) { statusLine.textContent = error.message; }
  finally { busy = false; }
}

byId("next").onclick = () => run(() => api("/api/refresh", "POST"));
byId("test").onclick = () => run(() => api("/api/push/test", "POST"), "푸시 서비스가 테스트 알림을 접수했습니다.");
byId("watch").onsubmit = event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.target));
  data.symbol = data.symbol.trim().toUpperCase();
  data.query = data.query.trim();
  run(async () => { await api("/api/watch", "POST", data); event.target.reset(); });
};
byId("enable").onclick = async () => {
  try {
    const encoded = state.push_key.replace(/-/g, "+").replace(/_/g, "/");
    const key = Uint8Array.from(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "=")), c => c.charCodeAt(0));
    const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    await api("/api/push", "POST", subscription.toJSON());
    statusLine.textContent = "알림을 켰습니다. 테스트 알림으로 확인하세요.";
  } catch (error) { statusLine.textContent = "알림 설정 실패 · " + error.message; }
};
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").then(() => navigator.serviceWorker.ready).then(ready => {
    if ("pushManager" in ready) registration = ready;
    if (state) render();
  }).catch(() => {});
}
load().catch(error => { statusLine.textContent = error.message; });
setInterval(() => { if (!busy) load().catch(() => {}); }, 5000);
