"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { StatusWebSocket } from "../lib/websocket";

interface ProductResult {
  name: string;
  model?: string;
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [statusMessages, setStatusMessages] = useState<string[]>([]);
  const [results, setResults] = useState<ProductResult[]>([]);
  const [loading, setLoading] = useState(false);

  const sessionIdRef = useRef(crypto.randomUUID().replace(/-/g, ""));
  const wsRef = useRef<StatusWebSocket | null>(null);

  useEffect(() => {
    return () => {
      wsRef.current?.disconnect();
    };
  }, []);

  const handleSearch = useCallback(async () => {
    if (!query.trim() || loading) return;

    setLoading(true);
    setStatusMessages([]);
    setResults([]);

    const sessionId = sessionIdRef.current;

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
          body: JSON.stringify({ query, session_id: sessionId }),
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
  }, [query, loading]);

  return (
    <main style={{ maxWidth: 800, margin: "0 auto", padding: "2rem" }}>
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
          <h2>Results</h2>
          <ul style={{ listStyle: "none", padding: 0 }}>
            {results.map((r, i) => (
              <li
                key={i}
                style={{
                  padding: "0.75rem",
                  border: "1px solid #ddd",
                  borderRadius: 4,
                  marginBottom: "0.5rem",
                }}
              >
                <strong>{r.name}</strong>
                {r.model && (
                  <span style={{ color: "#888", marginInlineStart: "0.5rem" }}>
                    ({r.model})
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </main>
  );
}
