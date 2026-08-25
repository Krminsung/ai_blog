"use client";

import { AsyncSection, PageHeader } from "@/components/console/page-parts";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/surface";
import { Notice } from "@/components/ui/feedback";
import { useToast } from "@/components/ui/toast";
import { publishing } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";

/**
 * Channel connections. Credential expiry and last error are surfaced on the
 * card because a silently degraded connection is what turns a scheduled
 * publish into a failed one.
 */
export function ConnectionsView() {
  const { notify } = useToast();
  const list = useApi("publishing-connections", () => publishing.connections());

  const diagnose = async (id: string) => {
    try {
      await publishing.diagnoseConnection(id);
      notify("연결 진단을 실행했습니다.", "positive");
      void list.mutate();
    } catch (error) {
      notify(errorMessage(error), "critical");
    }
  };

  return (
    <>
      <PageHeader
        title="채널 연결"
        description="공식 API로만 연결합니다. 자격 증명은 서버에서 봉인되어 저장되고 원문은 다시 표시되지 않습니다."
      />

      <Notice tone="caution" className="mb-4">
        네이버 블로그는 공식 자동 게시 API가 없어 연결을 만들 수 없습니다. 대신
        콘텐츠 상세에서 수동 발행 패키지를 생성하세요.
      </Notice>

      <AsyncSection
        data={list.data}
        error={list.error}
        errorText={list.errorText}
        isLoading={list.isLoading}
        onRetry={() => void list.mutate()}
        isEmpty={(data) => data.length === 0}
        empty={{
          title: "연결된 채널이 없습니다",
          description:
            "WordPress, Ghost, Blogger 또는 승인된 고객 CMS를 연결하면 발행할 수 있습니다.",
        }}
      >
        {(rows) => (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {rows.map((connection) => (
              <Card key={connection.id} className="p-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate text-[16px] font-semibold tracking-[-0.02em]">
                      {connection.name}
                    </h2>
                    <p className="type-caption mt-0.5 truncate">
                      {connection.site_url}
                    </p>
                  </div>
                  <StatusBadge
                    registry="connectionState"
                    value={connection.state}
                  />
                </div>

                <div className="mt-3 flex flex-wrap gap-1.5">
                  <Badge>{connection.provider}</Badge>
                  <Badge>API {connection.api_version}</Badge>
                  {connection.capabilities.slice(0, 2).map((capability) => (
                    <Badge key={capability}>{capability}</Badge>
                  ))}
                </div>

                <dl className="mt-4 flex flex-col gap-1.5 border-t border-[var(--hairline-soft)] pt-4 text-[12.5px]">
                  <div className="flex justify-between gap-2">
                    <dt className="text-[var(--text-tertiary)]">시간대</dt>
                    <dd>{connection.site_timezone}</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-[var(--text-tertiary)]">최근 성공</dt>
                    <dd>{formatDateTime(connection.last_success_at)}</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-[var(--text-tertiary)]">자격 만료</dt>
                    <dd>{formatDateTime(connection.credential_expires_at)}</dd>
                  </div>
                  {connection.last_error_code ? (
                    <div className="flex justify-between gap-2">
                      <dt className="text-[var(--text-tertiary)]">최근 오류</dt>
                      <dd className="text-[var(--critical)]">
                        {connection.last_error_code}
                      </dd>
                    </div>
                  ) : null}
                </dl>

                <div className="mt-4 flex gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void diagnose(connection.id)}
                  >
                    진단
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}
      </AsyncSection>
    </>
  );
}
