import type { Metadata } from "next";

import { BulkView } from "@/components/console/bulk-view";

export const metadata: Metadata = { title: "대량 생성" };

export default function BulkPage() {
  return <BulkView />;
}
