import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { Shell } from "@/components/Shell";

// Self-hosted Source Sans 3 (variable, OFL 1.1) — the same files the API serves for its demo page.
const sourceSans = localFont({
  src: [
    { path: "./fonts/SourceSans3VF-Upright.woff2", weight: "200 900", style: "normal" },
    { path: "./fonts/SourceSans3VF-Italic.woff2", weight: "200 900", style: "italic" },
  ],
  display: "swap",
  fallback: ["-apple-system", "SF Pro Text", "system-ui", "sans-serif"],
});

export const metadata: Metadata = {
  title: { default: "ShipDoc", template: "%s · ShipDoc" },
  description: "Shipping-document intake console: inbox, SI/BL diff reports, review queues, evaluation.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={sourceSans.className}>
      <body><Shell>{children}</Shell></body>
    </html>
  );
}
