import { SectionTabs } from "@/components/SectionTabs";

export default function GamesLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <SectionTabs groupId="games" />
      <div className="app-main flex min-h-0 flex-1 flex-col">{children}</div>
    </>
  );
}
