"use client";

import { useState } from "react";

import { AsyncSection, FilterBar, PageHeader } from "@/components/console/page-parts";
import { StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/field";
import { Mono, Table, TableWrap, Td, Th, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { publishing } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { PUBLISHING_PROVIDERS } from "@/lib/enums";
import { formatDateTime, shortHash } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";

/**
 * Published posts and their reconciliation state. A post in CONFLICT means
 * the remote copy changed outside BlogOps; reconciling re-reads the remote
 * and records the divergence rather than silently overwriting it.
 */
export function PostsView() {
  const { notify } = useToast();
  const [provider, setProvider] = useState("");

  const list = useApi(["published-posts", provider], () =>
    publishing.posts({ provider: provider || undefined, limit: 200 }),
  );

  const reconcile = async (postId: string) => {
    try {
      await publishing.reconcilePost(postId);
      notify("원격 상태와 대조했습니다.", "positive");
      void list.mutate();
    } catch (error) {
      notify(errorMessage(error), "critical");
    }
  };

  return (
    <>
      <PageHeader
        title="발행된 글"
        description="원격에서 글이 수정되면 충돌로 표시됩니다. 대조를 실행하면 차이를 확인할 수 있습니다."
      />

      <FilterBar>
        <Select
          value={provider}
          onChange={(event) => setProvider(event.target.value)}
          aria-label="채널 필터"
          className="w-auto"
        >
          <option value="">모든 채널</option>
          {PUBLISHING_PROVIDERS.map((option) => (
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
          title: "발행된 글이 없습니다",
          description: "발행 작업이 완료되면 이곳에 기록됩니다.",
        }}
      >
        {(rows) => (
          <TableWrap>
            <Table>
              <thead>
                <tr>
                  <Th>원격 글</Th>
                  <Th>채널</Th>
                  <Th>고정 해시</Th>
                  <Th>최근 대조</Th>
                  <Th>상태</Th>
                  <Th align="right">조치</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((post) => (
                  <Tr key={post.id}>
                    <Td>
                      {post.remote_url ? (
                        <a
                          href={post.remote_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[var(--accent-link)] hover:underline"
                        >
                          {post.remote_url}
                        </a>
                      ) : (
                        <Mono>{post.remote_id}</Mono>
                      )}
                    </Td>
                    <Td>
                      {PUBLISHING_PROVIDERS.find(
                        (item) => item.value === post.provider,
                      )?.label ?? post.provider}
                    </Td>
                    <Td>
                      <Mono>{shortHash(post.content_hash, 12)}</Mono>
                    </Td>
                    <Td>
                      <span className="text-[13px]">
                        {formatDateTime(post.last_reconciled_at)}
                      </span>
                    </Td>
                    <Td>
                      <StatusBadge
                        registry="publishedPostState"
                        value={post.state}
                      />
                    </Td>
                    <Td align="right">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => void reconcile(post.id)}
                      >
                        대조
                      </Button>
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
