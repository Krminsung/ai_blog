"use client";

import { useState } from "react";

import {
  AsyncSection,
  DescriptionList,
  PageHeader,
} from "@/components/console/page-parts";
import { QualityPanel } from "@/components/console/quality-panel";
import { PublishModal } from "@/components/console/publish-modal";
import { StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/field";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { Mono } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/cn";
import { downloadResponse } from "@/lib/browser-download";
import { content as contentApi, research } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { formatDateTime, formatNumber, shortHash } from "@/lib/format";
import { labelFor } from "@/lib/labels";
import { useApi } from "@/lib/hooks/use-query";

type Tab = "document" | "quality" | "evidence" | "versions";

interface Block {
  block_type?: string;
  plain_text?: string;
  payload?: Record<string, unknown>;
}

/**
 * Content studio. The document, its quality evidence and its citations are
 * three views of the same version, so the version selector sits above the
 * tabs and drives all of them.
 */
export function ContentDetail({ contentId }: { contentId: string }) {
  const { notify } = useToast();
  const [tab, setTab] = useState<Tab>("document");
  // `null` means "follow the content's current version"; picking one in the
  // selector pins it. Derived rather than synced, so no effect is needed.
  const [pinnedVersionId, setPinnedVersionId] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);

  const item = useApi(["content", contentId], () => contentApi.get(contentId));
  const versions = useApi(["content-versions", contentId], () =>
    contentApi.versions(contentId),
  );

  const versionId = pinnedVersionId ?? item.data?.current_version_id ?? null;

  const version = useApi(
    versionId ? ["content-version", contentId, versionId] : null,
    () => contentApi.version(contentId, versionId as string),
  );

  const claims = useApi(
    tab === "evidence" ? ["content-claims", contentId] : null,
    () => research.claims(contentId),
  );

  const exportContent = async () => {
    try {
      const response = await contentApi.exportContent(contentId, {
        format: "markdown",
      });
      await downloadResponse(response, `${item.data?.title ?? "content"}.md`);
    } catch (error) {
      notify(errorMessage(error), "critical");
    }
  };

  const blocks = (version.data?.document ?? []) as Block[];

  return (
    <>
      <PageHeader
        title={item.data?.title ?? "콘텐츠"}
        description={
          item.data
            ? `${labelFor("contentType", item.data.content_type).label} · ${item.data.channel} · ${item.data.language}`
            : undefined
        }
        breadcrumb={{ href: "/console/content", label: "콘텐츠" }}
        actions={
          item.data ? (
            <>
              <StatusBadge registry="contentState" value={item.data.state} />
              <Button size="sm" variant="secondary" onClick={() => void exportContent()}>
                내보내기
              </Button>
              <Button size="sm" onClick={() => setPublishing(true)}>
                발행
              </Button>
            </>
          ) : null
        }
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <Segmented
          options={[
            { value: "document", label: "본문" },
            { value: "quality", label: "품질" },
            { value: "evidence", label: "근거" },
            { value: "versions", label: "버전" },
          ]}
          value={tab}
          onChange={setTab}
        />

        {(versions.data ?? []).length > 0 ? (
          <label className="flex items-center gap-2 text-[13px] text-[var(--text-secondary)]">
            버전
            <select
              value={versionId ?? ""}
              onChange={(event) => setPinnedVersionId(event.target.value)}
              className="rounded-[9px] border border-[var(--hairline)] bg-[var(--surface)] px-2.5 py-1.5 text-[13px]"
            >
              {(versions.data ?? []).map((entry) => (
                <option key={entry.id} value={entry.id}>
                  v{entry.version_number}
                  {entry.id === item.data?.current_version_id ? " (현재)" : ""}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      {tab === "document" ? (
        <div className="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
          <Card>
            <CardHeader
              title="본문"
              description={
                version.data
                  ? `${formatNumber(version.data.plain_text.length)}자 · ${version.data.change_kind}`
                  : undefined
              }
            />
            <CardBody>
              <AsyncSection
                data={blocks}
                error={version.error}
                errorText={version.errorText}
                isLoading={version.isLoading && Boolean(versionId)}
                onRetry={() => void version.mutate()}
                isEmpty={(data) => data.length === 0}
                empty={{
                  title: "본문이 비어 있습니다",
                  description:
                    "승인된 브리프에서 생성 작업을 실행하면 본문이 채워집니다.",
                }}
              >
                {(rows) => (
                  <article className="flex flex-col gap-4">
                    {rows.map((block, index) => (
                      <ContentBlock key={index} block={block} />
                    ))}
                  </article>
                )}
              </AsyncSection>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="문서 정보" />
            <CardBody>
              <AsyncSection
                data={item.data}
                error={item.error}
                errorText={item.errorText}
                isLoading={item.isLoading}
                onRetry={() => void item.mutate()}
                skeletonRows={4}
              >
                {(data) => (
                  <DescriptionList
                    columns={1}
                    items={[
                      {
                        term: "브리프",
                        value: data.brief_id ? (
                          <Mono>{data.brief_id.split("-")[0]}</Mono>
                        ) : (
                          "연결 없음"
                        ),
                      },
                      {
                        term: "현재 버전 해시",
                        value: version.data ? (
                          <Mono>{shortHash(version.data.content_hash, 18)}</Mono>
                        ) : (
                          "—"
                        ),
                      },
                      { term: "잠금 버전", value: data.lock_version },
                      {
                        term: "보존 홀드",
                        value: data.retention_hold ? "설정됨" : "없음",
                      },
                      { term: "생성", value: formatDateTime(data.created_at) },
                      { term: "수정", value: formatDateTime(data.updated_at) },
                    ]}
                  />
                )}
              </AsyncSection>
            </CardBody>
          </Card>
        </div>
      ) : null}

      {tab === "quality" ? (
        <QualityPanel contentId={contentId} versionId={versionId} />
      ) : null}

      {tab === "evidence" ? (
        <Card>
          <CardHeader
            title="주장과 인용"
            description="문장 단위 주장마다 연결된 출처와 판정 결과입니다."
          />
          <CardBody>
            <AsyncSection
              data={claims.data}
              error={claims.error}
              errorText={claims.errorText}
              isLoading={claims.isLoading}
              onRetry={() => void claims.mutate()}
              isEmpty={(data) => data.length === 0}
              empty={{
                title: "등록된 주장이 없습니다",
                description:
                  "근거 조사를 실행하면 주장과 인용이 이곳에 정리됩니다.",
              }}
            >
              {(rows) => (
                <ul className="flex flex-col gap-2">
                  {rows.map((claim, index) => (
                    <li
                      key={String(claim.id ?? index)}
                      className="rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                    >
                      <p className="text-[14px]">
                        {String(claim.statement ?? claim.text ?? "주장")}
                      </p>
                      <p className="type-caption mt-1">
                        상태 {String(claim.status ?? claim.decision ?? "—")}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </AsyncSection>
          </CardBody>
        </Card>
      ) : null}

      {tab === "versions" ? (
        <Card>
          <CardHeader
            title="버전"
            description="승인은 특정 버전과 해시에 고정됩니다. 새 버전을 만들면 기존 승인은 무효가 됩니다."
          />
          <CardBody>
            <AsyncSection
              data={versions.data}
              error={versions.error}
              errorText={versions.errorText}
              isLoading={versions.isLoading}
              onRetry={() => void versions.mutate()}
              isEmpty={(data) => data.length === 0}
              empty={{ title: "버전이 없습니다" }}
            >
              {(rows) => (
                <ul className="flex flex-col gap-2">
                  {rows.map((entry) => (
                    <li
                      key={entry.id}
                      className={cn(
                        "rounded-[12px] border px-4 py-3",
                        entry.id === item.data?.current_version_id
                          ? "border-[var(--accent)]"
                          : "border-[var(--hairline-soft)]",
                      )}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-[14px] font-medium">
                          버전 {entry.version_number}
                          {entry.id === item.data?.current_version_id ? (
                            <span className="ml-2 text-[12px] text-[var(--accent-link)]">
                              현재
                            </span>
                          ) : null}
                        </span>
                        <span className="type-caption">
                          {formatDateTime(entry.created_at)}
                        </span>
                      </div>
                      <p className="type-caption mt-1">
                        {entry.change_kind}
                        {entry.change_note ? ` · ${entry.change_note}` : ""}
                      </p>
                      <p className="mt-1">
                        <Mono>{shortHash(entry.content_hash, 20)}</Mono>
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </AsyncSection>
          </CardBody>
        </Card>
      ) : null}

      {item.data ? (
        <PublishModal
          open={publishing}
          onClose={() => setPublishing(false)}
          contentId={contentId}
          contentVersionId={versionId}
        />
      ) : null}
    </>
  );
}

/**
 * Renders one document block. The backend keeps blocks open-ended
 * (`block_type` is a free string), so heading levels are recognised and
 * everything else falls back to its plain text.
 */
function ContentBlock({ block }: { block: Block }) {
  const type = (block.block_type ?? "paragraph").toLowerCase();
  const text =
    block.plain_text ||
    String((block.payload?.text as string | undefined) ?? "");

  if (!text) return null;

  if (type === "h1" || type === "heading_1" || type === "title") {
    return (
      <h2 className="text-[24px] font-semibold tracking-[-0.02em]">{text}</h2>
    );
  }
  if (type === "h2" || type === "heading_2") {
    return (
      <h3 className="mt-2 text-[19px] font-semibold tracking-[-0.02em]">
        {text}
      </h3>
    );
  }
  if (type === "h3" || type === "heading_3") {
    return <h4 className="mt-1 text-[16px] font-semibold">{text}</h4>;
  }
  if (type === "quote" || type === "blockquote") {
    return (
      <blockquote className="border-l-2 border-[var(--accent)] pl-4 text-[15px] text-[var(--text-secondary)] italic">
        {text}
      </blockquote>
    );
  }
  if (type === "list" || type === "bulleted_list") {
    return (
      <ul className="list-disc pl-5 text-[15px] leading-relaxed">
        {text.split("\n").map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>
    );
  }

  return <p className="text-[15px] leading-[1.75]">{text}</p>;
}
