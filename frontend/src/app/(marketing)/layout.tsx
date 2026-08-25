import { Footer } from "@/components/marketing/footer";
import { GlobalNav } from "@/components/marketing/global-nav";

export default function MarketingLayout({
  children,
}: LayoutProps<"/">) {
  return (
    <>
      <GlobalNav />
      <main id="main" className="flex-1">
        {children}
      </main>
      <Footer />
    </>
  );
}
