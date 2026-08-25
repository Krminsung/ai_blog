import type { Metadata } from "next";

import { ButtonLink, ChevronLink } from "@/components/ui/button";
import { Reveal } from "@/components/ui/reveal";
import { Section, Tile } from "@/components/ui/surface";
import { Pipeline } from "@/components/marketing/pipeline";

export const metadata: Metadata = {
  title: "워크플로",
  description:
    "브리프 승인부터 생성, 품질 검수, 승인, 발행까지. 각 단계가 어떤 스냅샷을 고정하고 무엇을 차단하는지 살펴봅니다.",
};

const GUARANTEES = [
  {
    title: "승인 없이는 생성하지 않습니다",
    body: "생성 Job은 승인된 브리프 버전을 참조할 때만 시작합니다. 브리프가 바뀌면 새 버전이 만들어지고 승인을 다시 받습니다.",
  },
  {
    title: "입력이 바뀌면 승인이 무효화됩니다",
    body: "브랜드 자료, 상품 사실, 모델, 가격 중 승인에 영향을 주는 값이 달라지면 기존 승인은 자동으로 무효 처리됩니다.",
  },
  {
    title: "공급자가 없으면 실패로 처리합니다",
    body: "모델·검색·예산 공급자가 구성되지 않은 상태에서 워커는 성공을 반환하지 않습니다. 조용한 빈 결과 대신 명시적 실패입니다.",
  },
  {
    title: "발행은 한 번만 일어납니다",
    body: "멱등 키와 Saga 단계로 중복 발행을 막고, 중간 실패는 보상 단계를 거쳐 정리됩니다.",
  },
];

const STATES = [
  { state: "CREATED", note: "작업 생성" },
  { state: "QUEUED", note: "실행 대기" },
  { state: "RESEARCHING", note: "근거 수집" },
  { state: "GENERATING", note: "초안 작성" },
  { state: "VERIFYING", note: "품질 검수" },
  { state: "QUALITY_BLOCKED", note: "정책 차단" },
  { state: "WAITING_REVIEW", note: "사람 검수" },
  { state: "APPROVED", note: "승인 완료" },
  { state: "SCHEDULED", note: "발행 예약" },
  { state: "PUBLISHING", note: "발행 실행" },
  { state: "SUCCEEDED", note: "완료" },
  { state: "PARTIAL", note: "부분 완료" },
];

export default function WorkflowPage() {
  return (
    <>
      <Section className="pt-16 pb-16 text-center sm:pt-24">
        <div className="shell">
          <Reveal>
            <p className="type-eyebrow">워크플로</p>
            <h1 className="type-display mt-4">
              단계마다
              <br />
              멈출 수 있습니다.
            </h1>
            <p className="type-subhead mx-auto mt-6 max-w-[36rem]">
              자동화는 사람의 판단을 대체하지 않습니다. 각 단계는 승인 지점을
              가지고 있고, 통과하지 못한 콘텐츠는 다음으로 넘어가지 않습니다.
            </p>
          </Reveal>
        </div>
      </Section>

      <Section tone="alt" className="pt-0 sm:pt-0">
        <div className="shell">
          <Reveal>
            <Pipeline />
          </Reveal>
        </div>
      </Section>

      <Section>
        <div className="shell">
          <Reveal className="mb-12 max-w-2xl">
            <p className="type-eyebrow">보장</p>
            <h2 className="type-headline mt-3">지켜지는 네 가지.</h2>
          </Reveal>
          <div className="grid gap-4 md:grid-cols-2">
            {GUARANTEES.map((item, index) => (
              <Reveal key={item.title} delay={index * 60}>
                <Tile className="h-full">
                  <h3 className="type-title">{item.title}</h3>
                  <p className="mt-3 text-[15px] leading-relaxed text-[var(--text-secondary)]">
                    {item.body}
                  </p>
                </Tile>
              </Reveal>
            ))}
          </div>
        </div>
      </Section>

      <Section tone="alt">
        <div className="shell">
          <Reveal className="mb-10 max-w-2xl">
            <p className="type-eyebrow">상태</p>
            <h2 className="type-headline mt-3">지금 어디에 있는지 보입니다.</h2>
            <p className="type-subhead mt-5">
              모든 장기 작업은 같은 상태 기계를 공유합니다. 취소와 재시도의
              허용 여부도 상태에 따라 결정됩니다.
            </p>
          </Reveal>

          <Reveal delay={80}>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
              {STATES.map((item) => (
                <div
                  key={item.state}
                  className="rounded-[12px] border border-[var(--hairline-soft)] bg-[var(--surface-raised)] px-4 py-3"
                >
                  <p className="font-mono text-[11px] tracking-tight text-[var(--accent-link)]">
                    {item.state}
                  </p>
                  <p className="mt-1 text-[13px] text-[var(--text-secondary)]">
                    {item.note}
                  </p>
                </div>
              ))}
            </div>
          </Reveal>
        </div>
      </Section>

      <Section className="py-24 text-center">
        <div className="shell">
          <Reveal>
            <h2 className="type-headline">워크플로를 만들어 보세요.</h2>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-x-8 gap-y-4">
              <ButtonLink href="/signup" size="lg">
                시작하기
              </ButtonLink>
              <ChevronLink href="/security">보안 살펴보기</ChevronLink>
            </div>
          </Reveal>
        </div>
      </Section>
    </>
  );
}
