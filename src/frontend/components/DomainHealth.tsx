"use client";

import { useCallback, useEffect, useState } from "react";
import { useLocale } from "../lib/LocaleContext";
import { t } from "../lib/i18n";

interface DomainRate {
  domain: string;
  success_rate: number;
  updated_at: string | null;
}

function getStatus(rate: number): "healthy" | "degraded" | "blocked" {
  if (rate >= 0.5) return "healthy";
  if (rate >= 0.2) return "degraded";
  return "blocked";
}

const statusColors: Record<string, string> = {
  healthy: "#16a34a",
  degraded: "#d97706",
  blocked: "#dc2626",
};

const statusBg: Record<string, string> = {
  healthy: "#f0fdf4",
  degraded: "#fffbeb",
  blocked: "#fef2f2",
};

export default function DomainHealth() {
  const [rates, setRates] = useState<DomainRate[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const { locale } = useLocale();

  const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const fetchRates = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/scraping/success-rates`);
      if (res.ok) {
        const data = await res.json();
        setRates(data.rates || []);
      }
    } catch {
      // Non-critical
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    fetchRates();
  }, [fetchRates]);

  if (loading || rates.length === 0) return null;

  return (
    <div style={{ marginTop: "2rem" }}>
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          background: "none",
          border: "1px solid #d1d5db",
          borderRadius: 6,
          padding: "0.4rem 0.75rem",
          fontSize: "0.85rem",
          cursor: "pointer",
          color: "#6b7280",
          display: "flex",
          alignItems: "center",
          gap: "0.4rem",
        }}
      >
        <span style={{ fontSize: "0.7rem" }}>{expanded ? "▼" : "▶"}</span>
        {t(locale, "domain_health.title")} ({rates.length})
      </button>

      {expanded && (
        <div
          style={{
            marginTop: "0.5rem",
            border: "1px solid #e5e7eb",
            borderRadius: 8,
            overflow: "hidden",
          }}
        >
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "0.85rem",
            }}
          >
            <thead>
              <tr style={{ background: "#f9fafb", textAlign: "start" }}>
                <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600, color: "#374151" }}>
                  {t(locale, "domain_health.domain")}
                </th>
                <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600, color: "#374151" }}>
                  {t(locale, "domain_health.success_rate")}
                </th>
                <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600, color: "#374151" }}>
                  {t(locale, "domain_health.status")}
                </th>
              </tr>
            </thead>
            <tbody>
              {rates.map((r) => {
                const status = getStatus(r.success_rate);
                return (
                  <tr key={r.domain} style={{ borderTop: "1px solid #f0f0f0" }}>
                    <td style={{ padding: "0.4rem 0.75rem", fontFamily: "monospace", fontSize: "0.8rem" }}>
                      {r.domain}
                    </td>
                    <td style={{ padding: "0.4rem 0.75rem" }}>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "0.5rem",
                        }}
                      >
                        <div
                          style={{
                            width: 60,
                            height: 6,
                            background: "#e5e7eb",
                            borderRadius: 3,
                            overflow: "hidden",
                          }}
                        >
                          <div
                            style={{
                              width: `${Math.round(r.success_rate * 100)}%`,
                              height: "100%",
                              background: statusColors[status],
                              borderRadius: 3,
                            }}
                          />
                        </div>
                        <span style={{ fontSize: "0.8rem", color: "#374151" }}>
                          {Math.round(r.success_rate * 100)}%
                        </span>
                      </div>
                    </td>
                    <td style={{ padding: "0.4rem 0.75rem" }}>
                      <span
                        style={{
                          display: "inline-block",
                          padding: "0.15rem 0.5rem",
                          borderRadius: 4,
                          fontSize: "0.75rem",
                          fontWeight: 600,
                          color: statusColors[status],
                          background: statusBg[status],
                        }}
                      >
                        {t(locale, `domain_health.${status}`)}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
