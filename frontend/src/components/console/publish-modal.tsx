"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";
import {
  approvals as approvalsApi,
  content as contentApi,
  publishing,
} from "@/lib/api/endpoints";
import { PUBLISH_VISIBILITIES } from "@/lib/enums";
import { DISPLAY_TIME_ZONE } from "@/lib/env";
import { useApi, useMutation } from "@/lib/hooks/use-query";

/**
 * Publishing is gated on an approval. The backend requires the approved
 * version id and content hash alongside the approval request, so this form
 * derives them from the approval rather than letting the user pick — a
 * mismatch is exactly what the contract exists to prevent.
 *
 * Naver has no official publishing API, so it is offered as a manual package
 * instead of a publish job.
 */
export function PublishModal({
  open,
  onClose,
  contentId,
  contentVersionId,
}: {
  open: boolean;
  onClose: () => void;
  contentId: string;
  contentVersionId: string | null;
}) {
  const { notify } = useToast();
  const [connectionId, setConnectionId] = useState("");
  const [approvalId, setApprovalId] = useState("");
  const [visibility, setVisibility] = useState("PUBLISH");
  const [scheduledLocal, setScheduledLocal] = useState("");

  const connections = useApi(open ? "publish-connections" : null, () =>
    publishing.connections(),
  );
  const approved = useApi(open ? ["approvals", contentId] : null, () =>
    approvalsApi.list({ content_id: contentId, status: "APPROVED", limit: 20 }),
  );
  const versions = useApi(open ? ["content-versions", contentId] : null, () =>
    contentApi.versions(contentId),
  );

  const approval = useMemo(
    () => (approved.data ?? []).find((item) => item.id === approvalId),
    [approved.data, approvalId],
  );

  const connection = useMemo(
    () => (connections.data ?? []).find((item) => item.id === connectionId),
    [connections.data, connectionId],
  );

  const publish = useMutation(publishing.publish);
  const naver = useMutation(publishing.createNaverPackage);

  const isNaver = connection?.provider === "NAVER_MANUAL";
  const selectedVersionId =
    approval?.approved_content_version_id ??
    approval?.content_version_id ??
    contentVersionId ??
    (versions.data ?? [])[0]?.id ??
    "";
  const selectedHash =
    approval?.approved_content_hash ?? approval?.content_hash ?? "";

  const submit = async () => {
    if (!approval) return;

    if (isNaver) {
      const result = await naver.run(contentId, {
        content_version_id: selectedVersionId,
        content_hash: selectedHash,
        approval_request_id: approval.id,
        acknowledged_policy_version: "manual-publishing-v1",
        acknowledge_manual_responsibility: true,
        tags: [],
      });
      if (result) {
        notify("네이버 수동 발행 패키지를 만들었습니다.", "positive");
        onClose();
      }
      return;
    }

    const result = await publish.run(contentId, {
      content_version_id: selectedVersionId,
      content_hash: selectedHash,
      approval_request_id: approval.id,
      connection_id: connectionId,
      visibility,
      // The backend resolves the UTC instant from the local time plus the
      // site's zone, which is how DST folds stay unambiguous.
      scheduled_local:
        visibility === "SCHEDULED" && scheduledLocal ? scheduledLocal : null,
      site_timezone:
        visibility === "SCHEDULED"
          ? (connection?.site_timezone ?? DISPLAY_TIME_ZONE)
          : null,
    });
    if (result) {
      notify("발행 작업을 시작했습니다.", "positive");
      onClose();
    }
  };

  const pending = publish.isPending || naver.isPending;
  const error = publish.error ?? naver.error;
  const noApprovals = (approved.data ?? []).length === 0;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="발행"
      description="승인된 버전만 발행할 수 있습니다. 승인에 고정된 해시가 그대로 사용됩니다."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            취소
          </Button>
          <Button
            onClick={() => void submit()}
            loading={pending}
            disabled={!approval || !connectionId}
          >
            {isNaver ? "수동 패키지 만들기" : "발행"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {error ? <Notice tone="critical">{error}</Notice> : null}

        {noApprovals && !approved.isLoading ? (
          <Notice tone="caution">
            승인 완료된 버전이 없습니다. 품질 검수를 마치고 승인을 받은 뒤에
            발행할 수 있습니다.
          </Notice>
        ) : null}

        <Field label="승인 요청" required>
          {(props) => (
            <Select
              {...props}
              value={approvalId}
              onChange={(event) => setApprovalId(event.target.value)}
            >
              <option value="">승인을 선택하세요</option>
              {(approved.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id.split("-")[0]} · {item.approved_at ?? ""}
                </option>
              ))}
            </Select>
          )}
        </Field>

        <Field label="채널 연결" required>
          {(props) => (
            <Select
              {...props}
              value={connectionId}
              onChange={(event) => setConnectionId(event.target.value)}
            >
              <option value="">연결을 선택하세요</option>
              {(connections.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.provider}
                </option>
              ))}
            </Select>
          )}
        </Field>

        {isNaver ? (
          <Notice tone="caution">
            네이버는 공식 자동 게시 API가 없습니다. 발행 대신 체크리스트가 포함된
            수동 패키지를 만들고, 담당자가 직접 게시한 뒤 확인 처리를 해야
            합니다. 수동 게시 책임에 동의하는 것으로 처리됩니다.
          </Notice>
        ) : (
          <>
            <Field label="공개 상태" required>
              {(props) => (
                <Select
                  {...props}
                  value={visibility}
                  onChange={(event) => setVisibility(event.target.value)}
                >
                  {PUBLISH_VISIBILITIES.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>

            {visibility === "SCHEDULED" ? (
              <Field
                label="예약 시각"
                hint={`사이트 시간대(${connection?.site_timezone ?? DISPLAY_TIME_ZONE}) 기준으로 저장됩니다.`}
                required
              >
                {(props) => (
                  <Input
                    {...props}
                    type="datetime-local"
                    value={scheduledLocal}
                    onChange={(event) => setScheduledLocal(event.target.value)}
                    required
                  />
                )}
              </Field>
            ) : null}
          </>
        )}

        {approval ? (
          <p className="type-caption">
            고정될 버전 {selectedVersionId.split("-")[0]} · 해시{" "}
            {selectedHash.slice(0, 12)}…
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
