import type { Metadata } from "next";

import { ButtonLink, ChevronLink } from "@/components/ui/button";
import { Reveal } from "@/components/ui/reveal";
import { Section, Tile } from "@/components/ui/surface";
import { Pipeline } from "@/components/marketing/pipeline";
import {
  ChannelGrid,
  ConsoleMock,
  EvidenceChain,
  QualityScoreVisual,
} from "@/components/marketing/visuals";

export const metadata: Metadata = {
  title: "근거 기반 콘텐츠 운영 플랫폼",
  description:
    "브랜드 자료 고정부터 키워드, 기획, 생성, 품질 검수, 승인, 공식 채널 발행, 성과 분석까지. BlogOps AI는 모든 단계에 근거와 이력을 남깁니다.",
};

export default function HomePage() {
  return (
    <>
      {/* ---------------------------------------------------------- hero */}
      <section className="relative overflow-hidden pt-16 pb-20 text-center sm:pt-24 sm:pb-28">
        <div className="shell">
          <Reveal>
            <p className="type-eyebrow">BlogOps AI</p>
            <h1 className="type-display mt-4">
              콘텐츠를
              <br />
              운영하세요.
            </h1>
            <p className="type-subhead mx-auto mt-6 max-w-[34rem]">
              기획, 생성, 검수, 승인, 발행, 성과를 하나의 흐름으로. 모든 문장
              뒤에 출처가, 모든 발행 뒤에 승인 이력이 남습니다.
            </p>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-x-8 gap-y-4">
              <ButtonLink href="/signup" size="lg">
                무료로 시작하기
              </ButtonLink>
              <ChevronLink href="/product">제품 살펴보기</ChevronLink>
            </div>
          </Reveal>

          <Reveal delay={120}>
            <ConsoleMock className="mt-16 text-left" />
          </Reveal>
        </div>

        {/* Soft accent wash behind the hero, no hard edges. */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 -z-1 h-[560px] opacity-45"
          style={{
            background:
              "radial-gradient(52% 52% at 50% -8%, var(--accent-soft) 0%, transparent 72%)",
          }}
        />
      </section>

      {/* ------------------------------------------------------- numbers */}
      <Section tone="alt" className="py-16 sm:py-20">
        <div className="shell">
          <Reveal className="grid gap-8 text-center sm:grid-cols-2 lg:grid-cols-4">
            {[
              { value: "50종", label: "콘텐츠 유형별 입력·안전 계약" },
              { value: "7요소", label: "설명 가능한 품질 산식" },
              { value: "14종", label: "승인 게이트 기반 재활용" },
              { value: "5채널", label: "공식 API 발행 대상" },
            ].map((stat) => (
              <div key={stat.label}>
                <p className="numeric type-headline text-[var(--text-primary)]">
                  {stat.value}
                </p>
                <p className="mt-2 text-[14px] text-[var(--text-secondary)]">
                  {stat.label}
                </p>
              </div>
            ))}
          </Reveal>
        </div>
      </Section>

      {/* ------------------------------------------------------ pipeline */}
      <Section>
        <div className="shell">
          <Reveal className="mb-12 max-w-2xl">
            <p className="type-eyebrow">운영 파이프라인</p>
            <h2 className="type-headline mt-3">
              아홉 단계가
              <br />
              끊기지 않습니다.
            </h2>
            <p className="type-subhead mt-5">
              각 단계는 앞 단계의 결과를 불변 스냅샷으로 받아옵니다. 자료가
              바뀌면 승인이 자동으로 무효화되고, 다시 검수를 거칩니다.
            </p>
          </Reveal>
          <Reveal delay={80}>
            <Pipeline />
          </Reveal>
        </div>
      </Section>

      {/* ------------------------------------------------------- quality */}
      <Section tone="alt" id="quality">
        <div className="shell grid items-center gap-12 lg:grid-cols-2">
          <Reveal>
            <p className="type-eyebrow">품질</p>
            <h2 className="type-headline mt-3">
              점수의 근거까지
              <br />
              보여줍니다.
            </h2>
            <p className="type-subhead mt-5">
              형태소, 자연스러움, SEO, 중복, 팩트·인용, 브랜드 준수, 안전 정책.
              일곱 요소의 가중치와 기여도를 그대로 공개하고, 사용된 분석기와
              사전 버전을 함께 고정합니다.
            </p>
            <p className="mt-4 text-[15px] leading-relaxed text-[var(--text-secondary)]">
              안전 정책 위반은 예외 승인이 불가능한 Hard Block으로 처리됩니다.
            </p>
            <ChevronLink href="/product#quality" className="mt-6 inline-flex">
              품질 검수 자세히 보기
            </ChevronLink>
          </Reveal>

          <Reveal delay={100}>
            <Tile className="bg-[var(--surface-raised)]">
              <div className="mb-6 flex items-baseline justify-between">
                <span className="text-[13px] text-[var(--text-secondary)]">
                  종합 점수
                </span>
                <span className="numeric text-[40px] leading-none font-semibold tracking-[-0.03em]">
                  91.2
                </span>
              </div>
              <QualityScoreVisual />
            </Tile>
          </Reveal>
        </div>
      </Section>

      {/* ------------------------------------------------------ evidence */}
      <Section>
        <div className="shell grid items-center gap-12 lg:grid-cols-2">
          <Reveal className="lg:order-2">
            <p className="type-eyebrow">근거</p>
            <h2 className="type-headline mt-3">
              모든 주장에
              <br />
              출처가 있습니다.
            </h2>
            <p className="type-subhead mt-5">
              공식·계약·사용자 제공 출처만 사용합니다. 문장 단위로 주장을
              분해해 인용을 연결하고, 자료의 최신성과 사용 권리를 함께
              판정합니다.
            </p>
            <ChevronLink href="/workflow" className="mt-6 inline-flex">
              워크플로 살펴보기
            </ChevronLink>
          </Reveal>

          <Reveal delay={100} className="lg:order-1">
            <Tile tone="alt">
              <EvidenceChain />
            </Tile>
          </Reveal>
        </div>
      </Section>

      {/* ---------------------------------------------------- publishing */}
      <Section tone="dark" id="publishing">
        <div className="shell text-center">
          <Reveal>
            <p className="type-eyebrow text-[#86868b]">발행</p>
            <h2 className="type-headline mt-3">공식 API로만.</h2>
            <p className="type-subhead mx-auto mt-5 max-w-[36rem] text-[#a1a1a6]">
              WordPress, Ghost, Blogger, 승인된 고객 CMS에 멱등 Saga로
              발행합니다. 예약은 DST를 고려하고, 원격에서 글이 바뀌면 충돌을
              감지해 복구 경로를 제시합니다.
            </p>
          </Reveal>

          <Reveal delay={80} className="mt-10 flex justify-center">
            <div className="[&_*]:!border-white/12 [&_p:first-child]:!text-white [&_p:last-child]:!text-[#86868b] [&>div>div]:!bg-white/6">
              <ChannelGrid className="justify-center" />
            </div>
          </Reveal>

          <Reveal delay={140}>
            <p className="mx-auto mt-10 max-w-[36rem] rounded-[14px] border border-white/12 px-5 py-4 text-[13px] leading-relaxed text-[#a1a1a6]">
              네이버 블로그는 공식 자동 게시 API가 없습니다. BlogOps는 무인
              게시를 시도하는 대신, 불변 수동 패키지와 체크리스트를 만들어
              담당자가 직접 확인하고 게시하도록 합니다.
            </p>
          </Reveal>
        </div>
      </Section>

      {/* --------------------------------------------------------- bento */}
      <Section tone="alt">
        <div className="shell">
          <Reveal className="mb-12 max-w-2xl">
            <p className="type-eyebrow">플랫폼</p>
            <h2 className="type-headline mt-3">운영에 필요한 전부.</h2>
          </Reveal>

          <div className="grid gap-4 md:grid-cols-3">
            <Reveal className="md:col-span-2">
              <Tile className="h-full">
                <h3 className="type-title">대량 생성과 비용 통제</h3>
                <p className="type-subhead mt-3 text-[15px]">
                  서버에서 검증한 CSV·XLSX 스냅샷으로 수천 행을 생성합니다. 행마다
                  비용을 홀드하고, 예산을 넘기면 Kill Switch가 작업을 멈춥니다.
                </p>
                <div className="mt-6 grid grid-cols-3 gap-3 text-center">
                  {[
                    { k: "처리 행", v: "1,000" },
                    { k: "검수 대기", v: "37" },
                    { k: "홀드 비용", v: "₩412,000" },
                  ].map((item) => (
                    <div
                      key={item.k}
                      className="rounded-[12px] bg-[var(--surface-alt)] px-3 py-4"
                    >
                      <p className="numeric text-[19px] font-semibold">{item.v}</p>
                      <p className="mt-1 text-[12px] text-[var(--text-tertiary)]">
                        {item.k}
                      </p>
                    </div>
                  ))}
                </div>
              </Tile>
            </Reveal>

            <Reveal delay={60}>
              <Tile className="h-full">
                <h3 className="type-title">미디어 권리</h3>
                <p className="type-subhead mt-3 text-[15px]">
                  업로드는 격리 검사, 생성 이미지는 공급자 정책을 고정합니다.
                  채널·지역·용도별 사용 권리를 콘텐츠 버전과 함께 묶습니다.
                </p>
              </Tile>
            </Reveal>

            <Reveal delay={60}>
              <Tile className="h-full">
                <h3 className="type-title">성과와 ROI</h3>
                <p className="type-subhead mt-3 text-[15px]">
                  공식 분석 공급자의 원본 증거와 지표 정의를 고정해 전환과 ROI를
                  계산합니다.
                </p>
              </Tile>
            </Reveal>

            <Reveal delay={120} className="md:col-span-2">
              <Tile className="h-full">
                <h3 className="type-title">대행사와 고객</h3>
                <p className="type-subhead mt-3 text-[15px]">
                  Agency·Client 데이터를 격리하고, 고객 검수 포털과 White-label
                  버전, 비용 배부, 자동 프로비저닝을 제공합니다. 지원 접근은
                  고객 동의와 2인 승인을 거칩니다.
                </p>
              </Tile>
            </Reveal>
          </div>
        </div>
      </Section>

      {/* ----------------------------------------------------------- cta */}
      <Section className="py-24 text-center">
        <div className="shell">
          <Reveal>
            <h2 className="type-headline">지금 시작하세요.</h2>
            <p className="type-subhead mx-auto mt-5 max-w-[30rem]">
              워크스페이스를 만들고 브랜드 자료를 올리면, 첫 브리프까지 몇 분이면
              됩니다.
            </p>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-x-8 gap-y-4">
              <ButtonLink href="/signup" size="lg">
                계정 만들기
              </ButtonLink>
              <ChevronLink href="/pricing">요금제 비교</ChevronLink>
            </div>
          </Reveal>
        </div>
      </Section>
    </>
  );
}
