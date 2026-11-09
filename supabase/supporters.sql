-- MapleCharts supporters: everything stays free; people who donate get a small thank-you.
--   * a "Supporter" badge next to their name on the trade board and in chats,
--   * 20 price alerts instead of 3 (website and Discord /alert),
--   * the weekly market report "so far this week" in the app before the public report comes out on Monday.
-- Run after schema.sql and discord_trade.sql. Safe to run more than once.
--
-- No payment account is connected. Daniel (an admin) marks a supporter by hand in the owner dashboard after a donation
-- arrives (Ko-fi / PayPal / card link in site.config.json "donate"), or in the SQL editor:
--   select public.set_supporter('<profile id>', 30);   -- 30 days; 0 removes

create table if not exists public.supporters (
  user_id uuid primary key references public.profiles(id) on delete cascade,
  until timestamptz not null,
  note text check (note is null or char_length(note) <= 200),
  created_at timestamptz not null default now()
);
alter table public.supporters enable row level security;
drop policy if exists "read own supporter row" on public.supporters;
create policy "read own supporter row" on public.supporters for select using (auth.uid() = user_id);

create or replace function public.is_supporter(p_user uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.supporters s where s.user_id = p_user and s.until > now())
$$;
grant execute on function public.is_supporter(uuid) to anon, authenticated;

-- Free: 3 alerts. Supporters: 20.
create or replace function public.alert_limit(p_user uuid) returns int
language sql stable security definer set search_path = public as $$
  select case when p_user is not null and public.is_supporter(p_user) then 20 else 3 end
$$;
grant execute on function public.alert_limit(uuid) to authenticated;

create or replace function public.alerts_guard() returns trigger
language plpgsql security definer set search_path = public as $$
declare lim int := public.alert_limit(new.user_id);
begin
  if (select count(*) from public.alerts where user_id = new.user_id) >= lim then
    raise exception 'You can keep up to % alerts.', lim;
  end if;
  return new;
end $$;
drop trigger if exists alerts_guard on public.alerts;
create trigger alerts_guard before insert on public.alerts for each row execute function public.alerts_guard();

-- Profiles as shown to others now carry the badge.
create or replace view public.profile_stats as
  select p.id, p.display_name, p.ign, p.avatar_url, p.created_at,
         coalesce(round(avg(r.stars)::numeric, 1), 0) as rating, count(r.stars) as ratings,
         public.is_supporter(p.id) as supporter
  from public.profiles p left join public.ratings r on r.ratee = p.id
  where not p.is_banned
  group by p.id;
grant select on public.profile_stats to anon, authenticated;

-- My own perks (for the "Me" tab).
create or replace function public.my_perks() returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object(
    'supporter', public.is_supporter(auth.uid()),
    'until', (select until from public.supporters where user_id = auth.uid()),
    'alertLimit', public.alert_limit(auth.uid()))
$$;
grant execute on function public.my_perks() to authenticated;

-- Admin only: mark someone as a supporter for p_days (0 removes). Finds the person by profile id.
create or replace function public.set_supporter(p_user uuid, p_days int, p_note text default null) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  if not exists (select 1 from public.profiles where id = auth.uid() and is_admin) and auth.uid() is not null then
    raise exception 'Admins only.';
  end if;
  if coalesce(p_days, 0) <= 0 then
    delete from public.supporters where user_id = p_user;
  else
    insert into public.supporters (user_id, until, note) values (p_user, now() + make_interval(days => p_days), p_note)
    on conflict (user_id) do update
      set until = greatest(public.supporters.until, now()) + make_interval(days => p_days), note = coalesce(excluded.note, public.supporters.note);
  end if;
  insert into public.notifications (user_id, kind, title, body, url)
  select p_user, 'supporter', 'Thank you for supporting MapleCharts!', 'Your supporter perks are on: badge, 20 alerts and the early weekly report.', '?tab=saved'
  where coalesce(p_days, 0) > 0;
  return jsonb_build_object('user', p_user, 'until', (select until from public.supporters where user_id = p_user));
end $$;
revoke all on function public.set_supporter(uuid, int, text) from public, anon;
grant execute on function public.set_supporter(uuid, int, text) to authenticated;

-- Admin only: find a profile by name to mark as supporter.
create or replace function public.find_profiles(p_q text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if not exists (select 1 from public.profiles where id = auth.uid() and is_admin) then raise exception 'Admins only.'; end if;
  return coalesce((select jsonb_agg(jsonb_build_object('id', id, 'name', display_name, 'ign', ign, 'supporter', public.is_supporter(id)))
                   from (select * from public.profiles
                         where display_name ilike '%' || p_q || '%' or ign ilike '%' || p_q || '%' order by created_at desc limit 10) x), '[]'::jsonb);
end $$;
revoke all on function public.find_profiles(text) from public, anon;
grant execute on function public.find_profiles(text) to authenticated;
