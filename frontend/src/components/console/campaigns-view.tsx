"use client";

import { useState, type FormEvent } from "react";

import { AsyncSection, PageHeader } from "@/components/console/page-parts";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/modal";
import { Table, TableWrap, Td, Th, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { brands as brandsApi, planning } from "@/lib/api/endpoints";
import { BUDGET_ENFORCEMENTS, CHANNELS } from "@/lib/enums";
import { DISPLAY_TIME_ZONE } from "@/lib/env";
import { formatDate } from "@/lib/format";
import { useApi, useMutation } from "@/lib/hooks/use-query";

/**
 * Campaigns hold the budget envelope and the policy hashes that every brief
 * underneath them inherits, so changing a campaign is a governance action.
 */
export function CampaignsView() {
  const { notify } = useToast();
  const [creating, setCreating] = useState(false);
  const list = useApi("campaigns", () => planning.campaigns({ limit: 200 }));

  return (
    <>
      <PageHeader
        title="캠페인"
        description="캠페인은 기간, 채널, 예산 한도와 정책 스냅샷을 함께 고정합니다."
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            캠페인 만들기
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
          title: "캠페인이 없습니다",
          description: "캠페인을 만들면 브리프와 캘린더를 묶어서 관리할 수 있습니다.",
          action: <Button onClick={() => setCreating(true)}>캠페인 만들기</Button>,
        }}
      >
        {(rows) => (
          <TableWrap>
            <Table>
              <thead>
                <tr>
                  <Th>이름</Th>
                  <Th>목표</Th>
                  <Th>채널</Th>
                  <Th>기간</Th>
                  <Th>예산 정책</Th>
                  <Th align="right">상태</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((campaign) => (
                  <Tr key={campaign.id}>
                    <Td>
                      <span className="font-medium">{campaign.name}</span>
                      {campaign.description ? (
                        <p className="type-caption mt-0.5 line-clamp-1">
                          {campaign.description}
                        </p>
                      ) : null}
                    </Td>
                    <Td>
                      <span className="line-clamp-1 max-w-52">
                        {campaign.objective}
                      </span>
                    </Td>
                    <Td>
                      <span className="flex flex-wrap gap-1">
                        {campaign.channels.map((channel) => (
                          <Badge key={channel}>
                            {CHANNELS.find((item) => item.value === channel)
                              ?.label ?? channel}
                          </Badge>
                        ))}
                      </span>
                    </Td>
                    <Td>
                      <span className="text-[13px] whitespace-nowrap">
                        {formatDate(campaign.start_date)} –{" "}
                        {formatDate(campaign.end_date)}
                      </span>
                    </Td>
                    <Td>
                      {BUDGET_ENFORCEMENTS.find(
                        (item) => item.value === campaign.budget_enforcement,
                      )?.label ?? campaign.budget_enforcement}
                    </Td>
                    <Td align="right">
                      <StatusBadge
                        registry="campaignStatus"
                        value={campaign.status}
                      />
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableWrap>
        )}
      </AsyncSection>

      <CreateCampaignModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          notify("캠페인을 만들었습니다.", "positive");
          void list.mutate();
        }}
      />
    </>
  );
}

function CreateCampaignModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const brands = useApi(open ? "brands-for-campaign" : null, () =>
    brandsApi.list({ limit: 200 }),
  );
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({
    name: "",
    objective: "",
    description: "",
    brand_id: "",
    channels: ["blog"] as string[],
    start_date: today,
    end_date: today,
    budget_enforcement: "BLOCK",
  });
  const create = useMutation(planning.createCampaign);

  const toggleChannel = (channel: string) =>
    setForm((current) => ({
      ...current,
      channels: current.channels.includes(channel)
        ? current.channels.filter((item) => item !== channel)
        : [...current.channels, channel],
    }));

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const result = await create.run({
      name: form.name,
      objective: form.objective,
      description: form.description || null,
      brand_id: form.brand_id || null,
      channels: form.channels,
      start_date: form.start_date,
      end_date: form.end_date,
      timezone: DISPLAY_TIME_ZONE,
      budget_enforcement: form.budget_enforcement,
    });
    if (result) onCreated();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="캠페인 만들기"
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            취소
          </Button>
          <Button
            type="submit"
            form="campaign-create"
            loading={create.isPending}
            disabled={form.channels.length === 0}
          >
            만들기
          </Button>
        </>
      }
    >
      <form id="campaign-create" onSubmit={onSubmit} className="flex flex-col gap-4">
        {create.error ? <Notice tone="critical">{create.error}</Notice> : null}

        <Field label="캠페인 이름" error={create.fieldErrors.name} required>
          {(props) => (
            <Input
              {...props}
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              required
            />
          )}
        </Field>

        <Field
          label="목표"
          error={create.fieldErrors.objective}
          hint="달성하려는 결과를 한 문장으로 적으세요."
          required
        >
          {(props) => (
            <Input
              {...props}
              value={form.objective}
              onChange={(event) =>
                setForm({ ...form, objective: event.target.value })
              }
              placeholder="예: 신제품 라인 인지도 확보"
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

        <fieldset>
          <legend className="mb-2 text-[13px] font-medium text-[var(--text-secondary)]">
            채널 <span className="text-[var(--critical)]">*</span>
          </legend>
          <div className="flex flex-wrap gap-2">
            {CHANNELS.map((channel) => {
              const selected = form.channels.includes(channel.value);
              return (
                <button
                  key={channel.value}
                  type="button"
                  onClick={() => toggleChannel(channel.value)}
                  aria-pressed={selected}
                  className={
                    selected
                      ? "rounded-full bg-[var(--accent)] px-3.5 py-1.5 text-[13px] text-white"
                      : "rounded-full border border-[var(--hairline)] px-3.5 py-1.5 text-[13px] text-[var(--text-secondary)] transition-colors hover:border-[var(--text-tertiary)]"
                  }
                >
                  {channel.label}
                </button>
              );
            })}
          </div>
        </fieldset>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="시작일" error={create.fieldErrors.start_date} required>
            {(props) => (
              <Input
                {...props}
                type="date"
                value={form.start_date}
                onChange={(event) =>
                  setForm({ ...form, start_date: event.target.value })
                }
                required
              />
            )}
          </Field>
          <Field label="종료일" error={create.fieldErrors.end_date} required>
            {(props) => (
              <Input
                {...props}
                type="date"
                value={form.end_date}
                min={form.start_date}
                onChange={(event) =>
                  setForm({ ...form, end_date: event.target.value })
                }
                required
              />
            )}
          </Field>
        </div>

        <Field
          label="예산 초과 처리"
          hint="예산을 넘겼을 때 작업을 어떻게 다룰지 결정합니다."
        >
          {(props) => (
            <Select
              {...props}
              value={form.budget_enforcement}
              onChange={(event) =>
                setForm({ ...form, budget_enforcement: event.target.value })
              }
            >
              {BUDGET_ENFORCEMENTS.map((option) => (
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
