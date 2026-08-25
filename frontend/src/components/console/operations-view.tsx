"use client";

import { AsyncSection, PageHeader } from "@/components/console/page-parts";
import { Badge, StatusDot } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { Mono } from "@/components/ui/table";
import { operations } from "@/lib/api/endpoints";
import { formatDateTime, formatRelative, humanizeEnum } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";
import type { Tone } from "@/lib/labels";

const COMPONENT_TONE: Record<string, Tone> = {
  OPERATIONAL: "positive",
  DEGRADED: "caution",
  PARTIAL_OUTAGE: "caution",
  MAJOR_OUTAGE: "critical",
  MAINTENANCE: "progress",
};

/** Internal operations view: components, incidents and runbooks. */
export function OperationsView() {
  const components = useApi("ops-components", () => operations.components());
  const incidents = useApi("ops-incidents", () =>
    operations.incidents({ limit: 50 }),
  );
  const runbooks = useApi("ops-runbooks", () => operations.runbooks());

  return (
    <>
      <PageHeader
        title="운영"
        description="구성 요소 상태와 장애 타임라인, 런북을 확인합니다. 공개 상태 페이지는 이 데이터에서 생성됩니다."
      />

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <Card>
          <CardHeader title="구성 요소" />
          <CardBody>
            <AsyncSection
              data={components.data}
              error={components.error}
              errorText={components.errorText}
              isLoading={components.isLoading}
              onRetry={() => void components.mutate()}
              isEmpty={(data) => data.length === 0}
              empty={{ title: "등록된 구성 요소가 없습니다" }}
              skeletonRows={4}
            >
              {(rows) => (
                <ul className="flex flex-col gap-2">
                  {rows.map((component, index) => {
                    const status = String(component.status ?? "UNKNOWN");
                    return (
                      <li
                        key={String(component.id ?? index)}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                      >
                        <span className="flex min-w-0 items-center gap-2.5">
                          <StatusDot tone={COMPONENT_TONE[status] ?? "neutral"} />
                          <span className="min-w-0">
                            <span className="block truncate text-[14px]">
                              {String(component.display_name ?? component.name ?? "")}
                            </span>
                            <Mono>{String(component.component_key ?? "")}</Mono>
                          </span>
                        </span>
                        <Badge tone={COMPONENT_TONE[status] ?? "neutral"}>
                          {humanizeEnum(status)}
                        </Badge>
                      </li>
                    );
                  })}
                </ul>
              )}
            </AsyncSection>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="장애 이력" />
          <CardBody>
            <AsyncSection
              data={incidents.data}
              error={incidents.error}
              errorText={incidents.errorText}
              isLoading={incidents.isLoading}
              onRetry={() => void incidents.mutate()}
              isEmpty={(data) => data.length === 0}
              empty={{
                title: "기록된 장애가 없습니다",
                description: "좋은 소식입니다.",
              }}
              skeletonRows={3}
            >
              {(rows) => (
                <ul className="flex flex-col gap-2">
                  {rows.map((incident, index) => (
                    <li
                      key={String(incident.id ?? index)}
                      className="rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-[14px] font-medium">
                          {String(incident.title ?? "장애")}
                        </span>
                        <Badge
                          tone={
                            incident.resolved_at ? "positive" : "critical"
                          }
                        >
                          {incident.resolved_at ? "복구됨" : "진행 중"}
                        </Badge>
                      </div>
                      <p className="type-caption mt-1">
                        {formatDateTime(String(incident.started_at ?? incident.created_at ?? ""))}
                        {incident.resolved_at
                          ? ` · 복구 ${formatRelative(String(incident.resolved_at))}`
                          : ""}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </AsyncSection>
          </CardBody>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader
          title="런북"
          description="장애 대응 절차입니다. 실행 이력은 감사 로그에 남습니다."
        />
        <CardBody>
          <AsyncSection
            data={runbooks.data}
            error={runbooks.error}
            errorText={runbooks.errorText}
            isLoading={runbooks.isLoading}
            onRetry={() => void runbooks.mutate()}
            isEmpty={(data) => data.length === 0}
            empty={{ title: "등록된 런북이 없습니다" }}
            skeletonRows={2}
          >
            {(rows) => (
              <ul className="grid gap-2 sm:grid-cols-2">
                {rows.map((runbook, index) => (
                  <li
                    key={String(runbook.id ?? index)}
                    className="rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                  >
                    <p className="text-[14px] font-medium">
                      {String(runbook.title ?? runbook.name ?? "런북")}
                    </p>
                    <p className="type-caption mt-0.5">
                      {String(runbook.summary ?? runbook.description ?? "")}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </AsyncSection>
        </CardBody>
      </Card>
    </>
  );
}
