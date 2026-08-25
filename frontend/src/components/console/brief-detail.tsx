"use client";

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
import { planning } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { formatDateTime, shortHash } from "@/lib/format";
import { useApi, useMutation } from "@/lib/hooks/use-query";

type Decision = "APPROVE" | "REJECT" | "REQUEST_CHANGES";

const DECISION_LABELS: Record<Decision, string> = {
  APPROVE: "승인",
  REJECT: "반려",
  REQUEST_CHANGES: "수정 요청",
};

/**
 * Brief detail with the review actions. Every write carries the brief's
 * `lock_version`, which is how the backend rejects concurrent edits.
 */
export function BriefDetail({ briefId }: { briefId: string }) {
  const { notify } = useToast();
  const [decision, setDecision] = useState<Decision | null>(null);

  const brief = useApi(["brief", briefId], () => planning.brief(briefId));
  const versions = useApi(["brief-versions", briefId], () =>
    planning.briefVersions(briefId),
  );
  const submit = useMutation(planning.submitBrief);

  const current = brief.data?.current_version;

  const onSubmitForReview = async () => {
    if (!brief.data) return;
    const result = await submit.run(briefId, brief.data.lock_version);
    if (result) {
      notify("검토를 요청했습니다.", "positive");
      void brief.mutate();
    } else if (submit.error) {
      notify(submit.error, "critical");
    }
  };

  return (
    <>
      <PageHeader
        title={current?.title ?? "브리프"}
        description={current?.objective ?? undefined}
        breadcrumb={{ href: "/console/briefs", label: "브리프" }}
        actions={
          brief.data ? (
            <>
              <StatusBadge registry="briefStatus" value={brief.data.status} />
              {brief.data.status === "DRAFT" ||
              brief.data.status === "REVISION_REQUESTED" ? (
                <Button
                  size="sm"
                  onClick={() => void onSubmitForReview()}
                  loading={submit.isPending}
                >
                  검토 요청
                </Button>
              ) : null}
              {brief.data.status === "WAITING_REVIEW" ? (
                <>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => setDecision("REQUEST_CHANGES")}
                  >
                    수정 요청
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

      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader
              title="아웃라인"
              description="생성 단계는 이 구조를 따릅니다."
            />
            <CardBody>
              {!current ? (
                <p className="text-[13.5px] text-[var(--text-secondary)]">
                  아직 버전이 없습니다.
                </p>
              ) : (current.outline as { heading?: string }[]).length === 0 ? (
                <p className="text-[13.5px] text-[var(--text-secondary)]">
                  아웃라인이 비어 있습니다.
                </p>
              ) : (
                <ol className="flex flex-col gap-2">
                  {(current.outline as { heading?: string; purpose?: string }[]).map(
                    (section, index) => (
                      <li
                        key={index}
                        className="rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                      >
                        <p className="text-[14px] font-medium">
                          <span className="numeric mr-2 text-[var(--text-tertiary)]">
                            {index + 1}
                          </span>
                          {section.heading ?? "제목 없음"}
                        </p>
                        {section.purpose ? (
                          <p className="type-caption mt-1">{section.purpose}</p>
                        ) : null}
                      </li>
                    ),
                  )}
                </ol>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="버전 이력" />
            <CardBody>
              <AsyncSection
                data={versions.data}
                error={versions.error}
                errorText={versions.errorText}
                isLoading={versions.isLoading}
                onRetry={() => void versions.mutate()}
                isEmpty={(data) => data.length === 0}
                empty={{ title: "버전이 없습니다" }}
                skeletonRows={3}
              >
                {(rows) => (
                  <ul className="flex flex-col gap-2">
                    {rows.map((version) => (
                      <li
                        key={version.id}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                      >
                        <span>
                          <span className="text-[14px] font-medium">
                            버전 {version.version_number}
                          </span>
                          <span className="type-caption ml-2">
                            {formatDateTime(version.created_at)}
                          </span>
                        </span>
                        <Mono>{shortHash(version.snapshot_hash, 14)}</Mono>
                      </li>
                    ))}
                  </ul>
                )}
              </AsyncSection>
            </CardBody>
          </Card>
        </div>

        <Card>
          <CardHeader title="메타데이터" />
          <CardBody>
            <AsyncSection
              data={brief.data}
              error={brief.error}
              errorText={brief.errorText}
              isLoading={brief.isLoading}
              onRetry={() => void brief.mutate()}
              skeletonRows={4}
            >
              {(data) => (
                <DescriptionList
                  columns={1}
                  items={[
                    {
                      term: "승인 단계",
                      value: `${data.approval_stage_index + 1}단계`,
                    },
                    { term: "잠금 버전", value: data.lock_version },
                    {
                      term: "캠페인",
                      value: data.campaign_id ? (
                        <Mono>{data.campaign_id.split("-")[0]}</Mono>
                      ) : (
                        "—"
                      ),
                    },
                    {
                      term: "채널",
                      value: current?.channel ?? "—",
                    },
                    {
                      term: "분량",
                      value: current
                        ? `${current.target_length_min} – ${current.target_length_max}자`
                        : "—",
                    },
                    {
                      term: "참조 스냅샷",
                      value: current ? (
                        <Mono>{shortHash(current.reference_snapshot_hash, 14)}</Mono>
                      ) : (
                        "—"
                      ),
                    },
                    {
                      term: "다음 갱신",
                      value: formatDateTime(data.next_refresh_at),
                    },
                    { term: "생성일", value: formatDateTime(data.created_at) },
                  ]}
                />
              )}
            </AsyncSection>
          </CardBody>
        </Card>
      </div>

      <DecisionModal
        briefId={briefId}
        lockVersion={brief.data?.lock_version ?? 0}
        decision={decision}
        onClose={() => setDecision(null)}
        onDone={() => {
          setDecision(null);
          void brief.mutate();
        }}
      />
    </>
  );
}

function DecisionModal({
  briefId,
  lockVersion,
  decision,
  onClose,
  onDone,
}: {
  briefId: string;
  lockVersion: number;
  decision: Decision | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { notify } = useToast();
  const [comment, setComment] = useState("");
  const decide = useMutation(planning.decideBrief);

  if (!decision) return null;

  const submit = async () => {
    const result = await decide.run(briefId, {
      decision,
      comment: comment || null,
      expected_lock_version: lockVersion,
    });
    if (result) {
      notify(`${DECISION_LABELS[decision]} 처리했습니다.`, "positive");
      setComment("");
      onDone();
    } else if (decide.error) {
      notify(errorMessage(decide.error), "critical");
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`브리프 ${DECISION_LABELS[decision]}`}
      description="결정과 코멘트는 감사 이력으로 남습니다."
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            취소
          </Button>
          <Button
            variant={decision === "REJECT" ? "danger" : "primary"}
            onClick={() => void submit()}
            loading={decide.isPending}
          >
            {DECISION_LABELS[decision]}
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
            : "무엇을 고쳐야 하는지 구체적으로 적어 주세요."
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
  );
}
