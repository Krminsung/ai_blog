import type { Metadata } from "next";

import { ButtonLink, ChevronLink } from "@/components/ui/button";
import { Reveal } from "@/components/ui/reveal";
import { Section, Tile } from "@/components/ui/surface";
import {
  ChannelGrid,
  EvidenceChain,
  QualityScoreVisual,
} from "@/components/marketing/visuals";

export const metadata: Metadata = {
  title: "제품",
  description:
    "브랜드 자료, 지식 수집, 키워드, 기획, 생성, 품질, 승인, 미디어, 발행, 성과. BlogOps AI가 다루는 전체 범위.",
};

interface Capability {
  title: string;
  body: string;
  points: string[];
}

const FOUNDATION: Capability[] = [
  {
    title: "브랜드와 상품",
    body: "보이스, 금지 표현, 필수 문구, 경쟁사 정책을 버전으로 고정합니다.",
    points: [
      "불변 브랜드·상품 스냅샷과 버전 이력",
      "승인된 사실과 금지 주장 규칙",
      "가격·권리·제휴 고지 자동 반영",
    ],
  },
  {
    title: "지식 수집",
    body: "파일과 URL을 안전하게 받아 검색 가능한 지식으로 만듭니다.",
    points: [
      "악성코드 검사와 PII 마스킹",
      "파싱·청킹·임베딩과 위치 계보",
      "권한 기반 하이브리드 검색",
    ],
  },
  {
    title: "키워드",
    body: "공식·계약·사용자 제공 출처만 사용하고 호출 한도를 지킵니다.",
    points: [
      "정규화·의도·추세·점수·군집",
      "캐시와 호출 한도, 수집 계보",
      "콘텐츠 연결과 중복 검사",
    ],
  },
];

const OPERATIONS: Capability[] = [
  {
    title: "기획",
    body: "캠페인 예산부터 토픽 트리, 브리프까지 한 곳에서 관리합니다.",
    points: [
      "캠페인·예산·토픽·아이디어",
      "불변 콘텐츠 브리프 버전과 단계 승인",
      "워크스페이스 시간대 기반 캘린더",
    ],
  },
  {
    title: "생성",
    body: "승인된 브리프와 고정된 입력 스냅샷으로만 초안을 만듭니다.",
    points: [
      "50개 콘텐츠 유형별 입력·안전 계약",
      "단계형 Job과 부분 결과, 원가 메타",
      "버전형 콘텐츠 보관함과 복원",
    ],
  },
  {
    title: "대량 생성",
    body: "스프레드시트 한 장으로 수천 건을 만들고 통제합니다.",
    points: [
      "서버 검증 CSV·XLSX 스냅샷",
      "행 멱등성·재시도·승인 기반 실행",
      "비용 홀드와 Kill Switch",
    ],
  },
];

const GOVERNANCE: Capability[] = [
  {
    title: "승인",
    body: "콘텐츠 버전과 해시를 데이터베이스 외래키까지 고정합니다.",
    points: [
      "다단계·정족수 승인과 승인 증명",
      "새 버전·복원·재생성 시 자동 무효화",
      "감사 가능한 결정 이력",
    ],
  },
  {
    title: "보안과 규정",
    body: "테넌트 격리, 보존 정책, 정보주체 요청을 기본 제공합니다.",
    points: [
      "PostgreSQL RLS 기반 테넌트 격리",
      "보존 정책·Legal Hold·삭제 증명",
      "저작권 신고와 침해 통지 처리",
    ],
  },
  {
    title: "개발자",
    body: "API Key와 Webhook으로 기존 파이프라인에 연결합니다.",
    points: [
      "원문 1회 표시·회전·Scope·IP 정책",
      "Workspace·Key·Endpoint 다층 Rate Limit",
      "HMAC 서명 Webhook과 DLQ, 수동 Replay",
    ],
  },
];

export default function ProductPage() {
  return (
    <>
      <Section className="pt-16 pb-16 text-center sm:pt-24">
        <div className="shell">
          <Reveal>
            <p className="type-eyebrow">제품</p>
            <h1 className="type-display mt-4">전부 한 자리에.</h1>
            <p className="type-subhead mx-auto mt-6 max-w-[36rem]">
              자료를 고정하는 일부터 성과를 되먹이는 일까지. BlogOps AI는
              콘텐츠 운영의 모든 단계를 하나의 제품으로 다룹니다.
            </p>
          </Reveal>
        </div>
      </Section>

      <CapabilitySection
        eyebrow="기반"
        title="근거가 되는 자료부터."
        capabilities={FOUNDATION}
      />

      <Section tone="alt" id="quality">
        <div className="shell grid items-center gap-12 lg:grid-cols-2">
          <Reveal>
            <p className="type-eyebrow">품질 검수</p>
            <h2 className="type-headline mt-3">숫자에 이유가 있습니다.</h2>
            <p className="type-subhead mt-5">
              버전이 고정된 분석기와 사전으로 여섯 종류의 리포트를 만들고, 일곱
              요소를 가중 합산해 종합 점수를 냅니다. 어떤 규칙이 몇 점을
              깎았는지 그대로 보입니다.
            </p>
            <ul className="mt-6 flex flex-col gap-2 text-[15px] text-[var(--text-secondary)]">
              <li>· 형태소·자연스러움·SEO·중복·팩트/인용·안전 정책 리포트</li>
              <li>· 계층형 정책 우선순위와 예외 불가 Hard Block</li>
              <li>· 임계값 미달 항목과 차단 사유의 전체 이력</li>
            </ul>
          </Reveal>
          <Reveal delay={100}>
            <Tile className="bg-[var(--surface-raised)]">
              <QualityScoreVisual />
            </Tile>
          </Reveal>
        </div>
      </Section>

      <CapabilitySection
        eyebrow="운영"
        title="기획에서 생성까지."
        capabilities={OPERATIONS}
      />

      <Section tone="alt" id="publishing">
        <div className="shell grid items-center gap-12 lg:grid-cols-2">
          <Reveal>
            <p className="type-eyebrow">발행</p>
            <h2 className="type-headline mt-3">공식 채널에 안전하게.</h2>
            <p className="type-subhead mt-5">
              멱등 Saga로 중복 발행을 막고, DST를 고려해 예약합니다. 원격에서
              글이 수정되면 충돌을 감지하고 되돌릴 경로를 제시합니다.
            </p>
            <ChevronLink href="/workflow" className="mt-6 inline-flex">
              발행 흐름 보기
            </ChevronLink>
          </Reveal>
          <Reveal delay={100}>
            <ChannelGrid />
          </Reveal>
        </div>
      </Section>

      <Section id="analytics">
        <div className="shell grid items-center gap-12 lg:grid-cols-2">
          <Reveal className="lg:order-2">
            <p className="type-eyebrow">성과</p>
            <h2 className="type-headline mt-3">되먹임까지 한 흐름.</h2>
            <p className="type-subhead mt-5">
              공식 분석 공급자의 원본 증거와 지표 정의를 고정해 전환과 ROI를
              계산합니다. 성과가 확인된 콘텐츠는 14종 형식으로 재활용하고, 다시
              승인 게이트를 거칩니다.
            </p>
          </Reveal>
          <Reveal delay={100} className="lg:order-1">
            <Tile tone="alt">
              <EvidenceChain />
            </Tile>
          </Reveal>
        </div>
      </Section>

      <CapabilitySection
        eyebrow="거버넌스"
        title="믿을 수 있게 운영합니다."
        capabilities={GOVERNANCE}
        tone="alt"
      />

      <Section className="py-24 text-center">
        <div className="shell">
          <Reveal>
            <h2 className="type-headline">직접 확인해 보세요.</h2>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-x-8 gap-y-4">
              <ButtonLink href="/signup" size="lg">
                시작하기
              </ButtonLink>
              <ChevronLink href="/pricing">요금제 보기</ChevronLink>
            </div>
          </Reveal>
        </div>
      </Section>
    </>
  );
}

function CapabilitySection({
  eyebrow,
  title,
  capabilities,
  tone = "surface",
}: {
  eyebrow: string;
  title: string;
  capabilities: Capability[];
  tone?: "surface" | "alt";
}) {
  return (
    <Section tone={tone}>
      <div className="shell">
        <Reveal className="mb-12 max-w-2xl">
          <p className="type-eyebrow">{eyebrow}</p>
          <h2 className="type-headline mt-3">{title}</h2>
        </Reveal>
        <div className="grid gap-4 md:grid-cols-3">
          {capabilities.map((capability, index) => (
            <Reveal key={capability.title} delay={index * 70}>
              <Tile className="h-full">
                <h3 className="type-title">{capability.title}</h3>
                <p className="mt-3 text-[15px] leading-relaxed text-[var(--text-secondary)]">
                  {capability.body}
                </p>
                <ul className="mt-5 flex flex-col gap-2 border-t border-[var(--hairline-soft)] pt-5 text-[14px] text-[var(--text-secondary)]">
                  {capability.points.map((point) => (
                    <li key={point}>{point}</li>
                  ))}
                </ul>
              </Tile>
            </Reveal>
          ))}
        </div>
      </div>
    </Section>
  );
}
