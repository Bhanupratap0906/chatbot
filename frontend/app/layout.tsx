import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Voice AI — SIP Realtime",
  description: "Incoming SIP calls are automatically answered by AI.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  /** Provide the shared HTML document and metadata for every Next.js page. */
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
