"use client";

import { useMemo, useState } from "react";

import { PageHeader } from "@/components/console/page-parts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { Card } from "@/components/ui/surface";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/cn";
import { planning } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { DISPLAY_TIME_ZONE } from "@/lib/env";
import { formatTime, humanizeEnum } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";
import type { CalendarEntry } from "@/lib/api/types";

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

/** Local Y-M-D key for an instant, evaluated in the workspace time zone. */
function dayKey(iso: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: DISPLAY_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(iso));
  const get = (type: string) => parts.find((part) => part.type === type)?.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/**
 * Month grid. The backend stores UTC and the workspace owns a time zone, so
 * the range sent to the API is built from local month boundaries and every
 * entry is bucketed by its local calendar day.
 */
export function CalendarView() {
  const { notify } = useToast();
  const [cursor, setCursor] = useState(() => {
    const now = new Date();
    return { year: now.getFullYear(), month: now.getMonth() };
  });

  const range = useMemo(() => {
    const start = new Date(Date.UTC(cursor.year, cursor.month, 1));
    const end = new Date(Date.UTC(cursor.year, cursor.month + 1, 1));
    return { start: start.toISOString(), end: end.toISOString() };
  }, [cursor]);

  const entries = useApi(["calendar", range.start, range.end], () =>
    planning.calendar(range.start, range.end, { include_cancelled: false }),
  );

  const byDay = useMemo(() => {
    const map = new Map<string, CalendarEntry[]>();
    for (const entry of entries.data ?? []) {
      const key = dayKey(entry.scheduled_at);
      const bucket = map.get(key);
      if (bucket) bucket.push(entry);
      else map.set(key, [entry]);
    }
    return map;
  }, [entries.data]);

  // Pad the grid so the 1st lands on the correct weekday.
  const firstWeekday = new Date(cursor.year, cursor.month, 1).getDay();
  const daysInMonth = new Date(cursor.year, cursor.month + 1, 0).getDate();
  const cells: (number | null)[] = [
    ...Array.from({ length: firstWeekday }, () => null),
    ...Array.from({ length: daysInMonth }, (_, index) => index + 1),
  ];
  while (cells.length % 7 !== 0) cells.push(null);

  const todayKey = dayKey(new Date().toISOString());

  const shift = (delta: number) =>
    setCursor((current) => {
      const next = new Date(current.year, current.month + delta, 1);
      return { year: next.getFullYear(), month: next.getMonth() };
    });

  const download = async (kind: "ics" | "csv") => {
    try {
      const response =
        kind === "ics"
          ? await planning.exportCalendarIcs(range.start, range.end)
          : await planning.exportCalendarCsv(range.start, range.end);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `calendar-${cursor.year}-${String(cursor.month + 1).padStart(2, "0")}.${kind}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      notify(errorMessage(error), "critical");
    }
  };

  return (
    <>
      <PageHeader
        title="콘텐츠 캘린더"
        description={`${DISPLAY_TIME_ZONE} 기준으로 표시합니다. 저장은 UTC로 이루어집니다.`}
        actions={
          <>
            <Button size="sm" variant="secondary" onClick={() => void download("ics")}>
              ICS 내보내기
            </Button>
            <Button size="sm" variant="secondary" onClick={() => void download("csv")}>
              CSV 내보내기
            </Button>
          </>
        }
      />

      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={() => shift(-1)} aria-label="이전 달">
            ‹
          </Button>
          <span className="numeric min-w-32 text-center text-[17px] font-semibold tracking-[-0.02em]">
            {cursor.year}년 {cursor.month + 1}월
          </span>
          <Button variant="ghost" size="sm" onClick={() => shift(1)} aria-label="다음 달">
            ›
          </Button>
        </div>
        <span className="text-[13px] text-[var(--text-secondary)]">
          {(entries.data ?? []).length}건 예정
        </span>
      </div>

      {entries.error ? (
        <ErrorState
          message={errorMessage(entries.error)}
          requestId={entries.error.requestId}
          onRetry={() => void entries.mutate()}
        />
      ) : entries.isLoading ? (
        <Skeleton className="h-[560px] w-full rounded-[16px]" />
      ) : (
        <Card className="overflow-hidden">
          <div className="grid grid-cols-7 border-b border-[var(--hairline-soft)] bg-[var(--surface-sunken)]">
            {WEEKDAYS.map((day, index) => (
              <div
                key={day}
                className={cn(
                  "px-2 py-2 text-center text-[12px] font-medium",
                  index === 0
                    ? "text-[var(--critical)]"
                    : "text-[var(--text-secondary)]",
                )}
              >
                {day}
              </div>
            ))}
          </div>

          <div className="grid grid-cols-7">
            {cells.map((day, index) => {
              if (day === null) {
                return (
                  <div
                    key={`pad-${index}`}
                    className="min-h-28 border-r border-b border-[var(--hairline-soft)] bg-[var(--surface-sunken)] last:border-r-0"
                  />
                );
              }
              const key = `${cursor.year}-${String(cursor.month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
              const items = byDay.get(key) ?? [];
              const isToday = key === todayKey;

              return (
                <div
                  key={key}
                  className="min-h-28 border-r border-b border-[var(--hairline-soft)] p-1.5 last:border-r-0"
                >
                  <span
                    className={cn(
                      "numeric mb-1 inline-grid size-6 place-items-center rounded-full text-[12px]",
                      isToday
                        ? "bg-[var(--accent)] font-semibold text-white"
                        : "text-[var(--text-secondary)]",
                    )}
                  >
                    {day}
                  </span>
                  <div className="flex flex-col gap-1">
                    {items.slice(0, 3).map((entry) => (
                      <div
                        key={entry.id}
                        title={entry.title_snapshot}
                        className="rounded-[7px] bg-[var(--accent-soft)] px-1.5 py-1 text-[11px] text-[var(--accent-link)]"
                      >
                        <span className="numeric mr-1 opacity-70">
                          {formatTime(entry.scheduled_at)}
                        </span>
                        <span className="line-clamp-1">
                          {entry.title_snapshot}
                        </span>
                      </div>
                    ))}
                    {items.length > 3 ? (
                      <span className="type-caption pl-1">
                        외 {items.length - 3}건
                      </span>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {(entries.data ?? []).some(
        (entry) => (entry.conflict_warnings ?? []).length > 0,
      ) ? (
        <Card className="mt-4 px-5 py-4">
          <p className="mb-2 text-[14px] font-semibold">일정 충돌 경고</p>
          <ul className="flex flex-col gap-1.5 text-[13px] text-[var(--text-secondary)]">
            {(entries.data ?? [])
              .filter((entry) => (entry.conflict_warnings ?? []).length > 0)
              .map((entry) => (
                <li key={entry.id} className="flex items-center gap-2">
                  <Badge tone="caution">
                    {humanizeEnum(entry.status)}
                  </Badge>
                  {entry.title_snapshot}
                </li>
              ))}
          </ul>
        </Card>
      ) : null}
    </>
  );
}
