import type { Metadata } from "next";

import { CampaignsView } from "@/components/console/campaigns-view";

export const metadata: Metadata = { title: "캠페인" };

export default function CampaignsPage() {
  return <CampaignsView />;
}
