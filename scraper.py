"""
Farming & Homesteading Knowledge Scraper
Gathers current information from trusted agricultural sources
and builds a searchable database for Emmet AI to reference.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

# Knowledge sources (seed data + structure for future web scraping)
FARMING_KNOWLEDGE_BASE = {
    "weather_&_seasons": [
        {
            "topic": "Frost dates",
            "region": "Lancaster County PA",
            "content": "Last spring frost: ~May 10. First fall frost: ~October 1. Growing season: ~144 days."
        },
        {
            "topic": "Planting calendar",
            "month": "March",
            "content": "Start seeds indoors: tomatoes, peppers, eggplant. Direct sow: peas, spinach, lettuce, kale."
        },
        {
            "topic": "Planting calendar",
            "month": "April",
            "content": "Plant: asparagus, rhubarb, garlic. Direct sow: carrots, beets, beans, corn after last frost date."
        },
        {
            "topic": "Planting calendar",
            "month": "May",
            "content": "Transplant: tomatoes, peppers, eggplant after last frost. Direct sow: beans, corn, squash, cucumbers."
        }
    ],
    "crops": [
        {
            "crop": "Tomatoes",
            "spacing": "24-36 inches apart",
            "days_to_harvest": "60-85 days",
            "sunlight": "6-8 hours minimum",
            "tips": "Use cages or stakes. Prune suckers for better fruit. Rotate crops yearly."
        },
        {
            "crop": "Corn",
            "spacing": "8-12 inches apart, rows 30 inches",
            "days_to_harvest": "60-100 days depending on variety",
            "sunlight": "Full sun (6+ hours)",
            "tips": "Plant in blocks for good pollination. Water deeply. Harvest when silks brown."
        },
        {
            "crop": "Beans",
            "spacing": "4-6 inches apart",
            "days_to_harvest": "50-60 days",
            "sunlight": "6+ hours",
            "tips": "Don't soak seeds. Plant after last frost. Bush beans faster than pole."
        },
        {
            "crop": "Potatoes",
            "spacing": "12 inches apart, rows 3 feet",
            "days_to_harvest": "70-120 days",
            "sunlight": "6+ hours",
            "tips": "Plant seed potatoes 2-4 inches deep. Hill soil around plants as they grow."
        }
    ],
    "livestock": [
        {
            "animal": "Chickens",
            "space_per_bird": "3-4 sq ft indoor, 8-10 sq ft outdoor",
            "feed": "~0.25 lb per day (layer feed)",
            "water": "Continuous access. ~1 cup per day.",
            "tips": "Provide ventilation not drafts. Predator-proof coop essential. Expect 5-6 eggs/week per hen."
        },
        {
            "animal": "Goats",
            "space_per_animal": "200+ sq ft pasture per goat",
            "feed": "2-3% of body weight daily (hay + grain)",
            "water": "~1 gallon per day per 100 lbs",
            "tips": "Goats are escape artists. Strong fencing needed. Can eat brush and weeds."
        },
        {
            "animal": "Horses",
            "space_per_animal": "1-2 acres per horse",
            "feed": "1.5-2% of body weight daily (hay + grain)",
            "water": "5-10 gallons per day",
            "tips": "Require shelter, regular farrier care (8-10 weeks), dental care, vaccines."
        },
        {
            "animal": "Pigs",
            "space_per_animal": "50 sq ft per pig (minimum)",
            "feed": "3-6 lbs per day depending on size/stage",
            "water": "Continuous access, 1-2 gallons per day",
            "tips": "Provide mud wallow or shade. Good for clearing land. 6-12 month grow-out."
        }
    ],
    "soil_care": [
        {
            "practice": "Crop rotation",
            "description": "Don't plant same crop family in same spot 2 years running",
            "benefit": "Prevents disease buildup, improves soil",
            "timeline": "Rotate every 1-2 years"
        },
        {
            "practice": "Cover cropping",
            "description": "Plant clover, alfalfa, rye in off-season",
            "benefit": "Fixes nitrogen, prevents erosion, adds organic matter",
            "timeline": "Plant fall, till in spring"
        },
        {
            "practice": "Composting",
            "description": "Kitchen scraps + yard waste → black gold",
            "benefit": "Rich in nutrients. Improves soil structure. Saves money.",
            "timeline": "3-12 months depending on method"
        }
    ],
    "food_preservation": [
        {
            "method": "Canning",
            "foods": "Tomatoes, salsa, jams, pickles, beans",
            "shelf_life": "1-2 years",
            "equipment": "Canner, jars, lids, pectin for jams",
            "safety": "Follow USDA guidelines. Use pressure canner for low-acid foods."
        },
        {
            "method": "Freezing",
            "foods": "Vegetables, fruits, herbs, prepared meals",
            "shelf_life": "6-12 months",
            "equipment": "Freezer, freezer bags, vacuum sealer (optional)",
            "tips": "Blanch vegetables first. Label with date. Store at 0°F."
        },
        {
            "method": "Root cellar storage",
            "foods": "Potatoes, onions, apples, squash, carrots",
            "shelf_life": "2-6 months depending on crop",
            "conditions": "Cool (32-50°F), dark, humid (90%+)",
            "tips": "Store away from ethylene-producing fruits (apples)."
        },
        {
            "method": "Dehydrating",
            "foods": "Herbs, peppers, tomatoes, apples, jerky",
            "shelf_life": "6-12 months",
            "equipment": "Dehydrator or oven on low",
            "tips": "Store in airtight containers. Use oxygen absorbers for long-term."
        }
    ],
    "tools_equipment": [
        {
            "tool": "Hoe",
            "use": "Weeding, breaking soil, making rows",
            "types": "Standard, warren (pointed), warren/push combo"
        },
        {
            "tool": "Tiller",
            "use": "Breaking ground, prepping beds, mixing soil",
            "types": "Front-tine (small), rear-tine (large), mini tillers"
        },
        {
            "tool": "Shovel vs Spade",
            "use": "Shovel: moving loose material. Spade: digging, edging.",
            "types": "Long handle vs D-handle"
        }
    ]
}

def initialize_knowledge_db(db_path: str = "farming_knowledge.db") -> None:
    """Create and populate the farming knowledge database."""
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Create tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            topic TEXT,
            content TEXT NOT NULL,
            source TEXT DEFAULT 'seed-data',
            added_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            knowledge_id INTEGER,
            keyword TEXT NOT NULL,
            FOREIGN KEY(knowledge_id) REFERENCES knowledge(id)
        )
    """)

    conn.commit()

    # Populate with initial knowledge
    now = datetime.utcnow().isoformat()

    for category, items in FARMING_KNOWLEDGE_BASE.items():
        for item in items:
            content = json.dumps(item)
            cursor.execute(
                "INSERT INTO knowledge (category, topic, content, added_at, updated_at) VALUES (?,?,?,?,?)",
                (category, item.get("topic") or item.get("crop") or item.get("method") or item.get("tool"), content, now, now)
            )

    conn.commit()
    conn.close()
    print(f"✅ Knowledge database initialized at {db_path}")


def search_knowledge(query: str, limit: int = 5, db_path: str = "farming_knowledge.db") -> list:
    """Search the knowledge base for relevant information."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cursor = conn.cursor()
    query_lower = query.lower()

    # Search in content and topic
    cursor.execute("""
        SELECT * FROM knowledge
        WHERE content LIKE ? OR topic LIKE ?
        LIMIT ?
    """, (f"%{query_lower}%", f"%{query_lower}%", limit))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


if __name__ == "__main__":
    initialize_knowledge_db()
    print("\nTesting search...")
    results = search_knowledge("tomato planting", limit=3)
    for r in results:
        print(f"- {r['topic']}: {r['content'][:80]}...")
