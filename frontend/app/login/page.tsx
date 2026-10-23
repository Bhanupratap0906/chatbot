"use client";

import { useEffect, useState, type FormEvent } from "react";

export default function LoginPage() {
  /** Portal credential form: POST /api/auth/login sets the session cookie. */
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [alreadySignedIn, setAlreadySignedIn] = useState(false);

  useEffect(() => {
    // Already have a valid session? Skip straight to the app.
    let alive = true;
    fetch("/api/auth/me", { credentials: "include" })
      .then((res) => {
        if (alive && res.ok) window.location.replace("/");
      })
      .catch(() => {
        /* backend unreachable — stay on the form and show submit errors */
      });
    return () => {
      alive = false;
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    /** Exchange credentials for the portal JWT cookie, then enter the app. */
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ username, password }),
      });
      if (res.ok) {
        setAlreadySignedIn(true);
        window.location.assign("/");
        return;
      }
      const body = (await res.json().catch(() => null)) as
        | { detail?: string }
        | null;
      setError(body?.detail || "Sign-in failed — check your credentials.");
    } catch {
      setError("Cannot reach the server — is the backend running on :8000?");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h1>Sign in</h1>
      <p className="description">
        Use your customer-portal credentials. The backend exchanges them for a
        session token and keeps it in an HttpOnly cookie.
      </p>

      <form onSubmit={handleSubmit} className="sip-credentials">
        <div className="input-group">
          <label htmlFor="portal-username">Portal username</label>
          <input
            id="portal-username"
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="e.g. john_doe"
            required
          />
        </div>

        <div className="input-group">
          <label htmlFor="portal-password">Password</label>
          <input
            id="portal-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            required
          />
        </div>

        <button
          type="submit"
          className="sip-register-btn"
          disabled={busy || alreadySignedIn}
        >
          {busy || alreadySignedIn ? "Signing in…" : "Sign in"}
        </button>
      </form>

      {error ? (
        <div className="status error" role="alert">
          {error}
        </div>
      ) : null}
    </div>
  );
}
