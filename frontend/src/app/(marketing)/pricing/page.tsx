import type { Metadata } from "next";

import { ButtonLink, ChevronLink } from "@/components/ui/button";
import { Reveal } from "@/components/ui/reveal";
import { Section, Tile } from "@/components/ui/surface";
import { cn } from "@/lib/cn";

export const metadata: Metadata = {
  title: "요금제",
  description:
    "워크스페이스 규모와 승인 단계, 발행 채널 수에 맞춰 고르세요. 생성·미디어·대량 작업은 크레딧으로 정산됩니다.",
};

interface Plan {
  name: string;
  tagline: string;
  price: string;
  unit: string;
  featured?: boolean;
  features: string[];
  cta: string;
}

const PLANS: Plan[] = [
  {
    name: "Starter",
    tagline: "한 브랜드로 시작하는 팀",
    price: "₩190,000",
    unit: "월",
    features: [
      "브랜드 1개, 멤버 3명",
      "월 크레딧 20,000",
      "품질 검수와 단일 단계 승인",
      "WordPress·Ghost 발행",
      "기본 성과 리포트",
    ],
    cta: "시작하기",
  },
  {
    name: "Growth",
    tagline: "여러 채널을 운영하는 팀",
    price: "₩690,000",
    unit: "월",
    featured: true,
    features: [
      "브랜드 5개, 멤버 15명",
      "월 크레딧 100,000",
      "다단계·정족수 승인",
      "전체 발행 채널과 네이버 수동 패키지",
      "대량 생성과 콘텐츠 재활용",
      "API Key와 Webhook",
    ],
    cta: "시작하기",
  },
  {
    name: "Agency",
    tagline: "고객사를 대행하는 조직",
    price: "문의",
    unit: "",
    features: [
      "브랜드·멤버 무제한",
      "Agency·Client 데이터 격리",
      "고객 검수 포털과 White-label",
      "비용 배부와 자동 프로비저닝",
      "SSO·SCIM과 인증 정책",
      "전용 지원과 운영 리뷰",
    ],
    cta: "문의하기",
  },
];

const CREDIT_TABLE = [
  { item: "블로그 본문 생성", credit: "120 ~ 400" },
  { item: "근거 조사 실행", credit: "40 ~ 180" },
  { item: "품질 검수 1회", credit: "20" },
  { item: "이미지 생성 1장", credit: "60 ~ 150" },
  { item: "대량 생성 1행", credit: "본문 생성과 동일" },
  { item: "콘텐츠 재활용 1건", credit: "30 ~ 90" },
];

const FAQ = [
  {
    q: "크레딧은 어떻게 소모되나요?",
    a: "작업을 시작할 때 예상 비용만큼 홀드하고, 완료 시점에 실제 사용량으로 확정합니다. 실패하거나 취소한 작업의 홀드는 반환됩니다.",
  },
  {
    q: "금액 결제와 크레딧은 어떻게 다른가요?",
    a: "금전 원장과 크레딧 원장은 분리되어 있습니다. 구독 요금은 금전으로, 생성·미디어·대량·재활용 사용량은 크레딧으로 정산됩니다.",
  },
  {
    q: "요금제를 바꾸면 어떻게 되나요?",
    a: "요금제와 구독은 버전으로 고정됩니다. 변경은 예약된 시점에 적용되고, 이전 버전의 계약 조건이 이력으로 남습니다.",
  },
  {
    q: "네이버 블로그도 자동 발행되나요?",
    a: "아니요. 네이버는 공식 자동 게시 API를 제공하지 않아 무인 게시를 지원하지 않습니다. 대신 수동 발행 패키지와 체크리스트를 제공합니다.",
  },
];

export default function PricingPage() {
  return (
    <>
      <Section className="pt-16 pb-12 text-center sm:pt-24">
        <div className="shell">
          <Reveal>
            <p className="type-eyebrow">요금제</p>
            <h1 className="type-display mt-4">쓰는 만큼.</h1>
            <p className="type-subhead mx-auto mt-6 max-w-[34rem]">
              구독으로 팀 규모와 거버넌스를, 크레딧으로 실제 생성량을
              정산합니다.
            </p>
          </Reveal>
        </div>
      </Section>

      <Section className="pt-0 pb-20 sm:pt-0">
        <div className="shell grid gap-4 lg:grid-cols-3">
          {PLANS.map((plan, index) => (
            <Reveal key={plan.name} delay={index * 70}>
              <Tile
                className={cn(
                  "flex h-full flex-col",
                  plan.featured &&
                    "border-2 border-[var(--accent)] shadow-[var(--shadow-card)]",
                )}
              >
                {plan.featured ? (
                  <span className="mb-4 inline-flex w-fit rounded-full bg-[var(--accent-soft)] px-3 py-1 text-[12px] font-medium text-[var(--accent-link)]">
                    가장 많이 선택
                  </span>
                ) : null}
                <h2 className="type-title">{plan.name}</h2>
                <p className="mt-1 text-[14px] text-[var(--text-secondary)]">
                  {plan.tagline}
                </p>
                <p className="mt-6 flex items-baseline gap-1">
                  <span className="numeric text-[34px] font-semibold tracking-[-0.03em]">
                    {plan.price}
                  </span>
                  {plan.unit ? (
                    <span className="text-[14px] text-[var(--text-tertiary)]">
                      / {plan.unit}
                    </span>
                  ) : null}
                </p>

                <ul className="mt-6 flex flex-1 flex-col gap-2.5 border-t border-[var(--hairline-soft)] pt-6 text-[14px] text-[var(--text-secondary)]">
                  {plan.features.map((feature) => (
                    <li key={feature} className="flex gap-2">
                      <span
                        aria-hidden
                        className="mt-[3px] shrink-0 text-[var(--accent)]"
                      >
                        <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
                          <path
                            d="M2 7.5 5.5 11 12 3.5"
                            stroke="currentColor"
                            strokeWidth="1.8"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      </span>
                      {feature}
                    </li>
                  ))}
                </ul>

                <ButtonLink
                  href="/signup"
                  variant={plan.featured ? "primary" : "secondary"}
                  className="mt-8 w-full"
                >
                  {plan.cta}
                </ButtonLink>
              </Tile>
            </Reveal>
          ))}
        </div>
      </Section>

      <Section tone="alt">
        <div className="shell grid gap-12 lg:grid-cols-[1fr_1.2fr]">
          <Reveal>
            <p className="type-eyebrow">크레딧</p>
            <h2 className="type-headline mt-3">작업 단위로 정산합니다.</h2>
            <p className="type-subhead mt-5">
              모델과 길이, 근거 조사 범위에 따라 소모량이 달라집니다. 작업을
              시작하기 전에 예상 비용을 먼저 보여 드립니다.
            </p>
            <ChevronLink href="/console/billing" className="mt-6 inline-flex">
              사용량 확인하기
            </ChevronLink>
          </Reveal>

          <Reveal delay={80}>
            <div className="overflow-hidden rounded-[18px] border border-[var(--hairline-soft)] bg-[var(--surface-raised)]">
              {CREDIT_TABLE.map((row) => (
                <div
                  key={row.item}
                  className="flex items-center justify-between border-b border-[var(--hairline-soft)] px-5 py-4 last:border-b-0"
                >
                  <span className="text-[15px]">{row.item}</span>
                  <span className="numeric text-[14px] text-[var(--text-secondary)]">
                    {row.credit}
                  </span>
                </div>
              ))}
            </div>
          </Reveal>
        </div>
      </Section>

      <Section>
        <div className="shell max-w-3xl">
          <Reveal className="mb-10">
            <h2 className="type-headline">자주 묻는 질문</h2>
          </Reveal>
          <div className="flex flex-col">
            {FAQ.map((item, index) => (
              <Reveal key={item.q} delay={index * 50}>
                <details className="group border-b border-[var(--hairline-soft)] py-5">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-[17px] font-medium tracking-[-0.02em] [&::-webkit-details-marker]:hidden">
                    {item.q}
                    <span
                      aria-hidden
                      className="shrink-0 text-[var(--text-tertiary)] transition-transform duration-300 [transition-timing-function:var(--ease-apple)] group-open:rotate-45"
                    >
                      <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                        <path
                          d="M7 1v12M1 7h12"
                          stroke="currentColor"
                          strokeWidth="1.6"
                          strokeLinecap="round"
                        />
                      </svg>
                    </span>
                  </summary>
                  <p className="mt-3 text-[15px] leading-relaxed text-[var(--text-secondary)]">
                    {item.a}
                  </p>
                </details>
              </Reveal>
            ))}
          </div>
        </div>
      </Section>
    </>
  );
}
