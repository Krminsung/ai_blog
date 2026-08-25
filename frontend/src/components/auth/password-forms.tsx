"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { AuthCard } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { auth } from "@/lib/api/endpoints";
import { useMutation } from "@/lib/hooks/use-query";

/**
 * Request a reset link. The backend answers with the same message whether or
 * not the address exists, so the UI must not imply the account was found.
 */
export function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const request = useMutation(auth.forgotPassword);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const result = await request.run(email);
    if (result) setSent(true);
  };

  return (
    <AuthCard
      title="비밀번호 재설정"
      description="가입한 이메일 주소로 재설정 링크를 보내 드립니다."
      footer={
        <Link href="/login" className="text-[var(--accent-link)] hover:underline">
          로그인으로 돌아가기
        </Link>
      }
    >
      {sent ? (
        <Notice tone="info">
          해당 주소로 가입된 계정이 있다면 재설정 메일이 전송됩니다. 메일함을
          확인해 주세요.
        </Notice>
      ) : (
        <form onSubmit={onSubmit} className="flex flex-col gap-5">
          {request.error ? (
            <Notice tone="critical">{request.error}</Notice>
          ) : null}
          <Field label="이메일" required>
            {(props) => (
              <Input
                {...props}
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="email"
                required
                autoFocus
              />
            )}
          </Field>
          <Button type="submit" size="lg" loading={request.isPending}>
            재설정 링크 보내기
          </Button>
        </form>
      )}
    </AuthCard>
  );
}

/** Consume the one-time token from the e-mail link and set a new password. */
export function ResetPasswordForm() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [mismatch, setMismatch] = useState(false);
  const reset = useMutation(auth.resetPassword);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (password !== confirm) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    const result = await reset.run(token, password, true);
    if (result) router.replace("/login");
  };

  if (!token) {
    return (
      <AuthCard
        title="링크가 올바르지 않습니다"
        description="재설정 링크에 토큰이 없습니다. 메일의 링크를 다시 열어 주세요."
        footer={
          <Link
            href="/forgot-password"
            className="text-[var(--accent-link)] hover:underline"
          >
            재설정 링크 다시 받기
          </Link>
        }
      >
        <Notice tone="caution">
          링크는 일정 시간이 지나면 만료됩니다. 만료된 경우 새로 요청하세요.
        </Notice>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="새 비밀번호 설정"
      description="설정을 마치면 기존에 로그인된 모든 세션이 해제됩니다."
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-5">
        {reset.error ? <Notice tone="critical">{reset.error}</Notice> : null}

        <Field label="새 비밀번호" hint="12자 이상으로 설정하세요." required>
          {(props) => (
            <Input
              {...props}
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
              minLength={12}
              required
              autoFocus
            />
          )}
        </Field>

        <Field
          label="새 비밀번호 확인"
          error={mismatch ? "두 비밀번호가 일치하지 않습니다." : undefined}
          required
        >
          {(props) => (
            <Input
              {...props}
              type="password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              autoComplete="new-password"
              minLength={12}
              required
            />
          )}
        </Field>

        <Button type="submit" size="lg" loading={reset.isPending}>
          비밀번호 변경
        </Button>
      </form>
    </AuthCard>
  );
}
