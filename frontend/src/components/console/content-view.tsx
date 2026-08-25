"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { AsyncSection, FilterBar, PageHeader } from "@/components/console/page-parts";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, SearchInput, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/modal";
import { Table, TableWrap, Td, Th, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { brands as brandsApi, content as contentApi } from "@/lib/api/endpoints";
import { CHANNELS, CONTENT_STATES, CONTENT_TYPE_GROUPS } from "@/lib/enums";
import { DEFAULT_LOCALE } from "@/lib/env";
import { formatRelative } from "@/lib/format";
import { labelFor } from "@/lib/labels";
import { useApi, useMutation } from "@/lib/hooks/use-query";

/** Content library — every version-controlled document in the workspace. */
export function ContentView() {
  const { notify } = useToast();
  const [state, setState] = useState("");
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);

  const list = useApi(["content", state, query], () =>
    contentApi.list({
      limit: 100,
      state: state || undefined,
      query: query || undefined,
    }),
  );

  return (
    <>
      <PageHeader
        title="콘텐츠"
        description="모든 문서는 버전으로 보관됩니다. 새 버전을 만들면 기존 승인은 무효화됩니다."
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            콘텐츠 만들기
          </Button>
        }
      />

      <FilterBar>
        <SearchInput
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="제목 검색"
          className="w-full sm:w-72"
          aria-label="콘텐츠 검색"
        />
        <Select
          value={state}
          onChange={(event) => setState(event.target.value)}
          aria-label="상태 필터"
          className="w-auto"
        >
          <option value="">모든 상태</option>
          {CONTENT_STATES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      </FilterBar>

      <AsyncSection
        data={list.data}
        error={list.error}
        errorText={list.errorText}
        isLoading={list.isLoading}
        onRetry={() => void list.mutate()}
        isEmpty={(data) => data.length === 0}
        empty={{
          title: query || state ? "조건에 맞는 콘텐츠가 없습니다" : "콘텐츠가 없습니다",
          description:
            "승인된 브리프에서 생성을 시작하거나 직접 문서를 만들 수 있습니다.",
          action: <Button onClick={() => setCreating(true)}>콘텐츠 만들기</Button>,
        }}
      >
        {(rows) => (
          <TableWrap>
            <Table>
              <thead>
                <tr>
                  <Th>제목</Th>
                  <Th>유형</Th>
                  <Th>채널</Th>
                  <Th>태그</Th>
                  <Th>상태</Th>
                  <Th align="right">수정</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <Tr key={item.id}>
                    <Td>
                      <Link
                        href={`/console/content/${item.id}`}
                        className="font-medium text-[var(--accent-link)] hover:underline"
                      >
                        {item.title}
                      </Link>
                    </Td>
                    <Td>
                      <span className="text-[13px]">
                        {labelFor("contentType", item.content_type).label}
                      </span>
                    </Td>
                    <Td>
                      {CHANNELS.find((channel) => channel.value === item.channel)
                        ?.label ?? item.channel}
                    </Td>
                    <Td>
                      {item.tags.length === 0 ? (
                        <span className="text-[var(--text-tertiary)]">—</span>
                      ) : (
                        <span className="flex flex-wrap gap-1">
                          {item.tags.slice(0, 3).map((tag) => (
                            <Badge key={tag}>{tag}</Badge>
                          ))}
                        </span>
                      )}
                    </Td>
                    <Td>
                      <StatusBadge registry="contentState" value={item.state} />
                    </Td>
                    <Td align="right">{formatRelative(item.updated_at)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableWrap>
        )}
      </AsyncSection>

      <CreateContentModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          notify("콘텐츠를 만들었습니다.", "positive");
          void list.mutate();
        }}
      />
    </>
  );
}

function CreateContentModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const brands = useApi(open ? "brands-for-content" : null, () =>
    brandsApi.list({ limit: 200 }),
  );
  const [form, setForm] = useState({
    title: "",
    content_type: "INFORMATIONAL",
    channel: "blog",
    brand_id: "",
  });
  const create = useMutation(contentApi.create);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const result = await create.run({
      title: form.title,
      content_type: form.content_type,
      channel: form.channel,
      language: DEFAULT_LOCALE,
      brand_id: form.brand_id || null,
    });
    if (result) onCreated();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="콘텐츠 만들기"
      description="빈 문서를 만듭니다. 생성 작업은 상세 화면에서 시작할 수 있습니다."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            취소
          </Button>
          <Button type="submit" form="content-create" loading={create.isPending}>
            만들기
          </Button>
        </>
      }
    >
      <form id="content-create" onSubmit={onSubmit} className="flex flex-col gap-4">
        {create.error ? <Notice tone="critical">{create.error}</Notice> : null}

        <Field label="제목" error={create.fieldErrors.title} required>
          {(props) => (
            <Input
              {...props}
              value={form.title}
              onChange={(event) => setForm({ ...form, title: event.target.value })}
              required
            />
          )}
        </Field>

        <Field
          label="콘텐츠 유형"
          error={create.fieldErrors.content_type}
          hint="유형마다 입력 계약과 안전 규칙이 다릅니다."
          required
        >
          {(props) => (
            <Select
              {...props}
              value={form.content_type}
              onChange={(event) =>
                setForm({ ...form, content_type: event.target.value })
              }
            >
              {CONTENT_TYPE_GROUPS.map((group) => (
                <optgroup key={group.label} label={group.label}>
                  {group.options.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </optgroup>
              ))}
            </Select>
          )}
        </Field>

        <Field label="채널" error={create.fieldErrors.channel} required>
          {(props) => (
            <Select
              {...props}
              value={form.channel}
              onChange={(event) =>
                setForm({ ...form, channel: event.target.value })
              }
            >
              {CHANNELS.map((channel) => (
                <option key={channel.value} value={channel.value}>
                  {channel.label}
                </option>
              ))}
            </Select>
          )}
        </Field>

        <Field label="브랜드">
          {(props) => (
            <Select
              {...props}
              value={form.brand_id}
              onChange={(event) =>
                setForm({ ...form, brand_id: event.target.value })
              }
            >
              <option value="">선택 안 함</option>
              {(brands.data ?? []).map((brand) => (
                <option key={brand.id} value={brand.id}>
                  {brand.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </form>
    </Modal>
  );
}
