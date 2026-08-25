import type { Metadata } from "next";

import { SignupForm } from "@/components/auth/signup-form";

export const metadata: Metadata = {
  title: "계정 만들기",
  description: "워크스페이스를 만들고 BlogOps AI를 시작합니다.",
};

export default function SignupPage() {
  return <SignupForm />;
}
