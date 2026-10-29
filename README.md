# MapleCharts (Meso Market) data and website

Code and data behind **MapleCharts**: NPC shop prices vs player Free Market prices for MapleStory Classic World,
with price charts, a market index, NPC flips, crafting profits and calculators. Unofficial fan project, not affiliated with Nexon.

## What runs here

| Workflow | When | What it does |
|---|---|---|
| **Copy prices** | every 15 minutes | Copies the public price file from [maplestory-classic.com](https://maplestory-classic.com/market) to the `data` branch, and starts **Build site** when the prices changed. |
| **Wiki data** | every Monday | Collects NPC sell prices, potion recovery, scroll success rates, crafting recipes and monster drops from [MapleClassic Wiki](https://mapleclassic.wiki) (CC BY-NC-SA 4.0) into `data/wiki.json`. |
| **Build site** | after new prices, code or wiki changes | Builds the website with `tools/build_site.py`: the app, one page per item, share images, a sitemap and the latest prices, and publishes it on GitHub Pages. |

- Prices: `https://raw.githubusercontent.com/DMOG189/meso-market-data/data/market.json`
- `web/` holds the built app and page template (made by `tools/build_pages.py` in the MapleCharts project).
- `site/` holds the older build used by the Wix page.

Public settings live in `site.config.json` (site address, custom domain, Supabase project address and its public
"anon" key). Private keys are repository secrets: `SUPABASE_SERVICE_KEY` (for notifications) and, for the Discord
bot, `DISCORD_APP_ID` and `DISCORD_BOT_TOKEN`.

Community database: run `supabase/schema.sql` in the Supabase SQL editor. Discord bot: deploy
`supabase/functions/discord` as an Edge Function with "Verify JWT" off and the secret `DISCORD_PUBLIC_KEY`.
