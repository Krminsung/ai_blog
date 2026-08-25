"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { AuthCard } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { auth, REQUIRED_TERMS } from "@/lib/api/endpoints";
import { DEFAULT_LOCALE, DISPLAY_TIME_ZONE } from "@/lib/env";
import { useMutation } from "@/lib/hooks/use-query";

/**
 * Signup creates the user and their first workspace in one call. The backend
 * always requires e-mail verification afterwards, so this never issues tokens.
 */
export function SignupForm() {
  const [form, setForm] = useState({
    display_name: "",
    workspace_name: "",
    email: "",
    password: "",
    industry: "",
  });
  const [accepted, setAccepted] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  const signup = useMutation(auth.signup);

  const update = (key: keyof typeof form) => (value: string) =>
    setForm((current) => ({ ...current, [key]: value }));

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const result = await signup.run({
      email: form.email,
      password: form.password,
      display_name: form.display_name,
      workspace_name: form.workspace_name,
      industry: form.industry || null,
      country_code: "KR",
      timezone: DISPLAY_TIME_ZONE,
      locale: DEFAULT_LOCALE,
      terms: REQUIRED_TERMS,
    });
    if (result) setDone(result.user.email);
  };

  if (done) {
    return (
      <AuthCard
        title="메일을 확인하세요"
        description={`${done} 주소로 인증 메일을 보냈습니다. 링크를 열면 계정이 활성화됩니다.`}
        footer={
          <Link href="/login" className="text-[var(--accent-link)] hover:underline">
            로그인으로 이동
          </Link>
        }
      >
        <Notice tone="info">
          메일이 도착하지 않았다면 스팸함을 확인하거나, 로그인 화면에서 인증
          메일을 다시 요청할 수 있습니다.
        </Notice>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="계정 만들기"
      description="워크스페이스가 함께 만들어집니다. 나중에 이름을 바꿀 수 있습니다."
      footer={
        <>
          이미 계정이 있으신가요?{" "}
          <Link href="/login" className="text-[var(--accent-link)] hover:underline">
            로그인
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-5">
        {signup.error ? <Notice tone="critical">{signup.error}</Notice> : null}

        <Field label="이름" error={signup.fieldErrors.display_name} required>
          {(props) => (
            <Input
              {...props}
              value={form.display_name}
              onChange={(event) => update("display_name")(event.target.value)}
              autoComplete="name"
              required
              autoFocus
            />
          )}
        </Field>

        <Field
          label="워크스페이스 이름"
          error={signup.fieldErrors.workspace_name}
          hint="팀 또는 브랜드 이름을 사용하세요."
          required
        >
          {(props) => (
            <Input
              {...props}
              value={form.workspace_name}
              onChange={(event) => update("workspace_name")(event.target.value)}
              autoComplete="organization"
              required
            />
          )}
        </Field>

        <Field label="업종" error={signup.fieldErrors.industry} hint="선택 사항">
          {(props) => (
            <Input
              {...props}
              value={form.industry}
              onChange={(event) => update("industry")(event.target.value)}
              placeholder="예: 화장품, SaaS, 교육"
            />
          )}
        </Field>

        <Field label="이메일" error={signup.fieldErrors.email} required>
          {(props) => (
            <Input
              {...props}
              type="email"
              value={form.email}
              onChange={(event) => update("email")(event.target.value)}
              autoComplete="email"
              required
            />
          )}
        </Field>

        <Field
          label="비밀번호"
          error={signup.fieldErrors.password}
          hint="12자 이상으로 설정하세요."
          required
        >
          {(props) => (
            <Input
              {...props}
              type="password"
              value={form.password}
              onChange={(event) => update("password")(event.target.value)}
              autoComplete="new-password"
              minLength={12}
              required
            />
          )}
        </Field>

        <label className="flex items-start gap-2.5 text-[13px] leading-relaxed text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={accepted}
            onChange={(event) => setAccepted(event.target.checked)}
            className="mt-0.5 size-4 accent-[var(--accent)]"
            required
          />
          <span>
            서비스 이용약관과 개인정보 처리방침에 동의합니다. 동의 내역은 버전과
            함께 기록됩니다.
          </span>
        </label>

        <Button
          type="submit"
          size="lg"
          loading={signup.isPending}
          disabled={!accepted}
        >
          계정 만들기
        </Button>
      </form>
    </AuthCard>
  );
}
