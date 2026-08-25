"use client";

import Link from "next/link";

import { PageHeader, StatCard } from "@/components/console/page-parts";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { ButtonLink } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { EmptyState, SkeletonRows } from "@/components/ui/feedback";
import {
  approvals as approvalsApi,
  billing as billingApi,
  content as contentApi,
  planning as planningApi,
  publishing as publishingApi,
} from "@/lib/api/endpoints";
import { formatDecimal, formatRelative } from "@/lib/format";
import { labelFor } from "@/lib/labels";
import { useApi } from "@/lib/hooks/use-query";
import { useSession } from "@/lib/auth/session-provider";

/**
 * Operating overview. Every tile is a live count from the domain it links to,
 * so the dashboard never disagrees with the page behind it.
 */
export function Dashboard() {
  const { user, workspace } = useSession();

  const contents = useApi("dash-content", () => contentApi.list({ limit: 8 }));
  const pending = useApi("dash-approvals", () =>
    approvalsApi.list({ status: "PENDING", limit: 50 }),
  );
  const jobs = useApi("dash-publish-jobs", () =>
    publishingApi.jobs({ limit: 20 }),
  );
  const campaigns = useApi("dash-campaigns", () =>
    planningApi.campaigns({ limit: 50 }),
  );
  const credits = useApi("dash-credits", () => billingApi.credits());

  const activeCampaigns = (campaigns.data ?? []).filter(
    (item) => item.status === "ACTIVE",
  );
  const runningJobs = (jobs.data ?? []).filter((job) =>
    ["QUEUED", "PUBLISHING", "SCHEDULED", "VALIDATING"].includes(job.state),
  );

  const greeting = user?.display_name ? `${user.display_name}님, 안녕하세요` : "안녕하세요";

  return (
    <>
      <PageHeader
        title={greeting}
        description={
          workspace
            ? `${workspace.name} · ${workspace.timezone} 기준으로 표시합니다.`
            : "워크스페이스를 불러오는 중입니다."
        }
        actions={
          <>
            <ButtonLink href="/console/briefs" variant="secondary" size="sm">
              브리프 만들기
            </ButtonLink>
            <ButtonLink href="/console/content" size="sm">
              콘텐츠로 이동
            </ButtonLink>
          </>
        }
      />

      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="승인 대기"
          value={pending.isLoading ? "—" : (pending.data?.length ?? 0)}
          hint="검수를 마치고 결정을 기다리는 항목"
          href="/console/approvals"
          tone={(pending.data?.length ?? 0) > 0 ? "caution" : "neutral"}
        />
        <StatCard
          label="진행 중 발행"
          value={jobs.isLoading ? "—" : runningJobs.length}
          hint="예약·실행 중인 발행 작업"
          href="/console/publishing"
        />
        <StatCard
          label="활성 캠페인"
          value={campaigns.isLoading ? "—" : activeCampaigns.length}
          hint="진행 상태의 캠페인"
          href="/console/campaigns"
        />
        <StatCard
          label="사용 가능 크레딧"
          value={
            credits.isLoading
              ? "—"
              : formatDecimal(credits.data?.available_balance, 0)
          }
          hint={
            credits.data
              ? `홀드 ${formatDecimal(credits.data.held_balance, 0)}`
              : "잔액을 불러오는 중"
          }
          href="/console/billing"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.35fr_1fr]">
        <Card>
          <CardHeader
            title="최근 콘텐츠"
            description="가장 최근에 수정된 항목입니다."
            actions={
              <Link
                href="/console/content"
                className="text-[13px] text-[var(--accent-link)] hover:underline"
              >
                전체 보기
              </Link>
            }
          />
          <CardBody>
            {contents.isLoading ? (
              <SkeletonRows rows={4} />
            ) : (contents.data ?? []).length === 0 ? (
              <EmptyState
                title="아직 콘텐츠가 없습니다"
                description="승인된 브리프에서 생성을 시작하면 이곳에 표시됩니다."
                action={
                  <ButtonLink href="/console/briefs" size="sm">
                    브리프로 이동
                  </ButtonLink>
                }
              />
            ) : (
              <ul className="flex flex-col">
                {(contents.data ?? []).map((item) => (
                  <li key={item.id}>
                    <Link
                      href={`/console/content/${item.id}`}
                      className="-mx-2 flex items-center justify-between gap-3 rounded-[10px] border-b border-[var(--hairline-soft)] px-2 py-3 transition-colors last:border-b-0 hover:bg-[var(--surface-alt)]"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-[14px] font-medium">
                          {item.title}
                        </span>
                        <span className="type-caption">
                          {labelFor("contentType", item.content_type).label} ·{" "}
                          {item.channel} · {formatRelative(item.updated_at)}
                        </span>
                      </span>
                      <StatusBadge registry="contentState" value={item.state} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader
              title="승인 대기"
              actions={
                <Link
                  href="/console/approvals"
                  className="text-[13px] text-[var(--accent-link)] hover:underline"
                >
                  전체 보기
                </Link>
              }
            />
            <CardBody>
              {pending.isLoading ? (
                <SkeletonRows rows={3} />
              ) : (pending.data ?? []).length === 0 ? (
                <p className="py-6 text-center text-[13px] text-[var(--text-secondary)]">
                  대기 중인 승인이 없습니다.
                </p>
              ) : (
                <ul className="flex flex-col gap-2.5">
                  {(pending.data ?? []).slice(0, 5).map((request) => (
                    <li key={request.id}>
                      <Link
                        href={`/console/approvals/${request.id}`}
                        className="flex items-center justify-between gap-3 rounded-[10px] border border-[var(--hairline-soft)] px-3 py-2.5 transition-colors hover:bg-[var(--surface-alt)]"
                      >
                        <span className="min-w-0">
                          <span className="block truncate font-mono text-[12px]">
                            {request.content_id.split("-")[0]}
                          </span>
                          <span className="type-caption">
                            {request.current_stage_index + 1}단계 ·{" "}
                            {formatRelative(request.requested_at)}
                          </span>
                        </span>
                        <Badge tone="caution">대기</Badge>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="발행 작업" />
            <CardBody>
              {jobs.isLoading ? (
                <SkeletonRows rows={3} />
              ) : (jobs.data ?? []).length === 0 ? (
                <p className="py-6 text-center text-[13px] text-[var(--text-secondary)]">
                  발행 작업이 없습니다.
                </p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {(jobs.data ?? []).slice(0, 5).map((job) => (
                    <li
                      key={job.id}
                      className="flex items-center justify-between gap-3"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-[13.5px]">
                          {labelFor("publishOperation", job.operation).label}
                        </span>
                        <span className="type-caption">
                          {formatRelative(job.updated_at)}
                        </span>
                      </span>
                      <StatusBadge registry="jobState" value={job.state} />
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}
