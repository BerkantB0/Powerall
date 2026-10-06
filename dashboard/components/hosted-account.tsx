"use client";
import { useState } from "react";
type Props = { csrf: string; connected: boolean; enabled: boolean; onUpdate: () => Promise<unknown> };
async function post(path: string, body: unknown, csrf: string) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Powerall-Token": csrf }, body: JSON.stringify(body) });
  const result = await response.json() as { error?: string };
  if (!response.ok) throw new Error(result.error || "The request could not be completed.");
  return result;
}
export default function HostedAccount({ csrf, connected, enabled, onUpdate }: Props) {
  const [brightUser, setBrightUser] = useState("");
  const [brightPassword, setBrightPassword] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [deletion, setDeletion] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setMessage("");
    try { await action(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function exportAccount() {
    const response = await fetch("/api/account/export", { cache: "no-store" });
    if (!response.ok) throw new Error("Could not export account data.");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a"); link.href = url; link.download = "powerall-account.json";
    document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    setMessage("Account data export prepared. Keep the downloaded file private.");
  }
  return <section className="hosted-account">
    <div className="section-heading"><div><h2>Your account and connection</h2><p>Only your signed-in account can access your readings.</p></div><a className="text-button" href="/privacy">Privacy information</a></div>
    {error && <p className="banner error" role="alert">{error}</p>}{message && <p className="banner success" role="status">{message}</p>}
    <form className="tariff-form" onSubmit={e => { e.preventDefault(); void run(async () => { await post("/api/connection", { username: brightUser, password: brightPassword }, csrf); setBrightPassword(""); setBrightUser(""); await onUpdate(); setMessage("Bright connection saved. Import queued."); }); }}>
      <h3>{connected ? "Update Bright connection" : "Connect Bright"}</h3><p>Your Bright credentials are encrypted on the server. They are used only to retrieve your meter data and are never returned to this browser.</p>
      {!enabled && <p className="banner warning">Bright connections are currently paused by the operator.</p>}<div className="form-grid"><label>Bright username<input required type="email" autoComplete="off" maxLength={320} value={brightUser} onChange={e => setBrightUser(e.target.value)} /></label><label>Bright password<input required type="password" autoComplete="new-password" maxLength={1024} value={brightPassword} onChange={e => setBrightPassword(e.target.value)} /></label></div>
      <div className="form-actions">{connected && <button type="button" className="button button-outline" disabled={busy} onClick={() => void run(async () => { await post("/api/connection", { disconnect: true }, csrf); await onUpdate(); setMessage("Bright disconnected. Stored readings remain available."); })}>Disconnect Bright</button>}<button className="button button-primary" disabled={busy || !enabled}>{busy ? "Working…" : "Save connection"}</button></div>
    </form>
    <div className="connection-card"><h3>Your stored data</h3><p>Download all stored readings and tariffs as JSON. Bright credentials and session tokens are excluded. The dashboard also offers CSV for individual periods.</p><button className="button button-outline" disabled={busy} onClick={() => void run(exportAccount)}>Download account data</button></div>
    <form className="tariff-form" onSubmit={e => { e.preventDefault(); void run(async () => { await post("/api/auth/password", { password, new_password: newPassword }, csrf); setPassword(""); setNewPassword(""); await onUpdate(); setMessage("Password changed. Other sessions have been signed out."); }); }}><h3>Change your sign-in password</h3><div className="form-grid"><label>Current password<input required type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></label><label>New password<input required minLength={12} maxLength={256} type="password" autoComplete="new-password" value={newPassword} onChange={e => setNewPassword(e.target.value)} /></label></div><button className="button button-outline" disabled={busy}>Change password</button></form>
    <form className="tariff-form" onSubmit={e => { e.preventDefault(); void run(async () => { await post("/api/account/delete", { password }, csrf); window.location.replace("/login"); }); }}><h3>Delete your account</h3><p>Removes your connection, sign-in sessions, readings and manual tariffs from the live service. Operator backups expire separately as described in the privacy notice. Download your data first if you need a copy.</p><div className="form-grid"><label>Confirm current password<input required type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></label><label>Type DELETE to confirm<input required value={deletion} autoComplete="off" onChange={e => setDeletion(e.target.value)} /></label></div><button className="button button-outline" disabled={busy || deletion !== "DELETE"}>Permanently delete account</button></form>
  </section>;
}
