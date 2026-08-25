"use client";

import { AsyncSection, PageHeader } from "@/components/console/page-parts";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { Mono } from "@/components/ui/table";
import { privacy } from "@/lib/api/endpoints";
import { formatDateTime, humanizeEnum } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";

/**
 * Data-protection surface: subject requests, retention policies, legal holds
 * and subprocessors. Read-only here — the destructive actions live behind
 * two-person approval on the backend.
 */
export function PrivacyView() {
  const requests = useApi("privacy-requests", () =>
    privacy.requests({ limit: 50 }),
  );
  const policies = useApi("retention-policies", () =>
    privacy.retentionPolicies(),
  );
  const holds = useApi("legal-holds", () => privacy.legalHolds({ limit: 50 }));
  const subprocessors = useApi("subprocessors", () => privacy.subprocessors());

  return (
    <>
      <PageHeader
        title="개인정보"
        description="정보주체 요청, 보존 정책, Legal Hold와 하위처리자 이력을 한 곳에서 확인합니다."
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="정보주체 요청"
            description="본인 확인을 거친 뒤 처리되고, 단계마다 증거가 남습니다."
          />
          <CardBody>
            <AsyncSection
              data={requests.data}
              error={requests.error}
              errorText={requests.errorText}
              isLoading={requests.isLoading}
              onRetry={() => void requests.mutate()}
              isEmpty={(data) => data.length === 0}
              empty={{ title: "접수된 요청이 없습니다" }}
              skeletonRows={3}
            >
              {(rows) => (
                <ul className="flex flex-col gap-2">
                  {rows.map((request, index) => (
                    <li
                      key={String(request.id ?? index)}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                    >
                      <span className="min-w-0">
                        <span className="block text-[14px]">
                          {humanizeEnum(String(request.request_type ?? request.kind ?? ""))}
                        </span>
                        <span className="type-caption">
                          {formatDateTime(String(request.created_at ?? ""))}
                        </span>
                      </span>
                      <Badge>{humanizeEnum(String(request.status ?? ""))}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </AsyncSection>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="보존 정책"
            description="정책에 도달한 데이터는 정기 스윕으로 정리됩니다."
          />
          <CardBody>
            <AsyncSection
              data={policies.data}
              error={policies.error}
              errorText={policies.errorText}
              isLoading={policies.isLoading}
              onRetry={() => void policies.mutate()}
              isEmpty={(data) => data.length === 0}
              empty={{ title: "설정된 보존 정책이 없습니다" }}
              skeletonRows={3}
            >
              {(rows) => (
                <ul className="flex flex-col gap-2">
                  {rows.map((policy, index) => (
                    <li
                      key={String(policy.id ?? index)}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                    >
                      <Mono>{String(policy.data_class ?? policy.scope ?? "")}</Mono>
                      <span className="numeric text-[13px]">
                        {String(policy.retention_days ?? policy.days ?? "—")}일
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </AsyncSection>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Legal Hold"
            description="홀드 중인 데이터는 보존 정책보다 우선해 유지됩니다."
          />
          <CardBody>
            <AsyncSection
              data={holds.data}
              error={holds.error}
              errorText={holds.errorText}
              isLoading={holds.isLoading}
              onRetry={() => void holds.mutate()}
              isEmpty={(data) => data.length === 0}
              empty={{ title: "설정된 Legal Hold가 없습니다" }}
              skeletonRows={2}
            >
              {(rows) => (
                <ul className="flex flex-col gap-2">
                  {rows.map((hold, index) => (
                    <li
                      key={String(hold.id ?? index)}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                    >
                      <span className="min-w-0 truncate text-[13.5px]">
                        {String(hold.reason ?? hold.name ?? "홀드")}
                      </span>
                      <Badge tone={hold.released_at ? "neutral" : "caution"}>
                        {hold.released_at ? "해제됨" : "적용 중"}
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </AsyncSection>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="하위처리자"
            description="데이터를 처리하는 외부 공급자 목록입니다."
          />
          <CardBody>
            <AsyncSection
              data={subprocessors.data}
              error={subprocessors.error}
              errorText={subprocessors.errorText}
              isLoading={subprocessors.isLoading}
              onRetry={() => void subprocessors.mutate()}
              isEmpty={(data) => data.length === 0}
              empty={{ title: "등록된 하위처리자가 없습니다" }}
              skeletonRows={3}
            >
              {(rows) => (
                <ul className="flex flex-col gap-2">
                  {rows.map((item, index) => (
                    <li
                      key={String(item.id ?? index)}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                    >
                      <span className="min-w-0">
                        <span className="block text-[14px]">
                          {String(item.name ?? "")}
                        </span>
                        <span className="type-caption">
                          {String(item.purpose ?? item.role ?? "")}
                        </span>
                      </span>
                      <span className="type-caption shrink-0">
                        {String(item.region ?? item.location ?? "")}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </AsyncSection>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
