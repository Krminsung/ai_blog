import type { Metadata } from "next";

import { KeywordsView } from "@/components/console/keywords-view";

export const metadata: Metadata = { title: "키워드" };

export default function KeywordsPage() {
  return <KeywordsView />;
}
