"use client";

import { Badge, StatusDot } from "@/components/ui/badge";
import { Card } from "@/components/ui/surface";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/feedback";
import { system } from "@/lib/api/endpoints";
import { formatRelative } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";
import type { Tone } from "@/lib/labels";

interface StatusRow {
  component: string;
  name: string;
  status: string;
  checked_at: string | null;
  valid_until: string | null;
}

const STATUS_LABELS: Record<string, { label: string; tone: Tone }> = {
  OPERATIONAL: { label: "정상", tone: "positive" },
  DEGRADED: { label: "성능 저하", tone: "caution" },
  PARTIAL_OUTAGE: { label: "부분 장애", tone: "caution" },
  MAJOR_OUTAGE: { label: "장애", tone: "critical" },
  MAINTENANCE: { label: "점검 중", tone: "progress" },
  UNKNOWN: { label: "확인 불가", tone: "neutral" },
};

function describe(status: string) {
  return STATUS_LABELS[status] ?? { label: status, tone: "neutral" as Tone };
}

/**
 * Public status board. Unauthenticated, and polls slowly — the backing probe
 * results only refresh on their own cadence.
 */
export function StatusBoard() {
  const { data, error, errorText, isLoading, mutate } = useApi<StatusRow[]>(
    "public-status",
    async () => (await system.publicStatus()) as unknown as StatusRow[],
    { refreshInterval: 60_000 },
  );

  if (isLoading) return <SkeletonRows rows={4} />;

  if (error) {
    return (
      <ErrorState
        message={
          errorText ??
          "상태 정보를 불러오지 못했습니다. 백엔드 연결을 확인해 주세요."
        }
        requestId={error.requestId}
        onRetry={() => void mutate()}
      />
    );
  }

  const rows = (data ?? []) as StatusRow[];

  if (rows.length === 0) {
    return (
      <EmptyState
        title="공개된 구성 요소가 없습니다"
        description="운영팀이 구성 요소를 등록하면 이곳에 상태가 표시됩니다."
      />
    );
  }

  const worst = rows.some((row) => row.status === "MAJOR_OUTAGE")
    ? "MAJOR_OUTAGE"
    : rows.some((row) => row.status !== "OPERATIONAL")
      ? "DEGRADED"
      : "OPERATIONAL";
  const overall = describe(worst);

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-wrap items-center justify-between gap-3 px-6 py-5">
        <span className="flex items-center gap-3">
          <StatusDot tone={overall.tone} />
          <span className="text-[19px] font-semibold tracking-[-0.02em]">
            {worst === "OPERATIONAL"
              ? "모든 시스템 정상"
              : "일부 구성 요소에 문제가 있습니다"}
          </span>
        </span>
        <Badge tone={overall.tone}>{overall.label}</Badge>
      </Card>

      <Card className="overflow-hidden">
        {rows.map((row) => {
          const spec = describe(row.status);
          return (
            <div
              key={row.component}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--hairline-soft)] px-6 py-4 last:border-b-0"
            >
              <div className="min-w-0">
                <p className="text-[15px] font-medium">
                  {row.name || row.component}
                </p>
                <p className="type-caption mt-0.5">
                  최근 점검 {formatRelative(row.checked_at)}
                </p>
              </div>
              <Badge tone={spec.tone}>{spec.label}</Badge>
            </div>
          );
        })}
      </Card>
    </div>
  );
}
