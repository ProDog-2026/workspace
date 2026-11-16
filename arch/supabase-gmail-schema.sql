-- Fresh installation only. Existing deployments must audit/migrate data first.
-- Never assign historical shared-user rows to whichever user logs in next.
BEGIN;
CREATE TABLE public.gmail_connections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    email text NOT NULL,
    status text NOT NULL CHECK (status IN ('active', 'reconnect_required')),
    connected_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(user_id, email), UNIQUE(id, user_id)
);
CREATE TABLE public.mail_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    connection_id uuid NOT NULL,
    gmail_id text NOT NULL, subject text, sender text, received_at timestamptz,
    body_hash text, attachments jsonb NOT NULL DEFAULT '[]',
    warnings text[] NOT NULL DEFAULT '{}', truncated boolean NOT NULL DEFAULT false,
    fetch_status text NOT NULL CHECK (fetch_status IN ('failed','incomplete','complete')),
    FOREIGN KEY(connection_id, user_id)
      REFERENCES public.gmail_connections(id, user_id) ON DELETE CASCADE,
    UNIQUE(connection_id, gmail_id), UNIQUE(id, user_id)
);
CREATE TABLE public.analysis_results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    mail_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','succeeded','failed')),
    verdict text CHECK (verdict IN ('safe','suspicious','danger')),
    static_score double precision, llm_score double precision, final_score double precision,
    types text[] NOT NULL DEFAULT '{}', evidence jsonb NOT NULL DEFAULT '[]',
    llm_reason text, missing_parts text[] NOT NULL DEFAULT '{}', status_detail text,
    model text, created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(mail_id, user_id)
      REFERENCES public.mail_messages(id, user_id) ON DELETE CASCADE
);
ALTER TABLE public.gmail_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mail_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analysis_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY own_connections ON public.gmail_connections FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = user_id);
CREATE POLICY own_messages ON public.mail_messages FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = user_id);
CREATE POLICY own_results ON public.analysis_results FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = user_id);
REVOKE ALL ON public.gmail_connections, public.mail_messages, public.analysis_results
    FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.gmail_connections, public.mail_messages, public.analysis_results
    TO authenticated;
GRANT ALL ON public.gmail_connections, public.mail_messages, public.analysis_results
    TO service_role;
COMMIT;
