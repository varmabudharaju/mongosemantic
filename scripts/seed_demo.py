"""Seed the demo MongoDB with realistic test data.

Loads three collections that exercise everything mongosemantic can do:

- ``articles``   – ~150 short news/blog posts. Good for multi-field search
                   (title + body) and cross-collection search.
- ``products``   – ~150 e-commerce listings. Used to demo *inline* mode.
- ``recipes``    – ~80 long-form recipes (700-1500 chars each). Used to
                   demo *chunking* — each one should produce 4–8 chunks.

Run with::

    MONGOSEMANTIC_URI="mongodb://localhost:27117/?replicaSet=rs0" \\
    MONGOSEMANTIC_DB=demo \\
        python3 scripts/seed_demo.py

The script is idempotent: it wipes the demo db's user collections before
inserting, but leaves any mongosemantic_* state intact (so the worker
can be left running while you re-seed).
"""
from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

random.seed(7)

URI = os.environ.get("MONGOSEMANTIC_URI", "mongodb://localhost:27117/?replicaSet=rs0")
DB_NAME = os.environ.get("MONGOSEMANTIC_DB", "demo")

# ---------------------------------------------------------------------------
# Articles — short news/blog posts. (title, body, category, published_at).
# Topics chosen to give clean cross-topic clustering on the visualize page.
# ---------------------------------------------------------------------------
_ARTICLE_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "travel": [
        ("Cheap flights to Tokyo",
         "Tips for finding affordable airfare to Japan, including off-season travel, budget airlines, and credit-card miles. Departures from JFK and SFO consistently undercut peak-season pricing by 30%."),
        ("Patagonia trekking guide",
         "Multi-day treks in southern Argentina and Chile. The W circuit and the longer O circuit traverse glaciers, lakes, and granite spires. Gear recommendations and weather windows."),
        ("Backpacking through Southeast Asia",
         "Save money traveling Thailand, Vietnam, and Cambodia on a tight budget. Hostels run $8/night, street food $2/meal, and overnight buses cut hotel costs entirely."),
        ("Train across Europe in a month",
         "Eurail pass tactics: which countries are worth it, when reservations are mandatory, and how to chain night trains to skip hotels. Italy and Spain have the best value."),
        ("Hiking the Camino de Santiago",
         "The Camino Francés from St-Jean-Pied-de-Port to Santiago takes five weeks of walking. Albergues, blisters, the meseta, and the Galician rain — all of it part of the pilgrimage."),
        ("Best budget hostels in Lisbon",
         "Old-town Alfama or central Bairro Alto — both have hostels under €25 a night with rooftops. Avoid the airport area unless you have an early flight."),
        ("Iceland ring road in winter",
         "Driving the ring road in February is doable with a 4WD and patience. Aurora viewing peaks near Vík, and most of the south coast remains accessible despite snow."),
        ("Diving the Great Barrier Reef",
         "Liveaboard trips from Cairns visit the outer reef and Coral Sea. Visibility, marine life, and which operators still actively support reef restoration efforts."),
        ("Walking holidays in the Lake District",
         "Self-guided walking weeks in Cumbria. Inn-to-inn routes through Borrowdale, Wasdale, and Eskdale with bag transfers handled by local outfits."),
        ("Road trip the Pacific Coast Highway",
         "Big Sur to San Francisco in three days. Where to stop for sea otters, the best non-touristy coastal towns, and the truth about California gas prices."),
    ],
    "programming": [
        ("Learning Rust in 2026",
         "Beginner-friendly introduction to memory safety, ownership, lifetimes, and async Rust. The borrow checker is less scary than the memes suggest once patterns click."),
        ("PostgreSQL performance tuning",
         "How to read EXPLAIN plans, build the right indexes, and avoid common query antipatterns. Most slow queries are missing a partial index or doing sequential scans on large tables."),
        ("MongoDB aggregation pipelines",
         "Complex data transformations using match, group, lookup, and the newer vectorSearch stage. Pipeline order matters more than people realize for performance."),
        ("Why Go beats Python for CLIs",
         "Single-binary distribution, fast startup, and a standard library that handles flags, JSON, and HTTP without dependencies. Python wins for data science, Go wins for tooling."),
        ("Async Python without tears",
         "asyncio finally feels mature. Best practices for structured concurrency, gathering tasks safely, and avoiding the gotchas around event loops and threads."),
        ("Building a TUI with Textual",
         "Textual makes Python terminal apps feel like web apps without the build step. Keyboard navigation, mouse events, reactive state — all without leaving the terminal."),
        ("Modern JavaScript without frameworks",
         "Vanilla JS in 2026 is genuinely good. Native modules, async/await, fetch, the DOM API — most apps don't need React for a single page."),
        ("SQLite is underrated",
         "Why so many production apps could just use SQLite: zero deployment, full SQL, transactions, and full-text search built in. WAL mode handles concurrency fine."),
        ("Rust for web services",
         "Axum and tokio are the right stack for high-throughput services. The compile-time guarantees catch a class of bugs that show up at 3am in production."),
        ("Type hints in Python finally work",
         "mypy and pyright caught up. Generic types, protocols, and PEP 695 syntax mean Python type hints are pleasant rather than ceremonial."),
        ("Database migrations as code",
         "Schema migrations should live in version control alongside the app. Tools that auto-generate from model diffs are fine for prototyping but bite in production."),
        ("Why your tests are slow",
         "Fixture scopes, module-level imports, real HTTP, and database setup all multiply. The fastest test suite hits a fresh in-memory DB and patches the network."),
        ("Git rebase workflows",
         "Interactive rebase is the single most useful Git skill nobody teaches. Reorder, squash, edit messages — all before the PR opens."),
        ("Webhooks done right",
         "Retries, idempotency keys, signature verification. Most webhook integrations break under load because nobody designed for replay."),
        ("Building reliable cron jobs",
         "Cron is fine until it isn't. Heartbeats, dead-letter queues, and exponential backoff turn a script into a production-grade job."),
    ],
    "cooking": [
        ("Sourdough starter from scratch",
         "Step-by-step for cultivating wild yeast over seven days. Flour, water, patience, and a warm kitchen. Common starter mistakes and how to rescue a sluggish one."),
        ("Knife skills for home cooks",
         "Master the basic cuts: dice, julienne, brunoise, chiffonade. Hold the knife with a pinch grip, claw the other hand. Practice on onions until cuts become uniform."),
        ("Pasta from scratch needs only two ingredients",
         "Flour and eggs. Knead until smooth, rest, roll thin. Wide ribbons of pappardelle take five minutes to cook. The texture is unmatched."),
        ("Roasting a chicken without drama",
         "Salt the bird the day before. High heat for crisp skin. A meat thermometer is non-negotiable. Rest for ten minutes before carving."),
        ("Stock from kitchen scraps",
         "Save chicken bones, onion skins, carrot ends, parsley stems. Simmer for hours, strain, freeze. Better than anything in a box."),
        ("Pickling vegetables in 24 hours",
         "Quick pickles need only vinegar, water, salt, sugar. Cucumbers, red onions, carrots — all transform overnight in the fridge."),
        ("Risotto without stirring constantly",
         "The Cook's Illustrated method: most of the broth at once, lid on, finish with butter and cheese. The texture is creamy without the wrist pain."),
        ("Bread baking with steam",
         "A cast-iron Dutch oven traps steam for the first half of the bake — crust forms slowly. Lid off for the second half for color."),
        ("Knife skills, part two: meat",
         "Breaking down a whole chicken yields more meat than store-bought parts. Sharp boning knife, follow the joints, save the carcass for stock."),
        ("Slow-cooked beans",
         "Soaking is optional. Low and slow in a Dutch oven beats canned every time. Aromatics, a bay leaf, salt at the end."),
        ("Sheet-pan dinners that don't suck",
         "Cut everything to similar thickness, oil generously, don't crowd the pan. Roast at 425°F. Add greens in the last five minutes."),
        ("Caramelizing onions properly",
         "It takes 45 minutes. Anyone who says 10 is lying. Low heat, lid on for the first half, lid off for the second."),
    ],
    "fitness": [
        ("Beginner strength training program",
         "Three days a week of squats, deadlifts, presses, and rows. Progressive overload is the whole game. Track your lifts, eat enough, sleep more."),
        ("Running a first 5K in eight weeks",
         "Couch-to-5K still works. Walk/run intervals, three sessions a week, no heroics. Most injuries come from doing too much too soon."),
        ("Mobility work that actually moves the needle",
         "Hip flexors, ankle dorsiflexion, thoracic rotation — the three areas where desk workers lose the most range. Five minutes a day adds up."),
        ("Why your push-ups plateau",
         "Most people stall at 20 because they never train with weight. Add a backpack, add a deficit, add tempo. The same logic as any other lift."),
        ("Walking is exercise too",
         "10,000 steps is arbitrary but the principle holds. Daily walking volume correlates with longevity more than any gym program."),
        ("Sleep is the most underrated supplement",
         "Eight hours beats every pre-workout. Resistance training adaptations happen during sleep, not in the gym."),
        ("Programming for hypertrophy",
         "Volume drives muscle growth. 10-20 sets per muscle group per week, RIR 1-3 on most sets. Frequency twice a week beats once."),
        ("Cardio for lifters",
         "Zone 2 work two-three days a week complements strength training without interference. Easy pace, nasal breathing, 30-45 minutes."),
        ("Foam rolling, honestly",
         "It's not magic. It feels good and reduces perceived soreness. Won't fix actual injuries — see a physio for those."),
        ("Deadlift form: the boring truths",
         "Brace the core, bar over midfoot, push the floor away. 99% of form issues come from rushing the setup."),
    ],
    "finance": [
        ("Index funds explained",
         "Total-market index funds outperform 90% of actively managed funds over 20 years. Fees compound; so does ignoring market noise."),
        ("Why your 401k allocation matters",
         "Target-date funds are reasonable defaults. Don't pick the highest-return option from last year — that's how people end up overweight tech in 2025."),
        ("Emergency funds without obsessing",
         "Three to six months of expenses in a high-yield savings account. Past six months, every extra dollar is opportunity cost."),
        ("Tax-loss harvesting basics",
         "Realize losses to offset gains, but watch the wash-sale rule. Most brokerages can automate this for taxable accounts."),
        ("Refinancing rules of thumb",
         "Refi when rates drop more than 0.75% and you'll stay in the house long enough to recover closing costs. The break-even calculator beats intuition."),
        ("Mortgage points: usually no",
         "Buying down the rate makes sense only if you'll hold the loan past the break-even. Most people sell or refi before then."),
        ("HSA as a stealth retirement account",
         "Triple tax advantage: pre-tax in, tax-free growth, tax-free out for medical. Pay current medical from cash, let the HSA compound for decades."),
        ("Bond ladders versus bond funds",
         "Ladders give predictable cash flows; funds give liquidity and management. For retirement income, ladders win at scale."),
    ],
}


def _articles() -> list[dict]:
    docs: list[dict] = []
    now = datetime.now(timezone.utc)
    for category, posts in _ARTICLE_TEMPLATES.items():
        for i, (title, body) in enumerate(posts):
            docs.append({
                "title": title,
                "body": body,
                "category": category,
                "tags": [category, f"topic-{i % 4}"],
                "published_at": now - timedelta(days=random.randint(0, 365)),
                "updated_at": now - timedelta(days=random.randint(0, 30)),
                "word_count": len(body.split()),
            })
    random.shuffle(docs)
    return docs


# ---------------------------------------------------------------------------
# Products — e-commerce catalog. Demo for inline mode.
# ---------------------------------------------------------------------------
_PRODUCTS: list[tuple[str, str, str, float]] = [
    # Footwear
    ("FW-001", "Trail running shoes", "Aggressive lugged trail runners with rock plate and water-resistant upper. Built for technical descents and muddy paths.", 145.00),
    ("FW-002", "Road running shoes", "Lightweight road runners with cushioned heel and breathable mesh. Designed for daily training on pavement.", 130.00),
    ("FW-003", "Hiking boots, waterproof", "Full-grain leather hiking boots with Vibram sole. Ankle support for loaded backpacking trips.", 220.00),
    ("FW-004", "Slip-on canvas sneakers", "Casual canvas sneakers, easy to put on. Good for short city walks and errands. Vulcanized rubber sole.", 65.00),
    ("FW-005", "Approach shoes", "Sticky-rubber climbing approach shoes. Edge-friendly toe, breathable mesh. Bridge between hiker and climber.", 165.00),
    ("FW-006", "Snow boots, insulated", "Waterproof insulated boots rated to -25°F. Removable felt liner. Built for actual winter, not aesthetic winter.", 195.00),
    ("FW-007", "Minimalist barefoot shoes", "Zero-drop, wide toe box, thin sole. Lets the foot move naturally. Not for everyone but devoted following.", 110.00),
    ("FW-008", "Trail running gaiters", "Lightweight breathable gaiters keep debris out of low-top trail shoes. Hook-and-loop closure under the foot.", 35.00),
    # Outdoor / camping
    ("OD-101", "Two-person backpacking tent", "Three-season freestanding tent. Two doors, two vestibules, 4.5 lb trail weight. Aluminum poles.", 380.00),
    ("OD-102", "Down sleeping bag, 20°F", "850-fill goose down. Stuffs to the size of a softball. Generous shoulder room without a draft tube gap.", 425.00),
    ("OD-103", "Inflatable sleeping pad", "R-value 4.5, 2.5-inch thick, baffled construction. Pump sack included. Quiet film for fewer crinkles.", 195.00),
    ("OD-104", "Titanium pot, 900ml", "Single-wall titanium pot for solo backpacking. Folding handles, etched volume marks. Weight 110g.", 65.00),
    ("OD-105", "Canister stove", "Reliable canister stove for backpacking. Boils 1L in about 3.5 minutes. Folds small. Piezo igniter.", 60.00),
    ("OD-106", "Trekking poles, carbon", "Three-section carbon trekking poles with cork grips. Extended length 130cm, collapsed 60cm.", 175.00),
    ("OD-107", "60L hiking backpack", "Internal-frame pack for week-long trips. Hydration sleeve, hip-belt pockets, lid converts to daypack.", 290.00),
    ("OD-108", "Water filter, hollow fiber", "Hollow-fiber filter rated to 0.1 micron. Squeeze bottle compatible. Lasts 100,000 liters.", 45.00),
    ("OD-109", "Headlamp, rechargeable", "USB-C rechargeable headlamp, 400 lumens. Red mode for night vision. Tilt adjustment. Burns 8 hours on low.", 55.00),
    ("OD-110", "Bear canister, hard-sided", "IGBC-approved bear-resistant canister for SEKI/Yosemite/Adirondacks. Holds 5 days of food.", 90.00),
    # Kitchen / espresso
    ("KT-201", "Semi-automatic espresso machine", "Dual-boiler espresso machine with PID temperature control. Pre-infusion, PID, programmable shot timer.", 1950.00),
    ("KT-202", "Burr coffee grinder", "Stepless conical burr grinder calibrated for espresso. Single-dose hopper. Workflow cleaner than stepped grinders.", 580.00),
    ("KT-203", "Cast-iron Dutch oven, 5.5L", "Enameled cast-iron Dutch oven for bread, braises, and stock. Sears, simmers, and bakes interchangeably.", 350.00),
    ("KT-204", "Chef's knife, 8-inch", "High-carbon stainless chef's knife. Lightweight, holds edge well. Bolster-less for a full-grind option.", 195.00),
    ("KT-205", "Carbon-steel wok, 14-inch", "Hand-hammered carbon-steel wok with wooden handle. Seasons in a few uses. Heats fast, cools fast.", 110.00),
    ("KT-206", "Sourdough proofing basket", "Cane banneton with linen liner. 9-inch round. Wicks moisture for a crisp crust and clean release.", 35.00),
    ("KT-207", "Digital kitchen scale", "0.1g precision up to 3kg. Coffee-shot timer mode. Auto-tare. The single biggest baking upgrade you can make.", 60.00),
    ("KT-208", "Cookbook: bread fundamentals", "Hardcover bread book covering levain, autolyse, shaping, scoring. Every recipe weighs ingredients in grams.", 45.00),
    ("KT-209", "Espresso tamper, 58mm", "Self-leveling tamper with stainless base. Reduces channeling versus a flat tamp. Heavy enough to do the work.", 65.00),
    ("KT-210", "Pour-over kettle, gooseneck", "1L stainless gooseneck kettle for V60 pour-over. Slow, controllable stream. Stovetop, not electric.", 75.00),
    # Books — gives the dataset some text-heavier rows
    ("BK-301", "Distributed systems textbook", "Graduate-level textbook on distributed systems: consensus, replication, ordering, fault tolerance. Math-heavy in places.", 95.00),
    ("BK-302", "Mediterranean cooking, hardcover", "500 recipes from Greece, Italy, Spain, Lebanon. Photography is gorgeous; technique notes are dense and useful.", 55.00),
    ("BK-303", "Trail-running training plans", "Eight-week and sixteen-week plans for 50K and 50-mile races. Includes elevation and heart-rate strategies.", 28.00),
    ("BK-304", "Software architecture: hard parts", "Trade-offs in modern service architecture. When to split, when to keep monolithic, contract testing patterns.", 65.00),
    # Electronics
    ("EL-401", "Mechanical keyboard, hot-swap", "Hot-swap mechanical keyboard with PBT keycaps. Compatible with most 3- and 5-pin switches.", 165.00),
    ("EL-402", "Ergonomic split keyboard", "Programmable split keyboard with column-staggered layout. Steep learning curve, big payoff for daily typists.", 295.00),
    ("EL-403", "27-inch 4K monitor", "27-inch IPS panel, 4K resolution, USB-C power delivery. Color-accurate enough for design work out of the box.", 600.00),
    ("EL-404", "Webcam, 4K with autofocus", "USB-C 4K webcam with autofocus and HDR. Better than the built-in laptop cam for any video meeting.", 195.00),
    ("EL-405", "Studio condenser microphone", "XLR condenser microphone with cardioid pickup. Needs an interface. Captures podcast-grade audio in a treated room.", 230.00),
    ("EL-406", "USB audio interface", "Two-channel USB audio interface with phantom power. Low-latency monitoring. Bus-powered.", 195.00),
]


def _products() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {
            "sku": sku,
            "name": name,
            "description": description,
            "price": price,
            "category": sku[:2],
            "in_stock": random.choice([True, True, True, False]),
            "updated_at": now - timedelta(hours=random.randint(0, 24 * 7)),
        }
        for sku, name, description, price in _PRODUCTS
    ]


# ---------------------------------------------------------------------------
# Recipes — long-form prose. Each recipe runs ~700–1500 chars so chunking
# (~300 token chunks) produces 3–6 chunks per doc.
# ---------------------------------------------------------------------------
_RECIPES: list[tuple[str, str, str, list[str]]] = [
    ("Classic French baguette",
     "bread",
     "The classic French baguette relies on a long, cool fermentation. Mix flour, water, salt, and a small amount of yeast the day before baking. The dough should be wet and shaggy. Cover and refrigerate for 12 to 18 hours. The next morning, the dough will have risen and developed strong gluten without much kneading. Turn it out gently on a floured surface and divide into three equal pieces. Shape each piece into a long log by gently rolling and stretching. Place the shaped baguettes on a couche or a linen towel dusted with flour. Cover and let rise for another hour at room temperature. While the dough proofs, preheat your oven to 475°F with a baking stone and a cast-iron pan on the bottom rack. Score the baguettes with three or four diagonal slashes using a razor blade or very sharp knife. Slide them onto the stone and immediately pour a cup of hot water into the cast-iron pan to create steam. Bake for about 25 minutes, rotating once, until the crust is deeply golden and the loaves sound hollow when tapped on the bottom. Cool on a rack for at least 30 minutes before slicing — the interior is still cooking as it cools.",
     ["bread", "french", "fermentation"]),
    ("Beef bourguignon",
     "stew",
     "Beef bourguignon is a slow-braised stew from Burgundy. Start with two pounds of beef chuck, cut into two-inch cubes. Pat the meat dry and salt it generously. In a heavy Dutch oven, render four ounces of bacon until crisp. Remove the bacon and brown the beef in batches in the rendered fat. Set the meat aside and add diced onions, carrots, and celery to the pot. Cook until softened, about ten minutes. Add a tablespoon of tomato paste and four cloves of minced garlic. Cook for another minute. Deglaze the pan with a bottle of red Burgundy, scraping up the fond. Return the beef and bacon to the pot. Add a bouquet garni of thyme, parsley stems, and a bay leaf. Bring to a simmer, cover, and transfer to a 325°F oven for two and a half to three hours, until the beef is fork-tender. In a separate pan, sauté pearl onions and mushrooms in butter until golden. Stir them into the stew during the final 20 minutes. Taste and adjust seasoning. Serve over buttered noodles or with crusty bread.",
     ["stew", "french", "braise"]),
    ("Pad thai at home",
     "noodle",
     "Pad thai is fast once you have the ingredients prepped. Soak six ounces of dried rice noodles in warm water for 20 minutes. Make the sauce: three tablespoons each of tamarind paste, fish sauce, and palm sugar (or brown sugar), whisked smooth. Heat a wok or large skillet over high heat. Add two tablespoons of oil and scramble two eggs, then push them to the side. Add half a cup of dried shrimp or sliced firm tofu, and stir-fry briefly. Drain the noodles and add to the pan. Pour the sauce over and toss vigorously. The noodles will absorb the sauce quickly. Add two cups of bean sprouts and a handful of chopped garlic chives. Toss for another minute. The dish is done when the noodles are tender but still have some chew, and the sauce coats everything. Plate and top with chopped roasted peanuts, lime wedges, and chili flakes. Eat immediately — pad thai does not hold well.",
     ["noodle", "thai", "stir-fry"]),
    ("Tuscan white bean soup",
     "soup",
     "Tuscan white bean soup uses dried cannellini beans soaked overnight. Drain and rinse. In a heavy pot, sauté diced pancetta until crisp. Add chopped onion, carrot, and celery. Cook until soft. Stir in four cloves of minced garlic and a tablespoon of tomato paste. Cook for one minute. Add the soaked beans, a parmesan rind, a sprig of fresh rosemary, and enough water or stock to cover by two inches. Bring to a simmer and cook for one and a half to two hours, until the beans are tender. Remove the rosemary and parmesan rind. With an immersion blender, puree about a third of the beans against the side of the pot — this thickens the soup without making it smooth. Taste and adjust salt. Ladle into bowls and finish with a glug of really good olive oil, freshly cracked black pepper, and a piece of toasted bread rubbed with raw garlic.",
     ["soup", "italian", "beans"]),
    ("Chocolate chip cookies, optimized",
     "dessert",
     "These chocolate chip cookies stay chewy for days. Brown one cup of unsalted butter in a saucepan until the milk solids turn deep amber and the butter smells nutty. Pour into a heatproof bowl and chill in the freezer for 20 minutes, stirring once, until it solidifies but stays scoopable. In a large bowl, beat the chilled butter with one cup of brown sugar and half a cup of granulated sugar until fluffy. Add two eggs, one at a time, and a tablespoon of vanilla. Whisk together two and a quarter cups of flour, a teaspoon of baking soda, and a teaspoon of fine salt. Fold the dry ingredients into the wet just until combined. Stir in a generous two cups of dark chocolate chunks. Scoop into golf-ball-sized portions and rest in the fridge for at least an hour — overnight is better. Bake at 375°F for 11 minutes on parchment-lined sheets. The edges should be set, the centers still slightly underdone. Cool on the sheet for five minutes, then transfer to a rack. Sprinkle with flaky sea salt while still warm.",
     ["cookies", "dessert", "chocolate"]),
    ("Roast chicken, simplest version",
     "poultry",
     "A four-pound chicken, salted the day before, will roast better than any complicated preparation. The day before cooking, pat the bird dry and season generously inside and out with kosher salt. Leave it uncovered on a rack in the fridge for 12 to 24 hours. The next day, preheat the oven to 450°F. Stuff the cavity with a lemon halved, half a head of garlic, and a few sprigs of thyme. Truss the legs if you care to. Place the bird breast-side up on a rack in a roasting pan. Roast for about 50 to 60 minutes, until the thigh registers 165°F and the breast 155°F. The skin should be deeply golden and crackling. Tip the bird so any juices in the cavity drain into the pan. Rest on a board for at least 15 minutes before carving. Pour the pan juices over the carved meat.",
     ["chicken", "roast", "weeknight"]),
    ("Margherita pizza on a home oven",
     "pizza",
     "Margherita pizza on a home oven requires a steel or stone preheated for at least an hour at the highest setting. Make a 65% hydration dough the day before: 500g 00 flour, 325g water, 10g salt, 1g instant yeast. Mix until smooth, cover, and refrigerate overnight. The next day, divide into four 200g balls and let them come to room temperature for an hour. Open each ball by hand on a floured surface — never use a rolling pin. The edge should stay puffy. Top with crushed San Marzano tomatoes seasoned only with salt, torn fresh mozzarella, a few basil leaves, and a drizzle of olive oil. Slide onto the preheated stone and bake for six to eight minutes, until the crust is leoparded and the cheese bubbles. The bottom should be crisp but not burned. Finish with more fresh basil and a final drizzle of oil. Eat immediately.",
     ["pizza", "italian", "dough"]),
    ("Miso-glazed salmon",
     "fish",
     "Miso-glazed salmon takes 15 minutes of active cooking but benefits from a brief marinade. Whisk together three tablespoons of white miso, two tablespoons of mirin, two tablespoons of sake, and a tablespoon of sugar until smooth. Pat four salmon fillets dry and submerge them in the marinade for at least 30 minutes — up to 24 hours in the fridge for deeper flavor. Heat the broiler to high with a rack about six inches from the element. Line a sheet pan with foil and oil it lightly. Place the salmon skin-side down and broil for six to eight minutes, watching closely. The miso glaze will char in spots and the fish should flake easily but stay translucent in the middle. Serve over short-grain rice with steamed greens and a pickled cucumber salad. Spoon any pan juices over the top.",
     ["fish", "japanese", "broil"]),
    ("Shakshuka",
     "egg",
     "Shakshuka is eggs poached in a spicy tomato sauce, eaten straight from the pan with bread. In a large skillet, heat olive oil over medium and cook diced onion and red bell pepper until soft, about 10 minutes. Add three cloves of garlic, a tablespoon of sweet paprika, a teaspoon of cumin, and a pinch of cayenne. Cook for a minute until fragrant. Pour in a 28-ounce can of crushed tomatoes and simmer for 15 minutes, breaking up larger pieces. Season with salt and pepper. Make four to six wells in the sauce with the back of a spoon and crack an egg into each. Cover and cook for five to seven minutes, until the whites are set but the yolks still jammy. Shower with crumbled feta, chopped parsley or cilantro, and serve with warm pita or crusty bread for dragging through the sauce.",
     ["egg", "middle-eastern", "skillet"]),
    ("Tom kha gai",
     "soup",
     "Tom kha gai is a coconut soup with chicken, galangal, and lemongrass. Bring a 14-ounce can of coconut milk and an equal amount of chicken stock to a simmer in a medium pot. Add several thick slices of galangal, two bruised stalks of lemongrass cut into segments, and four kaffir lime leaves torn at the spine. Simmer for ten minutes to infuse. Slice a pound of boneless chicken thighs thinly and add to the pot along with half a pound of straw mushrooms or quartered button mushrooms. Cook until the chicken is just done, about five minutes. Off heat, stir in three tablespoons of fish sauce, the juice of two limes, and a teaspoon of palm sugar. Taste and adjust the balance — it should be salty, sour, and slightly sweet. Add bird's-eye chilies to taste and a handful of cilantro. Serve immediately, fishing out the lemongrass and galangal if you'd rather not bite into them.",
     ["soup", "thai", "coconut"]),
]


def _recipes() -> list[dict]:
    now = datetime.now(timezone.utc)
    docs: list[dict] = []
    for title, kind, body, tags in _RECIPES:
        docs.append({
            "title": title,
            "kind": kind,
            "body": body,
            "tags": tags,
            "char_count": len(body),
            "updated_at": now - timedelta(days=random.randint(0, 90)),
        })
    return docs


# ---------------------------------------------------------------------------
def main() -> int:
    client = MongoClient(URI, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]
    try:
        client.admin.command("ping")
    except Exception as e:
        print(f"could not reach {URI}: {e}", file=sys.stderr)
        return 2

    user_collections = ("articles", "products", "recipes",
                        "longform")  # legacy from earlier demo runs
    for name in user_collections:
        db.drop_collection(name)
    # Drop shadow collections too so a fresh apply / index cycle starts clean.
    for name in db.list_collection_names():
        if name.endswith("_embeddings") or "_archive_" in name or "_mig_" in name:
            db.drop_collection(name)
    # Reset mongosemantic state — config, job queue, resume tokens, worker heartbeats.
    for name in (
        "mongosemantic_config",
        "mongosemantic_jobs",
        "mongosemantic_state",
        "mongosemantic_workers",
    ):
        db.drop_collection(name)

    articles = _articles()
    products = _products()
    recipes = _recipes()
    db["articles"].insert_many(articles)
    db["products"].insert_many(products)
    db["recipes"].insert_many(recipes)

    print(f"Seeded {DB_NAME}@{URI}:")
    print(f"  articles : {len(articles)} docs (multi-field, cross-collection demo)")
    print(f"  products : {len(products)} docs (inline-mode demo)")
    print(f"  recipes  : {len(recipes)} long-form docs (chunking demo)")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
