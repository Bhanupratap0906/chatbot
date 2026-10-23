"use client";

import { useEffect, useState, type ReactNode } from "react";

export function RequireAuth({ children }: { children: ReactNode }) {
  /** Client-side route guard: verify the portal session before rendering. */
  // WHAT: probes GET /api/auth/me (HttpOnly cookie rides along automatically);
  // renders children only when the backend confirms a live portal session,
  // otherwise bounces to /login. A reachable-but-unsigned state (401) and an
  // unreachable backend both land on /login, where the form reports errors.
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    let alive = true;
    fetch("/api/auth/me", { credentials: "include" })
      .then((res) => {
        if (!alive) return;
        if (res.ok) setAuthed(true);
        else window.location.replace("/login");
      })
      .catch(() => {
        if (alive) window.location.replace("/login");
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!authed) {
    return (
      <div className="card">
        <div className="status">Checking your portal session…</div>
      </div>
    );
  }

  return <>{children}</>;
}
