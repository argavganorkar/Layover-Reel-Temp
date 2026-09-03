import type { Metadata } from "next";
import AppHeader from "@/components/AppHeader";
// Self-hosted (not fetched from Google Fonts at build/request time) so the
// app works fully offline, which matters for a personal tool that doesn't
// assume the machine running it always has internet. Inter carries the
// UI/body text - clean and highly legible at small sizes. Fraunces is the
// warm, slightly editorial serif used for headings only - the pairing
// (soft serif display + crisp sans body) is what gives the app its
// "considered AI product" feel rather than a stock dashboard look, per
// Aveg's ask. Only the weights/styles actually used in the app are pulled
// in, to keep the font payload small.
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/fraunces/500.css";
import "@fontsource/fraunces/500-italic.css";
import "@fontsource/fraunces/600.css";
import "@fontsource/fraunces/600-italic.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Reel Maker",
  description: "Turn a long video into short-form vertical reels.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased" style={{ colorScheme: "dark" }}>
      <body className="min-h-full flex flex-col bg-background text-ink font-sans">
        <AppHeader />
        {children}
      </body>
    </html>
  );
}
