"use client";

import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { DriveExport, Integration, Page } from "@/lib/types";

// One /integrations request shared by every button on the page (a
// montage history can render a dozen of them at once).
let driveLookup: { token: string; at: number; promise: Promise<boolean> } | null = null;

function driveConnected(token: string): Promise<boolean> {
  if (!driveLookup || driveLookup.token !== token || Date.now() - driveLookup.at > 30_000) {
    driveLookup = {
      token,
      at: Date.now(),
      promise: api
        .get<Integration[]>("/integrations", token)
        .then((items) => items.some((item) => item.provider === "google_drive"))
        .catch(() => false),
    };
  }
  return driveLookup.promise;
}

function inFlight(item: DriveExport | null): boolean {
  return item?.status === "queued" || item?.status === "processing";
}

/** "Сохранить в Google Drive" for one finished file -- or, when it's
 * already there (usually auto-saved right after generation), a link to
 * it. Renders nothing until Google Drive is connected. */
export function SaveToDriveButton({ url, title }: { url: string; title?: string | null }) {
  const { token } = useAuth();
  const [connected, setConnected] = useState(false);
  const [item, setItem] = useState<DriveExport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    driveConnected(token).then((yes) => {
      if (cancelled || !yes) return;
      setConnected(true);
      api
        .get<Page<DriveExport>>(
          `/integrations/google-drive/exports?${new URLSearchParams({ url, limit: "1" })}`,
          token
        )
        .then((page) => !cancelled && setItem(page.items[0] ?? null))
        .catch(() => undefined);
    });
    return () => {
      cancelled = true;
    };
  }, [token, url]);

  const pendingId = inFlight(item) ? item?.id : null;
  useEffect(() => {
    if (!pendingId) return;
    const timer = setInterval(() => {
      api
        .get<DriveExport>(`/integrations/google-drive/exports/${pendingId}`, token)
        .then(setItem)
        .catch(() => undefined);
    }, 3000);
    return () => clearInterval(timer);
  }, [pendingId, token]);

  if (!connected) return null;

  async function handleSave() {
    setError(null);
    setSaving(true);
    try {
      setItem(
        await api.post<DriveExport>(
          "/integrations/google-drive/exports",
          { url, title: title ? title.slice(0, 200) : null },
          token
        )
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить в Google Drive");
    } finally {
      setSaving(false);
    }
  }

  if (item?.status === "completed" && item.web_view_link) {
    return (
      <a className="drive-link" href={item.web_view_link} target="_blank" rel="noreferrer">
        ✓ В Google Drive — открыть
      </a>
    );
  }
  if (inFlight(item)) {
    return <span className="muted">Сохраняем в Google Drive…</span>;
  }
  return (
    <span className="drive-save">
      <button type="button" className="secondary" onClick={handleSave} disabled={saving}>
        {saving ? "Сохраняем…" : item?.status === "failed" ? "Сохранить в Drive ещё раз" : "Сохранить в Google Drive"}
      </button>
      {item?.status === "failed" && item.error_message && (
        <span className="error">{item.error_message}</span>
      )}
      {error && <span className="error">{error}</span>}
    </span>
  );
}
