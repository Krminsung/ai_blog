/**
 * Console navigation. The grouping mirrors the backend's domain boundaries so
 * a route always maps to one service surface.
 */
export interface NavItem {
  href: string;
  label: string;
  /** Matched as a prefix so detail routes keep the parent highlighted. */
  match?: string;
}

export interface NavGroup {
  title: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    title: "개요",
    items: [{ href: "/console", label: "대시보드" }],
  },
  {
    title: "자료",
    items: [
      { href: "/console/brands", label: "브랜드" },
      { href: "/console/products", label: "상품" },
      { href: "/console/personas", label: "페르소나" },
      { href: "/console/knowledge", label: "지식 자료" },
    ],
  },
  {
    title: "기획",
    items: [
      { href: "/console/keywords", label: "키워드" },
      { href: "/console/campaigns", label: "캠페인" },
      { href: "/console/briefs", label: "브리프" },
      { href: "/console/calendar", label: "캘린더" },
    ],
  },
  {
    title: "제작",
    items: [
      { href: "/console/content", label: "콘텐츠" },
      { href: "/console/media", label: "미디어" },
      { href: "/console/bulk", label: "대량 생성" },
      { href: "/console/repurpose", label: "재활용" },
    ],
  },
  {
    title: "검수",
    items: [
      { href: "/console/quality", label: "품질" },
      { href: "/console/approvals", label: "승인" },
    ],
  },
  {
    title: "발행",
    items: [
      { href: "/console/publishing", label: "발행 작업" },
      { href: "/console/connections", label: "채널 연결" },
      { href: "/console/posts", label: "발행된 글" },
    ],
  },
  {
    title: "성과",
    items: [{ href: "/console/analytics", label: "분석" }],
  },
  {
    title: "관리",
    items: [
      { href: "/console/billing", label: "요금과 사용량" },
      { href: "/console/developer", label: "개발자" },
      { href: "/console/privacy", label: "개인정보" },
      { href: "/console/operations", label: "운영" },
      { href: "/console/settings", label: "설정" },
    ],
  },
];

/** Longest-prefix match so `/console` does not swallow every child route. */
export function isActive(pathname: string, item: NavItem): boolean {
  const target = item.match ?? item.href;
  if (target === "/console") return pathname === "/console";
  return pathname === target || pathname.startsWith(`${target}/`);
}
