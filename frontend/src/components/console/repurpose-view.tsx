"use client";

import { AsyncSection, PageHeader } from "@/components/console/page-parts";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/surface";
import { Notice } from "@/components/ui/feedback";
import { repurpose } from "@/lib/api/endpoints";
import { formatDateTime } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";

/**
 * Channel templates for repurposing. Variants produced from a template still
 * pass through an approval gate, which is why templates are versioned.
 */
export function RepurposeView() {
  const templates = useApi("repurpose-templates", () => repurpose.templates());

  return (
    <>
      <PageHeader
        title="콘텐츠 재활용"
        description="승인된 콘텐츠를 14종 형식으로 변환합니다. 근거·정책·모델·비용 스냅샷이 함께 고정되고, 변환 결과도 승인 게이트를 거칩니다."
      />

      <Notice tone="info" className="mb-4">
        재활용 작업은 콘텐츠 상세에서 승인된 버전을 선택해 시작합니다. 이 화면은
        사용 가능한 채널 템플릿을 보여 줍니다.
      </Notice>

      <AsyncSection
        data={templates.data}
        error={templates.error}
        errorText={templates.errorText}
        isLoading={templates.isLoading}
        onRetry={() => void templates.mutate()}
        isEmpty={(data) => data.length === 0}
        empty={{
          title: "채널 템플릿이 없습니다",
          description:
            "템플릿을 등록하면 채널별 길이와 형식 규칙에 맞춰 변환할 수 있습니다.",
        }}
      >
        {(rows) => (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {rows.map((template) => (
              <Card key={template.id} className="p-5">
                <div className="flex items-start justify-between gap-3">
                  <h2 className="text-[16px] font-semibold tracking-[-0.02em]">
                    {template.name}
                  </h2>
                  {template.retired_at ? (
                    <Badge tone="neutral">사용 중지</Badge>
                  ) : (
                    <Badge tone="positive">사용 중</Badge>
                  )}
                </div>
                {template.description ? (
                  <p className="mt-2 line-clamp-3 text-[13.5px] leading-relaxed text-[var(--text-secondary)]">
                    {template.description}
                  </p>
                ) : null}
                <div className="mt-4 flex flex-wrap gap-1.5 border-t border-[var(--hairline-soft)] pt-4">
                  <Badge>{template.kind}</Badge>
                  <Badge>{template.channel}</Badge>
                </div>
                <p className="type-caption mt-2">
                  수정 {formatDateTime(template.updated_at)}
                </p>
              </Card>
            ))}
          </div>
        )}
      </AsyncSection>
    </>
  );
}
