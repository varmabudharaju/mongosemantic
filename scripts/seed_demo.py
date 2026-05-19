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
        ("Visa-free travel for US passport holders",
         "Updated list of countries with visa-on-arrival or no visa requirements for American travelers. Schengen rules, the 90/180 trap, and overstaying penalties."),
        ("Sailing the Greek islands on a small budget",
         "Bareboat charters versus cabin charters. Cyclades versus Ionian. Anchoring fees, fuel costs, and provisioning at island grocery stores."),
        ("Trans-Mongolian railway primer",
         "Moscow to Beijing by train: a week of birch forests, lakes, and steppe. Booking from outside Russia, what to pack for week-long sleeper cars, and stopover ideas."),
        ("New Zealand south island campervan loop",
         "Three weeks driving Christchurch to Queenstown and back via Milford Sound. Freedom camping rules, fuel range planning, and the best free hot springs."),
        ("Cycling the Danube path",
         "Passau to Vienna by bike in a week. Flat, well-signposted, riverside the whole way. Bike rental shops drop and collect, so you only ride one direction."),
        ("Slow travel through the Balkans",
         "Albania, Montenegro, Bosnia, Croatia by overnight buses. Coastal towns, mountain monasteries, and prices half of western Europe."),
        ("Visiting Bhutan affordably",
         "The minimum daily package fee is real but lower in shoulder season. What it actually covers, which trekking routes need extra permits, and respecting monastery etiquette."),
        ("Long layovers in Doha and Singapore",
         "Both airlines run free transit tours. Doha's three-hour city tour shows the souq; Singapore's six-hour tour hits Marina Bay. Free hotel for layovers over 8 hours."),
        ("Antarctic expedition cruises",
         "Drake Passage crossings, ice landings, and the eternal question of whether to splurge on a kayaking add-on. Late-season pricing, photography conditions, and gear lists."),
        ("Best Japanese onsen towns for foreigners",
         "Hakone is closest to Tokyo but Kinosaki feels more authentic. Ryokan etiquette, tattoo policies, and the kaiseki dinner experience."),
        ("Visiting Cuba without a tour",
         "Casas particulares versus hotels, currency confusion since the dual-currency reform, and why having printed reservation paperwork helps at immigration."),
        ("Hiking the John Muir Trail",
         "211 miles through the Sierra Nevada, from Yosemite to Mount Whitney. Permit lottery strategies, resupply boxes at Muir Trail Ranch, bear canister rules."),
        ("Diving certifications worth getting",
         "Open Water versus Advanced versus Rescue. Where in Asia to certify cheaply, and which agencies are recognized globally."),
        ("Affordable safaris in Tanzania",
         "Budget overland trips through Serengeti and Ngorongoro versus mid-range lodges. Photography lenses, malaria prophylaxis, and tipping guides."),
        ("Hidden gems in Sicily",
         "Past Palermo and Taormina: the inland baroque towns of Ragusa and Modica, the salt flats of Trapani, the Greek temples at Agrigento."),
        ("Banff in shoulder season",
         "September after Labor Day weekend: trails empty, prices halved, larch trees turning gold. Wildlife is active before hibernation."),
        ("Visiting Turkey beyond Istanbul",
         "Cappadocia balloons, Ephesus ruins, Pamukkale terraces, the Lycian coast. Domestic flights are cheap and intercity buses are surprisingly comfortable."),
        ("Volunteering abroad ethically",
         "Why most short orphanage volunteering does harm. Better alternatives: skills-based volunteering with established NGOs, archaeological digs, conservation projects."),
        ("Cheap eats in Mexico City",
         "Tacos al pastor, tortas de chilaquiles, esquites, churros at El Moro. The vegan taco scene in Roma Norte. Tips for the metro and Uber."),
        ("Train travel in Japan beyond the JR Pass",
         "When the JR Pass actually saves money, when local IC cards win, and the underrated regional passes for Tohoku, Kyushu, and Hokkaido."),
        ("Walking the South West Coast Path",
         "630 miles around Devon and Cornwall. The full thru-hike takes two months; most people do sections. Best week-long stretches and B&B logistics."),
        ("First-time trekkers in Nepal",
         "Annapurna Base Camp versus Everest Base Camp versus Manaslu Circuit. Permits, teahouse pricing, altitude rules, and the best guide-and-porter agencies."),
        ("Visiting Antarctica's last frontiers",
         "Beyond the Antarctic Peninsula: Ross Sea expeditions, emperor penguin trips, fly-in camps. Costs, season windows, and the carbon question."),
        ("Train travel in India",
         "Booking tier 1AC versus 2AC versus 3AC, IRCTC quirks, the difference between Tatkal and general quota, and food on long-distance trains."),
        ("Visa runs for digital nomads",
         "Why the old Bali / Chiang Mai border bounce no longer works. The rise of remote-worker visas in Estonia, Portugal, Mexico, and Costa Rica."),
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
        ("Postgres replication options compared",
         "Logical replication, streaming replication, and the trade-offs of each. WAL shipping for disaster recovery, logical for selective sync."),
        ("Kubernetes is overkill for most teams",
         "If your team doesn't need autoscaling across thousands of nodes, a managed container service is probably enough. The cognitive cost of k8s is real."),
        ("HTTP/3 in production",
         "QUIC adoption is climbing. When it helps (high-latency, lossy networks), when it doesn't (LAN, high-bandwidth), and how to roll it out behind a CDN."),
        ("Designing observability from scratch",
         "Logs, metrics, traces — what to instrument, what to sample, and the difference between alerting and dashboards."),
        ("OpenAPI versus protobuf for new services",
         "JSON over HTTP is forgiving and debuggable; gRPC is faster and statically typed. Pick by team familiarity and consumer count, not benchmarks."),
        ("Refactoring large legacy codebases",
         "Strangler fig pattern, characterization tests, and the patience to leave parts of the code untouched. Big rewrites almost always fail."),
        ("Caching strategies beyond LRU",
         "Probabilistic eviction, segmented LRU, ARC, and the case for not caching at all. Memcached versus Redis for different workloads."),
        ("Distributed tracing without OpenTelemetry sprawl",
         "OTel is the right answer eventually, but a thin home-grown trace correlation header can carry you for years if your fanout is shallow."),
        ("Static-site generators in 2026",
         "Astro and 11ty both produce fast sites without React overhead. Hugo for content-heavy blogs, Next when you actually need SSR."),
        ("Why your CI is too slow",
         "Cache the dependency install, run tests in parallel, fail-fast on the cheap checks. A 20-minute CI loop is killing your shipping velocity."),
        ("WebAssembly outside the browser",
         "WASM for plugin systems, serverless compute, and sandboxed customer code. The interface-types story is still rough but improving."),
        ("Designing for offline-first",
         "Local-first databases like CRDTs and lightweight conflict resolution mean apps work on flaky networks. Sync is the hard part."),
        ("Cost optimization in AWS",
         "Reserved instances versus Savings Plans, spot for batch, lifecycle policies for S3, and tagging discipline so finance can see who owns what."),
        ("Why microservices keep failing teams",
         "The distributed monolith antipattern: services that can't deploy independently because their data is shared. Cohesion matters more than count."),
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
        ("Why your scrambled eggs are wrong",
         "Low heat, constant stirring with a silicone spatula, off the heat early. Butter at the end, not the start. They should be soft curds, not rubber."),
        ("Pickling without canning",
         "Refrigerator pickles keep two months and need no special equipment. Cucumbers, red onions, daikon, carrot — all transform in 24 hours."),
        ("Pan sauces in five minutes",
         "After searing a steak or chicken, deglaze with wine or stock, swirl in cold butter, finish with herbs. The fond on the pan is the flavor."),
        ("Making your own mayonnaise",
         "Egg yolk, mustard, oil added drop by drop. Lemon at the end. Tastes nothing like store-bought and is the base for aioli, tartar, remoulade."),
        ("Confit by sous vide",
         "Duck legs in their own fat at 80°C for 12 hours. Sear hot to crisp the skin. Equivalent to the traditional method without the splatter."),
        ("Knife sharpening at home",
         "Whetstones beat pull-through sharpeners. Two-grit setup — 1000 to set the edge, 6000 to polish. Practice on a cheap knife first."),
        ("Roast vegetables that aren't soggy",
         "High heat (425°F minimum), single layer, don't crowd. Cut to uniform thickness. Salt before roasting, not after."),
        ("Making ice cream without a machine",
         "Whip cream, fold into sweetened condensed milk and flavor. Freeze four hours. Texture is surprisingly close to churned."),
        ("Stir-fry technique",
         "Hot wok, dry ingredients, sear in small batches, build sauce at the end. Crowding the pan steams instead of browns."),
        ("The case for kosher salt",
         "Diamond Crystal kosher has the texture and pickup-rate that all modern recipes assume. Substituting table salt by volume oversalts everything."),
        ("Cooking dried beans, no soak",
         "Beans cook in 2-3 hours without an overnight soak. Pressure cooker drops it to 45 minutes. Salt early — the old advice was wrong."),
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
        ("Heart-rate training zones explained",
         "Zone 2 builds the aerobic base, zone 4 builds threshold. Most runners spend too much time in the gray middle zone where nothing adapts."),
        ("Strength training for runners",
         "Three exercises: squat, deadlift, single-leg work. Two sessions a week, opposite from key runs. Injury rates drop, paces improve."),
        ("Marathon training plans compared",
         "Pfitzinger 18/55 versus Hanson's versus Daniels — which actually fits a working adult's schedule and which assumes you're a college runner."),
        ("Why protein intake matters at any age",
         "1.6-2.2g per kg of body weight, distributed across three or four meals. Even sedentary adults benefit. Older adults need more, not less."),
        ("Hip mobility for cyclists",
         "Hours in the saddle shorten hip flexors. Five minutes daily of hip openers and thoracic extension undoes most of the cycling-posture damage."),
        ("Periodization without overthinking it",
         "Build, hold, peak, recover. Three weeks up, one week down. Most people skip the recovery week and wonder why they plateau."),
        ("Rowing machine programming",
         "The Concept2 is the most underused gym tool. Intervals, threshold pieces, and long steady-state — all carry over to running and cycling."),
        ("Olympic lifting for non-lifters",
         "Cleans and snatches build power that translates to every sport. Hire a coach for the first month — videos cannot fix bar-path errors."),
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
        ("Roth versus traditional IRA",
         "Roth wins if you expect higher taxes in retirement; traditional wins if your marginal rate is higher now. Most under-30 earners should default to Roth."),
        ("Backdoor Roth mechanics",
         "When MAGI excludes you from direct Roth contributions, the backdoor still works. Watch the pro-rata rule if you have existing pre-tax IRA balances."),
        ("ESPP arithmetic",
         "A 15% discount with a six-month lookback is essentially a guaranteed return if you sell immediately. The qualifying disposition usually isn't worth the tax savings."),
        ("Insurance you actually need",
         "Term life if anyone depends on your income, long-term disability if you're working. Whole life policies are sales products, not investments."),
        ("Donor-advised funds",
         "Front-load charitable giving in a high-income year for the tax deduction, then disburse over years. Vanguard Charitable and Fidelity Charitable have similar fees."),
        ("Rebalancing without obsessing",
         "Bands beat calendar rebalancing. If an asset class drifts 5+ percentage points from target, rebalance. Otherwise leave it alone."),
        ("Crypto as part of a portfolio",
         "If at all, treat it like a small high-volatility position — under 5%. Cost basis tracking is its own nightmare; pick an exchange that does it for you."),
    ],
    "science": [
        ("CRISPR ten years on",
         "Base editing and prime editing extended what the original Cas9 system could do. Therapeutic approvals for sickle cell and beta thalassemia changed the conversation."),
        ("James Webb's surprises",
         "Galaxies more massive and earlier than the standard model predicted. Atmospheric composition of exoplanets including potential biosignatures."),
        ("Quantum computing reality check",
         "Where we actually are: noisy intermediate-scale quantum useful for narrow chemistry and optimization. Cryptographically-relevant quantum computers still a decade off."),
        ("mRNA vaccines beyond COVID",
         "Cancer vaccines in trials, RSV vaccines approved, flu mRNA in late-stage trials. The platform's modularity is the real story."),
        ("Why the LHC's next runs matter",
         "Higher luminosity, more events, better detectors. Hints of physics beyond the Standard Model from B-meson decays need confirmation."),
        ("Climate models versus reality",
         "Where models have been accurate (global mean temperature), where they've been low (Arctic warming), and the uncertainty around feedback loops."),
        ("Brain organoids and consciousness",
         "Lab-grown neural tissue now shows organized electrical activity. The ethical question of when an organoid becomes more than a model is unanswered."),
        ("Fusion energy progress",
         "Inertial confinement passed scientific breakeven; magnetic confinement is closer to net commercial gain than five years ago. Engineering, not physics, is the gating problem."),
        ("Gravitational waves from neutron-star mergers",
         "LIGO and Virgo detect events monthly now. Kilonova electromagnetic counterparts confirm the origin of heavy elements like gold and platinum."),
        ("Dark matter searches turning empty",
         "Multiple direct-detection experiments now exclude vast regions of WIMP parameter space. Axion searches and modified-gravity models are getting more attention."),
        ("Plate tectonics inside Mars",
         "InSight mission's seismometer detected marsquakes consistent with limited tectonic activity. The Martian core is smaller and less dense than expected."),
        ("Microbiome research overreach",
         "Many headline correlations between gut bacteria and behavior don't replicate. The reproducibility crisis hit microbiome studies hard around 2022."),
        ("Why most CRISPR therapies are ex vivo",
         "Editing cells outside the body and reinfusing them avoids the delivery problem. In vivo editing is harder; lipid nanoparticles are the front-runner."),
        ("Renewable grids and storage",
         "Battery costs dropped 90% in a decade. Long-duration storage — flow batteries, gravity, compressed air — is the next bottleneck."),
        ("Antibiotic resistance and discovery",
         "AI-driven antibiotic discovery found halicin and abaucin. The pipeline is thicker than it was five years ago but still inadequate for projected resistance."),
        ("Animal cognition is broader than thought",
         "Crows make tools, octopuses solve novel problems, fish have distinct personalities. The bar for 'unique to humans' keeps dropping."),
        ("Whole-genome sequencing in newborns",
         "Pilot programs in the UK and US screen healthy newborns for actionable variants. The ethical questions outpace the technology."),
        ("Why room-temperature superconductors are hard",
         "Phonon-mediated pairing requires high temperatures or extreme pressure. The 2023 LK-99 saga showed how even promising materials fail under scrutiny."),
        ("Permian extinction and methane",
         "Recent isotope studies point to methane from Siberian Traps volcanism as the proximate cause of the 252-Mya mass extinction."),
        ("Lab-grown meat update",
         "Production costs dropped two orders of magnitude in five years but still 10x conventional. Scaling bioreactors is the engineering bottleneck."),
        ("Octopus neural architecture",
         "Two-thirds of an octopus's 500 million neurons are in its arms. Distributed cognition that doesn't map onto any vertebrate brain."),
        ("Solar geoengineering proposals",
         "Stratospheric aerosol injection is technically cheap and globally consequential. The governance problem dwarfs the engineering one."),
        ("Why mosquitoes are still a problem",
         "Genetic-drive mosquito releases work in cages but releasing them in the wild is politically blocked. Insecticide resistance is rising."),
        ("Aging research promising leads",
         "Senolytics, rapamycin analogs, partial reprogramming. None proven in humans yet but the targets are real and well-validated in mice."),
        ("Permafrost carbon feedback",
         "Northern permafrost holds roughly twice the carbon of the atmosphere. Thaw rates are exceeding model projections from 2015."),
    ],
    "music": [
        ("Vinyl pressing plants are backed up again",
         "Indie artists wait 9-12 months for vinyl runs. Lacquer cutters retiring with no replacement is the bottleneck nobody fixes."),
        ("Mixing in the box versus on a console",
         "Modern in-the-box mixes can match analog summing if the gain staging is right. The console workflow advantage is speed, not sound."),
        ("Why guitar amp modelers replaced tube amps for touring",
         "Kemper, Quad Cortex, Helix — all give consistent FOH tone night after night. Tube amps still win in the studio for the right sound."),
        ("Touring economics in 2026",
         "Streaming pays nothing; merch and ticket revenue carry artists. Mid-sized clubs are profitable; arena tours often aren't without sponsorship."),
        ("Synthesizer architectures explained",
         "Subtractive, FM, additive, wavetable, granular — the major flavors and what each does well. Modern soft synths blend two or more."),
        ("MIDI 2.0 in practice",
         "Per-note expression, higher resolution, bi-directional discovery. Adoption is slow because MIDI 1.0 still works and nobody wants to rewrite their DAW."),
        ("Mastering loudness wars are over",
         "Streaming platforms normalize to -14 LUFS. Mastering for dynamic range now sounds louder in playback than mastering for peak."),
        ("Why most home studios sound bad",
         "Bass traps and broadband absorption in the corners fix more than expensive monitors do. Treat the room before upgrading anything."),
        ("Songwriting habits that actually work",
         "Daily writing beats inspired writing. Voice memos for ideas; finish bad ones rather than chasing new ones. Co-writes accelerate craft."),
        ("Modal interchange in pop songwriting",
         "Borrowing chords from parallel modes — the bVI in a major key, the IV from major in a minor key — explains 80% of memorable pop progressions."),
        ("Why pianos are mostly equal-tempered",
         "Just intonation sounds better but only in one key. Equal temperament is the necessary compromise for fixed-pitch instruments."),
        ("Live sound for small rooms",
         "Most small-venue mixes are too loud. PA tuning, monitor wedges versus IEMs, and the actual physics of why feedback always finds the same frequency."),
        ("Studying jazz harmony as a non-jazz musician",
         "Tritone substitution, ii-V-I cycles, modal interchange — the vocabulary improves pop and rock writing too. The Berklee approach is one path; there are others."),
        ("DAW choices in 2026",
         "Logic for songwriters, Pro Tools for studio work, Ableton for electronic and live, Bitwig for modular fans. Reaper for everyone who wants to script everything."),
        ("Drum programming that sounds human",
         "Velocity variation, micro-timing, ghost notes. Programming a real drummer's habits beats fancy plugins. Listen to the kick and snare interplay."),
        ("Why string libraries got so much better",
         "Spitfire and similar labs sample every articulation, dynamics layers, round-robins, true legato. Modern mockups pass casual listening; expert ears still tell."),
        ("Modular synthesis primer",
         "Eurorack is a money pit with a learning curve. Start with a semi-modular like a Mother-32 to understand patching before committing to a case."),
        ("Mixing vocals in the box",
         "Stage 1 cleanup (gating, de-essing, subtractive EQ). Stage 2 character (compression, saturation). Stage 3 effects (reverb, delay) in parallel."),
        ("Why vinyl pressing affects mastering choices",
         "Sub-bass mono'd below 80Hz, sibilance controlled, side length under 20 minutes for loud genres. Digital masters often don't translate."),
        ("Recording acoustic guitar",
         "One mic on the 12th fret, one on the lower bout. Phase-check before committing. Small-diaphragm condensers usually beat large for steel-string."),
    ],
    "gardening": [
        ("Why your tomato leaves curl",
         "Heat stress, irregular watering, or herbicide drift. Curling alone isn't blight — check the leaf undersides for spotting before panicking."),
        ("Composting in small spaces",
         "Bokashi for apartments, worm bins for balconies, tumblers for small yards. The traditional pile needs space and patience few city gardeners have."),
        ("Cover crops for the home gardener",
         "Crimson clover fixes nitrogen and breaks up clay. Cut and drop in spring; the bed is ready in three weeks. Better than tilling."),
        ("Raised beds versus in-ground",
         "Raised beds warm faster and drain better but dry out faster too. In-ground holds moisture but takes years to build soil. Most gardeners benefit from both."),
        ("Drip irrigation on a budget",
         "A simple 1/4-inch tubing system with emitters at each plant beats hand-watering. Connect to a hose timer for vacation coverage."),
        ("Why direct-seeded carrots fail",
         "Carrot seeds need consistent moisture for 14-21 days to germinate. A burlap cover until emergence solves 80% of failures."),
        ("Saving seeds from open-pollinated varieties",
         "Tomatoes, peppers, beans, lettuce, peas — all easy. Squash and corn need isolation to prevent crossing. Drying and storage matter more than people think."),
        ("Native pollinator gardens",
         "Native plants support native bees, which support more species than honeybees do. Bee balm, milkweed, asters, goldenrod — and let the dandelions be."),
        ("Pruning fruit trees in winter",
         "Open-center for stone fruit, central-leader for apples and pears. Cuts in late dormancy heal cleanly. Aggressive thinning gives bigger, better fruit."),
        ("Why hugelkultur works (and doesn't)",
         "Buried wood holds water and provides slow nutrients for years. But the first year is nitrogen-poor as bacteria break down the wood; plant accordingly."),
        ("Container gardening for vegetables",
         "Bigger pots, better drainage, more frequent watering than people expect. Determinate tomatoes, bush beans, lettuce, herbs all thrive in 5-gallon pots."),
        ("Pest pressure changes with climate",
         "Squash vine borers and stink bugs are pushing north. Beneficial insect populations are shifting too. Local extension services have the current pest pressure data."),
        ("No-dig gardening foundations",
         "Cardboard plus six inches of compost makes a bed in a weekend. Subsequent years just need a top-up. The Charles Dowding method is the popular reference."),
        ("Microclimates in a small yard",
         "South-facing walls add 5°F. Tree canopy adds humidity. Pavement holds heat overnight. Map your yard's microclimates before placing perennials."),
        ("Why your zucchini plants suddenly died",
         "Squash vine borer. Slit the stem lengthwise to find and remove the larva, then mound soil over the cut. Plant later in the season to dodge the egg-laying window."),
        ("Fall garden planning",
         "Brassicas, root vegetables, hardy greens all thrive in cool nights. Count back from first frost using days-to-maturity to find your planting window."),
        ("Cold-frame season extension",
         "A simple cold frame extends harvests by 6-8 weeks on each end of the season. Salad greens through January is realistic in zone 6."),
        ("Improving heavy clay soil",
         "Compost, cover crops, broadforking. Sand alone makes it worse. The improvement takes years but compounds."),
        ("Why your basil bolts",
         "Stress: heat, drought, root-binding, or simply day length. Pinch flowers early and often; harvest aggressively to delay bolting."),
        ("Garlic planting and curing",
         "Plant cloves in October for harvest in July. Cure two to three weeks in a dry, shaded, airy spot. Hardneck for cold climates, softneck for warmer."),
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
