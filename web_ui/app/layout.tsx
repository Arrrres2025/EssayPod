import type { Metadata } from "next";
import "./globals.css";
import "./overrides.css";
import "./light-ui.css";

export const metadata: Metadata = {
  title: "EssayPod — Research Studio",
  description: "Turn research papers into thoughtful podcasts and editable videos.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body className="essaypod-light-ui">{children}</body></html>;
}
