import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "传播引擎｜AI 视频创作",
  description: "把一个想法变成有传播力的完整视频",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
