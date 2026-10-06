"use client";
import { useEffect, useState } from "react";
import { Zap } from "lucide-react";
export default function Login() {
  const [register, setRegister] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [invite, setInvite] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const token = new URLSearchParams(window.location.hash.slice(1)).get("invite");
    if (token) { setInvite(token); setRegister(true); window.history.replaceState(null, "", "/login"); }
  }, []);
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const response = await fetch(register ? "/api/auth/register" : "/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password, invite }) });
      const result = await response.json() as { error?: string };
      if (!response.ok) throw new Error(result.error || "Sign-in failed.");
      setPassword(""); window.location.replace("/");
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }
  return <main className="auth-page"><a className="brand" href="/"><span className="brand-mark"><Zap size={22} /></span>powerall.</a><form className="tariff-form" onSubmit={submit}><h1>{register ? "Your household, your account." : "Welcome back."}</h1><p>{register ? "Create an account with your invitation. Connect Bright after signing in." : "Sign in to view your electricity readings."}</p><label>Username<input required minLength={3} maxLength={64} pattern="[a-zA-Z0-9_.-]+" autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} /></label><label>Password<input required type="password" minLength={register ? 15 : undefined} maxLength={256} autoComplete={register ? "new-password" : "current-password"} value={password} onChange={e => setPassword(e.target.value)} /></label>{register && <><label>Invitation code<input required autoComplete="off" value={invite} onChange={e => setInvite(e.target.value)} /></label><p>Use a unique password or passphrase of at least 15 characters.</p></>}{error && <p className="form-error" role="alert">{error}</p>}<button className="button button-primary" disabled={busy}>{busy ? "Please wait…" : register ? "Create account" : "Sign in"}</button><button className="text-button" type="button" onClick={() => { setRegister(!register); setError(""); }}>{register ? "Already have an account? Sign in" : "Have an invitation? Create an account"}</button><a href="/privacy" className="text-button">Privacy information</a><p>No recovery email is collected. Contact the operator if you lose access.</p></form></main>;
}
