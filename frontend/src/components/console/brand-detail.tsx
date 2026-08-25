"use client";

import {
  AsyncSection,
  DescriptionList,
  PageHeader,
} from "@/components/console/page-parts";
import { StatusBadge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { Mono } from "@/components/ui/table";
import { brands as brandsApi } from "@/lib/api/endpoints";
import { formatDateTime, shortHash } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";

/**
 * Brand detail. The version list is the point of this screen: it is the audit
 * trail that generation snapshots reference.
 */
export function BrandDetail({ brandId }: { brandId: string }) {
  const brand = useApi(["brand", brandId], () => brandsApi.get(brandId));
  const versions = useApi(["brand-versions", brandId], () =>
    brandsApi.versions(brandId),
  );

  return (
    <>
      <PageHeader
        title={brand.data?.name ?? "브랜드"}
        description={brand.data?.description ?? undefined}
        breadcrumb={{ href: "/console/brands", label: "브랜드" }}
        actions={
          brand.data ? (
            <StatusBadge registry="catalogStatus" value={brand.data.status} />
          ) : null
        }
      />

      <div className="grid gap-4 lg:grid-cols-[1fr_1.3fr]">
        <Card>
          <CardHeader title="기본 정보" />
          <CardBody>
            <AsyncSection
              data={brand.data}
              error={brand.error}
              errorText={brand.errorText}
              isLoading={brand.isLoading}
              onRetry={() => void brand.mutate()}
              skeletonRows={4}
            >
              {(data) => (
                <DescriptionList
                  columns={1}
                  items={[
                    { term: "업종", value: data.industry ?? "—" },
                    {
                      term: "웹사이트",
                      value: data.website_url ? (
                        <a
                          href={data.website_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[var(--accent-link)] hover:underline"
                        >
                          {data.website_url}
                        </a>
                      ) : (
                        "—"
                      ),
                    },
                    {
                      term: "콘텐츠 해시",
                      value: <Mono>{shortHash(data.content_hash, 18)}</Mono>,
                    },
                    { term: "잠금 버전", value: data.lock_version },
                    { term: "생성일", value: formatDateTime(data.created_at) },
                    { term: "수정일", value: formatDateTime(data.updated_at) },
                  ]}
                />
              )}
            </AsyncSection>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="버전 이력"
            description="생성 작업은 이 중 하나의 버전을 스냅샷으로 고정합니다."
          />
          <CardBody>
            <AsyncSection
              data={versions.data}
              error={versions.error}
              errorText={versions.errorText}
              isLoading={versions.isLoading}
              onRetry={() => void versions.mutate()}
              isEmpty={(data) => data.length === 0}
              empty={{
                title: "아직 버전이 없습니다",
                description:
                  "브랜드 보이스와 금지 규칙을 담은 첫 버전을 만들어야 생성에 사용할 수 있습니다.",
              }}
            >
              {(rows) => (
                <ul className="flex flex-col gap-2">
                  {rows.map((version) => (
                    <li
                      key={version.id}
                      className="rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-[14px] font-medium">
                          버전 {version.version_number}
                        </span>
                        <span className="type-caption">
                          {formatDateTime(version.created_at)}
                        </span>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[12.5px] text-[var(--text-secondary)]">
                        <span>
                          금지어 {version.banned_terms?.length ?? 0}개
                        </span>
                        <span>
                          필수 용어 {version.required_terms?.length ?? 0}개
                        </span>
                        <span>
                          스타일 사전 {version.style_dictionary?.length ?? 0}개
                        </span>
                      </div>
                      <p className="type-caption mt-1.5">
                        해시 <Mono>{shortHash(version.content_hash, 16)}</Mono>
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </AsyncSection>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
