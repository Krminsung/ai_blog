import type { Metadata } from "next";
import { Suspense } from "react";

import { ResetPasswordForm } from "@/components/auth/password-forms";
import { Skeleton } from "@/components/ui/feedback";

export const metadata: Metadata = {
  title: "새 비밀번호 설정",
  description: "메일로 받은 링크에서 새 비밀번호를 설정합니다.",
};

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<Skeleton className="h-80 w-full rounded-[20px]" />}>
      <ResetPasswordForm />
    </Suspense>
  );
}
