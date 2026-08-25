import type { Metadata } from "next";

import { BillingView } from "@/components/console/billing-view";

export const metadata: Metadata = { title: "요금과 사용량" };

export default function BillingPage() {
  return <BillingView />;
}
