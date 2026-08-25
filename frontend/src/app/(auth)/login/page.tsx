import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginForm } from "@/components/auth/login-form";
import { Skeleton } from "@/components/ui/feedback";

export const metadata: Metadata = {
  title: "로그인",
  description: "BlogOps AI 콘솔에 로그인합니다.",
};

export default function LoginPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full rounded-[20px]" />}>
      <LoginForm />
    </Suspense>
  );
}
