# Main city module

Core hub-screen housekeeping for the `main_city` node.

## Scenarios

| Key | Priority | When |
|-----|----------|------|
| `check_main_city` | default cron priority | Every 5 minutes, or pushed by high-priority flows that need to return to the hub |

The scenario has no steps; its `node: main_city` is the work. The navigator
routes back to the main city, then normal overlay and cron work can resume from
the expected hub screen.
