import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "문서 에이전트",
  description: "업로드한 문서를 근거로 답하는 사내 에이전트",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
