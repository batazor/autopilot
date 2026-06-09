import { approvalsHref, instanceHref, queueHref } from "@/lib/fleet-links";
import type { AttentionItem } from "@/lib/types";

/** Where the operator goes to act on an item. Null → nowhere useful to link. */
export function attentionAction(
  item: AttentionItem,
): { href: string; label: string } | null {
  const iid = item.instance_id;
  switch (item.kind) {
    case "approval_pending":
      return iid ? { href: approvalsHref(iid), label: "Review" } : null;
    case "device_offline":
      return { href: "/adb", label: "Devices" };
    case "queue_stuck":
      return { href: queueHref(iid ? { instanceId: iid } : undefined), label: "Queue" };
    case "worker_down":
    case "instance_error":
    case "nav_error":
    case "task_stuck":
      return iid ? { href: instanceHref(iid), label: "Open" } : null;
    case "load_failure":
      // The detail names the broken YAML; there is no stable deep link from a
      // file path into the DSL editor yet.
      return null;
    default:
      return null;
  }
}
