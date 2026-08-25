"use client";

import { useState } from "react";

import { AsyncSection, FilterBar, PageHeader } from "@/components/console/page-parts";
import { Badge } from "@/components/ui/badge";
import { SearchInput } from "@/components/ui/field";
import { Card } from "@/components/ui/surface";
import { media } from "@/lib/api/endpoints";
import { formatBytes, formatDate, humanizeEnum } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";

const STATE_TONE: Record<string, "positive" | "caution" | "critical" | "neutral"> = {
  READY: "positive",
  ACTIVE: "positive",
  SCANNING: "neutral",
  QUARANTINED: "critical",
  REVIEW_REQUIRED: "caution",
  DELETED: "neutral",
};

/**
 * Media library. Assets are shown as cards with the two facts that decide
 * whether they can be used: processing state and whether an AI-generation
 * disclosure is required.
 */
export function MediaView() {
  const [search, setSearch] = useState("");
  const list = useApi("media-assets", () => media.assets({ limit: 200 }));

  const rows = (list.data ?? []).filter((asset) =>
    search ? asset.name.toLowerCase().includes(search.toLowerCase()) : true,
  );

  return (
    <>
      <PageHeader
        title="미디어"
        description="업로드 파일은 격리 검사와 EXIF·PII 확인을 거칩니다. 생성 이미지는 공급자 정책과 사용 권리가 함께 고정됩니다."
      />

      <FilterBar>
        <SearchInput
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="파일 이름 검색"
          className="w-full sm:w-72"
          aria-label="미디어 검색"
        />
        <span className="text-[13px] text-[var(--text-secondary)]">
          {rows.length}개
        </span>
      </FilterBar>

      <AsyncSection
        data={rows}
        error={list.error}
        errorText={list.errorText}
        isLoading={list.isLoading}
        onRetry={() => void list.mutate()}
        isEmpty={(data) => data.length === 0}
        empty={{
          title: search ? "검색 결과가 없습니다" : "미디어 자산이 없습니다",
          description:
            "이미지를 업로드하거나 이미지 생성 작업을 실행하면 이곳에 모입니다.",
        }}
      >
        {(assets) => (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {assets.map((asset) => (
              <Card key={asset.id} className="overflow-hidden">
                {/* No signed preview URL is exposed by the list endpoint, so
                    the tile shows a typed placeholder rather than a broken
                    image request. */}
                <div className="grid aspect-[4/3] place-items-center bg-[var(--surface-alt)] text-[var(--text-tertiary)]">
                  <span className="font-mono text-[12px]">
                    {asset.declared_mime_type}
                  </span>
                </div>
                <div className="p-4">
                  <p className="truncate text-[14px] font-medium" title={asset.name}>
                    {asset.name}
                  </p>
                  <p className="type-caption mt-0.5">
                    {formatBytes(asset.declared_size_bytes)} ·{" "}
                    {formatDate(asset.created_at)}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    <Badge tone={STATE_TONE[asset.state] ?? "neutral"}>
                      {humanizeEnum(asset.state)}
                    </Badge>
                    {asset.ai_generated ? <Badge>AI 생성</Badge> : null}
                    {asset.ai_disclosure_required ? (
                      <Badge tone="caution">고지 필요</Badge>
                    ) : null}
                    {asset.review_reason ? (
                      <Badge tone="critical">검토 필요</Badge>
                    ) : null}
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
      </AsyncSection>
    </>
  );
}
