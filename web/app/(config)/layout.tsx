import { SectionTabs } from "@/components/SectionTabs";

export default function ConfigLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <SectionTabs groupId="config" />
      <div className="app-main flex-1">{children}</div>
    </>
  );
}
