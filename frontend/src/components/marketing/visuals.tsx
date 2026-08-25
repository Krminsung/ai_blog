import { cn } from "@/lib/cn";

/**
 * Product visuals, drawn rather than photographed. Each one is an abstracted
 * view of a real console surface, so the marketing page shows the actual
 * information architecture instead of stock art.
 */

/** Hero visual: a floating console window with the pipeline mid-flight. */
export function ConsoleMock({ className }: { className?: string }) {
  const rows = [
    { title: "가을 신제품 런칭 가이드", state: "발행됨", tone: "positive", score: "92.4" },
    { title: "성분 비교: A vs B 라인", state: "승인 대기", tone: "caution", score: "88.1" },
    { title: "겨울 케어 루틴 3단계", state: "생성 중", tone: "progress", score: "—" },
    { title: "고객 리뷰 기반 Q&A", state: "품질 차단", tone: "critical", score: "61.7" },
  ];

  return (
    <div
      className={cn(
        "overflow-hidden rounded-[20px] border border-[var(--hairline-soft)]",
        "bg-[var(--surface-raised)] shadow-[var(--shadow-float)]",
        className,
      )}
      aria-hidden
    >
      {/* Window chrome */}
      <div className="flex items-center gap-2 border-b border-[var(--hairline-soft)] bg-[var(--surface-sunken)] px-4 py-3">
        <span className="size-2.5 rounded-full bg-[#ff5f57]" />
        <span className="size-2.5 rounded-full bg-[#febc2e]" />
        <span className="size-2.5 rounded-full bg-[#28c840]" />
        <span className="ml-3 truncate text-[11px] text-[var(--text-tertiary)]">
          BlogOps 콘솔 — 콘텐츠 보관함
        </span>
      </div>

      <div className="grid grid-cols-[132px_1fr] max-sm:grid-cols-1">
        <div className="border-r border-[var(--hairline-soft)] p-3 max-sm:hidden">
          {["개요", "기획", "콘텐츠", "품질", "발행", "성과"].map((item, index) => (
            <div
              key={item}
              className={cn(
                "mb-0.5 rounded-[7px] px-2.5 py-1.5 text-[11px]",
                index === 2
                  ? "bg-[var(--accent-soft)] font-medium text-[var(--accent-link)]"
                  : "text-[var(--text-secondary)]",
              )}
            >
              {item}
            </div>
          ))}
        </div>

        <div className="p-4">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-[12px] font-semibold">최근 콘텐츠</span>
            <span className="rounded-full bg-[var(--surface-alt)] px-2 py-0.5 text-[10px] text-[var(--text-secondary)]">
              4건
            </span>
          </div>
          <div className="flex flex-col gap-1.5">
            {rows.map((row) => (
              <div
                key={row.title}
                className="flex items-center gap-3 rounded-[10px] border border-[var(--hairline-soft)] px-3 py-2.5"
              >
                <span className="min-w-0 flex-1 truncate text-[11.5px]">
                  {row.title}
                </span>
                <span className="numeric hidden text-[11px] text-[var(--text-tertiary)] sm:inline">
                  {row.score}
                </span>
                <StatePill tone={row.tone as Tone}>{row.state}</StatePill>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

type Tone = "positive" | "caution" | "progress" | "critical" | "neutral";

function StatePill({ tone, children }: { tone: Tone; children: string }) {
  const styles: Record<Tone, string> = {
    positive: "bg-[var(--positive-soft)] text-[var(--positive)]",
    caution: "bg-[var(--caution-soft)] text-[var(--caution)]",
    progress: "bg-[var(--accent-soft)] text-[var(--accent-link)]",
    critical: "bg-[var(--critical-soft)] text-[var(--critical)]",
    neutral: "bg-[var(--surface-alt)] text-[var(--text-secondary)]",
  };
  return (
    <span
      className={cn(
        "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium whitespace-nowrap",
        styles[tone],
      )}
    >
      {children}
    </span>
  );
}

/** The seven-element quality formula, drawn as weighted bars. */
export function QualityScoreVisual({ className }: { className?: string }) {
  const components = [
    { name: "형태소·문법", weight: 18, value: 94 },
    { name: "자연스러움", weight: 20, value: 89 },
    { name: "SEO 적합도", weight: 16, value: 96 },
    { name: "중복도", weight: 14, value: 82 },
    { name: "팩트·인용", weight: 18, value: 91 },
    { name: "브랜드 준수", weight: 8, value: 97 },
    { name: "안전 정책", weight: 6, value: 100 },
  ];

  return (
    <div className={cn("flex flex-col gap-3", className)} aria-hidden>
      {components.map((component, index) => (
        <div key={component.name} className="flex items-center gap-3">
          <span className="w-24 shrink-0 text-[12px] text-[var(--text-secondary)]">
            {component.name}
          </span>
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--surface-alt)]">
            <span
              className="block h-full rounded-full bg-[var(--accent)]"
              style={{
                width: `${component.value}%`,
                // Stagger the fill so the bars cascade when revealed.
                transitionDelay: `${index * 60}ms`,
              }}
            />
          </span>
          <span className="numeric w-9 shrink-0 text-right text-[12px] text-[var(--text-tertiary)]">
            {component.weight}%
          </span>
        </div>
      ))}
    </div>
  );
}

/** Evidence chain: source → claim → citation → approval. */
export function EvidenceChain({ className }: { className?: string }) {
  const steps = [
    { label: "공식 출처", detail: "계약·1차 자료만" },
    { label: "주장 추출", detail: "문장 단위 분해" },
    { label: "인용 연결", detail: "출처·최신성 판정" },
    { label: "승인 증명", detail: "버전 해시 고정" },
  ];

  return (
    <ol className={cn("flex flex-col gap-0", className)}>
      {steps.map((step, index) => (
        <li key={step.label} className="flex gap-4">
          <div className="flex flex-col items-center">
            <span className="grid size-7 shrink-0 place-items-center rounded-full bg-[var(--accent)] text-[11px] font-semibold text-white">
              {index + 1}
            </span>
            {index < steps.length - 1 ? (
              <span className="w-px flex-1 bg-[var(--hairline)]" />
            ) : null}
          </div>
          <div className={cn("pb-6", index === steps.length - 1 && "pb-0")}>
            <p className="text-[15px] font-medium">{step.label}</p>
            <p className="text-[13px] text-[var(--text-secondary)]">
              {step.detail}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

/** Publishing targets, as connection chips. */
export function ChannelGrid({ className }: { className?: string }) {
  const channels = [
    { name: "WordPress", note: "REST API · 멱등 발행" },
    { name: "Ghost", note: "Admin API · 예약" },
    { name: "Blogger", note: "공식 API · 동기화" },
    { name: "고객 CMS", note: "승인된 엔드포인트" },
    { name: "네이버", note: "수동 패키지" },
  ];

  return (
    <div className={cn("flex flex-wrap gap-2", className)}>
      {channels.map((channel) => (
        <div
          key={channel.name}
          className="rounded-[14px] border border-[var(--hairline-soft)] bg-[var(--surface-raised)] px-4 py-3"
        >
          <p className="text-[14px] font-medium">{channel.name}</p>
          <p className="mt-0.5 text-[12px] text-[var(--text-tertiary)]">
            {channel.note}
          </p>
        </div>
      ))}
    </div>
  );
}
