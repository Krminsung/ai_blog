"use client";

import Link from "next/link";

import { AsyncSection, PageHeader, StatCard } from "@/components/console/page-parts";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { Mono, Table, TableWrap, Td, Th, Tr } from "@/components/ui/table";
import { quality } from "@/lib/api/endpoints";
import { formatDateTime, formatDecimal, shortHash } from "@/lib/format";
import { labelFor } from "@/lib/labels";
import { useApi } from "@/lib/hooks/use-query";

/**
 * Quality overview across the workspace. Blocked assessments are counted
 * separately from "needs revision" because only the former cannot proceed.
 */
export function QualityView() {
  const assessments = useApi("quality-all-assessments", () =>
    quality.assessments({ limit: 100 }),
  );
  const policyEvents = useApi("quality-policy-events", () =>
    quality.policyEvents({ limit: 50 }),
  );

  const rows = assessments.data ?? [];
  const blocked = rows.filter((item) => item.decision === "BLOCKED").length;
  const revision = rows.filter(
    (item) => item.decision === "NEEDS_REVISION",
  ).length;
  const passed = rows.filter((item) => item.decision === "PASS").length;
  const average =
    rows.length > 0
      ? rows.reduce((total, item) => total + Number(item.total_score), 0) /
        rows.length
      : null;

  return (
    <>
      <PageHeader
        title="품질"
        description="검수는 버전이 고정된 분석기로 실행되고, 같은 입력에는 같은 결과가 나옵니다."
      />

      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="평균 점수"
          value={average === null ? "—" : formatDecimal(average, 1)}
          hint={`최근 ${rows.length}건 기준`}
        />
        <StatCard label="통과" value={passed} tone="positive" />
        <StatCard label="수정 필요" value={revision} tone="caution" />
        <StatCard label="차단" value={blocked} tone="critical" />
      </div>

      <Card className="mb-4">
        <CardHeader
          title="최근 검수"
          description="검수 결과는 콘텐츠 버전과 해시에 묶여 보관됩니다."
        />
        <CardBody>
          <AsyncSection
            data={rows}
            error={assessments.error}
            errorText={assessments.errorText}
            isLoading={assessments.isLoading}
            onRetry={() => void assessments.mutate()}
            isEmpty={(data) => data.length === 0}
            empty={{
              title: "검수 기록이 없습니다",
              description: "콘텐츠 상세에서 품질 검수를 실행할 수 있습니다.",
            }}
          >
            {(data) => (
              <TableWrap>
                <Table>
                  <thead>
                    <tr>
                      <Th>콘텐츠</Th>
                      <Th align="right">점수</Th>
                      <Th>판정</Th>
                      <Th>차단 정책</Th>
                      <Th>산식</Th>
                      <Th align="right">시각</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.map((item) => (
                      <Tr key={item.id}>
                        <Td>
                          <Link
                            href={`/console/content/${item.content_id}`}
                            className="text-[var(--accent-link)] hover:underline"
                          >
                            <Mono>{item.content_id.split("-")[0]}</Mono>
                          </Link>
                          <p className="type-caption mt-0.5">
                            {shortHash(item.content_hash, 12)}
                          </p>
                        </Td>
                        <Td align="right">
                          <span className="numeric text-[15px] font-medium">
                            {formatDecimal(item.total_score, 1)}
                          </span>
                        </Td>
                        <Td>
                          <StatusBadge
                            registry="assessmentDecision"
                            value={item.decision}
                          />
                        </Td>
                        <Td>
                          {item.non_overrideable_policy_event_ids.length > 0 ? (
                            <Badge tone="critical">
                              예외 불가{" "}
                              {item.non_overrideable_policy_event_ids.length}
                            </Badge>
                          ) : item.blocking_policy_event_ids.length > 0 ? (
                            <Badge tone="caution">
                              차단 {item.blocking_policy_event_ids.length}
                            </Badge>
                          ) : (
                            <span className="text-[var(--text-tertiary)]">—</span>
                          )}
                        </Td>
                        <Td>
                          <Mono>{item.formula_version}</Mono>
                        </Td>
                        <Td align="right">
                          <span className="text-[13px]">
                            {formatDateTime(item.created_at)}
                          </span>
                        </Td>
                      </Tr>
                    ))}
                  </tbody>
                </Table>
              </TableWrap>
            )}
          </AsyncSection>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="정책 이벤트"
          description="정책 위반과 예외 승인 요청 기록입니다."
        />
        <CardBody>
          <AsyncSection
            data={policyEvents.data}
            error={policyEvents.error}
            errorText={policyEvents.errorText}
            isLoading={policyEvents.isLoading}
            onRetry={() => void policyEvents.mutate()}
            isEmpty={(data) => data.length === 0}
            empty={{ title: "정책 이벤트가 없습니다" }}
            skeletonRows={3}
          >
            {(events) => (
              <ul className="flex flex-col gap-2">
                {events.map((event, index) => (
                  <li
                    key={String(event.id ?? index)}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-[14px]">
                        {String(event.rule_key ?? event.policy_key ?? "정책")}
                      </span>
                      <span className="type-caption">
                        {String(event.reason ?? event.detail ?? "")}
                      </span>
                    </span>
                    <Badge
                      tone={
                        labelFor("assessmentDecision", String(event.severity))
                          .tone
                      }
                    >
                      {String(event.severity ?? event.status ?? "—")}
                    </Badge>
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
