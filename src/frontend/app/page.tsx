"use client";

import { useState } from "react";

export default function Home() {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");

  const handleSearch = async () => {
    setStatus("Searching...");
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/search`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query }),
        }
      );
      const data = await res.json();
      setStatus(`Search ${data.status}: ${data.status_message}`);
    } catch {
      setStatus("Failed to connect to backend");
    }
  };

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
        />
        <button onClick={handleSearch} style={{ padding: "0.5rem 1rem" }}>
          Search
        </button>
      </div>
      {status && <p style={{ marginTop: "1rem" }}>{status}</p>}
    </main>
  );
}
