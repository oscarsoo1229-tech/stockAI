const byId = id => document.getElementById(id);
const statusLine = byId("status");
let state;
let registration;
let busy = false;

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

function chart(canvas, bars) {
  const ctx = canvas.getContext("2d");
  const { width, height } = canvas;
  const prices = bars.map(bar => bar.close);
  const min = Math.min(...prices), range = Math.max(...prices) - min || 1;
  const maxVolume = Math.max(1, ...bars.map(bar => bar.volume));
  const space = width / bars.length;
  ctx.fillStyle = "#406489";
  bars.forEach((bar, index) => {
    const volume = 36 * bar.volume / maxVolume;
    ctx.fillRect(index * space, height - volume, Math.max(1, space - 2), volume);
  });
  ctx.strokeStyle = "#83cdff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  bars.forEach((bar, index) => {
    const x = index * space + space / 2, y = 8 + (1 - (bar.close - min) / range) * 73;
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function render() {
  byId("mode").textContent = state.demo ? "모의 시세 · 8초마다 갱신" : "토스증권 · 일봉 기준";
  const monitor = state.monitor;
  byId("server-status").textContent = monitor ? "서버 갱신 · " + monitor.status +
    (monitor.checked_at ? " · " + new Date(monitor.checked_at).toLocaleString("ko-KR") : "") : "";
  byId("next").hidden = !state.demo;
  byId("enable").disabled = !state.push_key || !registration;
  byId("test").disabled = !state.push_key || !registration;
  const stocks = byId("stocks");
  stocks.replaceChildren();
  if (!Object.keys(state.watch).length) element("p", "관심 종목을 추가하세요.", stocks);
  for (const [key, item] of Object.entries(state.watch)) {
    const card = element("article", "", stocks);
    element("h3", item.query + " · " + item.symbol, card);
    const result = state.results[key];
    if (result?.market) {
      const market = result.market;
      element("p", market.signal + " · 기술 점수 " + market.score, card).className = "signal";
      const rate = market.hit_rate;
      if (rate) {
        const label = state.demo ? "모의 과거 적중률" : "과거 적중률";
        const summary = rate.percent === null
          ? "표본 부족 (" + rate.samples + "/10건)"
          : rate.percent + "% (" + rate.hits + "/" + rate.samples + "건)";
        element("p", label + " " + summary + " · 신호 후 " + rate.horizon + "봉 기준", card);
      }
      const bars = market.bars;
      const latest = bars.at(-1);
      element("p", "종가 " + latest.close.toLocaleString("ko-KR") + " · 거래량 " +
        latest.volume.toLocaleString("ko-KR") + "주", card);
      const canvas = element("canvas", "", card);
      canvas.width = 600;
      canvas.height = 120;
      canvas.setAttribute("aria-label", "최근 60개 봉의 종가 선과 거래량 막대");
      chart(canvas, bars);
      element("small", "기준일 " + market.asof.slice(0, 10) + " · 20봉 수익률 " +
        (market.return_20d * 100).toFixed(2) + "% · RSI " +
        (market.rsi?.toFixed(1) ?? "—") + " · 거래량 " +
        (market.volume_ratio?.toFixed(2) ?? "—") + "배", card);
      const reasons = element("details", "", card);
      element("summary", "신호 근거", reasons);
      for (const [factor, score] of Object.entries(market.factors)) {
        element("p", factor + " " + score, reasons);
      }
    } else element("p", "시세 갱신을 기다리는 중", card);
    for (const error of Object.values(result?.errors || {})) element("p", error, card);
    for (const category of ["소식", "공시"]) {
      if (!result?.[category]?.length) continue;
      const details = element("details", "", card);
      element("summary", category + " " + result[category].length + "건", details);
      for (const article of result[category]) {
        const line = element("p", "", details);
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
    remove.onclick = () => run(() => api("/api/watch/" + encodeURIComponent(key), "DELETE"));
  }
  const events = byId("events");
  events.replaceChildren();
  for (const event of state.events) element("li", event.title + " · " + event.body, events);
  if (!state.events.length) element("li", "아직 새 알림이 없습니다.", events);
}

async function load() {
  state = await api("/api/state");
  // Keep open details while periodic data replaces the cards.
  const open = [...document.querySelectorAll("#stocks details")].map(node => node.open);
  render();
  document.querySelectorAll("#stocks details").forEach((node, index) => { node.open = !!open[index]; });
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
