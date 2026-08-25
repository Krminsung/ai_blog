import type { Metadata } from "next";

import { BriefDetail } from "@/components/console/brief-detail";

export const metadata: Metadata = { title: "브리프 상세" };

export default async function BriefDetailPage({
  params,
}: PageProps<"/console/briefs/[briefId]">) {
  const { briefId } = await params;
  return <BriefDetail briefId={briefId} />;
}
