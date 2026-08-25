import type { Metadata } from "next";

import { StatusBoard } from "@/components/marketing/status-board";
import { Reveal } from "@/components/ui/reveal";
import { Section } from "@/components/ui/surface";

export const metadata: Metadata = {
  title: "서비스 상태",
  description:
    "BlogOps AI 구성 요소의 현재 상태. 공개 상태 함수가 반환하는 실시간 값을 그대로 표시합니다.",
};

export default function StatusPage() {
  return (
    <>
      <Section className="pt-16 pb-10 text-center sm:pt-24">
        <div className="shell">
          <Reveal>
            <p className="type-eyebrow">상태</p>
            <h1 className="type-display mt-4">서비스 상태</h1>
            <p className="type-subhead mx-auto mt-6 max-w-[32rem]">
              구성 요소별 최신 점검 결과입니다. 값은 백엔드의 공개 상태
              엔드포인트에서 직접 가져옵니다.
            </p>
          </Reveal>
        </div>
      </Section>

      <Section className="pt-0 pb-24 sm:pt-0">
        <div className="shell">
          <StatusBoard />
        </div>
      </Section>
    </>
  );
}
