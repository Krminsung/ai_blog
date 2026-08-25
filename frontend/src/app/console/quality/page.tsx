import type { Metadata } from "next";

import { QualityView } from "@/components/console/quality-view";

export const metadata: Metadata = { title: "품질" };

export default function QualityPage() {
  return <QualityView />;
}
