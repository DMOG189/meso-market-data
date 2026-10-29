# meso-market-data

Price relay for the **Meso Market** page (MapleStory Classic World, NPC prices vs Free Market prices).

Every 15 minutes a GitHub Action copies the public price file from
[maplestory-classic.com](https://maplestory-classic.com/market) (`market.json`) to the `data` branch,
so a web page on another site can read it:

- Prices: `https://raw.githubusercontent.com/DMOG189/meso-market-data/data/market.json`
- When it was last copied: `https://raw.githubusercontent.com/DMOG189/meso-market-data/data/meta.json`

Only the newest copy is kept. The prices themselves come from maplestory-classic.com, which collects real
Free Market sales and updates about once a day. Unofficial fan project, not affiliated with Nexon.

To copy right now: **Actions → Copy prices → Run workflow**.

## Page code

`site/app.min.js` and `site/app.min.css` are the built code of the Meso Market web page. The page on Wix loads
them from jsDelivr, pinned to a commit and checked with an integrity hash, so changing these files does not
change the live page until its `index.html` points at the new commit.
