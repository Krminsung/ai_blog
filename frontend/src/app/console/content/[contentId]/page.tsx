import type { Metadata } from "next";

import { ContentDetail } from "@/components/console/content-detail";

export const metadata: Metadata = { title: "콘텐츠 상세" };

export default async function ContentDetailPage({
  params,
}: PageProps<"/console/content/[contentId]">) {
  const { contentId } = await params;
  return <ContentDetail contentId={contentId} />;
}
