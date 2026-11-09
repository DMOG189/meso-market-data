-- MapleCharts trade board from Discord: /sell, /buy, /sold, /alert, /mylistings and /forget on OUR bot.
-- Run after schema.sql. Safe to run more than once.
--
-- How it fits together
--   * The bot (supabase/functions/discord) uses only the public anon key. It calls the functions below, and each one first
--     checks a shared secret: the Vault secret 'discord_bot_rpc' must equal the function secret BOT_RPC_SECRET.
--     Without that secret set, every call fails and nothing can be written. (No service_role key in the bot.)
--   * A Discord listing goes into the same `listings` table as the website's trade board, with source = 'discord'.
--     The seller's Discord display name is shown ONLY because the seller posted the listing themselves.
--     If the Discord account is also linked to a MapleCharts account (signed in with Discord), the listing belongs to that account.
--   * Every Discord listing expires by itself after 6–24 hours (the seller picks; 24 by default). /sold closes it sooner.
--   * Seven days after a Discord listing ends, its name, note and link are wiped; only item, price and time stay (anonymous).
--   * /alert stores a price alert for a Discord user; tools/notify.py sends the DM (needs the DISCORD_BOT_TOKEN repo secret).
--   * The bot never reads other servers' messages and never runs as a user account.
--
-- Set up once (Daniel, in Supabase → SQL editor), with a long random value of your own (32+ characters):
--   select vault.create_secret('<the same value as BOT_RPC_SECRET>', 'discord_bot_rpc');

-- ------------------------------------------------------------------ listings: Discord columns
alter table public.listings alter column user_id drop not null;
alter table public.listings add column if not exists source text not null default 'site';
alter table public.listings add column if not exists discord_user_id text;
alter table public.listings add column if not exists discord_name text;
alter table public.listings add column if not exists discord_url text;
do $$ begin
  alter table public.listings add constraint listings_source_check check (source in ('site', 'discord'));
exception when duplicate_object then null; end $$;
do $$ begin
  alter table public.listings add constraint listings_owner_check
    check (user_id is not null or (source = 'discord' and discord_user_id is not null));
exception when duplicate_object then null; end $$;
do $$ begin
  alter table public.listings add constraint listings_discord_fields_check check (
    (discord_user_id is null or discord_user_id ~ '^(\d{5,25}|anon)$')
    and (discord_name is null or char_length(discord_name) between 1 and 40)
    and (discord_url is null or discord_url ~ '^https://discord\.com/channels/\d+/\d+(/\d+)?$'));
exception when duplicate_object then null; end $$;
create index if not exists listings_discord on public.listings (discord_user_id, status) where source = 'discord';
-- The website can't create Discord rows: its insert policy already requires auth.uid() = user_id, and source stays 'site'.
drop policy if exists "create own listing" on public.listings;
create policy "create own listing" on public.listings for insert to authenticated
  with check (auth.uid() = user_id and source = 'site' and discord_user_id is null and public.not_banned());

-- Limits count per account, or per Discord user for Discord listings.
create or replace function public.listing_limits() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.user_id is not null then
    if (select count(*) from public.listings where user_id = new.user_id and status = 'open' and expires_at > now()) >= 30 then
      raise exception 'You can have up to 30 open listings.';
    end if;
    if (select count(*) from public.listings where user_id = new.user_id and created_at > now() - interval '1 hour') >= 20 then
      raise exception 'Too many listings in the last hour. Try again later.';
    end if;
  else
    if (select count(*) from public.listings where discord_user_id = new.discord_user_id and status = 'open' and expires_at > now()) >= 15 then
      raise exception 'You can have up to 15 open listings.';
    end if;
    if (select count(*) from public.listings where discord_user_id = new.discord_user_id and created_at > now() - interval '1 hour') >= 10 then
      raise exception 'Too many listings in the last hour. Try again later.';
    end if;
  end if;
  if new.expires_at > now() + interval '14 days' then new.expires_at := now() + interval '14 days'; end if;
  return new;
end $$;

-- ------------------------------------------------------------------ Discord alerts and the DM outbox
create table if not exists public.discord_alerts (
  id bigint generated always as identity primary key,
  discord_user_id text not null check (discord_user_id ~ '^\d{5,25}$'),
  item_slug text not null check (item_slug ~ '^[a-z0-9-]{1,80}$'),
  item_name text not null check (char_length(item_name) <= 80),
  direction text not null check (direction in ('below', 'above')),
  price bigint not null check (price > 0 and price < 100000000000),
  active boolean not null default true,
  last_sent_at timestamptz,
  created_at timestamptz not null default now(),
  unique (discord_user_id, item_slug, direction)
);
alter table public.discord_alerts enable row level security;  -- no policies: only the functions below and notify.py

create table if not exists public.discord_outbox (
  id bigint generated always as identity primary key,
  discord_user_id text not null check (discord_user_id ~ '^\d{5,25}$'),
  content text not null check (char_length(content) <= 1900),
  created_at timestamptz not null default now(),
  sent_at timestamptz
);
create index if not exists discord_outbox_pending on public.discord_outbox (id) where sent_at is null;
alter table public.discord_outbox enable row level security;  -- no policies

create table if not exists public.discord_banned (
  discord_user_id text primary key,
  reason text,
  created_at timestamptz not null default now()
);
alter table public.discord_banned enable row level security;  -- no policies; the admin adds rows in the SQL editor

-- ------------------------------------------------------------------ helpers
create or replace function public.bot_ok(p_secret text) returns boolean
language sql stable security definer set search_path = public, extensions as $$
  select coalesce(length(p_secret) >= 32 and exists (
    select 1 from vault.decrypted_secrets s where s.name = 'discord_bot_rpc' and s.decrypted_secret = p_secret), false)
$$;
revoke all on function public.bot_ok(text) from public, anon, authenticated;

-- The MapleCharts account that signed in with this Discord account, if any.
create or replace function public.account_for_discord(p_discord_id text) returns uuid
language sql stable security definer set search_path = public, auth as $$
  select i.user_id from auth.identities i where i.provider = 'discord' and i.provider_id = p_discord_id limit 1
$$;
revoke all on function public.account_for_discord(text) from public, anon, authenticated;

-- How many price alerts someone may keep (supporters.sql replaces this with a bigger limit for supporters).
create or replace function public.alert_limit(p_user uuid) returns int
language sql stable as $$ select 3 $$;

-- ------------------------------------------------------------------ bot functions (called with the anon key + secret)
create or replace function public.discord_post_listing(
  p_secret text, p_discord_id text, p_name text, p_side text, p_slug text, p_item text,
  p_price bigint, p_qty int, p_hours int, p_note text, p_url text
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare acct uuid; row public.listings; hrs int;
begin
  if not public.bot_ok(p_secret) then raise exception 'Not allowed'; end if;
  if exists (select 1 from public.discord_banned where discord_user_id = p_discord_id) then
    raise exception 'This Discord account cannot post listings.';
  end if;
  acct := public.account_for_discord(p_discord_id);
  if acct is not null and exists (select 1 from public.profiles where id = acct and is_banned) then
    raise exception 'This Discord account cannot post listings.';
  end if;
  hrs := greatest(6, least(24, coalesce(p_hours, 24)));
  insert into public.listings (user_id, item_slug, item_name, side, price, qty, note, source, discord_user_id, discord_name, discord_url, expires_at)
  values (acct, p_slug, left(p_item, 80), p_side, p_price, greatest(1, least(100000, coalesce(p_qty, 1))),
          nullif(left(trim(coalesce(p_note, '')), 140), ''), 'discord', p_discord_id, left(nullif(trim(p_name), ''), 40),
          p_url, now() + make_interval(hours => hrs))
  returning * into row;
  return jsonb_build_object('id', row.id, 'expires_at', row.expires_at, 'linked', acct is not null);
end $$;

create or replace function public.discord_my_listings(p_secret text, p_discord_id text) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  if not public.bot_ok(p_secret) then raise exception 'Not allowed'; end if;
  return coalesce((select jsonb_agg(jsonb_build_object('id', id, 'item', item_name, 'slug', item_slug, 'side', side, 'price', price,
                                                        'qty', qty, 'expires_at', expires_at) order by created_at desc)
                   from public.listings
                   where discord_user_id = p_discord_id and status = 'open' and expires_at > now()), '[]'::jsonb);
end $$;

create or replace function public.discord_close_listing(p_secret text, p_discord_id text, p_id bigint) returns int
language plpgsql security definer set search_path = public as $$
declare n int;
begin
  if not public.bot_ok(p_secret) then raise exception 'Not allowed'; end if;
  update public.listings set status = 'closed'
  where discord_user_id = p_discord_id and status = 'open' and (p_id is null or id = p_id);
  get diagnostics n = row_count;
  return n;
end $$;

-- p_price = 0 removes the alert. Returns the number of alerts the user has afterwards.
create or replace function public.discord_set_alert(
  p_secret text, p_discord_id text, p_slug text, p_item text, p_direction text, p_price bigint
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare n int; lim int;
begin
  if not public.bot_ok(p_secret) then raise exception 'Not allowed'; end if;
  if coalesce(p_price, 0) <= 0 then
    delete from public.discord_alerts where discord_user_id = p_discord_id and item_slug = p_slug;
  else
    lim := public.alert_limit(public.account_for_discord(p_discord_id));
    if not exists (select 1 from public.discord_alerts where discord_user_id = p_discord_id and item_slug = p_slug and direction = p_direction)
       and (select count(*) from public.discord_alerts where discord_user_id = p_discord_id) >= lim then
      raise exception 'You can keep up to % alerts.', lim;
    end if;
    insert into public.discord_alerts (discord_user_id, item_slug, item_name, direction, price)
    values (p_discord_id, p_slug, left(p_item, 80), p_direction, p_price)
    on conflict (discord_user_id, item_slug, direction) do update set price = excluded.price, active = true, last_sent_at = null;
  end if;
  select count(*) into n from public.discord_alerts where discord_user_id = p_discord_id;
  return jsonb_build_object('count', n, 'limit', public.alert_limit(public.account_for_discord(p_discord_id)),
    'alerts', coalesce((select jsonb_agg(jsonb_build_object('item', item_name, 'direction', direction, 'price', price) order by created_at)
                        from public.discord_alerts where discord_user_id = p_discord_id), '[]'::jsonb));
end $$;

-- /forget: removes everything stored for this Discord account (listings, alerts, queued messages).
create or replace function public.discord_forget(p_secret text, p_discord_id text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare a int; b int; c int;
begin
  if not public.bot_ok(p_secret) then raise exception 'Not allowed'; end if;
  delete from public.listings where discord_user_id = p_discord_id and user_id is null;
  get diagnostics a = row_count;
  update public.listings set discord_user_id = null, discord_name = null, discord_url = null, source = 'site'
  where discord_user_id = p_discord_id and user_id is not null;
  delete from public.discord_alerts where discord_user_id = p_discord_id;
  get diagnostics b = row_count;
  delete from public.discord_outbox where discord_user_id = p_discord_id;
  get diagnostics c = row_count;
  return jsonb_build_object('listings', a, 'alerts', b, 'messages', c);
end $$;

revoke all on function public.discord_post_listing(text, text, text, text, text, text, bigint, int, int, text, text) from public;
revoke all on function public.discord_my_listings(text, text) from public;
revoke all on function public.discord_close_listing(text, text, bigint) from public;
revoke all on function public.discord_set_alert(text, text, text, text, text, bigint) from public;
revoke all on function public.discord_forget(text, text) from public;
grant execute on function public.discord_post_listing(text, text, text, text, text, text, bigint, int, int, text, text) to anon;
grant execute on function public.discord_my_listings(text, text) to anon;
grant execute on function public.discord_close_listing(text, text, bigint) to anon;
grant execute on function public.discord_set_alert(text, text, text, text, text, bigint) to anon;
grant execute on function public.discord_forget(text, text) to anon;

-- ------------------------------------------------------------------ matches and alerts (website + Discord)
create or replace function public.listing_matches() returns trigger
language plpgsql security definer set search_path = public as $$
declare price_txt text := to_char(new.price, 'FM999,999,999,999');
begin
  if new.side = 'sell' then
    insert into public.notifications (user_id, kind, title, body, url)
    select distinct l.user_id, 'match', new.item_name || ': someone is selling',
           'Listed for ' || price_txt || ' mesos (you offered ' || to_char(l.price, 'FM999,999,999,999') || ').',
           'item/' || new.item_slug || '/'
    from public.listings l
    where l.item_slug = new.item_slug and l.side = 'buy' and l.status = 'open' and l.expires_at > now()
      and l.price >= new.price and l.user_id is not null and l.user_id is distinct from new.user_id;
    insert into public.notifications (user_id, kind, title, body, url)
    select distinct a.user_id, 'alert', new.item_name || ' is listed under your price',
           'A player listed it for ' || price_txt || ' mesos.', 'item/' || new.item_slug || '/'
    from public.alerts a
    where a.item_slug = new.item_slug and a.active and a.direction = 'below' and new.price <= a.price
      and a.user_id is distinct from new.user_id;
    -- Discord: buyers who posted with /buy, and /alert holders.
    insert into public.discord_outbox (discord_user_id, content)
    select distinct x.discord_user_id, '**' || new.item_name || '** is listed for ' || price_txt || ' mesos on MapleCharts' ||
           coalesce(' by ' || new.discord_name, '') || '. https://dmog189.github.io/meso-market-data/item/' || new.item_slug || '/'
    from (
      select l.discord_user_id from public.listings l
      where l.item_slug = new.item_slug and l.side = 'buy' and l.status = 'open' and l.expires_at > now()
        and l.price >= new.price and l.discord_user_id is not null
      union
      select a.discord_user_id from public.discord_alerts a
      where a.item_slug = new.item_slug and a.active and a.direction = 'below' and new.price <= a.price
    ) x
    where x.discord_user_id is distinct from new.discord_user_id and x.discord_user_id <> 'anon';
  else
    insert into public.notifications (user_id, kind, title, body, url)
    select distinct l.user_id, 'match', new.item_name || ': someone wants to buy',
           'Offering ' || price_txt || ' mesos (you asked ' || to_char(l.price, 'FM999,999,999,999') || ').',
           'item/' || new.item_slug || '/'
    from public.listings l
    where l.item_slug = new.item_slug and l.side = 'sell' and l.status = 'open' and l.expires_at > now()
      and l.price <= new.price and l.user_id is not null and l.user_id is distinct from new.user_id;
    insert into public.discord_outbox (discord_user_id, content)
    select distinct l.discord_user_id, 'Someone wants to buy **' || new.item_name || '** for ' || price_txt || ' mesos on MapleCharts' ||
           coalesce(' (' || new.discord_name || ')', '') || '. https://dmog189.github.io/meso-market-data/item/' || new.item_slug || '/'
    from public.listings l
    where l.item_slug = new.item_slug and l.side = 'sell' and l.status = 'open' and l.expires_at > now()
      and l.price <= new.price and l.discord_user_id is not null and l.discord_user_id <> 'anon'
      and l.discord_user_id is distinct from new.discord_user_id;
  end if;
  return new;
end $$;

-- ------------------------------------------------------------------ hourly clean-up
-- Ended listings are closed; a week later Discord names, notes and links are wiped (item, price and time stay, anonymous).
create or replace function public.listings_cleanup() returns void
language sql security definer set search_path = public as $$
  update public.listings set status = 'closed' where status = 'open' and expires_at < now();
  update public.listings set discord_name = null, discord_url = null, note = null, discord_user_id = 'anon'
  where source = 'discord' and user_id is null and discord_user_id <> 'anon' and expires_at < now() - interval '7 days';
  delete from public.discord_outbox where created_at < now() - interval '3 days';
$$;
revoke all on function public.listings_cleanup() from public, anon, authenticated;

create extension if not exists pg_cron;
select cron.unschedule(jobname) from cron.job where jobname = 'mc-listings-cleanup';
select cron.schedule('mc-listings-cleanup', '17 * * * *', $$select public.listings_cleanup()$$);
