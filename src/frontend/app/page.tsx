"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { StatusWebSocket } from "../lib/websocket";
import ProductCard, { type ProductResultData } from "../components/ProductCard";

function getInitialSessionId(): string {
  if (typeof window !== "undefined") {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("session_id");
    if (fromUrl) return fromUrl;
  }
  return crypto.randomUUID().replace(/-/g, "");
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [statusMessages, setStatusMessages] = useState<string[]>([]);
  const [results, setResults] = useState<ProductResultData[]>([]);
  const [loading, setLoading] = useState(false);

  const sessionIdRef = useRef(getInitialSessionId());
  const wsRef = useRef<StatusWebSocket | null>(null);
  const didRestoreRef = useRef(false);

  const runSearch = useCallback(async (searchQuery: string) => {
    if (!searchQuery.trim()) return;

    setLoading(true);
    setStatusMessages([]);
    setResults([]);

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
        `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/search`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: searchQuery, session_id: sessionId }),
        }
      );
      const data = await res.json();
      setResults(data.results || []);
      setStatusMessages((prev) => [...prev, `Search ${data.status}`]);
    } catch {
      setStatusMessages((prev) => [...prev, "Failed to connect to backend"]);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSearch = useCallback(async () => {
    if (!query.trim() || loading) return;
    await runSearch(query);
  }, [query, loading, runSearch]);

  // Restore search from URL params on mount
  useEffect(() => {
    if (didRestoreRef.current) return;
    didRestoreRef.current = true;

    const params = new URLSearchParams(window.location.search);
    const urlQuery = params.get("q");
    if (urlQuery) {
      setQuery(urlQuery);
      runSearch(urlQuery);
    }
  }, [runSearch]);

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
      <h1>Smart Shopping Agent</h1>
      <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem" }}>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          placeholder="What are you looking for?"
          style={{ flex: 1, padding: "0.5rem", fontSize: "1rem" }}
          disabled={loading}
        />
        <button
          onClick={handleSearch}
          style={{ padding: "0.5rem 1rem" }}
          disabled={loading}
        >
          {loading ? "Searching..." : "Search"}
        </button>
      </div>

      {statusMessages.length > 0 && (
        <ul style={{ marginTop: "1rem", listStyle: "none", padding: 0 }}>
          {statusMessages.map((msg, i) => (
            <li key={i} style={{ color: "#666", fontSize: "0.9rem" }}>
              {msg}
            </li>
          ))}
        </ul>
      )}

      {results.length > 0 && (
        <div style={{ marginTop: "1.5rem" }}>
          <h2 style={{ marginBottom: "0.75rem" }}>
            Found {results.length} product{results.length !== 1 ? "s" : ""} from{" "}
            {sourceDomains.size} site{sourceDomains.size !== 1 ? "s" : ""}
          </h2>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
              gap: "1rem",
            }}
          >
            {results.map((r, i) => (
              <ProductCard key={i} product={r} />
            ))}
          </div>
        </div>
      )}
    </main>
  );
}
