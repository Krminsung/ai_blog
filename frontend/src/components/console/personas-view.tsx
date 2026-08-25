"use client";

import { useState, type FormEvent } from "react";

import { AsyncSection, PageHeader } from "@/components/console/page-parts";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/modal";
import { Card } from "@/components/ui/surface";
import { useToast } from "@/components/ui/toast";
import { brands as brandsApi, personas as personasApi } from "@/lib/api/endpoints";
import { JOURNEY_STAGES, KNOWLEDGE_LEVELS } from "@/lib/enums";
import { useApi, useMutation } from "@/lib/hooks/use-query";

/**
 * Audience personas. Rendered as cards rather than a table because the useful
 * content is the list-valued fields (situations, interests, challenges).
 */
export function PersonasView() {
  const { notify } = useToast();
  const [creating, setCreating] = useState(false);
  const list = useApi("personas", () => personasApi.list({ limit: 200 }));

  return (
    <>
      <PageHeader
        title="페르소나"
        description="독자의 상황과 지식 수준을 정의하면 생성 단계에서 어휘와 설명 깊이가 달라집니다."
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            페르소나 추가
          </Button>
        }
      />

      <AsyncSection
        data={list.data}
        error={list.error}
        errorText={list.errorText}
        isLoading={list.isLoading}
        onRetry={() => void list.mutate()}
        isEmpty={(data) => data.length === 0}
        empty={{
          title: "등록된 페르소나가 없습니다",
          description: "누구에게 쓰는 글인지 정의하면 결과가 크게 달라집니다.",
          action: (
            <Button onClick={() => setCreating(true)}>페르소나 추가</Button>
          ),
        }}
      >
        {(rows) => (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {rows.map((persona) => (
              <Card key={persona.id} className="p-5">
                <div className="flex items-start justify-between gap-3">
                  <h2 className="text-[16px] font-semibold tracking-[-0.02em]">
                    {persona.name}
                  </h2>
                  <StatusBadge
                    registry="catalogStatus"
                    value={persona.status}
                  />
                </div>

                {persona.description ? (
                  <p className="mt-2 line-clamp-3 text-[13.5px] leading-relaxed text-[var(--text-secondary)]">
                    {persona.description}
                  </p>
                ) : null}

                <dl className="mt-4 flex flex-col gap-2.5 border-t border-[var(--hairline-soft)] pt-4 text-[12.5px]">
                  <div className="flex gap-2">
                    <dt className="w-16 shrink-0 text-[var(--text-tertiary)]">
                      지식 수준
                    </dt>
                    <dd>
                      {KNOWLEDGE_LEVELS.find(
                        (item) => item.value === persona.knowledge_level,
                      )?.label ?? persona.knowledge_level}
                    </dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-16 shrink-0 text-[var(--text-tertiary)]">
                      여정 단계
                    </dt>
                    <dd>
                      {JOURNEY_STAGES.find(
                        (item) => item.value === persona.journey_stage,
                      )?.label ?? persona.journey_stage}
                    </dd>
                  </div>
                  {persona.interests.length > 0 ? (
                    <div className="flex gap-2">
                      <dt className="w-16 shrink-0 text-[var(--text-tertiary)]">
                        관심사
                      </dt>
                      <dd className="flex flex-wrap gap-1">
                        {persona.interests.slice(0, 4).map((interest) => (
                          <Badge key={interest}>{interest}</Badge>
                        ))}
                      </dd>
                    </div>
                  ) : null}
                </dl>
              </Card>
            ))}
          </div>
        )}
      </AsyncSection>

      <CreatePersonaModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          notify("페르소나를 추가했습니다.", "positive");
          void list.mutate();
        }}
      />
    </>
  );
}

function CreatePersonaModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const brands = useApi(open ? "brands-for-persona" : null, () =>
    brandsApi.list({ limit: 200 }),
  );
  const [form, setForm] = useState({
    name: "",
    description: "",
    brand_id: "",
    knowledge_level: "GENERAL",
    journey_stage: "AWARENESS",
    interests: "",
    challenges: "",
  });
  const create = useMutation(personasApi.create);

  /** Comma-separated input → the string arrays the API expects. */
  const toList = (value: string) =>
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const result = await create.run({
      name: form.name,
      description: form.description || null,
      brand_id: form.brand_id || null,
      knowledge_level: form.knowledge_level,
      journey_stage: form.journey_stage,
      interests: toList(form.interests),
      challenges: toList(form.challenges),
    });
    if (result) onCreated();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="페르소나 추가"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            취소
          </Button>
          <Button type="submit" form="persona-create" loading={create.isPending}>
            추가
          </Button>
        </>
      }
    >
      <form id="persona-create" onSubmit={onSubmit} className="flex flex-col gap-4">
        {create.error ? <Notice tone="critical">{create.error}</Notice> : null}

        <Field label="이름" error={create.fieldErrors.name} required>
          {(props) => (
            <Input
              {...props}
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="예: 첫 구매를 앞둔 30대 직장인"
              required
            />
          )}
        </Field>

        <Field label="설명">
          {(props) => (
            <Textarea
              {...props}
              value={form.description}
              onChange={(event) =>
                setForm({ ...form, description: event.target.value })
              }
            />
          )}
        </Field>

        <Field label="브랜드" hint="브랜드 전용 페르소나라면 선택하세요.">
          {(props) => (
            <Select
              {...props}
              value={form.brand_id}
              onChange={(event) =>
                setForm({ ...form, brand_id: event.target.value })
              }
            >
              <option value="">전체 워크스페이스</option>
              {(brands.data ?? []).map((brand) => (
                <option key={brand.id} value={brand.id}>
                  {brand.name}
                </option>
              ))}
            </Select>
          )}
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="지식 수준">
            {(props) => (
              <Select
                {...props}
                value={form.knowledge_level}
                onChange={(event) =>
                  setForm({ ...form, knowledge_level: event.target.value })
                }
              >
                {KNOWLEDGE_LEVELS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field label="여정 단계">
            {(props) => (
              <Select
                {...props}
                value={form.journey_stage}
                onChange={(event) =>
                  setForm({ ...form, journey_stage: event.target.value })
                }
              >
                {JOURNEY_STAGES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>

        <Field label="관심사" hint="쉼표로 구분해 입력하세요.">
          {(props) => (
            <Input
              {...props}
              value={form.interests}
              onChange={(event) =>
                setForm({ ...form, interests: event.target.value })
              }
              placeholder="성분, 가격 비교, 후기"
            />
          )}
        </Field>

        <Field label="고민" hint="쉼표로 구분해 입력하세요.">
          {(props) => (
            <Input
              {...props}
              value={form.challenges}
              onChange={(event) =>
                setForm({ ...form, challenges: event.target.value })
              }
              placeholder="어떤 걸 골라야 할지 모름"
            />
          )}
        </Field>
      </form>
    </Modal>
  );
}
