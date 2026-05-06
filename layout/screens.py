"""Screen coordinate constants for 720×1280 @ 320 DPI BlueStacks layout."""

from __future__ import annotations

from layout.types import Point, Region


class MainCityScreen:
    city_name_region = Region(200, 30, 320, 50)
    resource_wood_region = Region(10, 70, 160, 35)
    resource_food_region = Region(10, 110, 160, 35)
    resource_gold_region = Region(10, 150, 160, 35)
    alliance_btn = Point(40, 640)
    arena_btn = Point(680, 400)
    training_btn = Point(360, 800)
    world_map_btn = Point(680, 1200)
    daily_tasks_btn = Point(40, 900)
    profile_btn = Point(40, 40)
    back_btn = Point(30, 60)
    close_popup_btn = Point(660, 200)


class ArenaScreen:
    title_region = Region(200, 40, 320, 60)
    fight_btn = Point(360, 1100)
    tickets_region = Region(250, 200, 220, 50)
    rank_region = Region(200, 300, 320, 50)
    result_region = Region(150, 400, 420, 120)
    close_result_btn = Point(360, 900)
    back_btn = Point(30, 60)


class TrainingScreen:
    title_region = Region(200, 40, 320, 60)
    infantry_tab = Point(120, 200)
    lancer_tab = Point(240, 200)
    marksman_tab = Point(360, 200)
    train_btn = Point(360, 1100)
    queue_slots_region = Region(100, 300, 520, 100)
    quantity_field = Point(360, 700)
    max_btn = Point(580, 700)
    back_btn = Point(30, 60)


class GatheringScreen:
    title_region = Region(200, 40, 320, 60)
    wood_node = Point(180, 600)
    food_node = Point(360, 500)
    send_march_btn = Point(500, 1100)
    march_slots_region = Region(50, 800, 620, 80)
    back_btn = Point(30, 60)


class AllianceScreen:
    title_region = Region(200, 40, 320, 60)
    members_tab = Point(240, 150)
    attack_alerts_region = Region(50, 200, 620, 600)
    help_btn = Point(500, 900)
    back_btn = Point(30, 60)


class AccountSwitcherScreen:
    title_region = Region(100, 40, 520, 60)
    account_list_region = Region(50, 150, 620, 900)
    slot_1 = Point(360, 260)
    slot_2 = Point(360, 450)
    slot_3 = Point(360, 640)
    current_marker_region = Region(580, 200, 80, 600)
    back_btn = Point(30, 60)


class ChiefProfileScreen:
    # Approximate title region (720×1280). Tune via OCR/labeling if needed.
    title_region = Region(80, 0, 360, 80)
    back_btn = Point(30, 60)


MAIN_CITY = MainCityScreen()
ARENA = ArenaScreen()
TRAINING = TrainingScreen()
GATHERING = GatheringScreen()
ALLIANCE = AllianceScreen()
ACCOUNT_SWITCHER = AccountSwitcherScreen()
CHIEF_PROFILE = ChiefProfileScreen()
