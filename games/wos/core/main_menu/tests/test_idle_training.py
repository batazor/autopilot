"""Auto-training driver wiring (picking logic lives in the troop planner)."""

from games.wos.core.main_menu.exec import DSL_EXEC_HANDLERS


def test_driver_handler_registered():
    assert "find_idle_training_slot" in DSL_EXEC_HANDLERS
