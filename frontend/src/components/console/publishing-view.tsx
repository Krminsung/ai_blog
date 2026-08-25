"use client";

import { AsyncSection, PageHeader } from "@/components/console/page-parts";
import { StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Mono, Table, TableWrap, Td, Th, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { publishing } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { isTerminalJobState } from "@/lib/api/types";
import { formatDateTime, formatRelative, shortHash } from "@/lib/format";
import { labelFor } from "@/lib/labels";
import { usePolledApi } from "@/lib/hooks/use-query";

/**
 * Publishing jobs. Polls while any job is still moving — the backend has no
 * push channel and a Saga can take a while to settle.
 */
export function PublishingView() {
  const { notify } = useToast();

  const list = usePolledApi(
    "publish-jobs",
    () => publishing.jobs({ limit: 100 }),
    (data) => (data ?? []).some((job) => !isTerminalJobState(job.state)),
  );

  const act = async (
    action: "cancel" | "retry",
    jobId: string,
  ): Promise<void> => {
    try {
      if (action === "cancel") await publishing.cancelJob(jobId);
      else await publishing.retryJob(jobId);
      notify(action === "cancel" ? "취소를 요청했습니다." : "재시도를 요청했습니다.", "positive");
      void list.mutate();
    } catch (error) {
      notify(errorMessage(error), "critical");
    }
  };

  return (
    <>
      <PageHeader
        title="발행 작업"
        description="멱등 키로 중복 발행을 막습니다. 예약은 사이트 시간대를 기준으로 하고 DST 전환을 고려합니다."
        actions={
          <Button size="sm" variant="secondary" onClick={() => void list.mutate()}>
            새로 고침
          </Button>
        }
      />

      <AsyncSection
        data={list.data}
        error={list.error}
        errorText={list.errorText}
        isLoading={list.isLoading}
        onRetry={() => void list.mutate()}
        isEmpty={(data) => data.length === 0}
        empty={{
          title: "발행 작업이 없습니다",
          description: "승인된 콘텐츠를 발행하면 작업이 여기에 나타납니다.",
        }}
      >
        {(rows) => (
          <TableWrap>
            <Table>
              <thead>
                <tr>
                  <Th>작업</Th>
                  <Th>동작</Th>
                  <Th>공개</Th>
                  <Th>예약</Th>
                  <Th>시도</Th>
                  <Th>상태</Th>
                  <Th align="right">조치</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((job) => {
                  const terminal = isTerminalJobState(job.state);
                  const retryable =
                    job.state === "RETRYABLE_FAILED" ||
                    job.state === "FINAL_FAILED" ||
                    job.state === "PARTIAL";
                  return (
                    <Tr key={job.id}>
                      <Td>
                        <Mono>{job.id.split("-")[0]}</Mono>
                        <p className="type-caption mt-0.5">
                          콘텐츠 {shortHash(job.content_hash, 10)}
                        </p>
                      </Td>
                      <Td>{labelFor("publishOperation", job.operation).label}</Td>
                      <Td>{labelFor("publishVisibility", job.visibility).label}</Td>
                      <Td>
                        <span className="text-[13px]">
                          {job.scheduled_at_utc
                            ? formatDateTime(job.scheduled_at_utc)
                            : "즉시"}
                        </span>
                      </Td>
                      <Td>
                        <span className="numeric">
                          {job.attempt} / {job.max_attempts}
                        </span>
                      </Td>
                      <Td>
                        <StatusBadge registry="jobState" value={job.state} />
                        {job.error_code ? (
                          <p className="type-caption mt-0.5 text-[var(--critical)]">
                            {job.error_code}
                          </p>
                        ) : null}
                      </Td>
                      <Td align="right">
                        <span className="flex justify-end gap-1.5">
                          {!terminal ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => void act("cancel", job.id)}
                            >
                              취소
                            </Button>
                          ) : null}
                          {retryable ? (
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => void act("retry", job.id)}
                            >
                              재시도
                            </Button>
                          ) : null}
                          {terminal && !retryable ? (
                            <span className="type-caption">
                              {formatRelative(job.updated_at)}
                            </span>
                          ) : null}
                        </span>
                      </Td>
                    </Tr>
                  );
                })}
              </tbody>
            </Table>
          </TableWrap>
        )}
      </AsyncSection>
    </>
  );
}
