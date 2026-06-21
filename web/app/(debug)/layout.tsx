import { FleetLayoutShell } from "@/components/FleetLayoutShell";

export default function DebugLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <FleetLayoutShell groupId="debug">{children}</FleetLayoutShell>;
}
