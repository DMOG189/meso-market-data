-- MapleCharts: live player prices, privacy-friendly visit counts and the owner dashboard.
-- Run once in Supabase -> SQL editor (after schema.sql and feedback.sql). Safe to run again.

-- ------------------------------------------------------------------ 1) live player prices
-- Anonymous feed of the latest prices players saw in-game: item, price and time only (no names, no ids).
create or replace view public.recent_price_reports as
  select item_slug, price, created_at
  from public.price_reports
  where created_at > now() - interval '3 days'
  order by created_at desc
  limit 100;
grant select on public.recent_price_reports to anon, authenticated;

-- Community price: median of the last three days, shown once at least two different players reported.
create or replace view public.community_prices as
  select item_slug,
         percentile_cont(0.5) within group (order by price) as median,
         count(*) as reports, count(distinct user_id) as reporters, max(created_at) as last_at
  from public.price_reports
  where created_at > now() - interval '3 days'
  group by item_slug
  having count(distinct user_id) >= 2;
grant select on public.community_prices to anon, authenticated;

-- ------------------------------------------------------------------ 2) visit counts (no cookies, no IPs, no ids)
-- Only daily totals are kept: how many page views and visits, per page, language, referring site and device type.
create table if not exists public.visit_counts (
  day date not null,
  kind text not null check (kind in ('total', 'page', 'lang', 'ref', 'dev', 'event')),
  key text not null check (char_length(key) <= 90),
  views bigint not null default 0,
  visits bigint not null default 0,
  primary key (day, kind, key)
);
alter table public.visit_counts enable row level security;
revoke all on public.visit_counts from anon, authenticated;

create or replace function public.mc_hit(p_path text, p_lang text default '', p_ref text default '', p_dev text default '', p_new boolean default false)
returns void language plpgsql security definer set search_path = public as $$
declare
  d date := (now() at time zone 'utc')::date;
  v int := case when p_new then 1 else 0 end;
  pth text := lower(coalesce(p_path, ''));
  lng text := lower(left(coalesce(p_lang, ''), 2));
  rf text := lower(coalesce(p_ref, ''));
  dv text := lower(coalesce(p_dev, ''));
begin
  if pth !~ '^/[a-z0-9/_-]{0,88}$' then pth := '/other'; end if;
  if (select count(*) from public.visit_counts where day = d and kind = 'page') > 3000 then pth := '/other'; end if;
  if lng !~ '^[a-z]{2}$' then lng := '??'; end if;
  if rf = '' then rf := 'direct'; elsif rf !~ '^[a-z0-9.-]{1,60}$' then rf := 'other'; end if;
  if (select count(*) from public.visit_counts where day = d and kind = 'ref') > 500 then rf := 'other'; end if;
  if dv not in ('phone', 'tablet', 'desktop', 'app') then dv := 'other'; end if;
  insert into public.visit_counts as c (day, kind, key, views, visits) values
    (d, 'total', 'all', 1, v), (d, 'page', pth, 1, v), (d, 'lang', lng, 1, v), (d, 'dev', dv, 1, v)
  on conflict (day, kind, key) do update set views = c.views + excluded.views, visits = c.visits + excluded.visits;
  if p_new then
    insert into public.visit_counts as c (day, kind, key, views, visits) values (d, 'ref', rf, 1, 1)
    on conflict (day, kind, key) do update set views = c.views + 1, visits = c.visits + 1;
  end if;
end $$;

-- Feature use (which tabs and tools people open), counted the same way.
create or replace function public.mc_event(p_key text)
returns void language plpgsql security definer set search_path = public as $$
declare
  d date := (now() at time zone 'utc')::date;
  k text := lower(coalesce(p_key, ''));
begin
  if k !~ '^[a-z0-9:_-]{1,40}$' then return; end if;
  if (select count(*) from public.visit_counts where day = d and kind = 'event') > 300 then return; end if;
  insert into public.visit_counts as c (day, kind, key, views, visits) values (d, 'event', k, 1, 0)
  on conflict (day, kind, key) do update set views = c.views + 1;
end $$;

revoke all on function public.mc_hit(text, text, text, text, boolean) from public;
revoke all on function public.mc_event(text) from public;
grant execute on function public.mc_hit(text, text, text, text, boolean) to anon, authenticated;
grant execute on function public.mc_event(text) to anon, authenticated;

-- ------------------------------------------------------------------ 3) owner dashboard
create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profiles where id = auth.uid() and is_admin)
$$;
revoke all on function public.is_admin() from public;
grant execute on function public.is_admin() to authenticated;

-- Everything the owner page shows, in one call. Only for accounts marked is_admin in profiles.
create or replace function public.owner_stats(p_days int default 30) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  since date;
  r jsonb;
begin
  if not public.is_admin() then raise exception 'not_admin' using errcode = '42501'; end if;
  p_days := greatest(1, least(coalesce(p_days, 30), 365));
  since := (now() at time zone 'utc')::date - (p_days - 1);
  select jsonb_build_object(
    'days', p_days,
    'daily', coalesce((select jsonb_agg(jsonb_build_object('d', day, 'v', views, 'u', visits) order by day)
                       from public.visit_counts where kind = 'total' and day >= since), '[]'::jsonb),
    'top', coalesce((select jsonb_object_agg(kind, items) from (
              select kind, jsonb_agg(jsonb_build_array(key, views, visits) order by views desc) as items
              from (select kind, key, sum(views) as views, sum(visits) as visits,
                           row_number() over (partition by kind order by sum(views) desc) as rn
                    from public.visit_counts where day >= since and kind <> 'total' group by kind, key) t
              where rn <= 15 group by kind) k), '{}'::jsonb),
    'users', (select count(*) from public.profiles),
    'newUsers', (select count(*) from public.profiles where created_at >= since),
    'reports', (select count(*) from public.price_reports where created_at >= since),
    'reporters', (select count(distinct user_id) from public.price_reports where created_at >= since),
    'listings', (select count(*) from public.listings where status = 'open' and not hidden and expires_at > now()),
    'alerts', (select count(*) from public.alerts where active),
    'push', (select count(*) from public.push_subscriptions),
    'messages', (select count(*) from public.messages where created_at >= since),
    'flags', (select count(*) from public.reports where created_at >= since),
    'wantCounts', coalesce((select jsonb_object_agg(w, n) from (
              select w, count(*) as n from public.feedback, unnest(wants) as w where created_at >= since group by w) x), '{}'::jsonb),
    'wishes', coalesce((select jsonb_agg(jsonb_build_object('at', created_at, 'wants', wants, 'msg', message, 'contact', contact, 'lang', lang) order by created_at desc)
              from (select * from public.feedback order by created_at desc limit 40) f), '[]'::jsonb)
  ) into r;
  return r;
end $$;
revoke all on function public.owner_stats(int) from public;
grant execute on function public.owner_stats(int) to authenticated;

-- Make an account the owner (run once with your own account's id from Authentication -> Users):
--   update public.profiles set is_admin = true where id = '<your user id>';
