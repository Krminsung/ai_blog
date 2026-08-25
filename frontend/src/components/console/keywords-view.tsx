"use client";

import { useState } from "react";

import { AsyncSection, FilterBar, PageHeader } from "@/components/console/page-parts";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SearchInput, Segmented } from "@/components/ui/field";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { Mono, Table, TableWrap, Td, Th, Tr } from "@/components/ui/table";
import { keywords as keywordsApi } from "@/lib/api/endpoints";
import { formatDecimal } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";

const INTENT_FILTERS = [
  { value: "", label: "전체" },
  { value: "INFORMATIONAL", label: "정보" },
  { value: "COMPARISON", label: "비교" },
  { value: "PURCHASE", label: "구매" },
  { value: "LOCAL", label: "지역" },
] as const;

/**
 * Keyword inventory. Provider quota is shown alongside the list because
 * collection is rate-limited by contract and users need to see remaining
 * headroom before starting research.
 */
export function KeywordsView() {
  const [intent, setIntent] = useState<string>("");
  const [query, setQuery] = useState("");

  const list = useApi(["keywords", intent, query], () =>
    keywordsApi.list({
      limit: 100,
      intent: intent || undefined,
      q: query || undefined,
    }),
  );
  const providers = useApi("keyword-providers", () =>
    keywordsApi.providerStatus(),
  );

  return (
    <>
      <PageHeader
        title="키워드"
        description="공식·계약·사용자 제공 출처에서만 수집합니다. 정규화된 키워드마다 의도와 브랜드 적합도가 함께 기록됩니다."
        actions={
          <Button size="sm" variant="secondary" onClick={() => void list.mutate()}>
            새로 고침
          </Button>
        }
      />

      {(providers.data ?? []).length > 0 ? (
        <Card className="mb-5">
          <CardHeader
            title="공급자 상태"
            description="호출 수와 캐시 적중, 오류를 공급자별로 집계합니다."
          />
          <CardBody>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {(providers.data ?? []).map((status, index) => (
                <div
                  key={index}
                  className="rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                >
                  <p className="text-[13.5px] font-medium">
                    {String(
                      (status.connection as Record<string, unknown>)?.provider ??
                        "공급자",
                    )}
                  </p>
                  <div className="numeric mt-2 flex gap-4 text-[12.5px] text-[var(--text-secondary)]">
                    <span>호출 {status.calls}</span>
                    <span>캐시 {status.cache_hits}</span>
                    <span
                      className={
                        status.errors > 0 ? "text-[var(--critical)]" : undefined
                      }
                    >
                      오류 {status.errors}
                    </span>
                  </div>
                  {status.last_error_code ? (
                    <p className="type-caption mt-1">
                      최근 오류 {status.last_error_code}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      ) : null}

      <FilterBar>
        <SearchInput
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="키워드 검색"
          className="w-full sm:w-72"
          aria-label="키워드 검색"
        />
        <Segmented
          options={INTENT_FILTERS.map((option) => ({ ...option }))}
          value={intent}
          onChange={setIntent}
        />
      </FilterBar>

      <AsyncSection
        data={list.data?.items}
        error={list.error}
        errorText={list.errorText}
        isLoading={list.isLoading}
        onRetry={() => void list.mutate()}
        isEmpty={(data) => data.length === 0}
        empty={{
          title: "키워드가 없습니다",
          description:
            "키워드 조사를 실행하거나 CSV로 가져오면 이곳에 표시됩니다.",
        }}
      >
        {(rows) => (
          <TableWrap>
            <Table>
              <thead>
                <tr>
                  <Th>키워드</Th>
                  <Th>정규화</Th>
                  <Th>의도</Th>
                  <Th align="right">의도 신뢰도</Th>
                  <Th align="right">브랜드 적합도</Th>
                  <Th>지역</Th>
                  <Th>위험 태그</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((keyword) => (
                  <Tr key={keyword.id}>
                    <Td>
                      <span
                        className={
                          keyword.is_excluded
                            ? "font-medium line-through opacity-60"
                            : "font-medium"
                        }
                      >
                        {keyword.display_text}
                      </span>
                    </Td>
                    <Td>
                      <Mono>{keyword.normalized}</Mono>
                    </Td>
                    <Td>
                      <StatusBadge
                        registry="keywordIntent"
                        value={keyword.intent}
                      />
                    </Td>
                    <Td align="right">
                      <span className="numeric">
                        {formatDecimal(keyword.intent_confidence, 2)}
                      </span>
                    </Td>
                    <Td align="right">
                      <span className="numeric">
                        {formatDecimal(keyword.brand_alignment, 2)}
                      </span>
                    </Td>
                    <Td>
                      <Mono>{keyword.region}</Mono>
                    </Td>
                    <Td>
                      {keyword.risk_tags_json.length === 0 ? (
                        <span className="text-[var(--text-tertiary)]">—</span>
                      ) : (
                        <span className="flex flex-wrap gap-1">
                          {keyword.risk_tags_json.map((tag) => (
                            <Badge key={tag} tone="caution">
                              {tag}
                            </Badge>
                          ))}
                        </span>
                      )}
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
