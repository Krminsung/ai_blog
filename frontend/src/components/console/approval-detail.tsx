"use client";

import Link from "next/link";
import { useState } from "react";

import {
  AsyncSection,
  DescriptionList,
  PageHeader,
} from "@/components/console/page-parts";
import { StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/modal";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { Mono } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/cn";
import { approvals as approvalsApi } from "@/lib/api/endpoints";
import { formatDateTime, shortHash } from "@/lib/format";
import { useApi, useMutation } from "@/lib/hooks/use-query";

type Decision = "APPROVE" | "REJECT" | "REQUEST_CHANGES";

const DECISION_LABELS: Record<Decision, string> = {
  APPROVE: "승인",
  REJECT: "반려",
  REQUEST_CHANGES: "수정 요청",
};

interface Stage {
  key?: string;
  name?: string;
  required_approvals?: number;
  require_mfa?: boolean;
}

/**
 * Approval detail. The decision call re-states the version id, content hash
 * and lock version it believes it is acting on; the backend refuses if any of
 * them moved, which is how a stale tab cannot approve fresh content.
 */
export function ApprovalDetail({ requestId }: { requestId: string }) {
  const { notify } = useToast();
  const [decision, setDecision] = useState<Decision | null>(null);
  const [comment, setComment] = useState("");

  const request = useApi(["approval", requestId], () =>
    approvalsApi.get(requestId),
  );
  const decisions = useApi(["approval-decisions", requestId], () =>
    approvalsApi.decisions(requestId),
  );
  const decide = useMutation(approvalsApi.decide);

  const stages = (request.data?.approval_stages_snapshot ?? []) as Stage[];
  const isOpen =
    request.data?.status === "PENDING" ||
    request.data?.status === "CHANGES_REQUESTED";

  const submit = async () => {
    if (!request.data || !decision) return;
    const result = await decide.run(requestId, {
      decision,
      comment: comment || null,
      expected_lock_version: request.data.lock_version,
      expected_content_version_id: request.data.content_version_id,
      expected_content_hash: request.data.content_hash,
    });
    if (result) {
      notify(`${DECISION_LABELS[decision]} 처리했습니다.`, "positive");
      setDecision(null);
      setComment("");
      void request.mutate();
      void decisions.mutate();
    }
  };

  return (
    <>
      <PageHeader
        title="승인 요청"
        description={
          request.data
            ? `콘텐츠 ${request.data.content_id.split("-")[0]} · 버전 ${request.data.content_version_id.split("-")[0]}`
            : undefined
        }
        breadcrumb={{ href: "/console/approvals", label: "승인" }}
        actions={
          request.data ? (
            <>
              <StatusBadge
                registry="approvalStatus"
                value={request.data.status}
              />
              <Link
                href={`/console/content/${request.data.content_id}`}
                className="text-[13px] text-[var(--accent-link)] hover:underline"
              >
                콘텐츠 보기
              </Link>
              {isOpen ? (
                <>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => setDecision("REQUEST_CHANGES")}
                  >
                    수정 요청
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => setDecision("REJECT")}
                  >
                    반려
                  </Button>
                  <Button size="sm" onClick={() => setDecision("APPROVE")}>
                    승인
                  </Button>
                </>
              ) : null}
            </>
          ) : null
        }
      />

      {request.data?.invalidated_at ? (
        <Notice tone="critical" className="mb-4">
          이 승인은 {formatDateTime(request.data.invalidated_at)}에
          무효화되었습니다.
          {request.data.invalidation_reason
            ? ` 사유: ${request.data.invalidation_reason}`
            : ""}
        </Notice>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <Card>
          <CardHeader
            title="승인 단계"
            description="정족수를 채워야 다음 단계로 넘어갑니다."
          />
          <CardBody>
            {stages.length === 0 ? (
              <p className="text-[13.5px] text-[var(--text-secondary)]">
                단계 정보가 없습니다.
              </p>
            ) : (
              <ol className="flex flex-col gap-2">
                {stages.map((stage, index) => {
                  const current = index === request.data?.current_stage_index;
                  const done = index < (request.data?.current_stage_index ?? 0);
                  return (
                    <li
                      key={stage.key ?? index}
                      className={cn(
                        "flex items-center justify-between gap-3 rounded-[12px] border px-4 py-3",
                        current
                          ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                          : "border-[var(--hairline-soft)]",
                      )}
                    >
                      <span>
                        <span className="text-[14px] font-medium">
                          {index + 1}. {stage.name ?? stage.key ?? "단계"}
                        </span>
                        <span className="type-caption ml-2">
                          정족수 {stage.required_approvals ?? 1}명
                          {stage.require_mfa ? " · MFA 필요" : ""}
                        </span>
                      </span>
                      <span className="type-caption shrink-0">
                        {done ? "완료" : current ? "진행 중" : "대기"}
                      </span>
                    </li>
                  );
                })}
              </ol>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="고정된 값" />
          <CardBody>
            <AsyncSection
              data={request.data}
              error={request.error}
              errorText={request.errorText}
              isLoading={request.isLoading}
              onRetry={() => void request.mutate()}
              skeletonRows={4}
            >
              {(data) => (
                <DescriptionList
                  columns={1}
                  items={[
                    {
                      term: "콘텐츠 해시",
                      value: <Mono>{shortHash(data.content_hash, 20)}</Mono>,
                    },
                    {
                      term: "검수 해시",
                      value: <Mono>{shortHash(data.assessment_hash, 20)}</Mono>,
                    },
                    {
                      term: "품질 설정 해시",
                      value: (
                        <Mono>{shortHash(data.quality_config_hash, 20)}</Mono>
                      ),
                    },
                    {
                      term: "단계 스냅샷 해시",
                      value: (
                        <Mono>{shortHash(data.approval_stages_hash, 20)}</Mono>
                      ),
                    },
                    { term: "잠금 버전", value: data.lock_version },
                    {
                      term: "요청 시각",
                      value: formatDateTime(data.requested_at),
                    },
                    {
                      term: "승인 시각",
                      value: formatDateTime(data.approved_at),
                    },
                  ]}
                />
              )}
            </AsyncSection>
          </CardBody>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader title="결정 이력" />
        <CardBody>
          <AsyncSection
            data={decisions.data}
            error={decisions.error}
            errorText={decisions.errorText}
            isLoading={decisions.isLoading}
            onRetry={() => void decisions.mutate()}
            isEmpty={(data) => data.length === 0}
            empty={{ title: "아직 결정이 없습니다" }}
            skeletonRows={3}
          >
            {(rows) => (
              <ul className="flex flex-col gap-2">
                {rows.map((entry, index) => (
                  <li
                    key={String(entry.id ?? index)}
                    className="rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-[14px] font-medium">
                        {DECISION_LABELS[
                          String(entry.decision) as Decision
                        ] ?? String(entry.decision)}
                      </span>
                      <span className="type-caption">
                        {formatDateTime(String(entry.created_at ?? ""))}
                      </span>
                    </div>
                    {entry.comment ? (
                      <p className="mt-1 text-[13.5px] text-[var(--text-secondary)]">
                        {String(entry.comment)}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </AsyncSection>
        </CardBody>
      </Card>

      <Modal
        open={decision !== null}
        onClose={() => setDecision(null)}
        title={decision ? `콘텐츠 ${DECISION_LABELS[decision]}` : ""}
        description="결정은 감사 이력으로 남고, 승인은 현재 버전 해시에 고정됩니다."
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => setDecision(null)}>
              취소
            </Button>
            <Button
              variant={decision === "REJECT" ? "danger" : "primary"}
              onClick={() => void submit()}
              loading={decide.isPending}
            >
              {decision ? DECISION_LABELS[decision] : ""}
            </Button>
          </>
        }
      >
        {decide.error ? (
          <Notice tone="critical" className="mb-4">
            {decide.error}
          </Notice>
        ) : null}
        <Field
          label="코멘트"
          hint={
            decision === "APPROVE"
              ? "선택 사항입니다."
              : "무엇이 문제인지 구체적으로 적어 주세요."
          }
        >
          {(props) => (
            <Textarea
              {...props}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
            />
          )}
        </Field>
      </Modal>
    </>
  );
}
