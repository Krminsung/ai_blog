import type { Metadata } from "next";

import { BriefsBoard } from "@/components/console/briefs-board";

export const metadata: Metadata = { title: "브리프" };

export default function BriefsPage() {
  return <BriefsBoard />;
}
