"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { PageHeader } from "@/components/console/page-parts";
import { NewBriefModal } from "@/components/console/new-brief-modal";
import { StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/field";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Card } from "@/components/ui/surface";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/cn";
import { planning } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { formatRelative } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";
import type { Brief } from "@/lib/api/types";

/**
 * Brief board. Columns come from the workspace's configured board, and a card
 * is moved by posting the target column with the brief's expected lock
 * version — the backend rejects the move if someone else edited it first.
 */
export function BriefsBoard() {
  const { notify } = useToast();
  const [view, setView] = useState<"board" | "list">("board");
  const [creating, setCreating] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  const columns = useApi("board-columns", () => planning.boardColumns());
  const briefs = useApi("briefs", () => planning.briefs({ limit: 200 }));

  const grouped = useMemo(() => {
    const map = new Map<string, Brief[]>();
    for (const column of columns.data ?? []) map.set(column.id, []);
    const orphans: Brief[] = [];
    for (const brief of briefs.data ?? []) {
      if (brief.board_column_id && map.has(brief.board_column_id)) {
        map.get(brief.board_column_id)!.push(brief);
      } else {
        orphans.push(brief);
      }
    }
    return { map, orphans };
  }, [columns.data, briefs.data]);

  const move = async (brief: Brief, columnId: string) => {
    if (brief.board_column_id === columnId) return;
    try {
      await planning.moveBriefToColumn(brief.id, {
        board_column_id: columnId,
        expected_brief_lock_version: brief.lock_version,
      });
      notify("브리프를 이동했습니다.", "positive");
      void briefs.mutate();
    } catch (error) {
      notify(errorMessage(error), "critical");
      // Re-fetch so a stale lock version is refreshed for the next attempt.
      void briefs.mutate();
    }
  };

  const isLoading = columns.isLoading || briefs.isLoading;
  const error = columns.error ?? briefs.error;

  return (
    <>
      <PageHeader
        title="브리프"
        description="브리프는 생성의 계약입니다. 승인된 버전만 콘텐츠 생성에 사용할 수 있습니다."
        actions={
          <>
            <Segmented
              options={[
                { value: "board", label: "보드" },
                { value: "list", label: "목록" },
              ]}
              value={view}
              onChange={setView}
            />
            <Button size="sm" onClick={() => setCreating(true)}>
              브리프 만들기
            </Button>
          </>
        }
      />

      {error ? (
        <ErrorState
          message={errorMessage(error)}
          requestId={error.requestId}
          onRetry={() => {
            void columns.mutate();
            void briefs.mutate();
          }}
        />
      ) : isLoading ? (
        <div className="grid gap-3 md:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-64" />
          ))}
        </div>
      ) : (briefs.data ?? []).length === 0 ? (
        <EmptyState
          title="브리프가 없습니다"
          description="아이디어나 토픽에서 브리프를 만들면 승인 흐름이 시작됩니다."
          action={<Button onClick={() => setCreating(true)}>브리프 만들기</Button>}
        />
      ) : view === "list" ? (
        <BriefList briefs={briefs.data ?? []} />
      ) : (
        <div className="no-scrollbar flex gap-3 overflow-x-auto pb-3">
          {(columns.data ?? []).map((column) => {
            const items = grouped.map.get(column.id) ?? [];
            return (
              <section
                key={column.id}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDropTarget(column.id);
                }}
                onDragLeave={() => setDropTarget(null)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDropTarget(null);
                  const brief = (briefs.data ?? []).find(
                    (item) => item.id === dragging,
                  );
                  if (brief) void move(brief, column.id);
                  setDragging(null);
                }}
                className={cn(
                  "w-[300px] shrink-0 rounded-[16px] bg-[var(--surface-alt)] p-3 transition-colors",
                  dropTarget === column.id && "ring-2 ring-[var(--accent)]",
                )}
              >
                <div className="mb-2.5 flex items-center justify-between px-1">
                  <h2 className="flex items-center gap-2 text-[13.5px] font-semibold">
                    {column.color ? (
                      <span
                        aria-hidden
                        className="size-2 rounded-full"
                        style={{ backgroundColor: column.color }}
                      />
                    ) : null}
                    {column.name}
                  </h2>
                  <span className="numeric text-[12px] text-[var(--text-tertiary)]">
                    {items.length}
                  </span>
                </div>

                <div className="flex flex-col gap-2">
                  {items.map((brief) => (
                    <BriefCard
                      key={brief.id}
                      brief={brief}
                      columns={(columns.data ?? []).map((item) => ({
                        id: item.id,
                        name: item.name,
                      }))}
                      onDragStart={() => setDragging(brief.id)}
                      onDragEnd={() => setDragging(null)}
                      onMove={(columnId) => void move(brief, columnId)}
                    />
                  ))}
                  {items.length === 0 ? (
                    <p className="rounded-[12px] border border-dashed border-[var(--hairline)] px-3 py-6 text-center text-[12.5px] text-[var(--text-tertiary)]">
                      여기로 끌어다 놓으세요
                    </p>
                  ) : null}
                </div>
              </section>
            );
          })}

          {grouped.orphans.length > 0 ? (
            <section className="w-[300px] shrink-0 rounded-[16px] bg-[var(--surface-alt)] p-3">
              <h2 className="mb-2.5 px-1 text-[13.5px] font-semibold">
                컬럼 미지정
              </h2>
              <div className="flex flex-col gap-2">
                {grouped.orphans.map((brief) => (
                  <BriefCard
                    key={brief.id}
                    brief={brief}
                    columns={(columns.data ?? []).map((item) => ({
                      id: item.id,
                      name: item.name,
                    }))}
                    onDragStart={() => setDragging(brief.id)}
                    onDragEnd={() => setDragging(null)}
                    onMove={(columnId) => void move(brief, columnId)}
                  />
                ))}
              </div>
            </section>
          ) : null}
        </div>
      )}

      <NewBriefModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          notify("브리프를 만들었습니다.", "positive");
          void briefs.mutate();
        }}
      />
    </>
  );
}

function BriefCard({
  brief,
  columns,
  onDragStart,
  onDragEnd,
  onMove,
}: {
  brief: Brief;
  columns: { id: string; name: string }[];
  onDragStart: () => void;
  onDragEnd: () => void;
  onMove: (columnId: string) => void;
}) {
  const title = brief.current_version?.title ?? "제목 없는 브리프";

  return (
    <Card
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      className="cursor-grab p-3 active:cursor-grabbing"
    >
      <Link
        href={`/console/briefs/${brief.id}`}
        className="block text-[13.5px] font-medium hover:underline"
      >
        {title}
      </Link>
      <div className="mt-2 flex items-center justify-between gap-2">
        <StatusBadge registry="briefStatus" value={brief.status} />
        <span className="type-caption">{formatRelative(brief.updated_at)}</span>
      </div>
      {/* Keyboard-accessible equivalent of dragging the card. */}
      {columns.length > 0 ? (
        <label className="mt-2 flex items-center gap-1.5">
          <span className="type-caption shrink-0">이동</span>
          <span className="sr-only">{title}을(를) 다른 컬럼으로 이동</span>
          <select
            value={brief.board_column_id ?? ""}
            onChange={(event) => onMove(event.target.value)}
            className="min-w-0 flex-1 rounded-[7px] bg-transparent px-1 py-0.5 text-[12px] text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
          >
            <option value="">컬럼 선택</option>
            {columns.map((column) => (
              <option key={column.id} value={column.id}>
                {column.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </Card>
  );
}

function BriefList({ briefs }: { briefs: Brief[] }) {
  return (
    <div className="flex flex-col gap-2">
      {briefs.map((brief) => (
        <Link key={brief.id} href={`/console/briefs/${brief.id}`}>
          <Card className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 transition-colors hover:bg-[var(--surface-alt)]">
            <span className="min-w-0">
              <span className="block truncate text-[14.5px] font-medium">
                {brief.current_version?.title ?? "제목 없는 브리프"}
              </span>
              <span className="type-caption">
                {brief.approval_stage_index + 1}단계 ·{" "}
                {formatRelative(brief.updated_at)}
              </span>
            </span>
            <StatusBadge registry="briefStatus" value={brief.status} />
          </Card>
        </Link>
      ))}
    </div>
  );
}
