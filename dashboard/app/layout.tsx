import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Powerall · Electricity in focus",
  description: "Your half-hourly electricity usage, tariff history and estimated costs in one clear local dashboard.",
  icons: { icon: "/favicon.svg" },
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en-GB"><body>{children}</body></html>;
}
