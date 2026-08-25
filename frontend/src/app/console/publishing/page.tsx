import type { Metadata } from "next";

import { PublishingView } from "@/components/console/publishing-view";

export const metadata: Metadata = { title: "발행 작업" };

export default function PublishingPage() {
  return <PublishingView />;
}
