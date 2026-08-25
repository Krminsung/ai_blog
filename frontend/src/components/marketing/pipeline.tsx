import { cn } from "@/lib/cn";

/**
 * The nine-stage operating pipeline. Rendered as a horizontally scrollable
 * rail on narrow screens and a full row on wide ones — the same pattern Apple
 * uses for spec strips.
 */
const STAGES = [
  { step: "01", title: "브랜드·자료", body: "브랜드 보이스, 상품 사실, 페르소나를 불변 스냅샷으로 고정합니다." },
  { step: "02", title: "키워드", body: "공식·계약 출처만으로 수집하고 의도와 추세를 함께 판정합니다." },
  { step: "03", title: "기획", body: "캠페인, 토픽 트리, 콘텐츠 브리프를 버전으로 관리합니다." },
  { step: "04", title: "생성", body: "승인된 브리프와 고정된 입력 스냅샷으로만 초안을 만듭니다." },
  { step: "05", title: "근거", body: "주장마다 공식 출처를 연결하고 최신성과 권리를 확인합니다." },
  { step: "06", title: "품질", body: "7요소 산식으로 점수화하고 정책 위반은 예외 없이 차단합니다." },
  { step: "07", title: "승인", body: "콘텐츠 버전과 해시까지 고정하는 다단계 정족수 승인." },
  { step: "08", title: "발행", body: "공식 API로 멱등 발행하고 원격 변경을 지속 대조합니다." },
  { step: "09", title: "성과", body: "공식 분석 지표로 전환과 ROI를 집계하고 재활용을 제안합니다." },
];

export function Pipeline({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "no-scrollbar -mx-[22px] flex snap-x snap-mandatory gap-4 overflow-x-auto px-[22px] pb-2",
        "lg:mx-0 lg:grid lg:grid-cols-3 lg:overflow-visible lg:px-0",
        className,
      )}
    >
      {STAGES.map((stage) => (
        <article
          key={stage.step}
          className={cn(
            "w-[268px] shrink-0 snap-start rounded-[18px] border p-6 lg:w-auto",
            "border-[var(--hairline-soft)] bg-[var(--surface-raised)]",
          )}
        >
          <p className="numeric text-[12px] font-semibold tracking-[0.06em] text-[var(--accent-link)]">
            {stage.step}
          </p>
          <h3 className="mt-2 text-[19px] font-semibold tracking-[-0.02em]">
            {stage.title}
          </h3>
          <p className="mt-2 text-[14px] leading-relaxed text-[var(--text-secondary)]">
            {stage.body}
          </p>
        </article>
      ))}
    </div>
  );
}
