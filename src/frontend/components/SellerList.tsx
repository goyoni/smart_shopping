"use client";

interface Seller {
  name: string;
  price: number | null;
  currency: string;
  url: string | null;
  phone: string | null;
  email: string | null;
  rating: number | null;
}

interface SellerListProps {
  sellers: Seller[];
}

const currencySymbols: Record<string, string> = {
  USD: "$",
  ILS: "\u20AA",
  EUR: "\u20AC",
  GBP: "\u00A3",
};

function formatPrice(price: number | null, currency: string): string {
  if (price == null) return "N/A";
  const symbol = currencySymbols[currency] || currency + " ";
  return `${symbol}${price.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

function extractDomain(url: string): string {
  try {
    const hostname = new URL(url).hostname;
    return hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export default function SellerList({ sellers }: SellerListProps) {
  if (!sellers.length) return null;

  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {sellers.map((seller, i) => (
        <li
          key={i}
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "0.4rem 0",
            borderBottom: i < sellers.length - 1 ? "1px solid #f0f0f0" : "none",
            fontSize: "0.9rem",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", minWidth: 0 }}>
            {seller.url ? (
              <a
                href={seller.url}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "#2563eb", textDecoration: "none", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              >
                {seller.name && seller.name !== extractDomain(seller.url)
                  ? seller.name
                  : extractDomain(seller.url)}
              </a>
            ) : (
              <span>{seller.name}</span>
            )}
            {seller.rating != null && (
              <span style={{ color: "#f59e0b", fontSize: "0.8rem" }}>
                {seller.rating.toFixed(1)}
              </span>
            )}
          </div>
          <span
            style={{
              fontWeight: 600,
              color: seller.price != null ? "#16a34a" : "#999",
            }}
          >
            {formatPrice(seller.price, seller.currency)}
          </span>
        </li>
      ))}
    </ul>
  );
}
