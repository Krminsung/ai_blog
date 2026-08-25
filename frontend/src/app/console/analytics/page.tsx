import type { Metadata } from "next";

import { AnalyticsView } from "@/components/console/analytics-view";

export const metadata: Metadata = { title: "분석" };

export default function AnalyticsPage() {
  return <AnalyticsView />;
}
