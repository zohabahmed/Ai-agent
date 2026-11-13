import type { Metadata } from "next";
import "./globals.css";
import { TradingProvider } from "@/context/TradingContext";
import { WebsiteShell } from "@/components/WebsiteShell";

export const metadata: Metadata = {
  title: "SurvivalX — Autonomous Survival-First Crypto Futures Platform",
  description:
    "Multi-coin autonomous USD-M Futures exchange terminal, active trade manager, quantitative analytics, and survival-first risk governor.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-[#080c12] text-slate-100 antialiased">
        <TradingProvider>
          <WebsiteShell>{children}</WebsiteShell>
        </TradingProvider>
      </body>
    </html>
  );
}
