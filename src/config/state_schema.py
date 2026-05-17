"""Pydantic v2 models for the full per-gamer state (mirrors db/state.yaml)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ScreenState(BaseModel):
    isMainMenu: bool = False
    isWelcome: bool = False
    isMainCity: str = ""
    currentState: str = ""
    titleFact: str = ""


class VIPState(BaseModel):
    isNotify: bool = False
    isActive: bool = False
    isAdd: bool = False
    isAward: bool = False
    isClaim: bool = False
    isVIPAddAvailable: bool = False
    isVIPAddAvailableX: bool = False


class VIP(BaseModel):
    level: int = 0
    time: str = "0s"
    state: VIPState = Field(default_factory=VIPState)


class Resources(BaseModel):
    wood: int = 0
    food: int = 0
    iron: int = 0
    meat: int = 0
    # Hero Recruitment HUD (`key.silver` / `key.gold` / `diamond` regions).
    silver_keys: int = 0
    gold_keys: int = 0
    diamond: int = 0
    # Hero upgrade XP — read by the optimizer's level_up capacity.
    # OCR'd from the heroes screen (or hand-set for now).
    hero_xp: int = 0

    # ``extra: allow`` so we can stash manuals / per-rarity shards / event
    # currencies in ``state.yaml`` without touching the schema each time.
    # The optimizer pulls them via ``_GLOBAL_RESOURCE_KEYS`` in
    # ``optimizer/capacities.py``.
    model_config = {"extra": "allow"}


class ExplorationState(BaseModel):
    """Exploration → squad_settings matchup card readings.

    Populated on each ``squad_fight`` cron run by OCR'ing the squad screen:

    - ``myPower`` / ``enemyPower`` — power values for the matchup card; the
      scenario's root ``cond`` gates further fights on them
      (``exploration.state.myPower * 1.2 >= exploration.state.enemyPower``).
    - ``battleStatus`` — last banner outcome (``victory`` / ``defeat``).
    - ``isClaimActive`` — whether the claim-rewards button is currently visible.
    """

    isClaimActive: bool = False
    myPower: int = 0
    enemyPower: int = 0
    battleStatus: str = ""


class Exploration(BaseModel):
    """Exploration screen + the squad upgrade card it leads to.

    ``level`` is the squad upgrade tier (drives gear / march / heal-rate scaling)
    and is OCR'd by ``squad_fight``.
    """

    level: int = 0
    state: ExplorationState = Field(default_factory=ExplorationState)
    isNotify: bool = False


class Heroes(BaseModel):
    isnotify: bool = False
    entries: dict[str, object] = Field(default_factory=dict)


class MessagesState(BaseModel):
    isNewMessage: bool = False
    isNewReports: bool = False


class Messages(BaseModel):
    state: MessagesState = Field(default_factory=MessagesState)


class AllianceMembers(BaseModel):
    count: int = 0
    max: int = 0


class AllianceState(BaseModel):
    isNeedSupport: bool = False
    isWar: int = 0
    isChests: int = 0
    isAllianceContributeButton: bool = False
    isAllianceTechButton: bool = False
    polarTerrorCount: int = 0
    isClaimButton: bool = False
    isCanClaimAllChests: bool = True
    lootCountLimit: int = 0
    isGiftClaimAllButton: bool = False
    isMainChest: bool = False


class AllianceSection(BaseModel):
    isNotify: bool = False


class Alliance(BaseModel):
    name: str = ""
    myLevel: int = 0
    power: int = 0
    members: AllianceMembers = Field(default_factory=AllianceMembers)
    state: AllianceState = Field(default_factory=AllianceState)
    war: AllianceSection = Field(default_factory=AllianceSection)
    territory: AllianceSection = Field(default_factory=AllianceSection)
    shop: dict[str, object] = Field(default_factory=dict)
    chests: AllianceSection = Field(default_factory=AllianceSection)
    battle: AllianceSection = Field(default_factory=AllianceSection)
    tech: dict[str, object] = Field(default_factory=lambda: {"isNotify": False, "favorite": True})  # ty: ignore[invalid-assignment]
    help: AllianceSection = Field(default_factory=AllianceSection)


class BuildingState(BaseModel):
    text: str = ""


class FurnaceInfo(BaseModel):
    level: int = 0
    power: int = 0


class Buildings(BaseModel):
    queue1: str = ""
    queue2: str = ""
    state: BuildingState = Field(default_factory=BuildingState)
    furnace: FurnaceInfo = Field(default_factory=FurnaceInfo)
    # Generic building levels keyed by canonical building id (e.g. "furnace").
    # This is intentionally a plain dict to support incremental enrichment.
    levels: dict[str, int] = Field(default_factory=dict)


class ResearchLevel(BaseModel):
    level: int = 0


class Researches(BaseModel):
    battle: ResearchLevel = Field(default_factory=ResearchLevel)
    economy: ResearchLevel = Field(default_factory=ResearchLevel)


class TundraAdventureState(BaseModel):
    isExist: bool = False
    count: int = 0
    isPlay: bool = False
    isAdventurerDrillClaimIsExist: bool = False
    isAdventurerDrillClaim: bool = False
    isAdventureDailyClaim: bool = False


class TundraAdventure(BaseModel):
    state: TundraAdventureState = Field(default_factory=TundraAdventureState)


class FrostyFortune(BaseModel):
    state: dict[str, object] = Field(default_factory=lambda: {"isExist": False})  # ty: ignore[invalid-assignment]


class RecruitmentEvent(BaseModel):
    """Hero Recruitment screen (`hero.recrutment`): OCR region ``free_recruitments_today``."""

    free_recruitments_today: int = 0


class Events(BaseModel):
    tundraAdventure: TundraAdventure = Field(default_factory=TundraAdventure)
    frostyFortune: FrostyFortune = Field(default_factory=FrostyFortune)
    recruitment: RecruitmentEvent = Field(default_factory=RecruitmentEvent)


class TroopState(BaseModel):
    isAvailable: bool = False
    TextStatus: str = ""


class TroopEntry(BaseModel):
    state: TroopState = Field(default_factory=TroopState)


class Troops(BaseModel):
    infantry: TroopEntry = Field(default_factory=TroopEntry)
    lancer: TroopEntry = Field(default_factory=TroopEntry)
    marksman: TroopEntry = Field(default_factory=TroopEntry)


class TechState(BaseModel):
    is_available: bool = False
    TextStatus: str = ""


class Tech(BaseModel):
    state: TechState = Field(default_factory=TechState)


class MailState(BaseModel):
    isWars: int = 0
    isAlliance: int = 0
    isSystem: int = 0
    isReports: int = 0


class Mail(BaseModel):
    isHasMail: int = 0
    state: MailState = Field(default_factory=MailState)


class DailyMissionsState(BaseModel):
    isClaimAll: bool = False
    isClaimButton: bool = False


class DailyMissionsTasks(BaseModel):
    isReseachOneTechnologies: bool = False
    isGatherMeat: bool = False


class DailyMissions(BaseModel):
    isNotify: bool = False
    state: DailyMissionsState = Field(default_factory=DailyMissionsState)
    tasks: DailyMissionsTasks = Field(default_factory=DailyMissionsTasks)


class GrowthMissionsState(BaseModel):
    isClaimAll: bool = False
    isClaimButton: bool = False


class GrowthMissions(BaseModel):
    isNotify: bool = False
    state: GrowthMissionsState = Field(default_factory=GrowthMissionsState)


class ChiefState(BaseModel):
    isNotify: bool = False
    isUrgentMobilization: bool = False
    isComprehensiveCare: bool = False
    isProductivityDay: bool = False
    isRushJob: bool = False
    isDoubleTime: bool = False
    isFestivities: bool = False


class Chief(BaseModel):
    contentment: int = 0
    state: ChiefState = Field(default_factory=ChiefState)


class ArenaState(BaseModel):
    isFreeRefresh: bool = False
    isAvailableFight: bool = False
    countAvailableFight: int = 0
    enemyPower1: int = 0
    enemyPower2: int = 0
    enemyPower3: int = 0
    enemyPower4: int = 0
    enemyPower5: int = 0


class Arena(BaseModel):
    rank: int = 0
    myPower: int = 0
    state: ArenaState = Field(default_factory=ArenaState)


class HealInjuredState(BaseModel):
    isAvailable: bool = False
    isNext: str = ""
    isReplenishAll: bool = False
    statusHeal: str = ""


class HealInjured(BaseModel):
    state: HealInjuredState = Field(default_factory=HealInjuredState)


class GamerState(BaseModel):
    id: int
    nickname: str = ""
    kid: int = 0
    state: int = 0
    avatar: str = ""
    gems: int = 0
    power: int = 0

    century_player_sync_at: float = 0.0

    screenState: ScreenState = Field(default_factory=ScreenState)
    vip: VIP = Field(default_factory=VIP)
    resources: Resources = Field(default_factory=Resources)
    exploration: Exploration = Field(default_factory=Exploration)
    heroes: Heroes = Field(default_factory=Heroes)
    messages: Messages = Field(default_factory=Messages)
    alliance: Alliance = Field(default_factory=Alliance)
    buildings: Buildings = Field(default_factory=Buildings)
    researches: Researches = Field(default_factory=Researches)
    events: Events = Field(default_factory=Events)
    troops: Troops = Field(default_factory=Troops)
    tech: Tech = Field(default_factory=Tech)
    mail: Mail = Field(default_factory=Mail)
    shop: dict[str, object] = Field(default_factory=lambda: {"isnotify": False})  # ty: ignore[invalid-assignment]
    dailyMissions: DailyMissions = Field(default_factory=DailyMissions)
    growthMissions: GrowthMissions = Field(default_factory=GrowthMissions)
    chief: Chief = Field(default_factory=Chief)
    arena: Arena = Field(default_factory=Arena)
    healInjured: HealInjured = Field(default_factory=HealInjured)

    model_config = {"extra": "allow"}


class StateDB(BaseModel):
    gamers: list[GamerState] = []
