"""Checks the QuestieDB lookup against known Forever facts. Needs the data downloaded (companion
Settings > Download quest data); skips otherwise."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "companion"))
import questdata  # noqa: E402


def main():
    if not questdata.downloaded():
        print("skipped: quest data not downloaded")
        return
    qd = questdata.QuestData()
    assert qd.load(), qd.error
    ctx = {"zone": "Redridge Mountains", "position": "20.0, 46.5", "level": 19,
           "quests": [{"id": 92, "title": "Redridge Goulash", "area": "Redridge Mountains"}]}

    goulash = qd.facts_for("where can i find the spiders for the redridge goulash quest?", ctx)
    assert "[[quest:Redridge Goulash]]" in goulash and "[[npc:Chef Breanna]]" in goulash
    spider_part = goulash.split("[[item:Crisp Spider Meat]]")[1]
    assert "[[npc:Greater Tarantula]]" in spider_part and "[[npc:Tarantula]]" in spider_part
    assert "Hillsbrad" not in spider_part, "far-away sources hidden when local ones exist"
    assert "rare spawn" in spider_part, "Chatter is marked"

    merlot = qd.facts_for("how do I get the cask of merlot", ctx)
    assert "sold by [[npc:Roberto Pupellyverbos]]" in merlot and "60.0, 76.9" in merlot

    necklace = qd.facts_for("where is hilary's necklace", ctx)  # apostrophe-insensitive
    assert 'object "Glinting Mud"' in necklace and "nearest to the player" in necklace

    plural = qd.facts_for("where do tarantulas spawn", ctx)
    assert plural.startswith("NPC [[npc:Tarantula]]"), plural

    assert qd.facts_for("what talents should I take", ctx) is None, "no false matches on common words"
    fallback = qd.facts_for("which of my quests can I do here?", ctx)
    assert "[[quest:Redridge Goulash]]" in fallback, "quests in the current zone when none are named"
    # NPCs by title, friendly to the player's faction, nearest first
    sw = {"zone": "Stormwind City", "position": "62.0, 73.5", "level": 20, "faction": "Alliance", "class": "Hunter"}
    aid = qd.facts_for("where is the first aid trainer", sw)
    assert aid.startswith("First Aid Trainers") and "[[npc:Shaina Fuller]]" in aid.split("\n")[1], aid
    assert "Mary Edras" not in aid and 'object "First Aid"' not in aid, "no Horde trainer or sign object"
    cooking = qd.facts_for("where do i train cooking in stormwind", sw)
    assert "[[npc:Stephen Ryback]]" in cooking.split("\n")[1] and 'object "Stormwind"' not in cooking
    assert "[[npc:Einris Brightspear]]" in qd.facts_for("where is my class trainer", sw)
    flight = qd.facts_for("nearest flight master?", sw)
    assert "Gryphon Master" in flight.split("\n")[1], flight
    wool = qd.facts_for("where can i buy wool cloth", sw)
    assert "a common drop from" in wool
    print("quest data checks passed")


if __name__ == "__main__":
    main()
