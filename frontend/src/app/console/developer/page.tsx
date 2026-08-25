import type { Metadata } from "next";

import { DeveloperView } from "@/components/console/developer-view";

export const metadata: Metadata = { title: "개발자" };

export default function DeveloperPage() {
  return <DeveloperView />;
}
