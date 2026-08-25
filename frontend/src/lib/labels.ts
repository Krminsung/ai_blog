import { humanizeEnum } from "@/lib/format";
import { CONTENT_TYPE_GROUPS } from "@/lib/enums";

/**
 * Korean display labels and tone for the backend's enum vocabularies.
 *
 * `tone` maps onto the badge palette: neutral for resting states, `progress`
 * for anything in flight, `positive` for terminal success, `caution` for
 * states that need a human, `critical` for hard failures and blocks.
 */
export type Tone = "neutral" | "progress" | "positive" | "caution" | "critical";

export interface LabelSpec {
  label: string;
  tone: Tone;
}

const JOB_STATE: Record<string, LabelSpec> = {
  CREATED: { label: "생성됨", tone: "neutral" },
  QUEUED: { label: "대기 중", tone: "neutral" },
  VALIDATING: { label: "검증 중", tone: "progress" },
  WAITING_INPUT: { label: "입력 대기", tone: "caution" },
  RESEARCHING: { label: "자료 조사 중", tone: "progress" },
  PLANNING: { label: "구성 중", tone: "progress" },
  GENERATING: { label: "생성 중", tone: "progress" },
  VERIFYING: { label: "검증 중", tone: "progress" },
  OPTIMIZING: { label: "최적화 중", tone: "progress" },
  CREATING_MEDIA: { label: "이미지 생성 중", tone: "progress" },
  QUALITY_BLOCKED: { label: "품질 차단", tone: "critical" },
  WAITING_REVIEW: { label: "검수 대기", tone: "caution" },
  REVISION_REQUESTED: { label: "수정 요청", tone: "caution" },
  APPROVED: { label: "승인됨", tone: "positive" },
  SCHEDULED: { label: "예약됨", tone: "neutral" },
  PUBLISHING: { label: "발행 중", tone: "progress" },
  SUCCEEDED: { label: "완료", tone: "positive" },
  PARTIAL: { label: "부분 완료", tone: "caution" },
  RETRYABLE_FAILED: { label: "재시도 가능", tone: "caution" },
  FINAL_FAILED: { label: "실패", tone: "critical" },
  CANCEL_REQUESTED: { label: "취소 요청", tone: "caution" },
  CANCELLED: { label: "취소됨", tone: "neutral" },
  EXPIRED: { label: "만료됨", tone: "neutral" },
};

const CATALOG_STATUS: Record<string, LabelSpec> = {
  ACTIVE: { label: "사용 중", tone: "positive" },
  INACTIVE: { label: "비활성", tone: "neutral" },
};

const CAMPAIGN_STATUS: Record<string, LabelSpec> = {
  DRAFT: { label: "초안", tone: "neutral" },
  ACTIVE: { label: "진행 중", tone: "positive" },
  PAUSED: { label: "일시 중지", tone: "caution" },
  COMPLETED: { label: "종료", tone: "neutral" },
  ARCHIVED: { label: "보관됨", tone: "neutral" },
};

const BRIEF_STATUS: Record<string, LabelSpec> = {
  DRAFT: { label: "초안", tone: "neutral" },
  WAITING_REVIEW: { label: "검토 대기", tone: "caution" },
  REVISION_REQUESTED: { label: "수정 요청", tone: "caution" },
  APPROVED: { label: "승인됨", tone: "positive" },
  REJECTED: { label: "반려됨", tone: "critical" },
  SCHEDULED: { label: "예약됨", tone: "progress" },
  ARCHIVED: { label: "보관됨", tone: "neutral" },
};

const APPROVAL_STATUS: Record<string, LabelSpec> = {
  PENDING: { label: "승인 대기", tone: "caution" },
  CHANGES_REQUESTED: { label: "수정 요청", tone: "caution" },
  REJECTED: { label: "반려됨", tone: "critical" },
  APPROVED: { label: "승인 완료", tone: "positive" },
  EXPIRED: { label: "기한 만료", tone: "neutral" },
  INVALIDATED: { label: "승인 무효", tone: "critical" },
  SUPERSEDED: { label: "대체됨", tone: "neutral" },
};

const ASSESSMENT_DECISION: Record<string, LabelSpec> = {
  PASS: { label: "통과", tone: "positive" },
  NEEDS_REVISION: { label: "수정 필요", tone: "caution" },
  BLOCKED: { label: "차단", tone: "critical" },
};

const CONTENT_STATE: Record<string, LabelSpec> = {
  DRAFT: { label: "초안", tone: "neutral" },
  GENERATING: { label: "생성 중", tone: "progress" },
  IN_REVIEW: { label: "검수 중", tone: "caution" },
  WAITING_REVIEW: { label: "검수 대기", tone: "caution" },
  APPROVED: { label: "승인됨", tone: "positive" },
  SCHEDULED: { label: "예약됨", tone: "progress" },
  PUBLISHED: { label: "발행됨", tone: "positive" },
  ARCHIVED: { label: "보관됨", tone: "neutral" },
  BLOCKED: { label: "차단됨", tone: "critical" },
};

const PUBLISHED_POST_STATE: Record<string, LabelSpec> = {
  DRAFT: { label: "임시 저장", tone: "neutral" },
  SCHEDULED: { label: "예약됨", tone: "progress" },
  PUBLISHED: { label: "발행됨", tone: "positive" },
  PARTIAL: { label: "부분 발행", tone: "caution" },
  CONFLICT: { label: "원격 충돌", tone: "critical" },
  TRASHED: { label: "휴지통", tone: "neutral" },
  DELETED: { label: "삭제됨", tone: "neutral" },
  REMOTE_MISSING: { label: "원격 없음", tone: "critical" },
  MANUALLY_CONFIRMED: { label: "수동 확인", tone: "positive" },
};

const CONNECTION_STATE: Record<string, LabelSpec> = {
  PENDING: { label: "연결 대기", tone: "caution" },
  ACTIVE: { label: "정상", tone: "positive" },
  DEGRADED: { label: "불안정", tone: "caution" },
  EXPIRED: { label: "인증 만료", tone: "critical" },
  DISCONNECTED: { label: "연결 해제", tone: "neutral" },
};

const KEYWORD_INTENT: Record<string, LabelSpec> = {
  INFORMATIONAL: { label: "정보 탐색", tone: "neutral" },
  COMPARISON: { label: "비교", tone: "neutral" },
  PURCHASE: { label: "구매", tone: "positive" },
  LOCAL: { label: "지역", tone: "neutral" },
  NAVIGATIONAL: { label: "탐색", tone: "neutral" },
  MIXED: { label: "복합", tone: "neutral" },
  UNKNOWN: { label: "미분류", tone: "neutral" },
};

const REPORT_KIND: Record<string, LabelSpec> = {
  MORPHOLOGY: { label: "형태소", tone: "neutral" },
  NATURALNESS: { label: "자연스러움", tone: "neutral" },
  SEO: { label: "SEO", tone: "neutral" },
  DUPLICATION: { label: "중복", tone: "neutral" },
  FACT_CITATION: { label: "팩트·인용", tone: "neutral" },
  SAFETY_POLICY: { label: "안전 정책", tone: "neutral" },
};

const PROVIDER: Record<string, LabelSpec> = {
  WORDPRESS: { label: "WordPress", tone: "neutral" },
  GHOST: { label: "Ghost", tone: "neutral" },
  BLOGGER: { label: "Blogger", tone: "neutral" },
  CUSTOMER_CMS: { label: "고객 CMS", tone: "neutral" },
  NAVER_MANUAL: { label: "네이버 (수동)", tone: "caution" },
};

const PROPOSAL_STATUS: Record<string, LabelSpec> = {
  PENDING_APPROVAL: { label: "승인 대기", tone: "caution" },
  APPROVED: { label: "승인됨", tone: "positive" },
  REJECTED: { label: "반려됨", tone: "critical" },
};

const IDEA_STATUS: Record<string, LabelSpec> = {
  SUGGESTED: { label: "제안됨", tone: "neutral" },
  DISMISSED: { label: "보류", tone: "neutral" },
  PROMOTED: { label: "채택됨", tone: "positive" },
};

const PUBLISH_OPERATION: Record<string, LabelSpec> = {
  CREATE: { label: "신규 발행", tone: "neutral" },
  UPDATE: { label: "수정 발행", tone: "neutral" },
  DELETE: { label: "삭제", tone: "caution" },
  RECONCILE: { label: "원격 대조", tone: "neutral" },
  ROLLBACK: { label: "롤백", tone: "caution" },
};

const PUBLISH_VISIBILITY: Record<string, LabelSpec> = {
  DRAFT: { label: "임시 저장", tone: "neutral" },
  PUBLISH: { label: "즉시 발행", tone: "positive" },
  SCHEDULED: { label: "예약 발행", tone: "progress" },
  PENDING_REVIEW: { label: "검토 대기", tone: "caution" },
  PRIVATE: { label: "비공개", tone: "neutral" },
};

/** Flattened from the grouped picker so both share one source of truth. */
const CONTENT_TYPE: Record<string, LabelSpec> = Object.fromEntries(
  CONTENT_TYPE_GROUPS.flatMap((group) =>
    group.options.map((option) => [
      option.value,
      { label: option.label, tone: "neutral" as Tone },
    ]),
  ),
);

const REGISTRIES: Record<string, Record<string, LabelSpec>> = {
  jobState: JOB_STATE,
  catalogStatus: CATALOG_STATUS,
  campaignStatus: CAMPAIGN_STATUS,
  briefStatus: BRIEF_STATUS,
  approvalStatus: APPROVAL_STATUS,
  assessmentDecision: ASSESSMENT_DECISION,
  contentState: CONTENT_STATE,
  publishedPostState: PUBLISHED_POST_STATE,
  connectionState: CONNECTION_STATE,
  keywordIntent: KEYWORD_INTENT,
  reportKind: REPORT_KIND,
  provider: PROVIDER,
  proposalStatus: PROPOSAL_STATUS,
  ideaStatus: IDEA_STATUS,
  publishOperation: PUBLISH_OPERATION,
  publishVisibility: PUBLISH_VISIBILITY,
  contentType: CONTENT_TYPE,
};

export type Registry = keyof typeof REGISTRIES;

/**
 * Look a value up in one registry, falling back to a humanized version of the
 * raw enum. The backend owns these vocabularies and can add members ahead of
 * the UI, so an unknown value must render, not crash.
 */
export function labelFor(registry: Registry, value?: string | null): LabelSpec {
  if (!value) return { label: "—", tone: "neutral" };
  return (
    REGISTRIES[registry][value] ?? { label: humanizeEnum(value), tone: "neutral" }
  );
}

/** Search every registry — handy for generic tables over mixed job types. */
export function anyLabelFor(value?: string | null): LabelSpec {
  if (!value) return { label: "—", tone: "neutral" };
  for (const registry of Object.values(REGISTRIES)) {
    if (registry[value]) return registry[value];
  }
  return { label: humanizeEnum(value), tone: "neutral" };
}
