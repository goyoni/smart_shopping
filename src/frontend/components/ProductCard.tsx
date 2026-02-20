"use client";

import { useState } from "react";
import SellerList from "./SellerList";

interface Seller {
  name: string;
  price: number | null;
  currency: string;
  url: string | null;
  phone: string | null;
  email: string | null;
  rating: number | null;
}

export interface ProductResultData {
  name: string;
  model_id: string | null;
  brand: string | null;
  product_type: string | null;
  category: string | null;
  criteria: Record<string, string | number | boolean>;
  sellers: Seller[];
  image_url: string | null;
}

interface ProductCardProps {
  product: ProductResultData;
}

const currencySymbols: Record<string, string> = {
  USD: "$",
  ILS: "\u20AA",
  EUR: "\u20AC",
  GBP: "\u00A3",
};

function getBestPrice(sellers: Seller[]): { price: number; currency: string } | null {
  const priced = sellers.filter((s) => s.price != null);
  if (!priced.length) return null;
  priced.sort((a, b) => (a.price ?? 0) - (b.price ?? 0));
  return { price: priced[0].price!, currency: priced[0].currency };
}

function formatPrice(price: number, currency: string): string {
  const symbol = currencySymbols[currency] || currency + " ";
  return `${symbol}${price.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function getSourceDomains(sellers: Seller[]): string[] {
  const domains = new Set<string>();
  for (const s of sellers) {
    const d = s.url ? extractDomain(s.url) : s.name;
    if (d) domains.add(d);
  }
  return Array.from(domains);
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

function getProductUrl(sellers: Seller[]): string | null {
  for (const s of sellers) {
    if (s.url) return s.url;
  }
  return null;
}

export default function ProductCard({ product }: ProductCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [criteriaExpanded, setCriteriaExpanded] = useState(false);

  const bestPrice = getBestPrice(product.sellers);
  const domains = getSourceDomains(product.sellers);
  const productUrl = getProductUrl(product.sellers);
  const criteriaEntries = Object.entries(product.criteria).filter(
    ([, v]) => v !== "" && v !== null && v !== undefined
  );
  const visibleCriteria = criteriaExpanded ? criteriaEntries : criteriaEntries.slice(0, 4);
  const hasMoreCriteria = criteriaEntries.length > 4;

  return (
    <div
      style={{
        border: "1px solid #e5e7eb",
        borderRadius: 8,
        overflow: "hidden",
        background: "#fff",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Image */}
      <a
        href={productUrl || undefined}
        target="_blank"
        rel="noopener noreferrer"
        style={{
          height: 180,
          background: "#f9fafb",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          overflow: "hidden",
          cursor: productUrl ? "pointer" : "default",
          textDecoration: "none",
        }}
      >
        {product.image_url ? (
          <img
            src={product.image_url}
            alt={product.name}
            style={{ maxHeight: "100%", maxWidth: "100%", objectFit: "contain" }}
          />
        ) : (
          <svg width="64" height="64" viewBox="0 0 64 64" fill="none" aria-label="No image available">
            <rect x="8" y="12" width="48" height="40" rx="4" stroke="#d1d5db" strokeWidth="2" fill="none" />
            <circle cx="22" cy="28" r="5" stroke="#d1d5db" strokeWidth="2" fill="none" />
            <path d="M8 44 l16-12 8 6 12-10 12 16" stroke="#d1d5db" strokeWidth="2" fill="none" strokeLinejoin="round" />
          </svg>
        )}
      </a>

      {/* Body */}
      <div style={{ padding: "0.75rem", flex: 1 }}>
        {/* Brand */}
        {product.brand && (
          <div style={{ fontSize: "0.75rem", color: "#6b7280", marginBottom: "0.25rem" }}>
            {product.brand}
          </div>
        )}

        {/* Name */}
        <h3
          style={{
            fontSize: "0.95rem",
            fontWeight: 600,
            lineHeight: 1.3,
            marginBottom: "0.5rem",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {productUrl ? (
            <a
              href={productUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "inherit", textDecoration: "none" }}
              onMouseEnter={(e) => (e.currentTarget.style.color = "#2563eb")}
              onMouseLeave={(e) => (e.currentTarget.style.color = "inherit")}
            >
              {product.name}
            </a>
          ) : (
            product.name
          )}
        </h3>

        {/* Price */}
        {bestPrice ? (
          <div style={{ fontSize: "1.25rem", fontWeight: 700, color: "#16a34a", marginBottom: "0.5rem" }}>
            {formatPrice(bestPrice.price, bestPrice.currency)}
          </div>
        ) : (
          <div style={{ fontSize: "1rem", color: "#999", marginBottom: "0.5rem" }}>Price unavailable</div>
        )}

        {/* Sellers indicator */}
        {product.sellers.length > 0 && (
          <button
            onClick={() => setExpanded(!expanded)}
            style={{
              background: "none",
              border: "1px solid #d1d5db",
              borderRadius: 4,
              padding: "0.25rem 0.5rem",
              fontSize: "0.8rem",
              cursor: "pointer",
              color: "#2563eb",
              marginBottom: "0.5rem",
              width: "100%",
              textAlign: "start",
            }}
          >
            {expanded
              ? "Hide sellers"
              : `Available from ${product.sellers.length} seller${product.sellers.length > 1 ? "s" : ""}`}
          </button>
        )}

        {/* Expanded seller list */}
        {expanded && (
          <div style={{ marginBottom: "0.5rem" }}>
            <SellerList sellers={product.sellers} />
          </div>
        )}

        {/* Criteria badges */}
        {criteriaEntries.length > 0 && (
          <div style={{ marginBottom: "0.5rem" }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem" }}>
              {visibleCriteria.map(([key, value]) => (
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
            {hasMoreCriteria && (
              <button
                onClick={() => setCriteriaExpanded(!criteriaExpanded)}
                style={{
                  background: "none",
                  border: "none",
                  padding: "0.15rem 0",
                  fontSize: "0.7rem",
                  color: "#6b7280",
                  cursor: "pointer",
                  marginTop: "0.25rem",
                }}
              >
                {criteriaExpanded
                  ? "Show less"
                  : `+${criteriaEntries.length - 4} more`}
              </button>
            )}
          </div>
        )}

        {/* Source domains */}
        {domains.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem" }}>
            {domains.map((d) => (
              <span
                key={d}
                style={{
                  background: "#eff6ff",
                  borderRadius: 4,
                  padding: "0.1rem 0.35rem",
                  fontSize: "0.65rem",
                  color: "#1d4ed8",
                }}
              >
                {d}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
