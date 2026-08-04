import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import "@/styles/globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "crxes.app — Predict bugs before they happen",
  description:
    "Connect your log sources and let four AI agents forecast the bugs about to hit production.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable} scroll-smooth`}>
      <body className="font-sans text-body">{children}</body>
    </html>
  );
}
