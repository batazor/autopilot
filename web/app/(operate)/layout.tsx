import { FleetLayoutShell } from "@/components/FleetLayoutShell";

export default function OperateLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <FleetLayoutShell groupId="operate">{children}</FleetLayoutShell>;
}
