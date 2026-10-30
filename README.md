# MapleCharts (Meso Market) data and website

Code and data behind **MapleCharts**: NPC shop prices vs player Free Market prices for MapleStory Classic World,
with price charts, a market index, NPC flips, crafting profits and calculators. Unofficial fan project, not affiliated with Nexon.

## What runs here

| Workflow | When | What it does |
|---|---|---|
| **Copy prices** | every 15 minutes | Copies the public price file from [maplestory-classic.com](https://maplestory-classic.com/market) to the `data` branch, and starts **Build site** when the prices changed. |
| **Wiki data** | every Monday | Collects NPC sell prices, potion recovery, scroll success rates, crafting recipes and monster drops from [MapleClassic Wiki](https://mapleclassic.wiki) (CC BY-NC-SA 4.0) into `data/wiki.json`. |
| **Game data** | every Wednesday | Collects item details (requirements, stats, slots, potion effects, NPC sell price, description) into `data/items.json` and every map's monsters, platforms and portals into `data/hunt.json` + `data/huntmaps/`, from MapleStory Classic World's own game data via [maplestory.io](https://maplestory.io). |
| **Discord daily deals** | every day | Posts the day's best NPC flips, items cheaper than the NPC and the week's movers to a Discord channel (`tools/discord_deals.py`). Needs the secret `DISCORD_DEALS_WEBHOOK`. |
| **Build site** | after new prices, code or wiki changes | Builds the website with `tools/build_site.py`: the app, one page per item, share images, a sitemap and the latest prices, and publishes it on GitHub Pages. |

- Prices: `https://raw.githubusercontent.com/DMOG189/meso-market-data/data/market.json`
- `web/` holds the built app and page template (made by `tools/build_pages.py` in the MapleCharts project).
- `site/` holds the older build used by the Wix page.

Public settings live in `site.config.json` (site address, custom domain, Supabase project address and its public
"anon" key). Private keys are repository secrets: `SUPABASE_SERVICE_KEY` (for notifications) and, for the Discord
bot, `DISCORD_APP_ID` and `DISCORD_BOT_TOKEN`; for the daily deals post, `DISCORD_DEALS_WEBHOOK`. Optional public
setting `discordInvite` (a Discord server invite link) adds a "Join our Discord" button.

Community database: run `supabase/schema.sql`, `supabase/feedback.sql` and `supabase/insights.sql` (live player
prices, cookie-free visit counts, owner dashboard) in the Supabase SQL editor. The owner dashboard (Me tab) shows for
accounts with `is_admin` set in `profiles`. Discord bot: deploy
`supabase/functions/discord` as an Edge Function with "Verify JWT" off and the secret `DISCORD_PUBLIC_KEY`.
