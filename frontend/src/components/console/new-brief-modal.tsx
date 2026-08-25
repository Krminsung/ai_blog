"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/modal";
import {
  brands as brandsApi,
  personas as personasApi,
  planning,
} from "@/lib/api/endpoints";
import { CHANNELS, JOURNEY_STAGES } from "@/lib/enums";
import { DEFAULT_LOCALE } from "@/lib/env";
import { useApi, useMutation } from "@/lib/hooks/use-query";

const SEARCH_INTENTS = [
  { value: "INFORMATIONAL", label: "정보 탐색" },
  { value: "COMPARISON", label: "비교" },
  { value: "PURCHASE", label: "구매" },
  { value: "LOCAL", label: "지역" },
  { value: "NAVIGATIONAL", label: "탐색" },
  { value: "MIXED", label: "복합" },
];

/**
 * Creates the first version of a brief. The API accepts a much richer payload
 * (facts, banned claims, CTA and link plans); this form covers the required
 * contract plus the outline, which is what reviewers actually argue about.
 */
export function NewBriefModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const campaigns = useApi(open ? "campaigns-for-brief" : null, () =>
    planning.campaigns({ limit: 200 }),
  );
  const brands = useApi(open ? "brands-for-brief" : null, () =>
    brandsApi.list({ limit: 200 }),
  );
  const personas = useApi(open ? "personas-for-brief" : null, () =>
    personasApi.list({ limit: 200 }),
  );

  const [form, setForm] = useState({
    title: "",
    objective: "",
    campaign_id: "",
    brand_id: "",
    persona_id: "",
    channel: "blog",
    search_intent: "INFORMATIONAL",
    journey_stage: "AWARENESS",
    target_length_min: 1200,
    target_length_max: 2000,
  });
  const [headings, setHeadings] = useState<string[]>(["도입", "본문", "마무리"]);

  const create = useMutation(planning.createBrief);

  const setHeading = (index: number, value: string) =>
    setHeadings((current) =>
      current.map((item, position) => (position === index ? value : item)),
    );

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const outline = headings
      .map((heading) => heading.trim())
      .filter(Boolean)
      .map((heading) => ({ heading, level: 2, required_points: [] }));

    const result = await create.run({
      campaign_id: form.campaign_id || null,
      payload: {
        title: form.title,
        objective: form.objective,
        channel: form.channel,
        language: DEFAULT_LOCALE,
        search_intent: form.search_intent,
        journey_stage: form.journey_stage,
        outline,
        references: {
          brand_id: form.brand_id || null,
          persona_id: form.persona_id || null,
          product_ids: [],
          knowledge_source_ids: [],
          secondary_keyword_ids: [],
          secondary_keyword_texts: [],
        },
        questions: [],
        target_length_min: Number(form.target_length_min),
        target_length_max: Number(form.target_length_max),
      },
    });
    if (result) onCreated();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="브리프 만들기"
      description="여기서 만든 내용이 버전 1이 됩니다. 제출하면 승인 흐름이 시작됩니다."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            취소
          </Button>
          <Button type="submit" form="brief-create" loading={create.isPending}>
            만들기
          </Button>
        </>
      }
    >
      <form id="brief-create" onSubmit={onSubmit} className="flex flex-col gap-4">
        {create.error ? <Notice tone="critical">{create.error}</Notice> : null}

        <Field label="제목" error={create.fieldErrors["payload.title"]} required>
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
          label="목표"
          error={create.fieldErrors["payload.objective"]}
          hint="이 글이 달성해야 할 결과를 적으세요."
          required
        >
          {(props) => (
            <Textarea
              {...props}
              value={form.objective}
              onChange={(event) =>
                setForm({ ...form, objective: event.target.value })
              }
              required
            />
          )}
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="캠페인">
            {(props) => (
              <Select
                {...props}
                value={form.campaign_id}
                onChange={(event) =>
                  setForm({ ...form, campaign_id: event.target.value })
                }
              >
                <option value="">선택 안 함</option>
                {(campaigns.data ?? []).map((campaign) => (
                  <option key={campaign.id} value={campaign.id}>
                    {campaign.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field label="채널" required>
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

          <Field label="페르소나">
            {(props) => (
              <Select
                {...props}
                value={form.persona_id}
                onChange={(event) =>
                  setForm({ ...form, persona_id: event.target.value })
                }
              >
                <option value="">선택 안 함</option>
                {(personas.data ?? []).map((persona) => (
                  <option key={persona.id} value={persona.id}>
                    {persona.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field label="검색 의도" required>
            {(props) => (
              <Select
                {...props}
                value={form.search_intent}
                onChange={(event) =>
                  setForm({ ...form, search_intent: event.target.value })
                }
              >
                {SEARCH_INTENTS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field label="여정 단계" required>
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

          <Field label="최소 분량 (자)">
            {(props) => (
              <Input
                {...props}
                type="number"
                min={100}
                value={form.target_length_min}
                onChange={(event) =>
                  setForm({
                    ...form,
                    target_length_min: Number(event.target.value),
                  })
                }
              />
            )}
          </Field>

          <Field label="최대 분량 (자)">
            {(props) => (
              <Input
                {...props}
                type="number"
                min={form.target_length_min}
                value={form.target_length_max}
                onChange={(event) =>
                  setForm({
                    ...form,
                    target_length_max: Number(event.target.value),
                  })
                }
              />
            )}
          </Field>
        </div>

        <fieldset>
          <legend className="mb-2 text-[13px] font-medium text-[var(--text-secondary)]">
            아웃라인 <span className="text-[var(--critical)]">*</span>
          </legend>
          <div className="flex flex-col gap-2">
            {headings.map((heading, index) => (
              <div key={index} className="flex items-center gap-2">
                <span className="numeric w-5 shrink-0 text-[12px] text-[var(--text-tertiary)]">
                  {index + 1}
                </span>
                <Input
                  value={heading}
                  onChange={(event) => setHeading(index, event.target.value)}
                  placeholder="소제목"
                  aria-label={`아웃라인 ${index + 1}`}
                />
                <button
                  type="button"
                  onClick={() =>
                    setHeadings((current) =>
                      current.filter((_, position) => position !== index),
                    )
                  }
                  aria-label={`아웃라인 ${index + 1} 삭제`}
                  className="grid size-8 shrink-0 place-items-center rounded-full text-[var(--text-tertiary)] transition-colors hover:bg-[var(--surface-alt)]"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-2"
            onClick={() => setHeadings((current) => [...current, ""])}
          >
            + 소제목 추가
          </Button>
        </fieldset>
      </form>
    </Modal>
  );
}
