import type { Metadata } from "next";
import { Suspense } from "react";

import { VerifyEmailPanel } from "@/components/auth/verify-email-panel";
import { Skeleton } from "@/components/ui/feedback";

export const metadata: Metadata = {
  title: "이메일 인증",
  description: "메일로 받은 링크로 계정을 활성화합니다.",
};

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full rounded-[20px]" />}>
      <VerifyEmailPanel />
    </Suspense>
  );
}
