import { ConsoleShell } from "@/components/console/console-shell";

export default function ConsoleLayout({ children }: LayoutProps<"/console">) {
  return <ConsoleShell>{children}</ConsoleShell>;
}
