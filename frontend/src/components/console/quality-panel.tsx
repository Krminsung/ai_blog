"use client";

import { AsyncSection } from "@/components/console/page-parts";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { Mono } from "@/components/ui/table";
import { quality } from "@/lib/api/endpoints";
import { formatDateTime, formatDecimal, shortHash } from "@/lib/format";
import { labelFor } from "@/lib/labels";
import { useApi } from "@/lib/hooks/use-query";

/**
 * Quality evidence for one content version: the seven-component score with
 * per-component weights and contributions, plus the reports that produced it.
 *
 * The backend returns scores as decimal strings to preserve precision, so all
 * arithmetic here is display-only.
 */
export function QualityPanel({
  contentId,
  versionId,
}: {
  contentId: string;
  versionId: string | null;
}) {
  const assessments = useApi(["quality-assessments", contentId], () =>
    quality.assessments({ content_id: contentId, limit: 20 }),
  );
  const reports = useApi(["quality-reports", contentId], () =>
    quality.reports({ content_id: contentId, limit: 50 }),
  );

  // Prefer the assessment for the selected version; fall back to the newest.
  const assessment =
    (assessments.data ?? []).find(
      (item) => item.content_version_id === versionId,
    ) ?? (assessments.data ?? [])[0];

  const components = assessment
    ? Object.entries(assessment.component_scores as Record<string, unknown>)
    : [];
  const weights = (assessment?.component_weights ?? {}) as Record<string, unknown>;
  const contributions = (assessment?.weighted_contributions ?? {}) as Record<
    string,
    unknown
  >;
  const failed = Object.keys(
    (assessment?.failed_thresholds ?? {}) as Record<string, unknown>,
  );

  return (
    <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
      <Card>
        <CardHeader
          title="품질 점수"
          description={
            assessment
              ? `산식 ${assessment.formula_version} · ${formatDateTime(assessment.created_at)}`
              : "이 콘텐츠에 대한 검수 기록이 아직 없습니다."
          }
          actions={
            assessment ? (
              <StatusBadge
                registry="assessmentDecision"
                value={assessment.decision}
              />
            ) : null
          }
        />
        <CardBody>
          <AsyncSection
            data={assessments.data}
            error={assessments.error}
            errorText={assessments.errorText}
            isLoading={assessments.isLoading}
            onRetry={() => void assessments.mutate()}
            isEmpty={(data) => data.length === 0}
            empty={{
              title: "검수 결과가 없습니다",
              description:
                "품질 검수를 실행하면 요소별 점수와 차단 사유가 표시됩니다.",
            }}
          >
            {() =>
              assessment ? (
                <>
                  <div className="mb-6 flex items-baseline justify-between">
                    <span className="text-[13px] text-[var(--text-secondary)]">
                      종합 점수
                    </span>
                    <span className="numeric text-[40px] leading-none font-semibold tracking-[-0.03em]">
                      {formatDecimal(assessment.total_score, 1)}
                    </span>
                  </div>

                  <ul className="flex flex-col gap-3">
                    {components.map(([key, value]) => {
                      const score = Number(value);
                      const weight = Number(weights[key] ?? 0);
                      const contribution = Number(contributions[key] ?? 0);
                      const isFailed = failed.includes(key);
                      return (
                        <li key={key} className="flex items-center gap-3">
                          <span
                            className={
                              isFailed
                                ? "w-28 shrink-0 text-[12.5px] font-medium text-[var(--critical)]"
                                : "w-28 shrink-0 text-[12.5px] text-[var(--text-secondary)]"
                            }
                          >
                            {key}
                          </span>
                          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--surface-alt)]">
                            <span
                              className="block h-full rounded-full"
                              style={{
                                width: `${Math.max(0, Math.min(100, score))}%`,
                                backgroundColor: isFailed
                                  ? "var(--critical)"
                                  : "var(--accent)",
                              }}
                            />
                          </span>
                          <span className="numeric w-12 shrink-0 text-right text-[12.5px]">
                            {formatDecimal(score, 1)}
                          </span>
                          <span className="numeric w-16 shrink-0 text-right text-[11.5px] text-[var(--text-tertiary)]">
                            ×{formatDecimal(weight, 2)} ={" "}
                            {formatDecimal(contribution, 1)}
                          </span>
                        </li>
                      );
                    })}
                  </ul>

                  {assessment.non_overrideable_policy_event_ids.length > 0 ? (
                    <p className="mt-5 rounded-[12px] bg-[var(--critical-soft)] px-4 py-3 text-[13px] text-[var(--critical)]">
                      예외 승인이 불가능한 정책 위반이{" "}
                      {assessment.non_overrideable_policy_event_ids.length}건
                      있습니다. 본문을 수정해야 통과할 수 있습니다.
                    </p>
                  ) : assessment.blocking_policy_event_ids.length > 0 ? (
                    <p className="mt-5 rounded-[12px] bg-[var(--caution-soft)] px-4 py-3 text-[13px] text-[var(--caution)]">
                      차단 정책 위반 {assessment.blocking_policy_event_ids.length}건.
                      예외 승인 절차가 필요합니다.
                    </p>
                  ) : null}

                  <p className="type-caption mt-4">
                    검수 해시{" "}
                    <Mono>{shortHash(assessment.assessment_hash, 20)}</Mono>
                  </p>
                </>
              ) : null
            }
          </AsyncSection>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="검수 리포트"
          description="분석기와 사전 버전까지 고정되어 재현 가능합니다."
        />
        <CardBody>
          <AsyncSection
            data={reports.data}
            error={reports.error}
            errorText={reports.errorText}
            isLoading={reports.isLoading}
            onRetry={() => void reports.mutate()}
            isEmpty={(data) => data.length === 0}
            empty={{ title: "리포트가 없습니다" }}
            skeletonRows={4}
          >
            {(rows) => (
              <ul className="flex flex-col gap-2">
                {rows.map((report) => {
                  const hardBlockers = report.hard_blockers_json.length;
                  const findings = report.findings_json.length;
                  return (
                    <li
                      key={report.id}
                      className="rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-[14px] font-medium">
                          {labelFor("reportKind", report.report_kind).label}
                        </span>
                        {hardBlockers > 0 ? (
                          <Badge tone="critical">차단 {hardBlockers}</Badge>
                        ) : findings > 0 ? (
                          <Badge tone="caution">지적 {findings}</Badge>
                        ) : (
                          <Badge tone="positive">이상 없음</Badge>
                        )}
                      </div>
                      <p className="type-caption mt-1">
                        {report.analyzer_name} {report.analyzer_version}
                        {report.dictionary_name
                          ? ` · 사전 ${report.dictionary_name} ${report.dictionary_version}`
                          : ""}
                      </p>
                      <p className="type-caption mt-0.5">
                        {formatDateTime(report.created_at)}
                      </p>
                    </li>
                  );
                })}
              </ul>
            )}
          </AsyncSection>
        </CardBody>
      </Card>
    </div>
  );
}
