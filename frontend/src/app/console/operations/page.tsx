import type { Metadata } from "next";

import { OperationsView } from "@/components/console/operations-view";

export const metadata: Metadata = { title: "운영" };

export default function OperationsPage() {
  return <OperationsView />;
}
