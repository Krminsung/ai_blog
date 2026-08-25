"use client";

import { useState, type FormEvent } from "react";

import {
  AsyncSection,
  FilterBar,
  PageHeader,
} from "@/components/console/page-parts";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, SearchInput, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/modal";
import { Mono, Table, TableWrap, Td, Th, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { brands as brandsApi, products as productsApi } from "@/lib/api/endpoints";
import { PRODUCT_SOURCES } from "@/lib/enums";
import { formatDate, shortHash } from "@/lib/format";
import { useApi, useMutation } from "@/lib/hooks/use-query";

/**
 * Product catalogue. Approved facts and banned claims live on the product
 * version — generation reads them from there, never from this list.
 */
export function ProductsView() {
  const { notify } = useToast();
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);

  const list = useApi("products", () => productsApi.list({ limit: 200 }));

  const filtered = (list.data ?? []).filter((product) => {
    if (!query) return true;
    const needle = query.toLowerCase();
    return (
      product.name.toLowerCase().includes(needle) ||
      product.sku.toLowerCase().includes(needle)
    );
  });

  return (
    <>
      <PageHeader
        title="상품"
        description="상품별 승인된 사실과 금지 주장이 생성 단계에 그대로 적용됩니다."
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            상품 추가
          </Button>
        }
      />

      <FilterBar>
        <SearchInput
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="상품명 또는 SKU 검색"
          className="w-full sm:w-72"
          aria-label="상품 검색"
        />
        <span className="text-[13px] text-[var(--text-secondary)]">
          {filtered.length}개
        </span>
      </FilterBar>

      <AsyncSection
        data={filtered}
        error={list.error}
        errorText={list.errorText}
        isLoading={list.isLoading}
        onRetry={() => void list.mutate()}
        isEmpty={(data) => data.length === 0}
        empty={{
          title: query ? "검색 결과가 없습니다" : "등록된 상품이 없습니다",
          description: query
            ? "다른 이름이나 SKU로 검색해 보세요."
            : "상품을 추가하면 가격·권리·제휴 고지를 자동으로 반영할 수 있습니다.",
        }}
      >
        {(rows) => (
          <TableWrap>
            <Table>
              <thead>
                <tr>
                  <Th>상품</Th>
                  <Th>SKU</Th>
                  <Th>출처</Th>
                  <Th>상태</Th>
                  <Th>해시</Th>
                  <Th align="right">동기화</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((product) => (
                  <Tr key={product.id}>
                    <Td>
                      <span className="font-medium">{product.name}</span>
                    </Td>
                    <Td>
                      <Mono>{product.sku}</Mono>
                    </Td>
                    <Td>
                      <Badge>{product.source}</Badge>
                    </Td>
                    <Td>
                      <StatusBadge
                        registry="catalogStatus"
                        value={product.status}
                      />
                    </Td>
                    <Td>
                      <Mono>{shortHash(product.content_hash)}</Mono>
                    </Td>
                    <Td align="right">{formatDate(product.last_synced_at)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableWrap>
        )}
      </AsyncSection>

      <CreateProductModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          notify("상품을 추가했습니다.", "positive");
          void list.mutate();
        }}
      />
    </>
  );
}

function CreateProductModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const brands = useApi(open ? "brands-for-product" : null, () =>
    brandsApi.list({ limit: 200 }),
  );
  const [form, setForm] = useState({
    brand_id: "",
    name: "",
    sku: "",
    source: "MANUAL",
  });
  const create = useMutation(productsApi.create);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const result = await create.run({
      brand_id: form.brand_id,
      name: form.name,
      sku: form.sku,
      source: form.source,
    });
    if (result) onCreated();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="상품 추가"
      description="상품은 반드시 하나의 브랜드에 속합니다."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            취소
          </Button>
          <Button type="submit" form="product-create" loading={create.isPending}>
            추가
          </Button>
        </>
      }
    >
      <form id="product-create" onSubmit={onSubmit} className="flex flex-col gap-4">
        {create.error ? <Notice tone="critical">{create.error}</Notice> : null}

        <Field label="브랜드" error={create.fieldErrors.brand_id} required>
          {(props) => (
            <Select
              {...props}
              value={form.brand_id}
              onChange={(event) =>
                setForm({ ...form, brand_id: event.target.value })
              }
              required
            >
              <option value="">브랜드를 선택하세요</option>
              {(brands.data ?? []).map((brand) => (
                <option key={brand.id} value={brand.id}>
                  {brand.name}
                </option>
              ))}
            </Select>
          )}
        </Field>

        <Field label="상품명" error={create.fieldErrors.name} required>
          {(props) => (
            <Input
              {...props}
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              required
            />
          )}
        </Field>

        <Field label="SKU" error={create.fieldErrors.sku} required>
          {(props) => (
            <Input
              {...props}
              value={form.sku}
              onChange={(event) => setForm({ ...form, sku: event.target.value })}
              required
            />
          )}
        </Field>

        <Field label="등록 방식" error={create.fieldErrors.source}>
          {(props) => (
            <Select
              {...props}
              value={form.source}
              onChange={(event) =>
                setForm({ ...form, source: event.target.value })
              }
            >
              {PRODUCT_SOURCES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </form>
    </Modal>
  );
}
