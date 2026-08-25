import type { Metadata } from "next";

import { BrandsView } from "@/components/console/brands-view";

export const metadata: Metadata = { title: "브랜드" };

export default function BrandsPage() {
  return <BrandsView />;
}
