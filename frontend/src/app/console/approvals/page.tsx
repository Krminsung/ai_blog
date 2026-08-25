import type { Metadata } from "next";

import { ApprovalsView } from "@/components/console/approvals-view";

export const metadata: Metadata = { title: "승인" };

export default function ApprovalsPage() {
  return <ApprovalsView />;
}
