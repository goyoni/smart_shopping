/**
 * Centralized currency formatting.
 *
 * Currency symbols are defined here once — all components import from this module.
 */

const currencySymbols: Record<string, string> = {
  USD: "$",
  ILS: "\u20AA",
  EUR: "\u20AC",
  GBP: "\u00A3",
};

export function getCurrencySymbol(code: string): string {
  return currencySymbols[code] || code + " ";
}

export function formatPrice(price: number | null, currency: string): string {
  if (price == null) return "N/A";
  const symbol = getCurrencySymbol(currency);
  return `${symbol}${price.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}
