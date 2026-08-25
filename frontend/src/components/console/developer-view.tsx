"use client";

import { useState, type FormEvent } from "react";

import { AsyncSection, PageHeader } from "@/components/console/page-parts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/modal";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { Mono, Table, TableWrap, Td, Th, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { developer } from "@/lib/api/endpoints";
import { formatDateTime, humanizeEnum } from "@/lib/format";
import { useApi, useMutation } from "@/lib/hooks/use-query";

/**
 * API keys and webhooks. The secret is returned exactly once at creation, so
 * the reveal step is a modal the user must explicitly dismiss.
 */
export function DeveloperView() {
  const { notify } = useToast();
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);

  const keys = useApi("api-keys", () => developer.apiKeys());
  const webhooks = useApi("webhooks", () => developer.webhooks());

  const revoke = async (id: string) => {
    try {
      await developer.revokeApiKey(id, { reason: "console_revoke" });
      notify("키를 폐기했습니다.", "positive");
      void keys.mutate();
    } catch {
      notify("키를 폐기하지 못했습니다.", "critical");
    }
  };

  return (
    <>
      <PageHeader
        title="개발자"
        description="API Key는 원문이 발급 시 한 번만 표시됩니다. Webhook은 HMAC 서명과 재시도, DLQ를 지원합니다."
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            API Key 발급
          </Button>
        }
      />

      <Card className="mb-4">
        <CardHeader title="API Key" />
        <CardBody>
          <AsyncSection
            data={keys.data}
            error={keys.error}
            errorText={keys.errorText}
            isLoading={keys.isLoading}
            onRetry={() => void keys.mutate()}
            isEmpty={(data) => data.length === 0}
            empty={{
              title: "발급된 키가 없습니다",
              description: "외부 시스템에서 API를 호출하려면 키가 필요합니다.",
              action: <Button onClick={() => setCreating(true)}>API Key 발급</Button>,
            }}
          >
            {(rows) => (
              <TableWrap>
                <Table>
                  <thead>
                    <tr>
                      <Th>이름</Th>
                      <Th>접두사</Th>
                      <Th>환경</Th>
                      <Th>Scope</Th>
                      <Th>상태</Th>
                      <Th align="right">최근 사용</Th>
                      <Th align="right">조치</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((key) => (
                      <Tr key={key.id}>
                        <Td>
                          <span className="font-medium">{key.name}</span>
                          <p className="type-caption mt-0.5">
                            {key.generation}세대
                          </p>
                        </Td>
                        <Td>
                          <Mono>{key.prefix}…</Mono>
                        </Td>
                        <Td>{key.environment}</Td>
                        <Td>
                          <span className="flex flex-wrap gap-1">
                            {key.scopes.slice(0, 3).map((scope) => (
                              <Badge key={scope}>{scope}</Badge>
                            ))}
                            {key.scopes.length > 3 ? (
                              <Badge>+{key.scopes.length - 3}</Badge>
                            ) : null}
                          </span>
                        </Td>
                        <Td>
                          <Badge
                            tone={
                              key.state === "ACTIVE"
                                ? "positive"
                                : key.revoked_at
                                  ? "critical"
                                  : "neutral"
                            }
                          >
                            {humanizeEnum(key.state)}
                          </Badge>
                        </Td>
                        <Td align="right">
                          <span className="text-[13px]">
                            {formatDateTime(key.last_used_at)}
                          </span>
                        </Td>
                        <Td align="right">
                          {key.revoked_at ? (
                            <span className="type-caption">폐기됨</span>
                          ) : (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => void revoke(key.id)}
                            >
                              폐기
                            </Button>
                          )}
                        </Td>
                      </Tr>
                    ))}
                  </tbody>
                </Table>
              </TableWrap>
            )}
          </AsyncSection>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Webhook"
          description="DNS를 재검증하고, 연속 실패 시 자동으로 비활성화됩니다."
        />
        <CardBody>
          <AsyncSection
            data={webhooks.data}
            error={webhooks.error}
            errorText={webhooks.errorText}
            isLoading={webhooks.isLoading}
            onRetry={() => void webhooks.mutate()}
            isEmpty={(data) => data.length === 0}
            empty={{ title: "등록된 Webhook이 없습니다" }}
            skeletonRows={3}
          >
            {(rows) => (
              <ul className="flex flex-col gap-2">
                {rows.map((hook) => (
                  <li
                    key={hook.id}
                    className="rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="min-w-0">
                        <span className="block truncate text-[14px] font-medium">
                          {hook.name}
                        </span>
                        <Mono>{hook.normalized_url}</Mono>
                      </span>
                      <Badge
                        tone={
                          hook.disabled_at
                            ? "critical"
                            : hook.verified_at
                              ? "positive"
                              : "caution"
                        }
                      >
                        {hook.disabled_at
                          ? "비활성"
                          : hook.verified_at
                            ? "검증됨"
                            : "검증 대기"}
                      </Badge>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {hook.event_types.slice(0, 5).map((event) => (
                        <Badge key={event}>{event}</Badge>
                      ))}
                    </div>
                    {hook.failure_count > 0 ? (
                      <p className="type-caption mt-1.5 text-[var(--caution)]">
                        연속 실패 {hook.failure_count}회
                        {hook.disabled_reason ? ` · ${hook.disabled_reason}` : ""}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </AsyncSection>
        </CardBody>
      </Card>

      <CreateKeyModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(value) => {
          setCreating(false);
          setSecret(value);
          void keys.mutate();
        }}
      />

      <Modal
        open={secret !== null}
        onClose={() => setSecret(null)}
        title="API Key가 발급되었습니다"
        description="이 값은 지금 한 번만 표시됩니다. 안전한 곳에 저장하세요."
        size="sm"
        footer={
          <Button onClick={() => setSecret(null)}>저장했습니다</Button>
        }
      >
        <Notice tone="caution" className="mb-3">
          창을 닫으면 다시 확인할 수 없습니다. 분실하면 키를 회전해야 합니다.
        </Notice>
        <code className="block rounded-[10px] bg-[var(--surface-alt)] p-3 font-mono text-[12.5px] break-all">
          {secret}
        </code>
      </Modal>
    </>
  );
}

function CreateKeyModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (secret: string) => void;
}) {
  const [name, setName] = useState("");
  const [environment, setEnvironment] = useState("production");
  const create = useMutation(developer.createApiKey);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const result = await create.run({
      name,
      environment,
      scopes: ["content:read", "content:write"],
    });
    if (result) onCreated(result.secret);
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="API Key 발급"
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            취소
          </Button>
          <Button type="submit" form="key-create" loading={create.isPending}>
            발급
          </Button>
        </>
      }
    >
      <form id="key-create" onSubmit={onSubmit} className="flex flex-col gap-4">
        {create.error ? <Notice tone="critical">{create.error}</Notice> : null}
        <Field label="이름" error={create.fieldErrors.name} required>
          {(props) => (
            <Input
              {...props}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="예: 사내 CMS 연동"
              required
            />
          )}
        </Field>
        <Field label="환경" error={create.fieldErrors.environment}>
          {(props) => (
            <Input
              {...props}
              value={environment}
              onChange={(event) => setEnvironment(event.target.value)}
            />
          )}
        </Field>
      </form>
    </Modal>
  );
}
