-- MapleCharts "Support us" wishes: visitors can send what they want more of. Only the site owner reads them
-- (Supabase dashboard -> Table editor -> feedback). Visitors can add a row but never read any.

create table if not exists public.feedback (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  user_id uuid default auth.uid() references auth.users (id) on delete set null,
  wants text[] not null default '{}',
  message text not null default '',
  contact text not null default '',
  lang text not null default '',
  constraint feedback_wants check (coalesce(array_length(wants, 1), 0) <= 12),
  constraint feedback_message check (char_length(message) <= 1000),
  constraint feedback_contact check (char_length(contact) <= 120),
  constraint feedback_lang check (char_length(lang) <= 8),
  constraint feedback_not_empty check (coalesce(array_length(wants, 1), 0) > 0 or char_length(btrim(message)) > 0)
);

alter table public.feedback enable row level security;
drop policy if exists feedback_insert on public.feedback;
create policy feedback_insert on public.feedback for insert to anon, authenticated
  with check (user_id is null or user_id = auth.uid());
revoke all on public.feedback from anon, authenticated;
grant insert (wants, message, contact, lang) on public.feedback to anon, authenticated;

-- Flood guard: at most 40 messages per 10 minutes across the whole site, and 5 per signed-in user.
create or replace function public.feedback_guard() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if (select count(*) from public.feedback where created_at > now() - interval '10 minutes') >= 40 then
    raise exception 'slow_down' using errcode = 'P0001';
  end if;
  if new.user_id is not null and
     (select count(*) from public.feedback where user_id = new.user_id and created_at > now() - interval '10 minutes') >= 5 then
    raise exception 'slow_down' using errcode = 'P0001';
  end if;
  new.wants := (select coalesce(array_agg(left(w, 40)), '{}') from unnest(new.wants) as w);
  return new;
end $$;
drop trigger if exists feedback_guard on public.feedback;
create trigger feedback_guard before insert on public.feedback for each row execute function public.feedback_guard();
