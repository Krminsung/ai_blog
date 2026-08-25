"use client";

import { AsyncSection, PageHeader } from "@/components/console/page-parts";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { Notice } from "@/components/ui/feedback";
import { analytics } from "@/lib/api/endpoints";
import { formatDateTime, humanizeEnum } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";

const STATE_TONE: Record<string, "positive" | "caution" | "critical" | "neutral"> = {
  ACTIVE: "positive",
  CONNECTED: "positive",
  PENDING: "caution",
  DEGRADED: "caution",
  EXPIRED: "critical",
  DISCONNECTED: "neutral",
};

/**
 * Analytics connections and their freshness. The product refuses to compute
 * ROI from unofficial sources, so an unconnected workspace shows guidance
 * rather than fabricated numbers.
 */
export function AnalyticsView() {
  const connections = useApi("analytics-connections", () =>
    analytics.connections(),
  );

  return (
    <>
      <PageHeader
        title="성과 분석"
        description="공식 분석 공급자의 원본 증거와 지표 정의를 고정해 전환과 ROI를 계산합니다."
      />

      <Notice tone="info" className="mb-4">
        성과 지표는 공급자가 데이터를 확정한 뒤에 집계됩니다. 공급자마다 확정
        지연이 다르므로, 최근 구간은 값이 바뀔 수 있습니다.
      </Notice>

      <Card>
        <CardHeader
          title="연결된 분석 공급자"
          description="연결이 없으면 성과 리포트를 만들 수 없습니다."
        />
        <CardBody>
          <AsyncSection
            data={connections.data}
            error={connections.error}
            errorText={connections.errorText}
            isLoading={connections.isLoading}
            onRetry={() => void connections.mutate()}
            isEmpty={(data) => data.length === 0}
            empty={{
              title: "연결된 분석 공급자가 없습니다",
              description:
                "검색 콘솔이나 웹 분석 계정을 연결하면 발행된 글의 성과를 추적할 수 있습니다.",
            }}
          >
            {(rows) => (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {rows.map((connection) => (
                  <div
                    key={connection.id}
                    className="rounded-[14px] border border-[var(--hairline-soft)] p-4"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-[15px] font-medium">
                          {connection.name}
                        </p>
                        <p className="type-caption mt-0.5 truncate">
                          {connection.site_url ?? connection.external_property_id}
                        </p>
                      </div>
                      <Badge tone={STATE_TONE[connection.state] ?? "neutral"}>
                        {humanizeEnum(connection.state)}
                      </Badge>
                    </div>

                    <div className="mt-3 flex flex-wrap gap-1.5">
                      <Badge>{connection.provider}</Badge>
                      <Badge>API {connection.api_version}</Badge>
                    </div>

                    <dl className="mt-3 flex flex-col gap-1.5 border-t border-[var(--hairline-soft)] pt-3 text-[12.5px]">
                      <div className="flex justify-between gap-2">
                        <dt className="text-[var(--text-tertiary)]">최근 동기화</dt>
                        <dd>{formatDateTime(connection.last_synced_at)}</dd>
                      </div>
                      {connection.last_error_code ? (
                        <div className="flex justify-between gap-2">
                          <dt className="text-[var(--text-tertiary)]">오류</dt>
                          <dd className="text-[var(--critical)]">
                            {connection.last_error_code}
                          </dd>
                        </div>
                      ) : null}
                    </dl>
                  </div>
                ))}
              </div>
            )}
          </AsyncSection>
        </CardBody>
      </Card>
    </>
  );
}
