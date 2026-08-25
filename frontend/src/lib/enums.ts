/**
 * Option lists for the select controls. Values must match the backend enums
 * exactly; the labels are ours.
 */
export interface Option {
  value: string;
  label: string;
}

export const SOURCE_TYPES: Option[] = [
  { value: "URL", label: "URL" },
  { value: "FILE", label: "파일" },
  { value: "TEXT", label: "직접 입력" },
  { value: "FAQ", label: "FAQ" },
  { value: "API", label: "API" },
  { value: "SITEMAP", label: "사이트맵" },
  { value: "RSS", label: "RSS" },
  { value: "YOUTUBE_TRANSCRIPT", label: "YouTube 자막" },
  { value: "PRODUCT_FEED", label: "상품 피드" },
  { value: "CMS", label: "CMS" },
];

export const RIGHTS_STATUSES: Option[] = [
  { value: "OWNED", label: "자사 보유" },
  { value: "LICENSED", label: "라이선스 보유" },
  { value: "PERMISSION_GRANTED", label: "사용 허락 받음" },
  { value: "PUBLIC_DOMAIN", label: "퍼블릭 도메인" },
  { value: "UNCONFIRMED", label: "미확인" },
  { value: "PROHIBITED", label: "사용 금지" },
];

export const USE_SCOPES: Option[] = [
  { value: "INTERNAL_ONLY", label: "내부 참고만" },
  { value: "GENERATION_ALLOWED", label: "생성에 사용 가능" },
  { value: "CITATION_ALLOWED", label: "인용까지 가능" },
];

export const QUALITY_GRADES: Option[] = [
  { value: "A", label: "A · 1차 공식 자료" },
  { value: "B", label: "B · 검증된 2차 자료" },
  { value: "C", label: "C · 참고 자료" },
  { value: "D", label: "D · 신뢰도 낮음" },
];

export const KNOWLEDGE_LEVELS: Option[] = [
  { value: "BEGINNER", label: "입문" },
  { value: "GENERAL", label: "일반" },
  { value: "EXPERT", label: "전문가" },
];

export const JOURNEY_STAGES: Option[] = [
  { value: "AWARENESS", label: "인지" },
  { value: "CONSIDERATION", label: "고려" },
  { value: "PURCHASE", label: "구매" },
  { value: "RETENTION", label: "유지" },
];

export const PRODUCT_SOURCES: Option[] = [
  { value: "MANUAL", label: "직접 입력" },
  { value: "CSV", label: "CSV 가져오기" },
  { value: "API", label: "API" },
  { value: "SHOPIFY", label: "Shopify" },
  { value: "CAFE24", label: "Cafe24" },
];

export const BUDGET_ENFORCEMENTS: Option[] = [
  { value: "WARN", label: "경고만" },
  { value: "BLOCK", label: "초과 시 차단" },
  { value: "PAUSE", label: "초과 시 일시 중지" },
];

export const KEYWORD_INTENTS: Option[] = [
  { value: "INFORMATIONAL", label: "정보 탐색" },
  { value: "COMPARISON", label: "비교" },
  { value: "PURCHASE", label: "구매" },
  { value: "LOCAL", label: "지역" },
  { value: "NAVIGATIONAL", label: "탐색" },
  { value: "MIXED", label: "복합" },
  { value: "UNKNOWN", label: "미분류" },
];

export const PUBLISH_VISIBILITIES: Option[] = [
  { value: "DRAFT", label: "임시 저장" },
  { value: "PUBLISH", label: "즉시 발행" },
  { value: "SCHEDULED", label: "예약 발행" },
  { value: "PENDING_REVIEW", label: "검토 대기" },
  { value: "PRIVATE", label: "비공개" },
];

export const PUBLISHING_PROVIDERS: Option[] = [
  { value: "WORDPRESS", label: "WordPress" },
  { value: "GHOST", label: "Ghost" },
  { value: "BLOGGER", label: "Blogger" },
  { value: "CUSTOMER_CMS", label: "고객 CMS" },
  { value: "NAVER_MANUAL", label: "네이버 (수동)" },
];

export const BRIEF_STATUSES: Option[] = [
  { value: "DRAFT", label: "초안" },
  { value: "WAITING_REVIEW", label: "검토 대기" },
  { value: "REVISION_REQUESTED", label: "수정 요청" },
  { value: "APPROVED", label: "승인됨" },
  { value: "REJECTED", label: "반려됨" },
  { value: "SCHEDULED", label: "예약됨" },
  { value: "ARCHIVED", label: "보관됨" },
];

export const APPROVAL_STATUSES: Option[] = [
  { value: "PENDING", label: "승인 대기" },
  { value: "CHANGES_REQUESTED", label: "수정 요청" },
  { value: "APPROVED", label: "승인 완료" },
  { value: "REJECTED", label: "반려됨" },
  { value: "EXPIRED", label: "기한 만료" },
  { value: "INVALIDATED", label: "승인 무효" },
  { value: "SUPERSEDED", label: "대체됨" },
];

export const CONTENT_STATES: Option[] = [
  { value: "DRAFT", label: "초안" },
  { value: "IN_REVIEW", label: "검수 중" },
  { value: "APPROVED", label: "승인됨" },
  { value: "SCHEDULED", label: "예약됨" },
  { value: "PUBLISHED", label: "발행됨" },
  { value: "ARCHIVED", label: "보관됨" },
];

/**
 * The 50 generation contracts, grouped the way the spec groups them. Kept as
 * groups so the picker stays navigable.
 */
export const CONTENT_TYPE_GROUPS: { label: string; options: Option[] }[] = [
  {
    label: "정보·가이드",
    options: [
      { value: "INFORMATIONAL", label: "정보성 글" },
      { value: "INFORMATIONAL_V2", label: "정보성 글 V2" },
      { value: "PROBLEM_SOLUTION", label: "문제 해결" },
      { value: "GUIDE_TUTORIAL", label: "가이드·튜토리얼" },
      { value: "FAQ", label: "FAQ" },
      { value: "LISTICLE", label: "리스트형" },
      { value: "COMPARISON", label: "비교" },
      { value: "DEFINITION_GLOSSARY", label: "용어 정의" },
      { value: "LOCAL_INFORMATION", label: "지역 정보" },
      { value: "NAVER_HOME_FEED", label: "네이버 홈피드" },
    ],
  },
  {
    label: "리뷰·경험",
    options: [
      { value: "PRODUCT_EXPERIENCE_REVIEW", label: "상품 사용 후기" },
      { value: "VISIT_REVIEW", label: "방문 후기" },
      { value: "TRAVEL_REVIEW", label: "여행 후기" },
      { value: "SPONSORED_REVIEW_BRIEF", label: "협찬 리뷰 브리프" },
      { value: "PROS_CONS_REVIEW", label: "장단점 리뷰" },
      { value: "REPURCHASE_REVIEW", label: "재구매 리뷰" },
      { value: "SPONSORED_DISCLOSURE", label: "협찬 고지" },
      { value: "REAL_PHOTO_REVIEW", label: "실사진 리뷰" },
      { value: "EXPERIENCE_QUESTIONNAIRE", label: "경험 설문" },
      { value: "EXPERIENCE_INTEGRITY_REVIEW", label: "경험 진정성 검토" },
    ],
  },
  {
    label: "커머스",
    options: [
      { value: "PRODUCT_PROMOTION", label: "상품 홍보" },
      { value: "SMART_STORE", label: "스마트스토어" },
      { value: "AFFILIATE_COMMERCE", label: "제휴 커머스" },
      { value: "BUYING_GUIDE", label: "구매 가이드" },
      { value: "PRODUCT_COMPARISON", label: "상품 비교" },
      { value: "PRODUCT_HOW_TO", label: "상품 사용법" },
      { value: "PRODUCT_FAQ", label: "상품 FAQ" },
      { value: "LANDING_BLOG", label: "랜딩 블로그" },
      { value: "PROMOTION", label: "프로모션" },
      { value: "TRACKED_LINK_CONTENT", label: "추적 링크 콘텐츠" },
    ],
  },
  {
    label: "외부 자료 기반",
    options: [
      { value: "YOUTUBE_BASED", label: "YouTube 기반" },
      { value: "VIDEO_SUMMARY", label: "영상 요약" },
      { value: "NEWS_BASED", label: "뉴스 기반" },
      { value: "NEWS_OPINION", label: "뉴스 오피니언" },
      { value: "PDF_REPORT_BASED", label: "PDF 리포트 기반" },
      { value: "INTERVIEW_BASED", label: "인터뷰 기반" },
      { value: "PRESS_RELEASE_BASED", label: "보도자료 기반" },
      { value: "RSS_BASED", label: "RSS 기반" },
      { value: "URL_BASED", label: "URL 기반" },
      { value: "MULTI_SOURCE_SYNTHESIS", label: "복수 출처 종합" },
    ],
  },
  {
    label: "재작성·변환",
    options: [
      { value: "OWNED_CONTENT_REWRITE", label: "자사 콘텐츠 재작성" },
      { value: "CONTENT_REFRESH", label: "콘텐츠 갱신" },
      { value: "STRUCTURE_IMPROVEMENT", label: "구조 개선" },
      { value: "VOICE_TRANSFORMATION", label: "보이스 변환" },
      { value: "LENGTH_TRANSFORMATION", label: "분량 변환" },
      { value: "CONTENT_MERGE", label: "콘텐츠 병합" },
      { value: "CONTENT_SPLIT", label: "콘텐츠 분할" },
      { value: "DEDUPLICATION", label: "중복 제거" },
      { value: "CHANNEL_REFORMAT", label: "채널 재구성" },
      { value: "THIRD_PARTY_LIMITED_TRANSFORM", label: "third-party 제한 변환" },
    ],
  },
];

export const CHANNELS: Option[] = [
  { value: "blog", label: "블로그" },
  { value: "naver_blog", label: "네이버 블로그" },
  { value: "wordpress", label: "WordPress" },
  { value: "ghost", label: "Ghost" },
  { value: "newsletter", label: "뉴스레터" },
  { value: "instagram", label: "인스타그램" },
  { value: "youtube", label: "YouTube" },
];
