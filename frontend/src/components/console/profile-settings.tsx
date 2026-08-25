"use client";

import { useState, type FormEvent } from "react";

import { AsyncSection, PageHeader } from "@/components/console/page-parts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { Modal } from "@/components/ui/modal";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { useToast } from "@/components/ui/toast";
import { auth } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useApi, useMutation } from "@/lib/hooks/use-query";
import { useSession } from "@/lib/auth/session-provider";

/** Personal profile, MFA enrollment and active sessions. */
export function ProfileSettings() {
  const { notify } = useToast();
  const { user, refreshProfile } = useSession();

  // The enrollment request is fired by the button, not by the modal mounting,
  // so the dialog opens with the factor already in hand.
  const [factor, setFactor] = useState<MFAFactor | null>(null);
  const enroll = useMutation(auth.enrollMfa);
  const sessions = useApi("auth-sessions", () => auth.sessions());

  const revoke = async (sessionId: string) => {
    try {
      await auth.revokeSession(sessionId);
      notify("세션을 해제했습니다.", "positive");
      void sessions.mutate();
    } catch (error) {
      notify(errorMessage(error), "critical");
    }
  };

  return (
    <>
      <PageHeader
        title="프로필과 보안"
        description="계정 정보, 2단계 인증, 로그인된 기기를 관리합니다."
        breadcrumb={{ href: "/console/settings", label: "설정" }}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Keyed on the user id so the form mounts with real initial values
            rather than syncing them from an effect. */}
        <ProfileForm key={user?.id ?? "loading"} />

        <Card>
          <CardHeader
            title="2단계 인증"
            description="인증 앱으로 생성한 코드를 로그인 시 추가로 입력합니다."
            actions={
              user?.mfa_enabled ? (
                <Badge tone="positive">사용 중</Badge>
              ) : (
                <Button
                  size="sm"
                  loading={enroll.isPending}
                  onClick={async () => {
                    const result = await enroll.run();
                    if (result) setFactor(result);
                    else if (enroll.error) notify(enroll.error, "critical");
                  }}
                >
                  설정
                </Button>
              )
            }
          />
          <CardBody>
            <p className="text-[13.5px] leading-relaxed text-[var(--text-secondary)]">
              {user?.mfa_enabled
                ? "이 계정은 2단계 인증이 켜져 있습니다. 승인 단계에 MFA가 요구되는 워크스페이스에서도 사용할 수 있습니다."
                : "2단계 인증을 켜면 비밀번호가 유출되어도 계정을 보호할 수 있습니다. 일부 승인 단계는 MFA를 요구합니다."}
            </p>
            <p className="type-caption mt-3">
              이메일 인증 {user?.email_verified_at ? "완료" : "미완료"} · 가입{" "}
              {formatDateTime(user?.created_at)}
            </p>
          </CardBody>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader
          title="로그인된 기기"
          description="본인이 아닌 세션은 즉시 해제하세요."
          actions={
            <Button
              size="sm"
              variant="secondary"
              onClick={async () => {
                try {
                  await auth.revokeAllSessions();
                  notify("모든 세션을 해제했습니다.", "positive");
                  void sessions.mutate();
                } catch (error) {
                  notify(errorMessage(error), "critical");
                }
              }}
            >
              모두 해제
            </Button>
          }
        />
        <CardBody>
          <AsyncSection
            data={sessions.data}
            error={sessions.error}
            errorText={sessions.errorText}
            isLoading={sessions.isLoading}
            onRetry={() => void sessions.mutate()}
            isEmpty={(data) => data.length === 0}
            empty={{ title: "활성 세션이 없습니다" }}
            skeletonRows={3}
          >
            {(rows) => (
              <ul className="flex flex-col gap-2">
                {rows.map((session) => (
                  <li
                    key={session.id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-[12px] border border-[var(--hairline-soft)] px-4 py-3"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-[14px]">
                        {session.device_name ?? "알 수 없는 기기"}
                        {session.country_code ? ` · ${session.country_code}` : ""}
                      </span>
                      <span className="type-caption">
                        최근 활동 {formatRelative(session.last_activity_at)} · 만료{" "}
                        {formatDateTime(session.expires_at)}
                      </span>
                    </span>
                    <span className="flex shrink-0 items-center gap-2">
                      {session.mfa_verified_at ? (
                        <Badge tone="positive">MFA</Badge>
                      ) : null}
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => void revoke(session.id)}
                      >
                        해제
                      </Button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </AsyncSection>
        </CardBody>
      </Card>

      {factor ? (
        <MFAEnrollModal
          factor={factor}
          onClose={() => setFactor(null)}
          onDone={async () => {
            setFactor(null);
            notify("2단계 인증을 켰습니다.", "positive");
            await refreshProfile();
          }}
        />
      ) : null}
    </>
  );
}

function ProfileForm() {
  const { notify } = useToast();
  const { user, refreshProfile } = useSession();
  const [form, setForm] = useState({
    display_name: user?.display_name ?? "",
    timezone: user?.timezone ?? "",
    locale: user?.locale ?? "",
  });
  const update = useMutation(auth.updateMe);

  const onSave = async (event: FormEvent) => {
    event.preventDefault();
    const result = await update.run(form);
    if (result) {
      notify("프로필을 저장했습니다.", "positive");
      await refreshProfile();
    }
  };

  return (
    <Card>
      <CardHeader title="프로필" />
      <CardBody>
        <form onSubmit={onSave} className="flex flex-col gap-4">
          {update.error ? <Notice tone="critical">{update.error}</Notice> : null}

          <Field label="이름" error={update.fieldErrors.display_name} required>
            {(props) => (
              <Input
                {...props}
                value={form.display_name}
                onChange={(event) =>
                  setForm({ ...form, display_name: event.target.value })
                }
                required
              />
            )}
          </Field>

          <Field label="이메일" hint="이메일 주소는 변경할 수 없습니다.">
            {(props) => <Input {...props} value={user?.email ?? ""} disabled />}
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="시간대">
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
            <Field label="언어">
              {(props) => (
                <Input
                  {...props}
                  value={form.locale}
                  onChange={(event) =>
                    setForm({ ...form, locale: event.target.value })
                  }
                />
              )}
            </Field>
          </div>

          <Button type="submit" loading={update.isPending} className="self-start">
            저장
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

export interface MFAFactor {
  factor_id: string;
  secret: string;
  provisioning_uri: string;
}

/**
 * Confirmation half of TOTP enrollment. The factor is already provisioned by
 * the caller; recovery codes come back only on confirmation, so they are shown
 * before the dialog can be dismissed.
 */
function MFAEnrollModal({
  factor,
  onClose,
  onDone,
}: {
  factor: MFAFactor;
  onClose: () => void;
  onDone: () => void;
}) {
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const confirm = useMutation(auth.confirmMfa);

  const submit = async () => {
    const result = await confirm.run(factor.factor_id, code);
    if (result) setRecoveryCodes(result.recovery_codes);
  };

  if (recoveryCodes) {
    return (
      <Modal
        open
        onClose={onDone}
        title="복구 코드를 저장하세요"
        description="인증 앱을 사용할 수 없을 때 이 코드로 로그인합니다. 각 코드는 한 번만 쓸 수 있습니다."
        size="sm"
        footer={<Button onClick={onDone}>저장했습니다</Button>}
      >
        <ul className="grid grid-cols-2 gap-2">
          {recoveryCodes.map((recovery) => (
            <li
              key={recovery}
              className="rounded-[8px] bg-[var(--surface-alt)] px-3 py-2 text-center font-mono text-[13px]"
            >
              {recovery}
            </li>
          ))}
        </ul>
      </Modal>
    );
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="2단계 인증 설정"
      description="인증 앱에 아래 키를 등록한 뒤, 표시되는 6자리 코드를 입력하세요."
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            취소
          </Button>
          <Button
            onClick={() => void submit()}
            loading={confirm.isPending}
            disabled={code.length < 6}
          >
            확인
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {confirm.error ? <Notice tone="critical">{confirm.error}</Notice> : null}

        <div>
          <p className="mb-1.5 text-[13px] text-[var(--text-secondary)]">설정 키</p>
          <code className="block rounded-[10px] bg-[var(--surface-alt)] p-3 font-mono text-[13px] break-all">
            {factor.secret}
          </code>
        </div>

        <Field label="인증 코드" required>
          {(props) => (
            <Input
              {...props}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              inputMode="numeric"
              maxLength={6}
              placeholder="000000"
              className="text-center text-[20px] tracking-[0.3em]"
            />
          )}
        </Field>
      </div>
    </Modal>
  );
}
