"use client";

import Link from "next/link";
import { useState } from "react";

import { AsyncSection, FilterBar, PageHeader } from "@/components/console/page-parts";
import { StatusBadge } from "@/components/ui/badge";
import { Select } from "@/components/ui/field";
import { Mono, Table, TableWrap, Td, Th, Tr } from "@/components/ui/table";
import { approvals as approvalsApi } from "@/lib/api/endpoints";
import { APPROVAL_STATUSES } from "@/lib/enums";
import { formatDateTime, formatRelative, shortHash } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";

/**
 * Approval queue. Status defaults to PENDING because that is the only state
 * that needs action; everything else is history.
 */
export function ApprovalsView() {
  const [status, setStatus] = useState("PENDING");
  const list = useApi(["approvals", status], () =>
    approvalsApi.list({ status: status || undefined, limit: 200 }),
  );

  return (
    <>
      <PageHeader
        title="승인"
        description="승인은 콘텐츠 버전과 해시, 검수 결과에 함께 고정됩니다. 입력이 바뀌면 승인은 자동으로 무효화됩니다."
      />

      <FilterBar>
        <Select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          aria-label="승인 상태"
          className="w-auto"
        >
          <option value="">모든 상태</option>
          {APPROVAL_STATUSES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <span className="text-[13px] text-[var(--text-secondary)]">
          {(list.data ?? []).length}건
        </span>
      </FilterBar>

      <AsyncSection
        data={list.data}
        error={list.error}
        errorText={list.errorText}
        isLoading={list.isLoading}
        onRetry={() => void list.mutate()}
        isEmpty={(data) => data.length === 0}
        empty={{
          title: "해당 상태의 승인 요청이 없습니다",
          description:
            "품질 검수를 통과한 콘텐츠에 대해 승인 요청을 만들 수 있습니다.",
        }}
      >
        {(rows) => (
          <TableWrap>
            <Table>
              <thead>
                <tr>
                  <Th>요청</Th>
                  <Th>콘텐츠 버전</Th>
                  <Th>단계</Th>
                  <Th>요청 시각</Th>
                  <Th>기한</Th>
                  <Th align="right">상태</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((request) => (
                  <Tr key={request.id}>
                    <Td>
                      <Link
                        href={`/console/approvals/${request.id}`}
                        className="font-medium text-[var(--accent-link)] hover:underline"
                      >
                        {request.id.split("-")[0]}
                      </Link>
                      <p className="type-caption mt-0.5">
                        콘텐츠 {request.content_id.split("-")[0]}
                      </p>
                    </Td>
                    <Td>
                      <Mono>{shortHash(request.content_hash, 12)}</Mono>
                    </Td>
                    <Td>
                      {request.current_stage_index + 1} /{" "}
                      {request.approval_stages_snapshot.length}
                    </Td>
                    <Td>
                      <span className="text-[13px]">
                        {formatRelative(request.requested_at)}
                      </span>
                    </Td>
                    <Td>
                      <span className="text-[13px]">
                        {request.stage_due_at
                          ? formatDateTime(request.stage_due_at)
                          : "—"}
                      </span>
                    </Td>
                    <Td align="right">
                      <StatusBadge
                        registry="approvalStatus"
                        value={request.status}
                      />
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableWrap>
        )}
      </AsyncSection>
    </>
  );
}
