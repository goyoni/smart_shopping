"use client";

interface CrossSellerData {
  name: string;
  domain: string;
  url: string | null;
  phone: string | null;
  email: string | null;
  products: string[];
  prices: Record<string, number>;
  total_price: number | null;
  currency: string;
}

interface CrossSellerSectionProps {
  crossSellers: CrossSellerData[];
}

const currencySymbols: Record<string, string> = {
  USD: "$",
  ILS: "\u20AA",
  EUR: "\u20AC",
  GBP: "\u00A3",
};

function formatPrice(price: number, currency: string): string {
  const symbol = currencySymbols[currency] || currency + " ";
  return `${symbol}${price.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

export type { CrossSellerData };

export default function CrossSellerSection({ crossSellers }: CrossSellerSectionProps) {
  if (!crossSellers.length) return null;

  return (
    <div
      style={{
        marginBottom: "1.5rem",
        border: "2px solid #2563eb",
        borderRadius: 8,
        padding: "1rem",
        background: "#eff6ff",
      }}
    >
      <h3 style={{ margin: "0 0 0.75rem", fontSize: "1.1rem", color: "#1d4ed8" }}>
        Sellers with multiple items
      </h3>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
        {crossSellers.map((cs, i) => (
          <div
            key={i}
            style={{
              background: "#fff",
              borderRadius: 6,
              padding: "0.75rem",
              border: "1px solid #dbeafe",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
                gap: "0.5rem",
                flexWrap: "wrap",
              }}
            >
              <div style={{ flex: 1, minWidth: 200 }}>
                <div style={{ fontWeight: 600, fontSize: "0.95rem" }}>
                  {cs.url ? (
                    <a
                      href={cs.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: "#2563eb", textDecoration: "none" }}
                    >
                      {cs.name}
                    </a>
                  ) : (
                    cs.name
                  )}
                </div>
                <div
                  style={{
                    fontSize: "0.8rem",
                    color: "#6b7280",
                    marginTop: "0.25rem",
                  }}
                >
                  Carries {cs.products.length} of your items:
                </div>
                <ul
                  style={{
                    margin: "0.25rem 0 0",
                    paddingInlineStart: "1.2rem",
                    fontSize: "0.85rem",
                  }}
                >
                  {cs.products.map((product) => (
                    <li key={product}>
                      {product}
                      {cs.prices[product] != null && (
                        <span style={{ color: "#16a34a", marginInlineStart: "0.5rem" }}>
                          {formatPrice(cs.prices[product], cs.currency)}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "flex-end",
                  gap: "0.5rem",
                }}
              >
                {cs.total_price != null && (
                  <div
                    style={{
                      fontSize: "1.15rem",
                      fontWeight: 700,
                      color: "#16a34a",
                    }}
                  >
                    Total: {formatPrice(cs.total_price, cs.currency)}
                  </div>
                )}
                <div style={{ display: "flex", gap: "0.4rem" }}>
                  {cs.phone && (
                    <a
                      href={`https://wa.me/${cs.phone.replace(/[^+\d]/g, "")}?text=${encodeURIComponent(
                        `Hi, I'm interested in purchasing multiple items: ${cs.products.join(", ")}. Are they available?`
                      )}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      title="Contact via WhatsApp"
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        justifyContent: "center",
                        padding: "0.35rem 0.75rem",
                        borderRadius: 4,
                        background: "#25d366",
                        color: "#fff",
                        fontSize: "0.8rem",
                        textDecoration: "none",
                        fontWeight: 600,
                      }}
                    >
                      WhatsApp
                    </a>
                  )}
                  {cs.email && (
                    <a
                      href={`mailto:${cs.email}?subject=${encodeURIComponent(
                        "Inquiry about multiple products"
                      )}&body=${encodeURIComponent(
                        `Hi, I'm interested in purchasing: ${cs.products.join(", ")}. Could you provide pricing and availability?`
                      )}`}
                      title="Contact via Email"
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        justifyContent: "center",
                        padding: "0.35rem 0.75rem",
                        borderRadius: 4,
                        background: "#2563eb",
                        color: "#fff",
                        fontSize: "0.8rem",
                        textDecoration: "none",
                        fontWeight: 600,
                      }}
                    >
                      Email
                    </a>
                  )}
                  {cs.url && !cs.phone && !cs.email && (
                    <a
                      href={cs.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title="Visit seller"
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        justifyContent: "center",
                        padding: "0.35rem 0.75rem",
                        borderRadius: 4,
                        background: "#e5e7eb",
                        color: "#374151",
                        fontSize: "0.8rem",
                        textDecoration: "none",
                        fontWeight: 600,
                      }}
                    >
                      Visit
                    </a>
                  )}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
