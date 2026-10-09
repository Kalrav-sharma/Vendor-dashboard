-- Last Mile: Supabase pg_cron starts the sync watchdog every 20 min, instead of relying on
-- GitHub's own cron (which silently drops most ticks for this repo). Calls the
-- dispatch-last-mile-watchdog Edge Function, which starts sync-last-mile-watchdog.yml using the
-- GITHUB_DISPATCH_TOKEN secret Supabase already holds -- no token is pasted here. The watchdog
-- refreshes whichever Last Mile leg is stale (tracking > 90 min, intake > 26 h) and does nothing
-- otherwise.
--
-- Run once in the Supabase SQL Editor, AFTER deploying dispatch-last-mile-watchdog with
-- "Verify JWT" turned off. Safe to re-run: it replaces the job if it already exists.
-- Supersedes supabase/2026-10-06-last-mile-cron.sql (the same idea, but needing a pasted token).

create extension if not exists pg_cron;
create extension if not exists pg_net;

select cron.unschedule(jobid) from cron.job where jobname = 'last-mile-watchdog-dispatch';

-- 04-18 UTC = 09:30-00:30 IST: from just before the daily intake's 09:43 IST slot through the
-- end of the hourly tracking window. pg_cron on Supabase runs in UTC.
select cron.schedule(
  'last-mile-watchdog-dispatch',
  '*/20 4-18 * * *',
  $$
  select net.http_post(
    url := 'https://jfxfzulufaxrmopnvpqa.supabase.co/functions/v1/dispatch-last-mile-watchdog',
    headers := jsonb_build_object('Content-Type', 'application/json'),
    body := '{}'::jsonb
  );
  $$
);

-- Check it's scheduled:   select jobname, schedule, active from cron.job;
-- See its last calls:     select status, return_message, start_time from cron.job_run_details
--                           where jobid = (select jobid from cron.job where jobname = 'last-mile-watchdog-dispatch')
--                           order by start_time desc limit 5;
