import type { Metadata, Viewport } from "next";

import { Providers } from "@/app/providers";
import { THEME_BOOTSTRAP } from "@/components/ui/theme";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://blogops.ai"),
  title: {
    default: "BlogOps AI — 근거 기반 콘텐츠 운영 플랫폼",
    template: "%s — BlogOps AI",
  },
  description:
    "기획부터 생성, 품질 검수, 승인, 공식 채널 발행과 성과 분석까지. 근거와 승인 이력이 남는 콘텐츠 운영 플랫폼.",
  keywords: [
    "콘텐츠 자동화",
    "AI 블로그",
    "SEO",
    "콘텐츠 운영",
    "품질 검수",
    "발행 자동화",
  ],
  openGraph: {
    type: "website",
    locale: "ko_KR",
    siteName: "BlogOps AI",
    title: "BlogOps AI — 근거 기반 콘텐츠 운영 플랫폼",
    description:
      "기획부터 생성, 품질 검수, 승인, 공식 채널 발행과 성과 분석까지 하나의 흐름으로.",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#000000" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="ko"
      className="h-full antialiased"
      // The app opts into smooth scrolling; this tells the router to bypass it
      // during navigations so route changes still jump to the top instantly.
      data-scroll-behavior="smooth"
      suppressHydrationWarning
    >
      <head>
        {/* Applies the stored theme before first paint to avoid a flash. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body className="flex min-h-full flex-col">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-200 focus:rounded-full focus:bg-[var(--surface-inverse)] focus:px-4 focus:py-2 focus:text-[13px] focus:text-[var(--text-inverse)]"
        >
          본문으로 건너뛰기
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
