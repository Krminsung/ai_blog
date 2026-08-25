"use client";

import { AsyncSection, PageHeader, StatCard } from "@/components/console/page-parts";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/surface";
import { Mono } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { bulk } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { isTerminalJobState } from "@/lib/api/types";
import { formatCurrency, formatDecimal, formatNumber } from "@/lib/format";
import { usePolledApi } from "@/lib/hooks/use-query";
import type { BulkJob } from "@/lib/api/types";

/**
 * Bulk generation jobs. Cost is shown as authorized vs held vs actual because
 * that triple is what the kill switch acts on.
 */
export function BulkView() {
  const { notify } = useToast();

  const list = usePolledApi(
    "bulk-jobs",
    () => bulk.jobs({ limit: 50 }),
    (data) => (data ?? []).some((job) => !isTerminalJobState(job.state)),
  );

  const jobs = list.data ?? [];
  const running = jobs.filter((job) => !isTerminalJobState(job.state));
  const reviewRows = jobs.reduce((total, job) => total + job.review_rows, 0);
  const failedRows = jobs.reduce((total, job) => total + job.failed_rows, 0);

  const command = async (
    action: "pause" | "resume" | "cancel",
    jobId: string,
  ) => {
    try {
      if (action === "pause") await bulk.pause(jobId);
      else if (action === "resume") await bulk.resume(jobId);
      else await bulk.cancel(jobId);
      notify("요청을 전달했습니다.", "positive");
      void list.mutate();
    } catch (error) {
      notify(errorMessage(error), "critical");
    }
  };

  return (
    <>
      <PageHeader
        title="대량 생성"
        description="서버에서 검증한 스프레드시트 스냅샷으로 실행합니다. 행마다 비용을 홀드하고 예산을 넘기면 자동으로 멈춥니다."
        actions={
          <Button size="sm" variant="secondary" onClick={() => void list.mutate()}>
            새로 고침
          </Button>
        }
      />

      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="진행 중 작업" value={running.length} />
        <StatCard label="검수 대기 행" value={formatNumber(reviewRows)} tone="caution" />
        <StatCard label="실패 행" value={formatNumber(failedRows)} tone="critical" />
        <StatCard label="전체 작업" value={jobs.length} />
      </div>

      <AsyncSection
        data={jobs}
        error={list.error}
        errorText={list.errorText}
        isLoading={list.isLoading}
        onRetry={() => void list.mutate()}
        isEmpty={(data) => data.length === 0}
        empty={{
          title: "대량 생성 작업이 없습니다",
          description:
            "CSV 또는 XLSX 파일을 올리고 매핑을 지정하면 작업을 시작할 수 있습니다.",
        }}
      >
        {(rows) => (
          <div className="flex flex-col gap-3">
            {rows.map((job) => (
              <BulkJobCard
                key={job.id}
                job={job}
                onCommand={(action) => void command(action, job.id)}
              />
            ))}
          </div>
        )}
      </AsyncSection>
    </>
  );
}

function BulkJobCard({
  job,
  onCommand,
}: {
  job: BulkJob;
  onCommand: (action: "pause" | "resume" | "cancel") => void;
}) {
  const progress = Math.max(0, Math.min(100, Number(job.progress_percent)));
  const terminal = isTerminalJobState(job.state);

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-[15px] font-medium">
            <Mono>{job.id.split("-")[0]}</Mono>
            <span className="text-[var(--text-secondary)]">{job.operation}</span>
            {job.dry_run ? <Badge>시뮬레이션</Badge> : null}
          </p>
          <p className="type-caption mt-0.5">
            전체 {formatNumber(job.total_rows)}행 · 성공{" "}
            {formatNumber(job.succeeded_rows)} · 검수{" "}
            {formatNumber(job.review_rows)} · 실패{" "}
            {formatNumber(job.failed_rows)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge registry="jobState" value={job.state} />
          {job.budget_kill_switch_triggered ? (
            <Badge tone="critical">예산 정지</Badge>
          ) : null}
        </div>
      </div>

      <div className="mt-4">
        <div className="mb-1.5 flex items-center justify-between text-[12px] text-[var(--text-secondary)]">
          <span>진행률</span>
          <span className="numeric">{formatDecimal(progress, 1)}%</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-[var(--surface-alt)]">
          <div
            className="h-full rounded-full bg-[var(--accent)] transition-[width] duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      <dl className="numeric mt-4 grid grid-cols-2 gap-3 border-t border-[var(--hairline-soft)] pt-4 text-[12.5px] sm:grid-cols-4">
        <div>
          <dt className="text-[var(--text-tertiary)]">예상 비용</dt>
          <dd>{formatCurrency(job.estimated_cost, job.currency)}</dd>
        </div>
        <div>
          <dt className="text-[var(--text-tertiary)]">승인 한도</dt>
          <dd>{formatCurrency(job.authorized_cost, job.currency)}</dd>
        </div>
        <div>
          <dt className="text-[var(--text-tertiary)]">홀드</dt>
          <dd>{formatCurrency(job.held_cost, job.currency)}</dd>
        </div>
        <div>
          <dt className="text-[var(--text-tertiary)]">실제 비용</dt>
          <dd>{formatCurrency(job.actual_cost, job.currency)}</dd>
        </div>
      </dl>

      {job.error_code ? (
        <p className="mt-3 rounded-[10px] bg-[var(--critical-soft)] px-3 py-2 text-[12.5px] text-[var(--critical)]">
          {job.error_code}
          {job.error_detail ? ` · ${job.error_detail}` : ""}
        </p>
      ) : null}

      {!terminal ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {job.pause_requested ? (
            <Button size="sm" variant="secondary" onClick={() => onCommand("resume")}>
              재개
            </Button>
          ) : (
            <Button size="sm" variant="secondary" onClick={() => onCommand("pause")}>
              일시 중지
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => onCommand("cancel")}>
            취소
          </Button>
        </div>
      ) : null}
    </Card>
  );
}
