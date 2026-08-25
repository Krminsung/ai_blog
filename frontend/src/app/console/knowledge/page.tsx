import type { Metadata } from "next";

import { KnowledgeView } from "@/components/console/knowledge-view";

export const metadata: Metadata = { title: "지식 자료" };

export default function KnowledgePage() {
  return <KnowledgeView />;
}
