import type { Metadata } from "next";

import { ForgotPasswordForm } from "@/components/auth/password-forms";

export const metadata: Metadata = {
  title: "비밀번호 재설정",
  description: "가입한 이메일로 비밀번호 재설정 링크를 보냅니다.",
};

export default function ForgotPasswordPage() {
  return <ForgotPasswordForm />;
}
