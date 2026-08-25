"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { AuthCard } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { Notice, Spinner } from "@/components/ui/feedback";
import { auth } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";

type State =
  | { kind: "idle" }
  | { kind: "verifying" }
  | { kind: "verified"; email: string }
  | { kind: "failed"; message: string };

/** Consumes the verification token from the e-mail link exactly once. */
export function VerifyEmailPanel() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token");
  const [state, setState] = useState<State>({ kind: "idle" });
  // The token is single-use, so guard against React's double-invoked effects.
  const attempted = useRef(false);

  useEffect(() => {
    if (!token || attempted.current) return;
    attempted.current = true;
    setState({ kind: "verifying" });
    auth
      .verifyEmail(token)
      .then((user) => setState({ kind: "verified", email: user.email }))
      .catch((error) =>
        setState({ kind: "failed", message: errorMessage(error) }),
      );
  }, [token]);

  if (!token) {
    return (
      <AuthCard
        title="인증 링크가 필요합니다"
        description="메일에 포함된 인증 링크를 열어 주세요."
      >
        <Notice tone="caution">
          링크 주소에 토큰이 없습니다. 메일에서 버튼이나 링크를 직접 눌러
          주세요.
        </Notice>
      </AuthCard>
    );
  }

  if (state.kind === "verifying" || state.kind === "idle") {
    return (
      <AuthCard title="인증 중" description="잠시만 기다려 주세요.">
        <div className="flex justify-center py-4">
          <Spinner className="size-6" />
        </div>
      </AuthCard>
    );
  }

  if (state.kind === "failed") {
    return (
      <AuthCard
        title="인증하지 못했습니다"
        footer={
          <Link href="/login" className="text-[var(--accent-link)] hover:underline">
            로그인으로 이동
          </Link>
        }
      >
        <Notice tone="critical">{state.message}</Notice>
        <p className="mt-4 text-[13px] text-[var(--text-secondary)]">
          링크가 만료되었다면 로그인 화면에서 인증 메일을 다시 요청할 수
          있습니다.
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="인증이 완료되었습니다"
      description={`${state.email} 계정이 활성화되었습니다.`}
    >
      <Button onClick={() => router.push("/login")} size="lg" className="w-full">
        로그인하기
      </Button>
    </AuthCard>
  );
}
