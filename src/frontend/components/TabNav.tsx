"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useLocale } from "../lib/LocaleContext";
import { type Locale, t } from "../lib/i18n";

const LOCALE_LABELS: Record<Locale, string> = {
  en: "EN",
  he: "HE",
  ar: "AR",
};

export default function TabNav() {
  const pathname = usePathname();
  const { locale, setLocale } = useLocale();

  const tabs = [
    { href: "/", label: t(locale, "nav.search") },
    { href: "/shopping-list", label: t(locale, "nav.shopping_list") },
  ];

  return (
    <nav
      style={{
        display: "flex",
        alignItems: "center",
        borderBottom: "1px solid #e5e7eb",
        maxWidth: 1100,
        margin: "0 auto",
        padding: "0 1rem",
      }}
    >
      {tabs.map((tab) => {
        const active = tab.href === "/" ? pathname === "/" : pathname.startsWith(tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            style={{
              padding: "0.75rem 1.25rem",
              textDecoration: "none",
              color: active ? "#2563eb" : "#6b7280",
              borderBottom: active ? "2px solid #2563eb" : "2px solid transparent",
              fontWeight: active ? 600 : 400,
              fontSize: "0.9rem",
            }}
          >
            {tab.label}
          </Link>
        );
      })}

      {/* Language selector */}
      <div style={{ marginInlineStart: "auto" }}>
        <select
          value={locale}
          onChange={(e) => setLocale(e.target.value as Locale)}
          style={{
            padding: "0.3rem 0.5rem",
            border: "1px solid #d1d5db",
            borderRadius: 4,
            fontSize: "0.8rem",
            background: "#fff",
            cursor: "pointer",
          }}
          aria-label="Language"
        >
          {(Object.keys(LOCALE_LABELS) as Locale[]).map((loc) => (
            <option key={loc} value={loc}>
              {LOCALE_LABELS[loc]}
            </option>
          ))}
        </select>
      </div>
    </nav>
  );
}
