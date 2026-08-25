"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import {
  AsyncSection,
  FilterBar,
  PageHeader,
} from "@/components/console/page-parts";
import { StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea, Toggle } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/modal";
import { Mono, Table, TableWrap, Td, Th, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { brands as brandsApi } from "@/lib/api/endpoints";
import { formatDate, shortHash } from "@/lib/format";
import { useApi, useMutation } from "@/lib/hooks/use-query";

/**
 * Brand catalogue. A brand's substance lives in its immutable versions, so
 * this list intentionally stays thin — name, status and which version is
 * current — and defers detail to the version view.
 */
export function BrandsView() {
  const { notify } = useToast();
  const [includeInactive, setIncludeInactive] = useState(false);
  const [creating, setCreating] = useState(false);

  const list = useApi(["brands", includeInactive], () =>
    brandsApi.list({ include_inactive: includeInactive, limit: 200 }),
  );

  return (
    <>
      <PageHeader
        title="브랜드"
        description="브랜드 보이스와 금지 표현은 불변 버전으로 관리됩니다. 새 버전을 만들면 이전 버전은 그대로 보존됩니다."
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            브랜드 추가
          </Button>
        }
      />

      <FilterBar>
        <span className="flex items-center gap-2 text-[13px] text-[var(--text-secondary)]">
          <Toggle
            checked={includeInactive}
            onChange={setIncludeInactive}
            label="비활성 브랜드 포함"
          />
          비활성 포함
        </span>
      </FilterBar>

      <AsyncSection
        data={list.data}
        error={list.error}
        errorText={list.errorText}
        isLoading={list.isLoading}
        onRetry={() => void list.mutate()}
        isEmpty={(data) => data.length === 0}
        empty={{
          title: "등록된 브랜드가 없습니다",
          description:
            "브랜드를 추가하면 보이스, 금지 표현, 필수 문구를 버전으로 관리할 수 있습니다.",
          action: <Button onClick={() => setCreating(true)}>브랜드 추가</Button>,
        }}
      >
        {(rows) => (
          <TableWrap>
            <Table>
              <thead>
                <tr>
                  <Th>이름</Th>
                  <Th>업종</Th>
                  <Th>상태</Th>
                  <Th>현재 버전</Th>
                  <Th>콘텐츠 해시</Th>
                  <Th align="right">수정일</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((brand) => (
                  <Tr key={brand.id}>
                    <Td>
                      <Link
                        href={`/console/brands/${brand.id}`}
                        className="font-medium text-[var(--accent-link)] hover:underline"
                      >
                        {brand.name}
                      </Link>
                      {brand.description ? (
                        <p className="type-caption mt-0.5 line-clamp-1">
                          {brand.description}
                        </p>
                      ) : null}
                    </Td>
                    <Td>{brand.industry ?? "—"}</Td>
                    <Td>
                      <StatusBadge registry="catalogStatus" value={brand.status} />
                    </Td>
                    <Td>
                      <Mono>{brand.current_version_id ? "있음" : "없음"}</Mono>
                    </Td>
                    <Td>
                      <Mono>{shortHash(brand.content_hash)}</Mono>
                    </Td>
                    <Td align="right">{formatDate(brand.updated_at)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableWrap>
        )}
      </AsyncSection>

      <CreateBrandModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          notify("브랜드를 추가했습니다.", "positive");
          void list.mutate();
        }}
      />
    </>
  );
}

function CreateBrandModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form, setForm] = useState({
    name: "",
    description: "",
    industry: "",
    website_url: "",
  });
  const create = useMutation(brandsApi.create);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const result = await create.run({
      name: form.name,
      description: form.description || null,
      industry: form.industry || null,
      website_url: form.website_url || null,
    });
    if (result) onCreated();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="브랜드 추가"
      description="기본 정보만 먼저 등록하고, 보이스와 금지 규칙은 버전에서 설정합니다."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            취소
          </Button>
          <Button type="submit" form="brand-create" loading={create.isPending}>
            추가
          </Button>
        </>
      }
    >
      <form id="brand-create" onSubmit={onSubmit} className="flex flex-col gap-4">
        {create.error ? <Notice tone="critical">{create.error}</Notice> : null}

        <Field label="브랜드 이름" error={create.fieldErrors.name} required>
          {(props) => (
            <Input
              {...props}
              value={form.name}
              onChange={(event) =>
                setForm({ ...form, name: event.target.value })
              }
              required
            />
          )}
        </Field>

        <Field label="설명" error={create.fieldErrors.description}>
          {(props) => (
            <Textarea
              {...props}
              value={form.description}
              onChange={(event) =>
                setForm({ ...form, description: event.target.value })
              }
              placeholder="브랜드를 한두 문장으로 소개하세요."
            />
          )}
        </Field>

        <Field label="업종" error={create.fieldErrors.industry}>
          {(props) => (
            <Input
              {...props}
              value={form.industry}
              onChange={(event) =>
                setForm({ ...form, industry: event.target.value })
              }
            />
          )}
        </Field>

        <Field label="웹사이트" error={create.fieldErrors.website_url}>
          {(props) => (
            <Input
              {...props}
              type="url"
              value={form.website_url}
              onChange={(event) =>
                setForm({ ...form, website_url: event.target.value })
              }
              placeholder="https://"
            />
          )}
        </Field>
      </form>
    </Modal>
  );
}
