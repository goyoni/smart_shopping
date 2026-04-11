"use client";

import { useCallback, useEffect, useState } from "react";
import { formatPrice } from "../../lib/currency";
import { useLocale } from "../../lib/LocaleContext";
import { t } from "../../lib/i18n";

interface Seller {
  name: string;
  price: number | null;
  currency: string;
  url: string | null;
  phone: string | null;
  email: string | null;
  rating: number | null;
}

interface ProductResultData {
  name: string;
  model_id: string | null;
  brand: string | null;
  product_type: string | null;
  category: string | null;
  criteria: Record<string, string | number | boolean>;
  sellers: Seller[];
  image_url: string | null;
}

interface ShoppingListItemData {
  id: number;
  product: ProductResultData;
  quantity: number;
  notes: string | null;
}

function getBestPrice(sellers: Seller[]): { price: number; currency: string } | null {
  const priced = sellers.filter((s) => s.price != null);
  if (!priced.length) return null;
  priced.sort((a, b) => (a.price ?? 0) - (b.price ?? 0));
  return { price: priced[0].price!, currency: priced[0].currency };
}

export default function ShoppingListPage() {
  const [items, setItems] = useState<ShoppingListItemData[]>([]);
  const [loading, setLoading] = useState(true);
  const { locale } = useLocale();

  const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const fetchItems = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/shopping-list`);
      if (res.ok) {
        const data = await res.json();
        setItems(data.items || []);
      }
    } catch {
      // Non-critical
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  const handleRemove = useCallback(
    async (id: number) => {
      try {
        const res = await fetch(`${apiBase}/api/shopping-list/${id}`, {
          method: "DELETE",
        });
        if (res.ok) {
          setItems((prev) => prev.filter((item) => item.id !== id));
        }
      } catch {
        // Non-critical
      }
    },
    [apiBase]
  );

  return (
    <main style={{ maxWidth: 800, margin: "0 auto", padding: "2rem 1rem" }}>
      <h1>{t(locale, "nav.shopping_list")}</h1>

      {loading && <p style={{ color: "#666", marginTop: "1rem" }}>{t(locale, "list.loading")}</p>}

      {!loading && items.length === 0 && (
        <p style={{ color: "#999", marginTop: "1rem" }}>
          {t(locale, "list.empty")}
        </p>
      )}

      {items.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, marginTop: "1rem" }}>
          {items.map((item) => {
            const best = getBestPrice(item.product.sellers);
            return (
              <li
                key={item.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "1rem",
                  padding: "0.75rem 0",
                  borderBottom: "1px solid #e5e7eb",
                }}
              >
                {/* Thumbnail */}
                <div
                  style={{
                    width: 60,
                    height: 60,
                    background: "#f9fafb",
                    borderRadius: 4,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                    overflow: "hidden",
                  }}
                >
                  {item.product.image_url ? (
                    <img
                      src={item.product.image_url}
                      alt={item.product.name}
                      style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }}
                    />
                  ) : (
                    <span style={{ color: "#d1d5db", fontSize: "1.5rem" }}>?</span>
                  )}
                </div>

                {/* Info */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontWeight: 600,
                      fontSize: "0.9rem",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {item.product.name}
                  </div>
                  {item.product.brand && (
                    <div style={{ fontSize: "0.75rem", color: "#6b7280" }}>
                      {item.product.brand}
                    </div>
                  )}
                  {item.notes && (
                    <div style={{ fontSize: "0.75rem", color: "#9ca3af", fontStyle: "italic" }}>
                      {item.notes}
                    </div>
                  )}
                </div>

                {/* Price */}
                <div style={{ textAlign: "end", flexShrink: 0 }}>
                  {best ? (
                    <div style={{ fontWeight: 700, color: "#16a34a" }}>
                      {formatPrice(best.price, best.currency)}
                    </div>
                  ) : (
                    <div style={{ color: "#999", fontSize: "0.85rem" }}>N/A</div>
                  )}
                  {item.quantity > 1 && (
                    <div style={{ fontSize: "0.75rem", color: "#6b7280" }}>
                      x{item.quantity}
                    </div>
                  )}
                </div>

                {/* Remove button */}
                <button
                  onClick={() => handleRemove(item.id)}
                  style={{
                    background: "none",
                    border: "none",
                    color: "#ef4444",
                    cursor: "pointer",
                    fontSize: "1.2rem",
                    padding: "0.25rem",
                    flexShrink: 0,
                  }}
                  title={t(locale, "list.remove")}
                >
                  x
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
