import type { Metadata } from "next";

const KINGSHOT_DOCS_URL =
  "https://batazor.github.io/autopilot-page/games/kingshot/";

export const metadata: Metadata = {
  title: "Kingshot | Autopilot",
  description: "Kingshot documentation inside the Autopilot dashboard.",
};

export default function KingshotPage() {
  return (
    <section className="external-doc-page" aria-label="Kingshot documentation">
      <iframe
        title="Kingshot documentation"
        src={KINGSHOT_DOCS_URL}
        className="external-doc-page__frame"
        referrerPolicy="no-referrer-when-downgrade"
      />
    </section>
  );
}
