"use client";

import { useState, type FormEvent } from "react";

import { AsyncSection, PageHeader } from "@/components/console/page-parts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/modal";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { Mono, Table, TableWrap, Td, Th, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { workspaces as workspacesApi } from "@/lib/api/endpoints";
import { formatDate, formatDateTime } from "@/lib/format";
import { useApi, useMutation } from "@/lib/hooks/use-query";
import { useSession } from "@/lib/auth/session-provider";

/** Workspace profile, members and the audit trail. */
export function WorkspaceSettings() {
  const { notify } = useToast();
  const { workspace } = useSession();
  const workspaceId = workspace?.id ?? null;

  const [inviting, setInviting] = useState(false);

  const members = useApi(workspaceId ? ["members", workspaceId] : null, () =>
    workspacesApi.members(workspaceId as string),
  );
  const roles = useApi(workspaceId ? ["roles", workspaceId] : null, () =>
    workspacesApi.roles(workspaceId as string),
  );
  const auditLogs = useApi(workspaceId ? ["audit", workspaceId] : null, () =>
    workspacesApi.auditLogs(workspaceId as string, { limit: 30 }),
  );

  return (
    <>
      <PageHeader
        title="워크스페이스 설정"
        description="이름과 시간대는 캘린더, 예약 발행, 리포트 집계 기준에 그대로 반영됩니다."
      />

      <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
        {/* Keyed on the workspace id so switching workspaces remounts the form
            with fresh initial values instead of syncing state in an effect. */}
        <WorkspaceProfileForm key={workspaceId ?? "none"} />

        <Card>
          <CardHeader
            title="멤버"
            description="역할에 따라 접근 가능한 기능이 달라집니다."
            actions={
              <Button size="sm" onClick={() => setInviting(true)}>
                초대
              </Button>
            }
          />
          <CardBody>
            <AsyncSection
              data={members.data}
              error={members.error}
              errorText={members.errorText}
              isLoading={members.isLoading}
              onRetry={() => void members.mutate()}
              isEmpty={(data) => data.length === 0}
              empty={{ title: "멤버가 없습니다" }}
              skeletonRows={3}
            >
              {(rows) => (
                <TableWrap>
                  <Table>
                    <thead>
                      <tr>
                        <Th>이름</Th>
                        <Th>역할</Th>
                        <Th>상태</Th>
                        <Th align="right">합류</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((member) => (
                        <Tr key={member.id}>
                          <Td>
                            <span className="font-medium">
                              {member.display_name}
                            </span>
                            <p className="type-caption mt-0.5">{member.email}</p>
                          </Td>
                          <Td>
                            <Badge tone={member.role.is_owner ? "positive" : "neutral"}>
                              {member.role.name}
                            </Badge>
                          </Td>
                          <Td>{member.status}</Td>
                          <Td align="right">{formatDate(member.joined_at)}</Td>
                        </Tr>
                      ))}
                    </tbody>
                  </Table>
                </TableWrap>
              )}
            </AsyncSection>
          </CardBody>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader
          title="감사 로그"
          description="권한이 필요한 행위는 모두 불변 기록으로 남습니다."
        />
        <CardBody>
          <AsyncSection
            data={auditLogs.data}
            error={auditLogs.error}
            errorText={auditLogs.errorText}
            isLoading={auditLogs.isLoading}
            onRetry={() => void auditLogs.mutate()}
            isEmpty={(data) => data.length === 0}
            empty={{ title: "기록이 없습니다" }}
            skeletonRows={4}
          >
            {(rows) => (
              <ul className="flex flex-col gap-1.5">
                {rows.map((log) => (
                  <li
                    key={log.id}
                    className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--hairline-soft)] py-2.5 text-[13px] last:border-b-0"
                  >
                    <span className="min-w-0">
                      <Mono>{log.action}</Mono>
                      <span className="type-caption ml-2">
                        {log.target_type} {log.target_id.split("-")[0]}
                      </span>
                    </span>
                    <span className="type-caption shrink-0">
                      {formatDateTime(log.occurred_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </AsyncSection>
        </CardBody>
      </Card>

      <InviteModal
        open={inviting}
        onClose={() => setInviting(false)}
        workspaceId={workspaceId}
        roles={(roles.data ?? []).map((role) => ({ id: role.id, name: role.name }))}
        onInvited={() => {
          setInviting(false);
          notify("초대 메일을 보냈습니다.", "positive");
          void members.mutate();
        }}
      />
    </>
  );
}

function WorkspaceProfileForm() {
  const { notify } = useToast();
  const { workspace, refreshProfile } = useSession();
  const [form, setForm] = useState({
    name: workspace?.name ?? "",
    industry: workspace?.industry ?? "",
    timezone: workspace?.timezone ?? "",
  });
  const update = useMutation(workspacesApi.update);

  const onSave = async (event: FormEvent) => {
    event.preventDefault();
    if (!workspace) return;
    const result = await update.run(workspace.id, {
      name: form.name,
      industry: form.industry || null,
      timezone: form.timezone,
    });
    if (result) {
      notify("워크스페이스를 저장했습니다.", "positive");
      await refreshProfile();
    }
  };

  return (
    <Card>
      <CardHeader title="기본 정보" />
      <CardBody>
        <form onSubmit={onSave} className="flex flex-col gap-4">
          {update.error ? <Notice tone="critical">{update.error}</Notice> : null}

          <Field label="워크스페이스 이름" error={update.fieldErrors.name} required>
            {(props) => (
              <Input
                {...props}
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                required
              />
            )}
          </Field>

          <Field label="업종">
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

          <Field label="시간대" hint="IANA 시간대 이름을 사용합니다. 예: Asia/Seoul">
            {(props) => (
              <Input
                {...props}
                value={form.timezone}
                onChange={(event) =>
                  setForm({ ...form, timezone: event.target.value })
                }
              />
            )}
          </Field>

          <dl className="flex flex-col gap-2 border-t border-[var(--hairline-soft)] pt-4 text-[13px]">
            <div className="flex justify-between gap-2">
              <dt className="text-[var(--text-tertiary)]">슬러그</dt>
              <dd>
                <Mono>{workspace?.slug ?? "—"}</Mono>
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-[var(--text-tertiary)]">데이터 리전</dt>
              <dd>{workspace?.data_region ?? "—"}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-[var(--text-tertiary)]">기본 언어</dt>
              <dd>{workspace?.default_locale ?? "—"}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-[var(--text-tertiary)]">생성일</dt>
              <dd>{formatDate(workspace?.created_at)}</dd>
            </div>
          </dl>

          <Button type="submit" loading={update.isPending} className="self-start">
            저장
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

function InviteModal({
  open,
  onClose,
  workspaceId,
  roles,
  onInvited,
}: {
  open: boolean;
  onClose: () => void;
  workspaceId: string | null;
  roles: { id: string; name: string }[];
  onInvited: () => void;
}) {
  const [email, setEmail] = useState("");
  const [roleId, setRoleId] = useState("");
  const invite = useMutation(workspacesApi.invite);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!workspaceId) return;
    const result = await invite.run(workspaceId, { email, role_id: roleId });
    if (result) onInvited();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="멤버 초대"
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            취소
          </Button>
          <Button type="submit" form="invite" loading={invite.isPending}>
            초대 보내기
          </Button>
        </>
      }
    >
      <form id="invite" onSubmit={onSubmit} className="flex flex-col gap-4">
        {invite.error ? <Notice tone="critical">{invite.error}</Notice> : null}
        <Field label="이메일" error={invite.fieldErrors.email} required>
          {(props) => (
            <Input
              {...props}
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          )}
        </Field>
        <Field label="역할" error={invite.fieldErrors.role_id} required>
          {(props) => (
            <Select
              {...props}
              value={roleId}
              onChange={(event) => setRoleId(event.target.value)}
              required
            >
              <option value="">역할을 선택하세요</option>
              {roles.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </form>
    </Modal>
  );
}
