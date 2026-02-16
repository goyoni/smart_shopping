import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Smart Shopping Agent",
  description: "AI-powered shopping assistant with adaptive web discovery",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
