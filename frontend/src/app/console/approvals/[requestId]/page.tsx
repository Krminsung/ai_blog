import type { Metadata } from "next";

import { ApprovalDetail } from "@/components/console/approval-detail";

export const metadata: Metadata = { title: "승인 상세" };

export default async function ApprovalDetailPage({
  params,
}: PageProps<"/console/approvals/[requestId]">) {
  const { requestId } = await params;
  return <ApprovalDetail requestId={requestId} />;
}
