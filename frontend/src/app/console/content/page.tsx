import type { Metadata } from "next";

import { ContentView } from "@/components/console/content-view";

export const metadata: Metadata = { title: "콘텐츠" };

export default function ContentPage() {
  return <ContentView />;
}
