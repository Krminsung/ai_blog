"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { AuthCard } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/feedback";
import { auth } from "@/lib/api/endpoints";
import { useSession } from "@/lib/auth/session-provider";
import { useMutation } from "@/lib/hooks/use-query";

/**
 * Login is two-phase: the password call may answer `mfa_required`, in which
 * case it returns a short-lived challenge token instead of a token pair and
 * the form swaps to the TOTP step.
 */
export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { adoptTokens } = useSession();

  const nextPath = params.get("next") ?? "/console";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [challengeToken, setChallengeToken] = useState<string | null>(null);
  const [code, setCode] = useState("");

  const login = useMutation(auth.login);
  const verify = useMutation(auth.verifyMfaLogin);

  const onPasswordSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const result = await login.run({
      email,
      password,
      // Helps the account's device list stay readable across browsers.
      device_name:
        typeof navigator === "undefined" ? null : navigator.userAgent.slice(0, 120),
    });
    if (!result) return;

    if (result.mfa_required) {
      setChallengeToken(result.challenge_token ?? null);
      return;
    }
    if (result.tokens) {
      await adoptTokens(result.tokens);
      router.replace(nextPath);
    }
  };

  const onCodeSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!challengeToken) return;
    const tokens = await verify.run(challengeToken, code);
    if (!tokens) return;
    await adoptTokens(tokens);
    router.replace(nextPath);
  };

  if (challengeToken) {
    return (
      <AuthCard
        title="인증 코드 입력"
        description="등록한 인증 앱에 표시된 6자리 코드를 입력하세요."
        footer={
          <button
            type="button"
            className="text-[var(--accent-link)] hover:underline"
            onClick={() => {
              setChallengeToken(null);
              setCode("");
              verify.reset();
            }}
          >
            다른 방법으로 로그인
          </button>
        }
      >
        <form onSubmit={onCodeSubmit} className="flex flex-col gap-5">
          {verify.error ? <Notice tone="critical">{verify.error}</Notice> : null}
          <Field label="인증 코드" required>
            {(props) => (
              <Input
                {...props}
                value={code}
                onChange={(event) => setCode(event.target.value)}
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={8}
                placeholder="000000"
                className="text-center text-[22px] tracking-[0.3em]"
                required
                autoFocus
              />
            )}
          </Field>
          <Button type="submit" size="lg" loading={verify.isPending}>
            확인
          </Button>
        </form>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="로그인"
      description="워크스페이스 콘솔로 이동합니다."
      footer={
        <>
          계정이 없으신가요?{" "}
          <Link href="/signup" className="text-[var(--accent-link)] hover:underline">
            계정 만들기
          </Link>
        </>
      }
    >
      <form onSubmit={onPasswordSubmit} className="flex flex-col gap-5">
        {login.error ? <Notice tone="critical">{login.error}</Notice> : null}

        <Field label="이메일" error={login.fieldErrors.email} required>
          {(props) => (
            <Input
              {...props}
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              placeholder="name@company.com"
              required
              autoFocus
            />
          )}
        </Field>

        <Field label="비밀번호" error={login.fieldErrors.password} required>
          {(props) => (
            <Input
              {...props}
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          )}
        </Field>

        <div className="-mt-1 text-right">
          <Link
            href="/forgot-password"
            className="text-[13px] text-[var(--accent-link)] hover:underline"
          >
            비밀번호를 잊으셨나요?
          </Link>
        </div>

        <Button type="submit" size="lg" loading={login.isPending}>
          로그인
        </Button>
      </form>
    </AuthCard>
  );
}
