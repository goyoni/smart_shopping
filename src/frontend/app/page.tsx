"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { StatusWebSocket } from "../lib/websocket";
import { useLocale } from "../lib/LocaleContext";
import { t } from "../lib/i18n";
import ProductCard, { type ProductResultData } from "../components/ProductCard";
import CrossSellerSection, { type CrossSellerData } from "../components/CrossSellerSection";
import ModelSection from "../components/ModelSection";

interface HistoryEntry {
  session_id: string;
  query: string;
  status: string;
  result_count: number;
  created_at: string;
}

function getInitialSessionId(): string {
  if (typeof window !== "undefined") {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("session_id");
    if (fromUrl) return fromUrl;
  }
  return crypto.randomUUID().replace(/-/g, "");
}

function statusColor(status: string): string {
  if (status === "completed") return "#22c55e";
  if (status === "in_progress" || status === "pending") return "#eab308";
  return "#ef4444";
}

async function addToShoppingList(
  apiBase: string,
  product: ProductResultData,
): Promise<boolean> {
  try {
    const res = await fetch(`${apiBase}/api/shopping-list`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [statusMessages, setStatusMessages] = useState<string[]>([]);
  const [results, setResults] = useState<ProductResultData[]>([]);
  const [crossSellers, setCrossSellers] = useState<CrossSellerData[]>([]);
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  const { locale } = useLocale();

  const sessionIdRef = useRef(getInitialSessionId());
  const wsRef = useRef<StatusWebSocket | null>(null);
  const didRestoreRef = useRef(false);

  const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const relativeTime = useCallback(
    (iso: string): string => {
      const diff = Date.now() - new Date(iso).getTime();
      const mins = Math.floor(diff / 60000);
      if (mins < 1) return t(locale, "status.just_now");
      if (mins < 60) return t(locale, "status.minutes_ago", { n: mins });
      const hrs = Math.floor(mins / 60);
      if (hrs < 24) return t(locale, "status.hours_ago", { n: hrs });
      const days = Math.floor(hrs / 24);
      return t(locale, "status.days_ago", { n: days });
    },
    [locale]
  );

  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/history`);
      if (res.ok) {
        const data = await res.json();
        setHistory(data.items || []);
      }
    } catch {
      // History fetch is non-critical
    }
  }, [apiBase]);

  const runSearch = useCallback(async (searchQuery: string) => {
    if (!searchQuery.trim()) return;

    setLoading(true);
    setStatusMessages([]);
    setResults([]);
    setCrossSellers([]);

    const sessionId = sessionIdRef.current;

    // Update URL with search params
    const params = new URLSearchParams({ q: searchQuery, session_id: sessionId });
    window.history.pushState({}, "", `?${params.toString()}`);

    // Connect WebSocket before sending search request
    wsRef.current?.disconnect();
    const ws = new StatusWebSocket((message) => {
      setStatusMessages((prev) => [...prev, message]);
    });
    wsRef.current = ws;
    ws.connect(sessionId);

    try {
      const res = await fetch(
        `${apiBase}/api/search`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: searchQuery,
            session_id: sessionId,
            language: locale,
          }),
        }
      );
      const data = await res.json();
      setResults(data.results || []);
      setCrossSellers(data.cross_sellers || []);
      setStatusMessages((prev) => [...prev, `Search ${data.status}`]);
    } catch {
      setStatusMessages((prev) => [...prev, "Failed to connect to backend"]);
    } finally {
      setLoading(false);
      fetchHistory();
    }
  }, [apiBase, fetchHistory, locale]);

  const handleSearch = useCallback(async () => {
    if (!query.trim() || loading) return;
    await runSearch(query);
  }, [query, loading, runSearch]);

  const handleHistoryClick = useCallback(async (entry: HistoryEntry) => {
    sessionIdRef.current = entry.session_id;
    setQuery(entry.query);

    // Update URL with search params
    const params = new URLSearchParams({ q: entry.query, session_id: entry.session_id });
    window.history.pushState({}, "", `?${params.toString()}`);

    if (entry.status === "completed" && entry.result_count > 0) {
      try {
        const res = await fetch(`${apiBase}/api/search/${entry.session_id}`);
        if (res.ok) {
          const data = await res.json();
          setResults(data.results || []);
          setCrossSellers(data.cross_sellers || []);
          setStatusMessages([`Loaded ${(data.results || []).length} saved results`]);
          return;
        }
      } catch {
        // Fall through to re-run search
      }
    }

    runSearch(entry.query);
  }, [apiBase, runSearch]);

  // Restore search from URL params on mount + fetch history
  useEffect(() => {
    if (didRestoreRef.current) return;
    didRestoreRef.current = true;

    fetchHistory();

    const params = new URLSearchParams(window.location.search);
    const urlQuery = params.get("q");
    if (urlQuery) {
      setQuery(urlQuery);
      runSearch(urlQuery);
    }
  }, [runSearch, fetchHistory]);

  useEffect(() => {
    return () => {
      wsRef.current?.disconnect();
    };
  }, []);

  // Count distinct source domains
  const sourceDomains = new Set<string>();
  for (const r of results) {
    for (const s of r.sellers || []) {
      if (s.url) {
        try {
          const h = new URL(s.url).hostname.replace(/^www\./, "");
          if (h) sourceDomains.add(h);
        } catch { /* ignore */ }
      } else if (s.name) {
        sourceDomains.add(s.name);
      }
    }
  }

  return (
    <main style={{ maxWidth: 1100, margin: "0 auto", padding: "2rem 1rem" }}>
      <h1>{t(locale, "app.title")}</h1>
      <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem" }}>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          placeholder={t(locale, "search.placeholder")}
          style={{ flex: 1, padding: "0.5rem", fontSize: "1rem" }}
          disabled={loading}
        />
        <button
          onClick={handleSearch}
          style={{ padding: "0.5rem 1rem" }}
          disabled={loading}
        >
          {loading ? t(locale, "search.searching") : t(locale, "search.button")}
        </button>
      </div>

      {history.length > 0 && (
        <div style={{ marginTop: "1rem" }}>
          <h3 style={{ fontSize: "0.85rem", color: "#888", marginBottom: "0.5rem" }}>
            {t(locale, "search.recent")}
          </h3>
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {history.map((entry, i) => (
              <li
                key={`${entry.session_id}-${i}`}
                onClick={() => handleHistoryClick(entry)}
                style={{
                  padding: "0.4rem 0.6rem",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "0.5rem",
                  fontSize: "0.9rem",
                  borderBottom: "1px solid #eee",
                }}
              >
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    backgroundColor: statusColor(entry.status),
                    flexShrink: 0,
                  }}
                />
                <span style={{ flex: 1 }}>{entry.query}</span>
                <span style={{ color: "#999", fontSize: "0.8rem" }}>
                  {entry.result_count} result{entry.result_count !== 1 ? "s" : ""}
                </span>
                <span style={{ color: "#bbb", fontSize: "0.75rem" }}>
                  {relativeTime(entry.created_at)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {statusMessages.length > 0 && (
        <ul style={{ marginTop: "1rem", listStyle: "none", padding: 0 }}>
          {statusMessages.map((msg, i) => (
            <li key={i} style={{ color: "#666", fontSize: "0.9rem" }}>
              {msg}
            </li>
          ))}
        </ul>
      )}

      {results.length > 0 && (() => {
        // Check if results are grouped by model (multi-model search)
        const hasProductTypes = results.some((r) => r.product_type);
        const modelGroups: Map<string, ProductResultData[]> = new Map();
        if (hasProductTypes) {
          for (const r of results) {
            const key = r.product_type || "Other";
            if (!modelGroups.has(key)) modelGroups.set(key, []);
            modelGroups.get(key)!.push(r);
          }
        }
        const isMultiModel = modelGroups.size >= 2;

        return (
          <div style={{ marginTop: "1.5rem" }}>
            <h2 style={{ marginBottom: "0.75rem" }}>
              {t(locale, "search.results_count", {
                count: results.length,
                sites: sourceDomains.size,
              })}
            </h2>

            {/* Cross-seller section for multi-model searches */}
            {crossSellers.length > 0 && (
              <CrossSellerSection crossSellers={crossSellers} />
            )}

            {isMultiModel ? (
              /* Grouped by model — header + seller table per model */
              Array.from(modelGroups.entries()).map(([modelId, products]) => (
                <ModelSection
                  key={modelId}
                  modelId={modelId}
                  products={products}
                  onAddToList={(p) => addToShoppingList(apiBase, p)}
                />
              ))
            ) : (
              /* Flat grid for single-product searches */
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
                  gap: "1rem",
                }}
              >
                {results.map((r, i) => (
                  <ProductCard
                    key={i}
                    product={r}
                    onAddToList={(p) => addToShoppingList(apiBase, p)}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })()}
    </main>
  );
}
