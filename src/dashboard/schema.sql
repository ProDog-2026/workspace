-- Supabase 테이블 초안: 메일 1건 = 검사 결과 1행. 대시보드는 이 테이블만 읽는다.
-- 쓰기는 백엔드(추출기·탐지 파이프라인)가 service_role 키로 한다.
create table scans (
  id               bigint generated always as identity primary key,
  user_id          uuid not null references auth.users default auth.uid(),
  gmail_message_id text not null,
  sender           text not null,
  subject          text,
  received_at      timestamptz not null,
  scanned_at       timestamptz not null default now(),
  -- 수집·분석이 끝까지 됐는지. 판정(verdict)과 따로 둠: 일부만 검사해도 위험 근거가 있으면 위험으로 표시
  status           text not null default 'ok' check (status in ('ok', 'incomplete', 'failed')),
  status_detail    text,                           -- 실패 원인 (예: LLM 분석 시간 초과)
  missing_parts    text[] not null default '{}',   -- 수집 못 한 부분 (예: 첨부파일 2개 · 25MB 초과)
  static_score     int check (static_score between 0 and 100),  -- 최종 정적 모델 출력의 표시용 백분율, 실패 시 null
  llm_score        int check (llm_score between 0 and 100),     -- LLM 동적 분석 (박한빈), 실패 시 null
  final_score      int check (final_score between 0 and 100),   -- 판단 로직 (이지훈), 판정 못 하면 null
  verdict          text check (verdict in ('danger', 'suspicious', 'safe')), -- null = 판정 못 함
  types            text[] not null default '{}',   -- 집계용: hidden_color, html_comment, ... (src/rules.js TYPES)
  evidence         jsonb not null default '[]',    -- 근거 목록: [{ rule, type, snippet }] · snippet 은 텍스트로만 표시
  llm_reason       text,
  unique (user_id, gmail_message_id)
);

create index on scans (user_id, received_at desc);

-- 메일 내용이 들어가는 테이블이라 RLS 필수: 본인 행만 조회 가능
alter table scans enable row level security;
create policy "read own scans" on scans for select using (auth.uid() = user_id);
