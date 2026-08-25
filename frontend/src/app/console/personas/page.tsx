import type { Metadata } from "next";

import { PersonasView } from "@/components/console/personas-view";

export const metadata: Metadata = { title: "페르소나" };

export default function PersonasPage() {
  return <PersonasView />;
}
