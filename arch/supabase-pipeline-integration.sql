-- Additive integration for owner-scoped pipeline results. Existing RLS/grants stay unchanged.
alter table public.analysis_results
  add column if not exists analysis jsonb
  check (analysis is null or (jsonb_typeof(analysis) = 'object' and octet_length(analysis::text) <= 32768));

-- PostgREST on_conflict accepts column names, not the existing lower(email) expression.
create unique index if not exists gmail_connections_user_email_exact_key
  on public.gmail_connections(user_id,email);

create or replace view public.scans with (security_invoker=true) as
select coalesce(a.id,m.id) as id,
       m.user_id,m.gmail_id as gmail_message_id,m.sender,m.subject,m.received_at,
       coalesce(a.created_at,m.collected_at) as scanned_at,
       case when m.fetch_status='failed' or a.status='failed' then 'failed'
            when m.fetch_status='incomplete' then 'incomplete' else 'ok' end as status,
       case when a.status='failed' then a.status_detail
            when m.fetch_status='failed' then '메일을 수집하지 못했습니다.'
            else null end as status_detail,
       case when m.fetch_status='incomplete' and coalesce(cardinality(a.missing_parts),0)=0
            then array['메일 일부를 분석하지 못했습니다.']
            else coalesce(a.missing_parts,'{}'::text[]) end as missing_parts,
       a.static_score,a.llm_score,a.final_score,a.verdict,
       coalesce(a.types,'{}'::text[]) as types,
       coalesce(a.evidence,'[]'::jsonb) as evidence,a.llm_reason,a.analysis
from public.mail_messages m
left join lateral (
  select ar.* from public.analysis_results ar
  where ar.mail_id=m.id and ar.user_id=m.user_id
  order by (ar.analysis is not null) desc,
           (ar.status in ('succeeded','failed')) desc,ar.created_at desc,ar.id desc
  limit 1
) a on true;
