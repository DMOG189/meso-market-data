-- MapleCharts timer: starts the GitHub jobs every 15 minutes from Supabase, because GitHub's own
-- timer has not been starting the scheduled workflows for this repository.
-- Needs a Vault secret named 'github_dispatch': a fine-grained GitHub token for DMOG189/meso-market-data
-- with "Actions: Read and write" only. Without it the jobs do nothing.

create extension if not exists pg_cron;
create extension if not exists pg_net;

create or replace function public.mc_dispatch(workflow text) returns bigint
language sql security definer set search_path = public, extensions as $$
  select net.http_post(
    url := 'https://api.github.com/repos/DMOG189/meso-market-data/actions/workflows/' || workflow || '/dispatches',
    body := '{"ref":"main"}'::jsonb,
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || s.decrypted_secret,
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'maplecharts-timer',
      'Content-Type', 'application/json'),
    timeout_milliseconds := 10000)
  from vault.decrypted_secrets s
  where s.name = 'github_dispatch'
  limit 1;
$$;
revoke all on function public.mc_dispatch(text) from public, anon, authenticated;

select cron.unschedule(jobname) from cron.job where jobname in ('mc-copy-prices', 'mc-notify');
select cron.schedule('mc-copy-prices', '5,20,35,50 * * * *', $$select public.mc_dispatch('copy-prices.yml')$$);
select cron.schedule('mc-notify', '9,24,39,54 * * * *', $$select public.mc_dispatch('notify.yml')$$);

select jobid, jobname, schedule, active from cron.job where jobname like 'mc-%' order by jobname;
