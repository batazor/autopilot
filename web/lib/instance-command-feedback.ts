export type InstanceCommandBody = {
  cmd: "pause" | "resume" | "restart" | "switch_player" | "run_task";
  player_id?: string;
  task_type?: string;
};

export function instanceCommandSuccessMessage(body: InstanceCommandBody): string {
  switch (body.cmd) {
    case "switch_player":
      return `Switch queued${body.player_id ? ` (${body.player_id})` : ""}`;
    case "run_task":
      return `Task queued${body.task_type ? `: ${body.task_type}` : ""}`;
    case "restart":
      return "Restart queued";
    case "pause":
      return "Pause queued";
    case "resume":
      return "Resume queued";
    default:
      return "Command queued";
  }
}
