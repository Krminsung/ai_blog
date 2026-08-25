import type { Metadata } from "next";

import { ProductsView } from "@/components/console/products-view";

export const metadata: Metadata = { title: "상품" };

export default function ProductsPage() {
  return <ProductsView />;
}
