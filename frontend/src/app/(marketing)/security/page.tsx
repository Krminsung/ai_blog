import type { Metadata } from "next";

import { ButtonLink } from "@/components/ui/button";
import { Reveal } from "@/components/ui/reveal";
import { Section, Tile } from "@/components/ui/surface";

export const metadata: Metadata = {
  title: "보안과 규정 준수",
  description:
    "테넌트 격리, 인증, 감사 로그, 보존 정책과 정보주체 요청, 사고 대응까지. BlogOps AI의 보안 기반.",
};

const PILLARS = [
  {
    title: "테넌트 격리",
    body: "PostgreSQL Row Level Security로 워크스페이스 경계를 데이터베이스 계층에서 강제합니다. 대행사와 고객 데이터도 같은 방식으로 분리됩니다.",
  },
  {
    title: "인증",
    body: "Argon2id 비밀번호 해싱, 회전 Refresh Token, TOTP 기반 MFA, 세션과 기기 관리를 제공합니다. 워크스페이스 인증 정책을 별도로 강제할 수 있습니다.",
  },
  {
    title: "감사",
    body: "권한이 있는 행위는 불변 감사 로그로 남습니다. 관리자 작업은 별도의 관리자 감사 경로를 사용하고, 2인 승인 운영 명령을 지원합니다.",
  },
  {
    title: "무결성",
    body: "멱등성 레코드와 Transactional Outbox로 중복 실행과 유실을 막습니다. 콘텐츠 버전과 해시는 승인 시점에 외래키로 고정됩니다.",
  },
];

const PRIVACY = [
  {
    title: "보존과 삭제",
    points: [
      "데이터 종류별 보존 정책과 정기 스윕",
      "Legal Hold 설정과 해제 이력",
      "삭제 증명과 백업 소거 증거",
    ],
  },
  {
    title: "정보주체 요청",
    points: [
      "열람·정정·삭제 요청 접수와 본인 확인",
      "안전한 Export 다운로드 경로",
      "요청 처리 단계별 감사 기록",
    ],
  },
  {
    title: "투명성",
    points: [
      "하위처리자 목록과 변경 이력",
      "고객 동의형 지원 접근",
      "접근 이벤트 조회",
    ],
  },
];

const OPERATIONS = [
  { title: "공개 상태 페이지", body: "구성 요소별 상태와 장애 타임라인을 공개합니다." },
  { title: "백업과 복구 훈련", body: "백업 정책과 복구 훈련 결과를 증거와 함께 보관합니다." },
  { title: "사고 대응", body: "보안 사고 기록, 침해 통지, 컴플라이언스 증거를 관리합니다." },
  { title: "저작권 대응", body: "신고 접수, 판단, Counter Notice 흐름을 지원합니다." },
];

export default function SecurityPage() {
  return (
    <>
      <Section className="pt-16 pb-16 text-center sm:pt-24">
        <div className="shell">
          <Reveal>
            <p className="type-eyebrow">보안</p>
            <h1 className="type-display mt-4">
              기본값이
              <br />
              안전합니다.
            </h1>
            <p className="type-subhead mx-auto mt-6 max-w-[36rem]">
              격리, 인증, 감사, 무결성은 선택 기능이 아닙니다. 플랫폼의 기본
              동작으로 들어가 있습니다.
            </p>
          </Reveal>
        </div>
      </Section>

      <Section tone="alt" className="pt-0 sm:pt-0">
        <div className="shell grid gap-4 md:grid-cols-2">
          {PILLARS.map((pillar, index) => (
            <Reveal key={pillar.title} delay={index * 60}>
              <Tile className="h-full bg-[var(--surface-raised)]">
                <h2 className="type-title">{pillar.title}</h2>
                <p className="mt-3 text-[15px] leading-relaxed text-[var(--text-secondary)]">
                  {pillar.body}
                </p>
              </Tile>
            </Reveal>
          ))}
        </div>
      </Section>

      <Section id="privacy">
        <div className="shell">
          <Reveal className="mb-12 max-w-2xl">
            <p className="type-eyebrow">개인정보 보호</p>
            <h2 className="type-headline mt-3">요청에 답할 수 있습니다.</h2>
            <p className="type-subhead mt-5">
              보존 기한이 지난 데이터는 정책에 따라 정리되고, 정보주체 요청은
              증거를 남기며 처리됩니다.
            </p>
          </Reveal>

          <div className="grid gap-4 md:grid-cols-3">
            {PRIVACY.map((group, index) => (
              <Reveal key={group.title} delay={index * 60}>
                <Tile className="h-full">
                  <h3 className="type-title">{group.title}</h3>
                  <ul className="mt-5 flex flex-col gap-2 text-[14px] text-[var(--text-secondary)]">
                    {group.points.map((point) => (
                      <li key={point}>· {point}</li>
                    ))}
                  </ul>
                </Tile>
              </Reveal>
            ))}
          </div>
        </div>
      </Section>

      <Section tone="dark">
        <div className="shell">
          <Reveal className="mb-12 max-w-2xl">
            <p className="type-eyebrow text-[#86868b]">운영</p>
            <h2 className="type-headline mt-3">숨기지 않습니다.</h2>
          </Reveal>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {OPERATIONS.map((item, index) => (
              <Reveal key={item.title} delay={index * 60}>
                <div className="h-full rounded-[18px] border border-white/12 p-6">
                  <h3 className="text-[17px] font-semibold tracking-[-0.02em]">
                    {item.title}
                  </h3>
                  <p className="mt-2 text-[14px] leading-relaxed text-[#a1a1a6]">
                    {item.body}
                  </p>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </Section>

      <Section className="py-24 text-center">
        <div className="shell">
          <Reveal>
            <h2 className="type-headline">서비스 상태를 확인하세요.</h2>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-4">
              <ButtonLink href="/status" size="lg" variant="secondary">
                상태 페이지
              </ButtonLink>
              <ButtonLink href="/signup" size="lg">
                시작하기
              </ButtonLink>
            </div>
          </Reveal>
        </div>
      </Section>
    </>
  );
}
