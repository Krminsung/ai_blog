import type { Metadata } from "next";

import { PrivacyView } from "@/components/console/privacy-view";

export const metadata: Metadata = { title: "개인정보" };

export default function PrivacyPage() {
  return <PrivacyView />;
}
