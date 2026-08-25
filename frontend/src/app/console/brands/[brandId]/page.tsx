import type { Metadata } from "next";

import { BrandDetail } from "@/components/console/brand-detail";

export const metadata: Metadata = { title: "브랜드 상세" };

export default async function BrandDetailPage({
  params,
}: PageProps<"/console/brands/[brandId]">) {
  const { brandId } = await params;
  return <BrandDetail brandId={brandId} />;
}
