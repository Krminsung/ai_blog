"use client";

import { useState, type FormEvent } from "react";

import { AsyncSection, FilterBar, PageHeader } from "@/components/console/page-parts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, SearchInput, Select, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/modal";
import { Mono, Table, TableWrap, Td, Th, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { knowledge as knowledgeApi } from "@/lib/api/endpoints";
import {
  QUALITY_GRADES,
  RIGHTS_STATUSES,
  SOURCE_TYPES,
  USE_SCOPES,
} from "@/lib/enums";
import { formatDate, humanizeEnum } from "@/lib/format";
import { useApi, useMutation } from "@/lib/hooks/use-query";
import type { Tone } from "@/lib/labels";

const RIGHTS_TONE: Record<string, Tone> = {
  OWNED: "positive",
  LICENSED: "positive",
  PERMISSION_GRANTED: "positive",
  PUBLIC_DOMAIN: "neutral",
  UNCONFIRMED: "caution",
  PROHIBITED: "critical",
};

/**
 * Knowledge sources. Rights status is given equal weight to the name because
 * an unconfirmed source cannot be cited, and the list is where that gets
 * caught.
 */
export function KnowledgeView() {
  const { notify } = useToast();
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState("");

  const list = useApi("knowledge-sources", () => knowledgeApi.list({ limit: 100 }));

  const rows = (list.data?.items ?? []).filter((source) =>
    search ? source.name.toLowerCase().includes(search.toLowerCase()) : true,
  );

  return (
    <>
      <PageHeader
        title="지식 자료"
        description="업로드한 파일과 수집한 URL은 악성코드 검사와 PII 마스킹을 거쳐 검색 가능한 지식이 됩니다."
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            자료 추가
          </Button>
        }
      />

      <FilterBar>
        <SearchInput
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="자료 이름 검색"
          className="w-full sm:w-72"
          aria-label="자료 검색"
        />
        <span className="text-[13px] text-[var(--text-secondary)]">
          {rows.length}건
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
          title: "등록된 자료가 없습니다",
          description:
            "브랜드 문서, 상품 스펙, 공식 발표 자료를 올리면 근거로 인용할 수 있습니다.",
          action: <Button onClick={() => setCreating(true)}>자료 추가</Button>,
        }}
      >
        {(data) => (
          <TableWrap>
            <Table>
              <thead>
                <tr>
                  <Th>이름</Th>
                  <Th>유형</Th>
                  <Th>권리</Th>
                  <Th>사용 범위</Th>
                  <Th>등급</Th>
                  <Th>상태</Th>
                  <Th align="right">동기화</Th>
                </tr>
              </thead>
              <tbody>
                {data.map((source) => (
                  <Tr key={source.id}>
                    <Td>
                      <span className="font-medium">{source.name}</span>
                      {source.uri ? (
                        <p className="type-caption mt-0.5 line-clamp-1">
                          {source.uri}
                        </p>
                      ) : null}
                    </Td>
                    <Td>
                      <Mono>{source.source_type}</Mono>
                    </Td>
                    <Td>
                      <Badge tone={RIGHTS_TONE[source.rights_status] ?? "neutral"}>
                        {RIGHTS_STATUSES.find(
                          (item) => item.value === source.rights_status,
                        )?.label ?? source.rights_status}
                      </Badge>
                    </Td>
                    <Td>
                      {USE_SCOPES.find((item) => item.value === source.use_scope)
                        ?.label ?? source.use_scope}
                    </Td>
                    <Td>
                      <Mono>{source.quality_grade}</Mono>
                    </Td>
                    <Td>{humanizeEnum(source.state)}</Td>
                    <Td align="right">{formatDate(source.last_synced_at)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableWrap>
        )}
      </AsyncSection>

      <CreateSourceModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          notify("자료를 등록했습니다. 처리 상태는 목록에서 확인하세요.", "positive");
          void list.mutate();
        }}
      />
    </>
  );
}

function CreateSourceModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form, setForm] = useState({
    source_type: "URL",
    name: "",
    uri: "",
    content: "",
    rights_status: "OWNED",
    use_scope: "GENERATION_ALLOWED",
    quality_grade: "A",
  });
  const create = useMutation(knowledgeApi.create);
  const needsUri = ["URL", "SITEMAP", "RSS", "YOUTUBE_TRANSCRIPT", "API", "CMS", "PRODUCT_FEED"].includes(
    form.source_type,
  );
  const needsContent = form.source_type === "TEXT";

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const result = await create.run({
      source_type: form.source_type,
      name: form.name,
      uri: needsUri ? form.uri : null,
      content: needsContent ? form.content : null,
      rights_status: form.rights_status,
      use_scope: form.use_scope,
      quality_grade: form.quality_grade,
    });
    if (result) onCreated();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="지식 자료 추가"
      description="파일 업로드는 별도의 업로드 URL을 발급받아 진행합니다. 여기서는 URL과 직접 입력 자료를 등록합니다."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            취소
          </Button>
          <Button type="submit" form="source-create" loading={create.isPending}>
            등록
          </Button>
        </>
      }
    >
      <form id="source-create" onSubmit={onSubmit} className="flex flex-col gap-4">
        {create.error ? <Notice tone="critical">{create.error}</Notice> : null}

        <Field label="자료 유형" required>
          {(props) => (
            <Select
              {...props}
              value={form.source_type}
              onChange={(event) =>
                setForm({ ...form, source_type: event.target.value })
              }
            >
              {SOURCE_TYPES.filter((option) => option.value !== "FILE").map(
                (option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ),
              )}
            </Select>
          )}
        </Field>

        <Field label="이름" error={create.fieldErrors.name} required>
          {(props) => (
            <Input
              {...props}
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              required
            />
          )}
        </Field>

        {needsUri ? (
          <Field label="URL" error={create.fieldErrors.uri} required>
            {(props) => (
              <Input
                {...props}
                type="url"
                value={form.uri}
                onChange={(event) => setForm({ ...form, uri: event.target.value })}
                placeholder="https://"
                required
              />
            )}
          </Field>
        ) : null}

        {needsContent ? (
          <Field label="내용" error={create.fieldErrors.content} required>
            {(props) => (
              <Textarea
                {...props}
                value={form.content}
                onChange={(event) =>
                  setForm({ ...form, content: event.target.value })
                }
                className="min-h-40"
                required
              />
            )}
          </Field>
        ) : null}

        <Field
          label="권리 상태"
          hint="미확인이나 사용 금지 자료는 인용에 사용할 수 없습니다."
          required
        >
          {(props) => (
            <Select
              {...props}
              value={form.rights_status}
              onChange={(event) =>
                setForm({ ...form, rights_status: event.target.value })
              }
            >
              {RIGHTS_STATUSES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          )}
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="사용 범위">
            {(props) => (
              <Select
                {...props}
                value={form.use_scope}
                onChange={(event) =>
                  setForm({ ...form, use_scope: event.target.value })
                }
              >
                {USE_SCOPES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field label="자료 등급">
            {(props) => (
              <Select
                {...props}
                value={form.quality_grade}
                onChange={(event) =>
                  setForm({ ...form, quality_grade: event.target.value })
                }
              >
                {QUALITY_GRADES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
      </form>
    </Modal>
  );
}
