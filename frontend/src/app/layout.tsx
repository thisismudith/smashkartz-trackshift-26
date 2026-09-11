import type { Metadata, Viewport } from "next";
import { Archivo } from "next/font/google";
import RaceLoader from "@/components/loader/RaceLoader";
import { HAAS } from "@/lib/palette";
import "./globals.css";

/**
 * One variable family for the whole app (globals.css aliases --font-body to it), downloaded at
 * build time and self-hosted by Next — no runtime font fetch, one file instead of two.
 * The `wdth` axis is what lets the wordmark run expanded at 900 without a second file.
 * "block" (not "swap"): the intro reveals the wordmark letter by letter, and a mid-reveal
 * fallback→webfont swap would visibly reflow it. next/font preloads it, so the block is momentary.
 */
const display = Archivo({
  variable: "--font-display",
  subsets: ["latin"],
  axes: ["wdth"],
  display: "block",
});

export const metadata: Metadata = {
  title: "SmashKartz · TrackShift 2026",
  description:
    "E-Delta: energy and overtake intelligence for the 2026 Formula 1 regime. Team SmashKartz, TrackShift 2026.",
};

export const viewport: Viewport = {
  themeColor: HAAS.black,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={display.variable}>
      <body>
        {/* Lives in the root layout: renders once per full load, persists across client navigation. */}
        <RaceLoader />
        {children}
      </body>
    </html>
  );
}
