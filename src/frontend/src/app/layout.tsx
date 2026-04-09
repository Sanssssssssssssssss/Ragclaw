import type { Metadata } from "next";
import { IBM_Plex_Mono, VT323 } from "next/font/google";

import "./globals.css";

const monoBaseFont = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-body"
});

const monoFont = VT323({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-mono"
});

export const metadata: Metadata = {
  title: "Onyx Chat",
  description: "A dark, local-first workspace for retrieval and agent chat."
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className={`${monoBaseFont.variable} ${monoFont.variable}`}>
        {children}
      </body>
    </html>
  );
}
