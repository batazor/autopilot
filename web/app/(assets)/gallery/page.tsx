import { redirect } from "next/navigation";

/** Legacy path — reference browsing lives on Labeling. */
export default function GalleryPage() {
  redirect("/labeling");
}
