import type { Metadata } from "next";

import { ConnectionsView } from "@/components/console/connections-view";

export const metadata: Metadata = { title: "채널 연결" };

export default function ConnectionsPage() {
  return <ConnectionsView />;
}
