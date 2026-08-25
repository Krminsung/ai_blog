import type { Metadata } from "next";

import { RepurposeView } from "@/components/console/repurpose-view";

export const metadata: Metadata = { title: "재활용" };

export default function RepurposePage() {
  return <RepurposeView />;
}
