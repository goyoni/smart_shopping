"use client";

import { formatPrice } from "../lib/currency";
import { type ProductResultData } from "./ProductCard";

interface Seller {
  name: string;
  price: number | null;
  currency: string;
  url: string | null;
  phone: string | null;
  email: string | null;
  rating: number | null;
}

interface ModelSectionProps {
  modelId: string;
  products: ProductResultData[];
  onAddToList?: (product: ProductResultData) => void;
}

function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export default function ModelSection({ modelId, products, onAddToList }: ModelSectionProps) {
  // Use the first product (best match) for the model header info
  const primary = products[0];
  if (!primary) return null;

  // Collect all unique sellers across all products for this model
  const seenSellers = new Set<string>();
  const allSellers: Seller[] = [];
  for (const p of products) {
    for (const s of p.sellers) {
      const key = `${s.url || s.name}|${s.price}`;
      if (!seenSellers.has(key)) {
        seenSellers.add(key);
        allSellers.push(s);
      }
    }
  }
  // Sort by price (null-priced last)
  allSellers.sort((a, b) => {
    if (a.price == null && b.price == null) return 0;
    if (a.price == null) return 1;
    if (b.price == null) return -1;
    return a.price - b.price;
  });

  // Collect criteria from all products (merge)
  const mergedCriteria: Record<string, string | number | boolean> = {};
  for (const p of products) {
    for (const [k, v] of Object.entries(p.criteria)) {
      if (!(k in mergedCriteria) && v !== "" && v != null) {
        mergedCriteria[k] = v;
      }
    }
  }

  const criteriaDisplayNames: Record<string, string> = {
    noise_level: "Noise",
    energy_rating: "Energy",
    capacity: "Capacity",
    screen_size: "Screen",
    resolution: "Resolution",
    processor: "CPU",
    ram: "RAM",
    storage: "Storage",
    battery_life: "Battery",
    noise_cancelling: "ANC",
    spin_speed: "Spin",
    cooling_capacity: "Cooling",
    weight: "Weight",
    power: "Power",
    panel_type: "Panel",
    refresh_rate: "Refresh",
    frost_free: "Frost Free",
    inverter: "Inverter",
    filtration: "Filter",
  };

  const criteriaEntries = Object.entries(mergedCriteria).filter(
    ([, v]) => v !== "" && v !== null && v !== undefined
  );

  return (
    <div
      style={{
        marginBottom: "2rem",
        border: "1px solid #e5e7eb",
        borderRadius: 8,
        overflow: "hidden",
        background: "#fff",
      }}
    >
      {/* Model Header */}
      <div
        style={{
          padding: "0.75rem 1rem",
          background: "#f3f4f6",
          borderBottom: "1px solid #e5e7eb",
          borderInlineStart: "4px solid #2563eb",
        }}
      >
        <h3 style={{ margin: 0, fontSize: "1.1rem", fontWeight: 600 }}>
          {modelId}
          <span style={{ color: "#6b7280", fontWeight: 400, marginInlineStart: "0.75rem", fontSize: "0.85rem" }}>
            {allSellers.length} seller{allSellers.length !== 1 ? "s" : ""}
          </span>
        </h3>
      </div>

      {/* Product info row: image + details */}
      <div
        style={{
          display: "flex",
          gap: "1rem",
          padding: "1rem",
          borderBottom: "1px solid #f0f0f0",
          flexWrap: "wrap",
        }}
      >
        {/* Image */}
        <div
          style={{
            width: 120,
            height: 120,
            flexShrink: 0,
            background: "#f9fafb",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            borderRadius: 6,
            overflow: "hidden",
          }}
        >
          {primary.image_url ? (
            <img
              src={primary.image_url}
              alt={primary.name}
              style={{ maxHeight: "100%", maxWidth: "100%", objectFit: "contain" }}
            />
          ) : (
            <svg width="48" height="48" viewBox="0 0 64 64" fill="none" aria-label="No image">
              <rect x="8" y="12" width="48" height="40" rx="4" stroke="#d1d5db" strokeWidth="2" fill="none" />
              <circle cx="22" cy="28" r="5" stroke="#d1d5db" strokeWidth="2" fill="none" />
              <path d="M8 44 l16-12 8 6 12-10 12 16" stroke="#d1d5db" strokeWidth="2" fill="none" strokeLinejoin="round" />
            </svg>
          )}
        </div>

        {/* Details */}
        <div style={{ flex: 1, minWidth: 200 }}>
          {primary.brand && (
            <div style={{ fontSize: "0.75rem", color: "#6b7280", marginBottom: "0.15rem" }}>
              {primary.brand}
            </div>
          )}
          <div style={{ fontSize: "0.95rem", fontWeight: 600, marginBottom: "0.5rem" }}>
            {primary.name}
          </div>

          {/* Criteria badges */}
          {criteriaEntries.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem", marginBottom: "0.5rem" }}>
              {criteriaEntries.map(([key, value]) => (
                <span
                  key={key}
                  style={{
                    background: "#f3f4f6",
                    borderRadius: 4,
                    padding: "0.15rem 0.4rem",
                    fontSize: "0.7rem",
                    color: "#374151",
                  }}
                >
                  {criteriaDisplayNames[key] || key}: {String(value)}
                </span>
              ))}
            </div>
          )}

          {/* Add to list button */}
          {onAddToList && (
            <button
              onClick={() => onAddToList(primary)}
              style={{
                padding: "0.3rem 0.75rem",
                border: "1px solid #d1d5db",
                borderRadius: 4,
                background: "#f9fafb",
                cursor: "pointer",
                fontSize: "0.8rem",
                color: "#374151",
              }}
            >
              + Add to list
            </button>
          )}
        </div>
      </div>

      {/* Sellers table */}
      {allSellers.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "0.85rem",
            }}
          >
            <thead>
              <tr style={{ background: "#fafafa", textAlign: "start" }}>
                <th style={{ padding: "0.5rem 1rem", fontWeight: 600, color: "#374151" }}>Seller</th>
                <th style={{ padding: "0.5rem 1rem", fontWeight: 600, color: "#374151" }}>Price</th>
                <th style={{ padding: "0.5rem 1rem", fontWeight: 600, color: "#374151" }}>Contact</th>
              </tr>
            </thead>
            <tbody>
              {allSellers.map((seller, i) => (
                <tr
                  key={i}
                  style={{
                    borderTop: "1px solid #f0f0f0",
                  }}
                >
                  <td style={{ padding: "0.5rem 1rem" }}>
                    {seller.url ? (
                      <a
                        href={seller.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ color: "#2563eb", textDecoration: "none" }}
                      >
                        {seller.name && seller.name !== extractDomain(seller.url)
                          ? seller.name
                          : extractDomain(seller.url)}
                      </a>
                    ) : (
                      <span>{seller.name}</span>
                    )}
                    {seller.rating != null && (
                      <span style={{ color: "#f59e0b", fontSize: "0.75rem", marginInlineStart: "0.5rem" }}>
                        {seller.rating.toFixed(1)}
                      </span>
                    )}
                  </td>
                  <td
                    style={{
                      padding: "0.5rem 1rem",
                      fontWeight: 600,
                      color: seller.price != null ? "#16a34a" : "#999",
                    }}
                  >
                    {formatPrice(seller.price, seller.currency)}
                  </td>
                  <td style={{ padding: "0.5rem 1rem" }}>
                    <div style={{ display: "flex", gap: "0.3rem" }}>
                      {seller.phone && (
                        <a
                          href={`https://wa.me/${seller.phone.replace(/[^+\d]/g, "")}?text=${encodeURIComponent(
                            `Hi, I'm interested in ${modelId}. Is it available?`
                          )}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          title="Contact via WhatsApp"
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            justifyContent: "center",
                            padding: "0.2rem 0.5rem",
                            borderRadius: 4,
                            background: "#25d366",
                            color: "#fff",
                            fontSize: "0.7rem",
                            textDecoration: "none",
                            fontWeight: 600,
                          }}
                        >
                          WA
                        </a>
                      )}
                      {seller.email && (
                        <a
                          href={`mailto:${seller.email}?subject=${encodeURIComponent(
                            `Inquiry about ${modelId}`
                          )}&body=${encodeURIComponent(
                            `Hi, I'm interested in ${modelId}. Could you provide pricing and availability?`
                          )}`}
                          title="Contact via Email"
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            justifyContent: "center",
                            padding: "0.2rem 0.5rem",
                            borderRadius: 4,
                            background: "#2563eb",
                            color: "#fff",
                            fontSize: "0.7rem",
                            textDecoration: "none",
                            fontWeight: 600,
                          }}
                        >
                          @
                        </a>
                      )}
                      {seller.url && !seller.phone && !seller.email && (
                        <a
                          href={seller.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title="Visit seller"
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            justifyContent: "center",
                            padding: "0.2rem 0.5rem",
                            borderRadius: 4,
                            background: "#e5e7eb",
                            color: "#374151",
                            fontSize: "0.7rem",
                            textDecoration: "none",
                            fontWeight: 600,
                          }}
                        >
                          Visit
                        </a>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
