-- MapleCharts community features on Supabase (Postgres).
-- Accounts, synced favorites, price alerts, notifications, trade listings, messages, ratings, reports,
-- blocks and community price reports. Every table has row level security: people can only read what
-- they should and change only their own rows. Safe to run more than once.

-- ------------------------------------------------------------------ helpers
create or replace function public.clean_text(t text) returns boolean
language sql immutable as $$
  -- Blocks links, e-mail addresses, phone-like numbers and real-money payment talk.
  select t is null or not (
    t ~* '(https?://|www\.|discord\.gg|t\.me/|wa\.me/|\.(com|net|org|io|gg|me|co|ly)(/|\s|$))'
    or t ~* '[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}'
    or t ~ '\+?\d[\d\s\-\.\(\)]{8,}\d'
    or t ~* '(paypal|venmo|cash ?app|bitcoin|crypto|usdt|real money|irl money|\$ ?\d|\d ?(usd|eur|ils|nis)\M)'
  )
$$;

-- ------------------------------------------------------------------ profiles
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null default 'Player' check (char_length(display_name) between 1 and 32),
  ign text check (ign is null or char_length(ign) <= 24),
  avatar_url text,
  is_admin boolean not null default false,
  is_banned boolean not null default false,
  created_at timestamptz not null default now()
);
alter table public.profiles enable row level security;
drop policy if exists "profiles are public" on public.profiles;
create policy "profiles are public" on public.profiles for select using (true);
drop policy if exists "update own profile" on public.profiles;
create policy "update own profile" on public.profiles for update using (auth.uid() = id) with check (auth.uid() = id);
revoke update on public.profiles from anon, authenticated;
grant update (display_name, ign) on public.profiles to authenticated;

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
declare n text;
begin
  n := coalesce(nullif(new.raw_user_meta_data->>'full_name', ''), nullif(new.raw_user_meta_data->>'name', ''),
                nullif(new.raw_user_meta_data->>'user_name', ''), nullif(split_part(coalesce(new.email, ''), '@', 1), ''), 'Player');
  insert into public.profiles (id, display_name, avatar_url)
  values (new.id, left(n, 32), new.raw_user_meta_data->>'avatar_url')
  on conflict (id) do nothing;
  return new;
end $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users for each row execute function public.handle_new_user();

create or replace function public.not_banned() returns boolean
language sql stable security definer set search_path = public as $$
  select not exists (select 1 from public.profiles p where p.id = auth.uid() and p.is_banned)
$$;

-- ------------------------------------------------------------------ synced app state (favorites, checks, bag)
create table if not exists public.user_state (
  user_id uuid primary key references auth.users(id) on delete cascade,
  data jsonb not null default '{}'::jsonb check (pg_column_size(data) < 200000),
  updated_at timestamptz not null default now()
);
alter table public.user_state enable row level security;
drop policy if exists "own state" on public.user_state;
create policy "own state" on public.user_state for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ------------------------------------------------------------------ notifications and web push
create table if not exists public.notifications (
  id bigint generated always as identity primary key,
  user_id uuid not null references public.profiles(id) on delete cascade,
  kind text not null,
  title text not null,
  body text,
  url text,
  created_at timestamptz not null default now(),
  read_at timestamptz,
  pushed_at timestamptz
);
create index if not exists notifications_user on public.notifications (user_id, created_at desc);
alter table public.notifications enable row level security;
drop policy if exists "read own notifications" on public.notifications;
create policy "read own notifications" on public.notifications for select using (auth.uid() = user_id);
drop policy if exists "mark own notifications" on public.notifications;
create policy "mark own notifications" on public.notifications for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
revoke update on public.notifications from anon, authenticated;
grant update (read_at) on public.notifications to authenticated;

create table if not exists public.push_subscriptions (
  endpoint text primary key,
  user_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  p256dh text not null,
  auth text not null,
  created_at timestamptz not null default now()
);
alter table public.push_subscriptions enable row level security;
drop policy if exists "own push subscriptions" on public.push_subscriptions;
create policy "own push subscriptions" on public.push_subscriptions for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- Keys the server needs (web push). Only the service role can read the private parts.
create table if not exists public.app_secrets (name text primary key, value text not null);
alter table public.app_secrets enable row level security;
create or replace view public.app_public as select value as vapid_public_key from public.app_secrets where name = 'vapid_public';
grant select on public.app_public to anon, authenticated;

-- ------------------------------------------------------------------ price alerts
create table if not exists public.alerts (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  item_slug text not null check (item_slug ~ '^[a-z0-9-]{1,80}$'),
  item_name text not null check (char_length(item_name) <= 80),
  direction text not null check (direction in ('below', 'above')),
  price bigint not null check (price > 0 and price < 100000000000),
  active boolean not null default true,
  last_sent_at timestamptz,
  created_at timestamptz not null default now()
);
alter table public.alerts enable row level security;
drop policy if exists "own alerts" on public.alerts;
create policy "own alerts" on public.alerts for all using (auth.uid() = user_id) with check (auth.uid() = user_id and public.not_banned());

-- ------------------------------------------------------------------ trade listings
create table if not exists public.listings (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  item_slug text not null check (item_slug ~ '^[a-z0-9-]{1,80}$'),
  item_name text not null check (char_length(item_name) <= 80),
  side text not null check (side in ('sell', 'buy')),
  price bigint not null check (price > 0 and price < 100000000000),
  qty int not null default 1 check (qty between 1 and 100000),
  note text check (note is null or (char_length(note) <= 140 and public.clean_text(note))),
  status text not null default 'open' check (status in ('open', 'closed')),
  hidden boolean not null default false,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default now() + interval '7 days'
);
create index if not exists listings_item on public.listings (item_slug, side, status, price);
create index if not exists listings_recent on public.listings (created_at desc);
alter table public.listings enable row level security;
drop policy if exists "read listings" on public.listings;
create policy "read listings" on public.listings for select
  using ((status = 'open' and expires_at > now() and not hidden) or auth.uid() = user_id);
drop policy if exists "create own listing" on public.listings;
create policy "create own listing" on public.listings for insert to authenticated
  with check (auth.uid() = user_id and public.not_banned());
drop policy if exists "change own listing" on public.listings;
create policy "change own listing" on public.listings for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists "delete own listing" on public.listings;
create policy "delete own listing" on public.listings for delete using (auth.uid() = user_id);
revoke update on public.listings from anon, authenticated;
grant update (price, qty, note, status, expires_at) on public.listings to authenticated;

create or replace function public.listing_limits() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if (select count(*) from public.listings where user_id = new.user_id and status = 'open' and expires_at > now()) >= 30 then
    raise exception 'You can have up to 30 open listings.';
  end if;
  if (select count(*) from public.listings where user_id = new.user_id and created_at > now() - interval '1 hour') >= 20 then
    raise exception 'Too many listings in the last hour. Try again later.';
  end if;
  if new.expires_at > now() + interval '14 days' then new.expires_at := now() + interval '14 days'; end if;
  return new;
end $$;
drop trigger if exists listings_limits on public.listings;
create trigger listings_limits before insert on public.listings for each row execute function public.listing_limits();

-- A new listing tells matching traders and alert holders right away.
create or replace function public.listing_matches() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.side = 'sell' then
    insert into public.notifications (user_id, kind, title, body, url)
    select distinct l.user_id, 'match', new.item_name || ': someone is selling',
           'Listed for ' || to_char(new.price, 'FM999,999,999,999') || ' mesos (you offered ' || to_char(l.price, 'FM999,999,999,999') || ').',
           'item/' || new.item_slug || '/'
    from public.listings l
    where l.item_slug = new.item_slug and l.side = 'buy' and l.status = 'open' and l.expires_at > now()
      and l.price >= new.price and l.user_id <> new.user_id;
    insert into public.notifications (user_id, kind, title, body, url)
    select distinct a.user_id, 'alert', new.item_name || ' is listed under your price',
           'A player listed it for ' || to_char(new.price, 'FM999,999,999,999') || ' mesos.', 'item/' || new.item_slug || '/'
    from public.alerts a
    where a.item_slug = new.item_slug and a.active and a.direction = 'below' and new.price <= a.price and a.user_id <> new.user_id;
  else
    insert into public.notifications (user_id, kind, title, body, url)
    select distinct l.user_id, 'match', new.item_name || ': someone wants to buy',
           'Offering ' || to_char(new.price, 'FM999,999,999,999') || ' mesos (you asked ' || to_char(l.price, 'FM999,999,999,999') || ').',
           'item/' || new.item_slug || '/'
    from public.listings l
    where l.item_slug = new.item_slug and l.side = 'sell' and l.status = 'open' and l.expires_at > now()
      and l.price <= new.price and l.user_id <> new.user_id;
  end if;
  return new;
end $$;
drop trigger if exists listings_matches on public.listings;
create trigger listings_matches after insert on public.listings for each row execute function public.listing_matches();

-- ------------------------------------------------------------------ blocks, conversations, messages
create table if not exists public.blocks (
  blocker uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  blocked uuid not null references public.profiles(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (blocker, blocked)
);
alter table public.blocks enable row level security;
drop policy if exists "own blocks" on public.blocks;
create policy "own blocks" on public.blocks for all using (auth.uid() = blocker) with check (auth.uid() = blocker);

create or replace function public.is_blocked(a uuid, b uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.blocks where (blocker = a and blocked = b) or (blocker = b and blocked = a))
$$;

create table if not exists public.conversations (
  id bigint generated always as identity primary key,
  listing_id bigint references public.listings(id) on delete set null,
  item_slug text,
  item_name text,
  user_a uuid not null references public.profiles(id) on delete cascade,
  user_b uuid not null references public.profiles(id) on delete cascade,
  created_at timestamptz not null default now(),
  last_at timestamptz not null default now(),
  check (user_a <> user_b)
);
create unique index if not exists conversations_once on public.conversations (listing_id, user_a);
alter table public.conversations enable row level security;
drop policy if exists "participants read conversations" on public.conversations;
create policy "participants read conversations" on public.conversations for select using (auth.uid() in (user_a, user_b));

-- Opens (or reuses) a conversation with the owner of a listing.
create or replace function public.start_conversation(p_listing bigint) returns bigint
language plpgsql security definer set search_path = public as $$
declare l public.listings; cid bigint;
begin
  if auth.uid() is null then raise exception 'Sign in first.'; end if;
  if not public.not_banned() then raise exception 'Your account cannot send messages.'; end if;
  select * into l from public.listings where id = p_listing and status = 'open' and not hidden;
  if l.id is null then raise exception 'This listing is closed.'; end if;
  if l.user_id = auth.uid() then raise exception 'This is your own listing.'; end if;
  if public.is_blocked(auth.uid(), l.user_id) then raise exception 'You cannot message this player.'; end if;
  select id into cid from public.conversations where listing_id = l.id and user_a = auth.uid();
  if cid is null then
    insert into public.conversations (listing_id, item_slug, item_name, user_a, user_b)
    values (l.id, l.item_slug, l.item_name, auth.uid(), l.user_id) returning id into cid;
  end if;
  return cid;
end $$;
grant execute on function public.start_conversation(bigint) to authenticated;

create table if not exists public.messages (
  id bigint generated always as identity primary key,
  conversation_id bigint not null references public.conversations(id) on delete cascade,
  sender_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  body text not null check (char_length(body) between 1 and 500),
  created_at timestamptz not null default now(),
  read_at timestamptz
);
create index if not exists messages_conv on public.messages (conversation_id, created_at);
alter table public.messages enable row level security;
drop policy if exists "participants read messages" on public.messages;
create policy "participants read messages" on public.messages for select using (
  exists (select 1 from public.conversations c where c.id = conversation_id and auth.uid() in (c.user_a, c.user_b)));
drop policy if exists "participants send messages" on public.messages;
create policy "participants send messages" on public.messages for insert to authenticated with check (
  sender_id = auth.uid() and public.not_banned() and public.clean_text(body)
  and exists (select 1 from public.conversations c where c.id = conversation_id and auth.uid() in (c.user_a, c.user_b)
              and not public.is_blocked(c.user_a, c.user_b)));
revoke update on public.messages from anon, authenticated;

create or replace function public.message_sent() returns trigger
language plpgsql security definer set search_path = public as $$
declare c public.conversations; other uuid; sender text;
begin
  if (select count(*) from public.messages where sender_id = new.sender_id and created_at > now() - interval '1 minute') > 12 then
    raise exception 'You are sending messages too fast.';
  end if;
  select * into c from public.conversations where id = new.conversation_id;
  update public.conversations set last_at = now() where id = c.id;
  other := case when c.user_a = new.sender_id then c.user_b else c.user_a end;
  select display_name into sender from public.profiles where id = new.sender_id;
  insert into public.notifications (user_id, kind, title, body, url)
  values (other, 'message', coalesce(sender, 'A player') || ' · ' || coalesce(c.item_name, 'trade'), left(new.body, 140), '?tab=saved&chat=' || c.id);
  return new;
end $$;
drop trigger if exists messages_sent on public.messages;
create trigger messages_sent after insert on public.messages for each row execute function public.message_sent();

create or replace function public.mark_read(p_conversation bigint) returns void
language sql security definer set search_path = public as $$
  update public.messages m set read_at = now()
  from public.conversations c
  where m.conversation_id = p_conversation and c.id = m.conversation_id and auth.uid() in (c.user_a, c.user_b)
    and m.sender_id <> auth.uid() and m.read_at is null;
$$;
grant execute on function public.mark_read(bigint) to authenticated;

-- ------------------------------------------------------------------ ratings and reports
create table if not exists public.ratings (
  rater uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  ratee uuid not null references public.profiles(id) on delete cascade,
  conversation_id bigint not null references public.conversations(id) on delete cascade,
  stars smallint not null check (stars between 1 and 5),
  created_at timestamptz not null default now(),
  primary key (rater, conversation_id),
  check (rater <> ratee)
);
alter table public.ratings enable row level security;
drop policy if exists "ratings are public" on public.ratings;
create policy "ratings are public" on public.ratings for select using (true);
drop policy if exists "rate a trade partner" on public.ratings;
create policy "rate a trade partner" on public.ratings for insert to authenticated with check (
  rater = auth.uid() and exists (
    select 1 from public.conversations c where c.id = conversation_id
      and auth.uid() in (c.user_a, c.user_b) and ratee in (c.user_a, c.user_b)
      and exists (select 1 from public.messages m where m.conversation_id = c.id and m.sender_id = c.user_a)
      and exists (select 1 from public.messages m where m.conversation_id = c.id and m.sender_id = c.user_b)));

create or replace view public.profile_stats as
  select p.id, p.display_name, p.ign, p.avatar_url, p.created_at,
         coalesce(round(avg(r.stars)::numeric, 1), 0) as rating, count(r.stars) as ratings
  from public.profiles p left join public.ratings r on r.ratee = p.id
  where not p.is_banned
  group by p.id;
grant select on public.profile_stats to anon, authenticated;

create table if not exists public.reports (
  id bigint generated always as identity primary key,
  reporter uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  target_user uuid references public.profiles(id) on delete cascade,
  listing_id bigint references public.listings(id) on delete set null,
  message_id bigint references public.messages(id) on delete set null,
  reason text check (reason is null or char_length(reason) <= 300),
  created_at timestamptz not null default now()
);
alter table public.reports enable row level security;
drop policy if exists "file a report" on public.reports;
create policy "file a report" on public.reports for insert to authenticated with check (reporter = auth.uid());

-- ------------------------------------------------------------------ community price reports
create table if not exists public.price_reports (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references public.profiles(id) on delete cascade,
  item_slug text not null check (item_slug ~ '^[a-z0-9-]{1,80}$'),
  price bigint not null check (price > 0 and price < 100000000000),
  created_at timestamptz not null default now()
);
create unique index if not exists price_reports_hourly on public.price_reports (user_id, item_slug, date_trunc('hour', created_at at time zone 'utc'));
create index if not exists price_reports_item on public.price_reports (item_slug, created_at desc);
alter table public.price_reports enable row level security;
drop policy if exists "report prices" on public.price_reports;
create policy "report prices" on public.price_reports for insert to authenticated with check (user_id = auth.uid() and public.not_banned());
drop policy if exists "read own reports" on public.price_reports;
create policy "read own reports" on public.price_reports for select using (auth.uid() = user_id);

-- Median of the last three days, shown only when at least three different players reported.
create or replace view public.community_prices as
  select item_slug,
         percentile_cont(0.5) within group (order by price) as median,
         count(*) as reports, count(distinct user_id) as reporters, max(created_at) as last_at
  from public.price_reports
  where created_at > now() - interval '3 days'
  group by item_slug
  having count(distinct user_id) >= 3;
grant select on public.community_prices to anon, authenticated;

-- ------------------------------------------------------------------ live updates for chat and notifications
do $$ begin
  begin alter publication supabase_realtime add table public.messages; exception when others then null; end;
  begin alter publication supabase_realtime add table public.notifications; exception when others then null; end;
end $$;
