"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, BarChart3, Check, ChevronDown, CircleAlert, Clock3, Database, Download, Leaf, Pencil, RefreshCw, Settings2, Trash2, X, Zap } from "lucide-react";

import HostedAccount from "../components/hosted-account";
import { breakdown, readingsCsv, ukDate } from "../lib/energy";

type Meter = { id: string; name: string; provider: string; active: number };
type Run = { status: string; phase: string; imported: number; error: string | null; warnings: string[]; started: number; finished: number | null };
type Status = { provider_enabled: boolean; hosted: boolean; username: string | null; connected: boolean; meters: Meter[]; csrf: string; today: string; timezone: string; sync: { queue?: { state: string; available_at: number } | null; run: Run | null; last_success: number | null } };
type Slot = { start: string; time: string; zone: string; state: string; kwh: number | null; cost: number | null; rate: number | null; off_peak: boolean; tariff_label: string | null };
type Summary = { kwh: number; known_cost: number; estimated_cost: number | null; expected: number; received: number; missing: number; unpriced: number; priced: number; standing: number; average_unit_rate: number | null; off_peak_share: number | null; warnings: string[] };
type Day = Omit<Summary, "standing"> & { standing: number | null } & { date: string; full_day_expected: number; in_progress: boolean; slots: Slot[]; usage_cost: number };
type Dashboard = { view: string; from: string; to: string; anchor: string; now: string; summary: Summary; days: Day[]; latest_reading: string | null; earliest_reading: string | null; stored_readings: number; stale: boolean; comparison: { from: string; to: string; matched_elapsed_period: boolean; summary: Summary; usage_change_percent: number | null; cost_change_percent: number | null } };
type WindowRate = { start: string; end: string; rate: string; days?: number[] };
const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const windowDays = (w: WindowRate) => w.days?.length && w.days.length < 7 ? w.days.map(d => weekdays[d]).join(", ") : "Every day";
type Tariff = { id: string; label: string; source: string; rate: string; standing: string; windows: WindowRate[]; clock: string; vat: string; from_utc: string; to_utc: string | null; warning: string | null };
type View = "day" | "week" | "month";
const money = (value: number) => new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" }).format(value);
const number = (value: number, digits = 2) => value.toLocaleString("en-GB", { maximumFractionDigits: digits });
const dateLabel = (value: string, options: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", year: "numeric" }) => new Date(value.length === 10 ? value + "T12:00:00Z" : value).toLocaleDateString("en-GB", { ...options, timeZone: "Europe/London" });
const timestamp = (value: number | string) => new Date(typeof value === "number" ? value * 1000 : value).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/London", timeZoneName: "short" });

async function api<T>(path: string, payload?: unknown, csrf?: string): Promise<T> {
  const response = await fetch(path, payload !== undefined ? { method: "POST", headers: { "Content-Type": "application/json", "X-Powerall-Token": csrf || "" }, body: JSON.stringify(payload) } : { cache: "no-store" });
  const result = await response.json() as T & { error?: string; message?: string };
  if (response.status === 401) { window.location.replace("/login"); throw new Error("Sign in to continue."); }
  if (!response.ok) throw new Error(result.error || result.message || "The request could not be completed.");
  return result;
}
function shiftDate(value: string, view: View, direction: number) {
  const date = new Date(value + "T12:00:00Z");
  if (view === "month") { date.setUTCDate(1); date.setUTCMonth(date.getUTCMonth() + direction); }
  else date.setUTCDate(date.getUTCDate() + (view === "week" ? 7 : 1) * direction);
  return date.toISOString().slice(0, 10);
}
function Change({ value, noun, partial }: { value: number | null; noun: string; partial: boolean }) {
  if (value === null) return <span className="muted">Comparison needs complete data</span>;
  const Icon = value <= 0 ? ArrowDown : ArrowUp;
  return <span className={`change ${value <= 0 ? "saving" : "increase"}`}><Icon size={13} /> {number(Math.abs(value), 1)}% {value <= 0 ? "less" : "more"} {noun}<span className="muted"> · {partial ? "same elapsed period" : "previous period"}</span></span>;
}

export default function Home() {
  const [status, setStatus] = useState<Status | null>(null);
  const [meter, setMeter] = useState("");
  const [view, setView] = useState<View>("day");
  const [date, setDate] = useState("");
  const [data, setData] = useState<Dashboard | null>(null);
  const [tariffs, setTariffs] = useState<Tariff[]>([]);
  const [panel, setPanel] = useState<"dashboard" | "tariffs" | "data">("dashboard");
  const [metric, setMetric] = useState<"usage" | "cost">("usage");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [showReadings, setShowReadings] = useState(false);
  const [importDays, setImportDays] = useState("90");
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState({ label: "", from: "", to: "", rate: "", standing: "", clock: "Europe/London", vat: "included" });
  const [windows, setWindows] = useState<WindowRate[]>([]);
  const [formError, setFormError] = useState("");

  const newTariff = (from = "") => {
    setEditingId(null); setFormError(""); setWindows([]);
    setForm({ label: "", from, to: "", rate: "", standing: "", clock: "Europe/London", vat: "included" });
    setShowForm(true);
  };
  const editTariff = (tariff: Tariff) => {
    setEditingId(tariff.id); setFormError("");
    setForm({ label: tariff.label, from: ukDate(tariff.from_utc), to: tariff.to_utc ? ukDate(tariff.to_utc) : "", rate: tariff.rate, standing: tariff.standing, clock: tariff.clock, vat: tariff.vat });
    setWindows(tariff.windows.map(w => ({ ...w }))); setShowForm(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  useEffect(() => { setShowForm(false); setEditingId(null); }, [meter]);
  const exportCsv = () => {
    if (!data) return;
    const url = URL.createObjectURL(new Blob([readingsCsv(data.days)], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url; link.download = `powerall-${data.from}-${shiftDate(data.to, "day", -1)}.csv`;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setNotice("CSV exported for the selected period. Blank cells indicate missing readings or prices; costs are estimates.");
  };

  const readStatus = useCallback(async () => {
    const next = await api<Status>("/api/status");
    setStatus(next);
    setMeter(previous => next.meters.some(m => m.id === previous) ? previous : next.meters[0]?.id || "");
    setDate(previous => previous || shiftDate(next.today, "day", -1));
    return next;
  }, []);
  useEffect(() => {
    readStatus().catch(e => { setError(e.message); setLoading(false); });
    const timer = setInterval(() => readStatus().catch(e => setError(e.message)), 5000);
    return () => clearInterval(timer);
  }, [readStatus, status?.sync.run?.status === "running" || Boolean(status?.sync.queue)]);

  const loadData = useCallback(async () => {
    if (!meter || !date) { setLoading(false); return; }
    const [next, history] = await Promise.all([
      api<Dashboard>(`/api/dashboard?meter=${encodeURIComponent(meter)}&view=${view}&date=${date}`),
      api<{ tariffs: Tariff[] }>(`/api/tariffs?meter=${encodeURIComponent(meter)}`),
    ]);
    setData(next); setTariffs(history.tariffs); setError("");
  }, [meter, date, view]);
  // Keep each fetch associated with its selection: late responses never replace
  // a newer date, meter or view. Aborted effects suppress stale results.
  useEffect(() => {
    let current = true;
    setSelected(null);
    if (!meter || !date) { setLoading(false); return; }
    setLoading(true);
    Promise.all([
      api<Dashboard>(`/api/dashboard?meter=${encodeURIComponent(meter)}&view=${view}&date=${date}`),
      api<{ tariffs: Tariff[] }>(`/api/tariffs?meter=${encodeURIComponent(meter)}`),
    ]).then(([next, history]) => { if (current) { setData(next); setTariffs(history.tariffs); setError(""); } })
      .catch(e => { if (current) setError(e.message); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [meter, date, view, status?.sync.run?.imported, status?.sync.run?.finished]);

  const syncing = status?.sync.run?.status === "running" || Boolean(status?.sync.queue);
  const refresh = async (days?: number) => {
    setPending(true); setError("");
    try { await api("/api/sync", days ? { days } : {}, status?.csrf); await readStatus(); setNotice(status?.hosted ? "Import queued. Your readings will update as it runs." : "Import started. You can keep exploring while it runs."); }
    catch (e) { setError((e as Error).message); }
    finally { setPending(false); }
  };
  const saveTariff = async (event: React.FormEvent) => {
    event.preventDefault(); setFormError(""); setPending(true);
    try {
      await api("/api/tariffs", { ...form, windows, meter, ...(editingId ? { id: editingId } : {}) }, status?.csrf);
      await loadData(); setShowForm(false); setEditingId(null); setForm({ label: "", from: "", to: "", rate: "", standing: "", clock: "Europe/London", vat: "included" }); setWindows([]);
      setNotice("Tariff saved. Estimated costs have been recalculated.");
    } catch (e) { setFormError((e as Error).message); }
    finally { setPending(false); }
  };
  const deleteTariff = async (tariff: Tariff) => {
    if (!window.confirm(`Delete “${tariff.label}”? Costs for its dates will use Bright rates where available.`)) return;
    try { await api("/api/tariffs/delete", { id: tariff.id }, status?.csrf); await loadData(); setNotice("Manual tariff deleted."); }
    catch (e) { setError((e as Error).message); }
  };
  const summary = data?.summary;
  const chart = data ? view === "day" ? data.days[0].slots.map(s => ({
    value: metric === "usage" ? s.kwh : s.cost, label: `${s.time} ${s.zone}`, state: s.state,
    offPeak: s.off_peak, parts: null, slot: s, day: undefined as Day | undefined,
  })) : data.days.map(d => ({
    value: d.expected === 0 ? null : metric === "usage" ? d.received ? d.kwh : null : d.estimated_cost,
    label: dateLabel(d.date, { weekday: "short", day: "numeric", month: "short" }),
    state: d.expected === 0 ? "future" : d.missing || (metric === "cost" && d.estimated_cost === null) ? "missing" : "observed",
    offPeak: false, parts: breakdown(d, metric), slot: undefined as Slot | undefined, day: d,
  })) : [];
  const maximum = Math.max(...chart.map(c => c.value || 0), metric === "usage" ? 0.1 : 0.01) * 1.15;
  const currentBar = selected !== null ? chart[selected] : null;
  const periodTitle = data ? view === "day" ? dateLabel(data.from, { weekday: "long", day: "numeric", month: "long" }) : view === "month" ? dateLabel(data.from, { month: "long", year: "numeric" }) : `${dateLabel(data.from, { day: "numeric", month: "short" })} – ${dateLabel(shiftDate(data.to, "day", -1), { day: "numeric", month: "short", year: "numeric" })}` : "Your electricity";
  const coverage = summary && summary.expected ? Math.round(summary.received / summary.expected * 100) : 0;

  return <div className="app-shell">
    <aside className="rail"><a className="brand" href="/" aria-label="Powerall home"><span className="brand-mark"><Zap size={20} fill="currentColor" /></span><span>powerall<span className="brand-dot">.</span></span></a><div className="rail-caption">YOUR ENERGY, CLEARER</div>
      <nav aria-label="Main navigation">{([{ key: "dashboard", label: "Overview", icon: BarChart3 }, { key: "tariffs", label: "Tariffs", icon: Settings2 }, { key: "data", label: "Data & connection", icon: Database }] as const).map(item => <button key={item.key} onClick={() => setPanel(item.key)} aria-label={item.label} className={`nav-item ${panel === item.key ? "active" : ""}`} aria-current={panel === item.key ? "page" : undefined}><item.icon size={19} /><span>{item.label}</span></button>)}</nav>
    </aside>
    <div className="workspace">
      <header className="topbar"><div className="topbar-title"><span className={`status-dot ${status?.meters.length ? "connected" : ""}`} /><span>{status?.meters.length ? "Bright connected" : "Connecting to Bright"}</span><span className="topbar-divider" /><span className="topbar-secondary">Electricity · UK</span></div><button className="button button-outline refresh-button" onClick={() => refresh()} disabled={pending || syncing} aria-label="Refresh Bright readings"><RefreshCw size={15} className={syncing ? "spin" : ""} /><span>{syncing ? "Syncing…" : "Refresh data"}</span></button></header>
      <main>
        <div aria-live="polite" className="live-messages">{error && <div className="banner error"><CircleAlert size={17} /><span>{error}</span><button onClick={() => readStatus().then(loadData).catch(e => setError(e.message))}>Retry</button></div>}{notice && <div className="banner success"><Check size={17} /><span>{notice}</span><button className="icon-button" onClick={() => setNotice("")} aria-label="Dismiss notification"><X size={16} /></button></div>}{syncing && <div className="banner sync"><RefreshCw size={16} className="spin" /><span>{status?.sync.queue?.state === "queued" ? (status.sync.queue.available_at > Date.now() / 1000 ? `Import retry scheduled for ${timestamp(status.sync.queue.available_at)}` : "Import queued. Waiting for a worker.") : `${status?.sync.run?.phase} · ${number(status?.sync.run?.imported || 0, 0)} readings processed`}</span></div>}{status?.sync.run?.status === "failed" && <div className="banner error"><CircleAlert size={17} /><span>{status.sync.run.error} Your saved data is still available.</span></div>}</div>
        <div className="page-heading"><div><p className="eyebrow">{panel === "dashboard" ? "THE BIG PICTURE" : panel === "tariffs" ? "THE PRICE OF POWER" : "CONNECTED, AND IN CONTROL"}</p><h1>{panel === "dashboard" ? "Your energy, in focus." : panel === "tariffs" ? "Every rate has its place." : "A clear view of your data."}</h1><p className="subtitle">{panel === "dashboard" ? "See what you use, when you use it, and what it costs." : panel === "tariffs" ? "Keep the right prices attached to the right half-hours." : "Readings from Bright. Your history, under your control."}</p></div>{status && status.meters.length > 1 && <label className="meter-picker">Meter<select value={meter} onChange={e => setMeter(e.target.value)}>{status.meters.map(m => <option key={m.id} value={m.id}>{m.name}{m.active ? "" : " (previous meter)"}</option>)}</select></label>}</div>
        {status?.hosted && <div className="account-toolbar"><span className="muted">Signed in as {status.username}</span><button className="text-button" onClick={async () => { try { await api("/api/auth/logout", {}, status.csrf); window.location.replace("/login"); } catch (e) { setError((e as Error).message); } }}>Sign out</button></div>}
        {status?.hosted && (panel === "data" || !meter) && <HostedAccount csrf={status.csrf} connected={status.connected} enabled={status.provider_enabled} onUpdate={async () => { await readStatus(); await loadData(); }} />}
        {!meter ? <section className="empty-state"><span className="empty-icon"><Zap size={28} /></span><h2>{syncing ? "Bringing your electricity into view" : "Let’s connect your electricity"}</h2><p>{syncing ? "The first import collects your meter, tariff and recent half-hourly history. This may take a minute." : status?.hosted ? "Connect your Bright account above to import your electricity readings." : "Add your Bright credentials to the local .env file, restart the app, then connect. Nothing is entered into the browser."}</p><button className="button button-primary" disabled={syncing || pending || (status?.hosted && !status.connected)} onClick={() => refresh(90)}>{syncing ? "Importing readings…" : "Connect to Bright"}</button></section> : <>
          {panel === "dashboard" && <>
            <div className="period-toolbar"><div className="segmented" aria-label="View period">{(["day", "week", "month"] as View[]).map(v => <button key={v} aria-pressed={view === v} className={view === v ? "selected" : ""} onClick={() => setView(v)}>{v[0].toUpperCase() + v.slice(1)}</button>)}</div><div className="date-controls"><button className="icon-button" aria-label="Previous period" onClick={() => setDate(shiftDate(date, view, -1))}><ArrowLeft size={17} /></button><label className="date-field"><span className="sr-only">Choose date</span><input type="date" value={date} max={status?.today} onChange={e => { if (e.target.value) setDate(e.target.value); }} /></label><button className="icon-button" aria-label="Next period" disabled={!status || shiftDate(date, view, 1) > status.today} onClick={() => setDate(shiftDate(date, view, 1))}><ArrowRight size={17} /></button></div></div>
            {loading && !data ? <div className="empty-state"><RefreshCw className="spin" size={24} /><p>Loading your readings…</p></div> : data && summary && <div className={loading ? "content loading" : "content"} aria-busy={loading}>
              <div className="period-heading"><h2>{periodTitle}</h2><span className={`pill ${summary.missing || !summary.expected ? "amber" : "green"}`}>{summary.expected ? `${coverage}% of elapsed readings` : "No elapsed readings"}</span></div>
              <div className="stat-grid"><article className="stat-card"><div className="stat-label">Electricity used<Zap size={16} /></div><div className="stat-value">{summary.received ? number(summary.kwh) : "—"}<span> kWh</span></div><Change value={data.comparison.usage_change_percent} noun="energy" partial={data.comparison.matched_elapsed_period} /></article>
                <article className="stat-card cost-card"><div className="stat-label">Estimated cost<span className="tiny-pill">ESTIMATE</span></div><div className="stat-value">{summary.estimated_cost !== null ? money(summary.estimated_cost) : summary.priced || summary.standing ? money(summary.known_cost) : "—"}{summary.estimated_cost === null && (summary.priced > 0 || summary.standing > 0) && <span> subtotal</span>}</div>{summary.estimated_cost !== null ? <Change value={data.comparison.cost_change_percent} noun="cost" partial={data.comparison.matched_elapsed_period} /> : <span className="muted">{summary.unpriced ? `${summary.unpriced} readings need tariff rates` : "Incomplete readings or tariff coverage"}</span>}</article>
                <article className="stat-card"><div className="stat-label">Average unit price<BarChart3 size={16} /></div><div className="stat-value">{summary.average_unit_rate !== null ? number(summary.average_unit_rate) : "—"}<span>{summary.average_unit_rate !== null ? " p/kWh" : ""}</span></div><span className="muted">Weighted by priced usage · excludes standing</span></article>
                <article className="stat-card"><div className="stat-label">Off-peak share<Leaf size={16} /></div><div className="stat-value">{summary.off_peak_share !== null && summary.priced ? number(summary.off_peak_share, 1) : "—"}<span>{summary.off_peak_share !== null && summary.priced ? "%" : ""}</span></div><span className="muted">{summary.unpriced ? "Share of observed use; unpriced periods unknown" : "Your usage in lower-rate windows"}</span></article></div>
              {(summary.missing > 0 || summary.unpriced > 0 || data.stale) && <div className="data-notice"><CircleAlert size={18} /><div><strong>{summary.missing ? `${summary.missing} elapsed half-hours are missing.` : summary.unpriced ? "Some half-hours do not have a tariff." : "Readings are more than 48 hours behind."}</strong><p>{summary.missing ? "Usage totals include received readings only. Gaps are shown in the chart and are never counted as zero." : summary.unpriced ? "Usage is available. Add historical rates to calculate costs for earlier dates." : "Bright provides delayed data. Refresh to check for newer readings."}</p></div>{summary.unpriced > 0 && <button className="text-button" onClick={() => { setPanel("tariffs"); newTariff(data.from); }}>Add tariff <ArrowRight size={14} /></button>}</div>}
              <div className="export-toolbar"><button className="button button-outline" onClick={exportCsv} disabled={loading || !summary.expected}><Download size={15} /> Export {view} as CSV</button><span className="muted">Half-hourly readings, rates and estimated costs</span></div><section className="chart-card"><div className="chart-heading"><div><h3>{view === "day" ? "The rhythm of your day" : view === "week" ? "Your week at a glance" : "Your month, day by day"}</h3><p>{view === "day" ? "Half-hourly readings · tap a bar for the detail" : "Daily totals · tap a day to explore its half-hours"}</p></div><div className="segmented compact"><button className={metric === "usage" ? "selected" : ""} aria-pressed={metric === "usage"} onClick={() => setMetric("usage")}>kWh</button><button className={metric === "cost" ? "selected" : ""} aria-pressed={metric === "cost"} onClick={() => setMetric("cost")}>£</button></div></div>
                <div className="chart-detail" aria-live="polite">{currentBar ? <><strong>{currentBar.label}</strong><span>{currentBar.value !== null ? metric === "usage" ? `${number(currentBar.value, 3)} kWh` : `${money(currentBar.value)} ${view === "day" ? "usage cost" : "estimated total"}` : currentBar.state === "future" ? "Not yet elapsed" : metric === "cost" && currentBar.slot?.kwh !== null ? "Tariff unavailable" : "Reading unavailable"}</span>{currentBar.slot?.rate !== null && currentBar.slot?.rate !== undefined && <span>{number(currentBar.slot.rate)}p/kWh · {currentBar.slot.rate === 0 ? "Free electricity" : currentBar.offPeak ? "Off-peak" : "Standard rate"}</span>}{currentBar.parts && currentBar.value !== null && <span>{metric === "usage" ? `${number(currentBar.parts.offPeak, 3)} kWh` : money(currentBar.parts.offPeak)} off-peak</span>}{currentBar.day?.missing ? <span>{currentBar.day.missing} missing half-hours</span> : null}</> : <><span className="chart-unit">{metric === "usage" ? "ENERGY / kWh" : view === "day" ? "USAGE COST / £" : "TOTAL COST / £"}</span><span className="muted">{view === "day" ? "All times shown in UK local time" : metric === "cost" ? "Includes standing charge when known" : "Standard and off-peak usage stacked"}</span></>}</div>
                <div className="chart-layout"><div className="y-axis">{[1, 0.75, 0.5, 0.25, 0].map(scale => <span key={scale}>{metric === "usage" ? number(maximum * scale, maximum > 5 ? 0 : 2) : money(maximum * scale)}</span>)}</div><div className="chart-plot"><div className="grid-lines">{[0, 1, 2, 3, 4].map(i => <div key={i} />)}</div><div className={`bars ${view !== "day" ? "daily-bars" : ""}`} role="group" aria-label={`${view} ${metric} chart`}>{chart.map((item, i) => <button key={i} className={`bar-column ${item.offPeak ? "off-peak" : ""} ${item.state} ${selected === i ? "focused" : ""}`} onMouseEnter={() => setSelected(i)} onFocus={() => setSelected(i)} onClick={() => { setSelected(i); if (item.day) { setDate(item.day.date); setView("day"); } }} aria-label={`${item.label}: ${item.value !== null ? metric === "usage" ? `${number(item.value, 3)} kWh` : money(item.value) : item.state === "future" ? "not yet elapsed" : "unavailable"}${item.value !== null ? item.parts ? `; ${metric === "usage" ? `${number(item.parts.offPeak, 3)} kWh` : money(item.parts.offPeak)} off-peak` : item.slot?.rate === null ? "; tariff unknown" : item.offPeak ? "; off-peak" : "; standard rate" : ""}${item.day ? ". Open this day" : ""}`}><span className={`bar ${item.parts && item.value !== null ? "stacked" : item.slot?.rate === null && metric === "usage" ? "unpriced-usage" : ""} ${item.value === null && item.state !== "future" ? "unknown" : ""}`} style={{ height: item.value !== null ? `${Math.max(item.value / maximum * 100, 1)}%` : item.state === "future" ? "1%" : "13%" }} >{item.parts && item.value !== null && item.value > 0 && Object.entries(item.parts).map(([key, value]) => <span key={key} className={`bar-segment ${key}`} style={{ height: `${value / item.value! * 100}%` }} />)}</span></button>)}</div><div className="x-axis">{chart.filter((_, i) => i % Math.ceil(chart.length / (view === "week" ? 7 : 6)) === 0).map(item => <span key={item.label}>{view === "day" ? item.label.split(" ")[0] : item.label.split(" ").slice(0, 2).join(" ")}</span>)}</div></div></div>
                <div className="chart-footer"><div className="legend"><span><i className="standard" />{metric === "usage" ? "Standard-rate usage" : "Standard-rate cost"}</span><span><i className="offpeak" />{metric === "usage" ? "Off-peak / free usage" : "Off-peak cost"}</span>{metric === "usage" ? <span><i className="other" />Tariff unknown</span> : view !== "day" && <span><i className="other" />Standing charge</span>}<span><i className="missing" />Missing / unpriced</span></div><span>{view === "day" ? `${data.days[0].full_day_expected} half-hours this UK day` : "Tap a day to drill down"}</span></div>
              </section>
              <div className="below-grid"><section className="insight-card"><div className="section-label"><Clock3 size={17} /> YOUR DATA AT A GLANCE</div><h3>{summary.missing === 0 && summary.expected > 0 ? "Every elapsed half-hour accounted for." : "A clear picture includes the gaps."}</h3><p>{number(summary.received, 0)} of {number(summary.expected, 0)} expected elapsed readings received. {view === "day" && data.days[0].full_day_expected !== 48 ? "This date includes a UK clock change; its day length is handled automatically." : "Missing intervals stay visible while Bright catches up."}</p><div className="coverage-track"><span style={{ width: `${coverage}%` }} /></div><div className="coverage-label"><span>Reading coverage</span><strong>{coverage}%</strong></div></section>
                <section className="insight-card tariff-insight"><div className="section-label"><Settings2 size={17} /> UNDERSTANDING YOUR COST</div><h3>{summary.estimated_cost !== null ? "Small intervals. The full cost." : "The right tariff fills in the picture."}</h3><p>Estimated cost adds each half-hour’s usage × its unit rate, plus one standing charge per UK calendar day. {summary.unpriced ? "Earlier dates need their own historical rates." : "Supplier rounding and billing adjustments may differ."}</p><button className="text-button" onClick={() => setPanel("tariffs")}>View tariff history <ArrowRight size={15} /></button></section></div>
              {summary.warnings.map(w => <p key={w} className="estimate-note"><CircleAlert size={14} />{w}</p>)}
              <details className="readings-details" open={showReadings} onToggle={e => setShowReadings(e.currentTarget.open)}><summary>Explore the underlying readings <ChevronDown size={15} /></summary>{showReadings && <div className="table-scroll"><table><caption className="sr-only">Half-hourly readings and estimated usage costs</caption><thead><tr><th>Date / UK time</th><th>Usage</th><th>Rate</th><th>Usage cost</th><th>Status</th></tr></thead><tbody>{data.days.flatMap(d => d.slots.map(s => <tr key={s.start}><td>{dateLabel(d.date, { day: "numeric", month: "short" })} · {s.time} {s.zone}</td><td>{s.kwh === null ? "—" : `${number(s.kwh, 3)} kWh`}</td><td>{s.rate === null ? "—" : `${number(s.rate)}p`}</td><td>{s.cost === null ? "—" : money(s.cost)}</td><td>{s.state === "future" ? "Not yet elapsed" : s.state === "missing" ? "Missing reading" : s.cost === null ? "Missing tariff" : "Received"}</td></tr>))}</tbody></table></div>}</details>
            </div>}
          </>}
          {panel === "tariffs" && <section className="tariff-panel"><div className="section-heading"><div><h2>Your tariff timeline</h2><p>Manual entries take priority over Bright within their effective dates.</p></div><button className="button button-primary" onClick={() => { if (showForm) setShowForm(false); else newTariff(); }}> {showForm ? "Close form" : "+ Add historical tariff"}</button></div>
            {showForm && <form className="tariff-form" onSubmit={saveTariff}><h3>{editingId ? "Edit tariff period" : "Add a tariff period"}</h3><p>Use the rates from your bill. Effective dates start at midnight in UK local time; the end date is excluded.</p><div className="form-grid"><label className="span-two">Tariff name<input required maxLength={120} value={form.label} onChange={e => setForm({ ...form, label: e.target.value })} placeholder="e.g. Economy 7 · summer 2026" /></label><label>Effective from<input required type="date" value={form.from} onChange={e => setForm({ ...form, from: e.target.value })} /></label><label>Effective until (optional)<input type="date" value={form.to} onChange={e => setForm({ ...form, to: e.target.value })} /><small>Leave empty for an ongoing tariff.</small></label><label>Standard rate · p/kWh<input required inputMode="decimal" type="number" min="0" max="10000" step="any" value={form.rate} onChange={e => setForm({ ...form, rate: e.target.value })} /></label><label>Standing charge · p/day<input required inputMode="decimal" type="number" min="0" max="10000" step="any" value={form.standing} onChange={e => setForm({ ...form, standing: e.target.value })} /></label><label>Tariff window clock<select value={form.clock} onChange={e => setForm({ ...form, clock: e.target.value })}><option value="Europe/London">UK local time (changes with BST/GMT)</option><option value="UTC">UTC / GMT all year</option></select></label><label>Rates include VAT?<select value={form.vat} onChange={e => setForm({ ...form, vat: e.target.value })}><option value="included">Yes · use rates as entered</option><option value="excluded">No · add 5% domestic VAT</option></select></label></div><div className="windows-heading"><h4>Off-peak and free-hour windows</h4><button type="button" className="text-button" disabled={windows.length >= 12} onClick={() => setWindows([...windows, { start: "00:30", end: "07:30", rate: "" }])}>+ Add window</button></div><p className="form-hint">Enter 0p/kWh for free hours. Weekday-specific windows override daily windows. Times use the selected tariff clock; overnight windows start on the selected day. Standing charges still apply.</p>{windows.map((w, i) => <div className="window-row" key={i}><label>Applies on<select value={w.days?.length === 1 ? String(w.days[0]) : w.days?.length && w.days.length < 7 ? "custom" : "daily"} onChange={e => setWindows(windows.map((v, j) => i === j ? { ...v, days: e.target.value === "daily" ? undefined : [Number(e.target.value)] } : v))}><option value="daily">Every day</option>{w.days && w.days.length > 1 && w.days.length < 7 && <option value="custom">{windowDays(w)}</option>}{weekdays.map((day, d) => <option key={day} value={d}>{day}</option>)}</select></label><label>Starts<input required type="time" step="1800" value={w.start} onChange={e => setWindows(windows.map((v, j) => i === j ? { ...v, start: e.target.value } : v))} /></label><label>Ends<input required type="time" step="1800" value={w.end} onChange={e => setWindows(windows.map((v, j) => i === j ? { ...v, end: e.target.value } : v))} /></label><label>Rate · p/kWh<input required inputMode="decimal" type="number" min="0" step="any" value={w.rate} onChange={e => setWindows(windows.map((v, j) => i === j ? { ...v, rate: e.target.value } : v))} /></label><button type="button" className="icon-button" aria-label={`Remove window ${i + 1}`} onClick={() => setWindows(windows.filter((_, j) => i !== j))}><Trash2 size={17} /></button></div>)}{formError && <p className="form-error" role="alert">{formError}</p>}<div className="form-actions"><button type="button" className="button button-outline" onClick={() => setShowForm(false)}>Cancel</button><button className="button button-primary" disabled={pending}>{pending ? "Saving…" : "Save & recalculate"}</button></div></form>}
            <div className="tariff-list">{tariffs.length === 0 && <div className="empty-state"><Settings2 size={28} /><h3>No tariff rates yet</h3><p>Add your bill rates to see estimated costs alongside consumption.</p></div>}{tariffs.map(t => <article className="tariff-card" key={t.id}><div className="tariff-card-heading"><div><span className={`pill ${t.source === "manual" ? "green" : "neutral"}`}>{t.source === "manual" ? "Manually entered" : "From Bright"}</span><h3>{t.label === "fromDCC" ? "Smart meter tariff" : t.label}</h3><p>From {timestamp(t.from_utc)}{t.to_utc ? ` · until ${timestamp(t.to_utc)} (excluded)` : " · until superseded"}</p></div>{t.source === "manual" && <div className="tariff-actions"><button className="icon-button" aria-label={`Edit ${t.label}`} onClick={() => editTariff(t)}><Pencil size={17} /></button><button className="icon-button" aria-label={`Delete ${t.label}`} onClick={() => deleteTariff(t)}><Trash2 size={17} /></button></div>}</div><div className="tariff-rates"><div><span>Standard rate</span><strong>{number(Number(t.rate))}<small>p/kWh</small></strong></div><div><span>Standing charge</span><strong>{number(Number(t.standing))}<small>p/day</small></strong></div>{t.windows.map((w, i) => <div key={i}><span>{windowDays(w)} · {w.start}–{w.end} · {t.clock === "UTC" ? "UTC" : "UK time"}</span><strong>{number(Number(w.rate))}<small>p/kWh</small></strong></div>)}</div><div className="tariff-meta"><span>{t.clock === "UTC" ? "UTC / GMT all year" : "UK local time · BST/GMT"}</span><span>{t.vat === "included" ? "VAT included" : t.vat === "excluded" ? "5% VAT added to estimates" : "VAT unconfirmed · rates used as reported"}</span></div>{t.warning && <p className="estimate-note"><CircleAlert size={14} />{t.warning}</p>}</article>)}</div>
          </section>}
          {panel === "data" && <section className="connection-panel"><div className="connection-card"><div className="section-label"><Database size={17} /> BRIGHT CONNECTION</div><h2>{status?.meters.find(m => m.id === meter)?.name || "Your home"}</h2><dl><div><dt>Provider</dt><dd>Bright / Hildebrand Glowmarkt</dd></div><div><dt>Latest half-hour begins</dt><dd>{data?.latest_reading ? timestamp(data.latest_reading) : "No readings imported"}</dd></div><div><dt>Last successful sync</dt><dd>{status?.sync.last_success ? timestamp(status.sync.last_success) : "Not yet synced"}</dd></div><div><dt>Stored readings</dt><dd>{number(data?.stored_readings || 0, 0)}</dd></div><div><dt>Earliest reading</dt><dd>{data?.earliest_reading ? timestamp(data.earliest_reading) : "—"}</dd></div><div><dt>Automatic refresh</dt><dd>Every 6 hours by default while the app runs</dd></div><div><dt>Display timezone</dt><dd>Europe/London · BST/GMT</dd></div></dl><p className="form-hint">Bright supplies delayed readings. A successful refresh can still leave gaps or older readings; the dashboard reports both separately.</p><button className="button button-primary" disabled={pending || syncing || (status?.hosted && !status.connected)} onClick={() => refresh()}><RefreshCw size={15} />Refresh recent readings</button></div><div className="connection-card"><div className="section-label"><Clock3 size={17} /> HISTORICAL IMPORT</div><h3>Give your history more context.</h3><p>Import older half-hours or re-fetch an existing period. Updates replace matching readings without counting them twice.</p><label>Days to look back<select value={importDays} onChange={e => setImportDays(e.target.value)}><option value="30">30 days</option><option value="90">90 days</option><option value="180">180 days</option><option value="365">1 year</option><option value="730">2 years</option></select></label><button className="button button-outline" disabled={syncing || pending || (status?.hosted && !status.connected)} onClick={() => refresh(Number(importDays))}>Import history <ArrowRight size={15} /></button><p className="form-hint">Availability depends on your meter and Bright history. Each request is limited to ten days, and incomplete days remain clearly marked.</p></div>{status?.sync.run?.warnings.map(w => <div key={w} className="banner warning"><CircleAlert size={16} /><span>{w}</span></div>)}</section>}
        </>}
        <footer className="page-footer"><span><span className="status-dot connected" />{data?.latest_reading ? `Latest reading: ${timestamp(data.latest_reading)}` : "Waiting for readings"}</span><span>Half-hourly insight. Everyday clarity.</span></footer>
      </main>
    </div>
  </div>;
}
