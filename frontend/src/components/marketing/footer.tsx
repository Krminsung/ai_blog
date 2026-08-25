import Link from "next/link";

import { Logo } from "@/components/ui/logo";

const GROUPS: { title: string; links: { href: string; label: string }[] }[] = [
  {
    title: "제품",
    links: [
      { href: "/product", label: "개요" },
      { href: "/workflow", label: "워크플로" },
      { href: "/product#quality", label: "품질 검수" },
      { href: "/product#publishing", label: "발행 채널" },
      { href: "/product#analytics", label: "성과 분석" },
    ],
  },
  {
    title: "플랫폼",
    links: [
      { href: "/security", label: "보안과 규정 준수" },
      { href: "/security#privacy", label: "개인정보 보호" },
      { href: "/status", label: "서비스 상태" },
      { href: "/pricing", label: "요금제" },
    ],
  },
  {
    title: "시작하기",
    links: [
      { href: "/signup", label: "계정 만들기" },
      { href: "/login", label: "로그인" },
      { href: "/console", label: "콘솔" },
    ],
  },
];

export function Footer() {
  return (
    <footer className="bg-[var(--surface-alt)] text-[12px] text-[var(--text-secondary)]">
      <div className="shell-wide py-12">
        <div className="grid gap-10 border-b border-[var(--hairline)] pb-10 sm:grid-cols-2 lg:grid-cols-4">
          <div className="text-[var(--text-primary)]">
            <Logo />
            <p className="mt-3 max-w-56 text-[12px] leading-relaxed text-[var(--text-secondary)]">
              근거와 승인 이력이 남는 콘텐츠 운영. 기획부터 발행, 성과까지
              하나의 흐름으로.
            </p>
          </div>

          {GROUPS.map((group) => (
            <div key={group.title}>
              <p className="mb-3 font-semibold text-[var(--text-primary)]">
                {group.title}
              </p>
              <ul className="flex flex-col gap-2.5">
                {group.links.map((link) => (
                  <li key={link.href + link.label}>
                    <Link
                      href={link.href}
                      className="transition-colors hover:text-[var(--text-primary)] hover:underline underline-offset-4"
                    >
                      {link.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="flex flex-col gap-3 pt-6 sm:flex-row sm:items-center sm:justify-between">
          <p>
            Copyright © {new Date().getFullYear()} BlogOps AI. 모든 권리 보유.
          </p>
          <p className="max-w-2xl leading-relaxed">
            네이버 블로그는 공식 자동 게시 API를 제공하지 않으므로, 무인 게시
            대신 수동 발행 패키지와 체크리스트를 제공합니다.
          </p>
        </div>
      </div>
    </footer>
  );
}
