-- Run only in a disposable PostgreSQL after the fresh schema and synthetic auth bootstrap.
\set ON_ERROR_STOP on
INSERT INTO auth.users VALUES ('00000000-0000-0000-0000-000000000001'),
                              ('00000000-0000-0000-0000-000000000002');
INSERT INTO public.gmail_connections(id,user_id,email,status) VALUES
 ('10000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001','a@example.invalid','active'),
 ('10000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000002','b@example.invalid','active');
INSERT INTO public.mail_messages(id,user_id,connection_id,gmail_id,fetch_status) VALUES
 ('20000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','same-mail','failed'),
 ('20000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000002','10000000-0000-0000-0000-000000000002','same-mail','failed');
INSERT INTO public.analysis_results(user_id,mail_id,status) VALUES
 ('00000000-0000-0000-0000-000000000001','20000000-0000-0000-0000-000000000001','failed'),
 ('00000000-0000-0000-0000-000000000002','20000000-0000-0000-0000-000000000002','failed');
DO $$ BEGIN
  BEGIN
    INSERT INTO public.mail_messages(user_id,connection_id,gmail_id,fetch_status)
    VALUES ('00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000002','forbidden','failed');
    RAISE EXCEPTION 'cross-owner foreign key accepted';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
END $$;
SET ROLE authenticated;
SELECT set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000001',false);
DO $$ BEGIN
  IF (SELECT count(*) FROM public.mail_messages) != 1 THEN
    RAISE EXCEPTION 'RLS did not isolate mail'; END IF;
  IF (SELECT count(*) FROM public.analysis_results) != 1 THEN
    RAISE EXCEPTION 'RLS did not isolate analysis'; END IF;
  BEGIN
    DELETE FROM public.gmail_connections;
    RAISE EXCEPTION 'browser delete permitted';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
RESET ROLE;
DELETE FROM public.gmail_connections WHERE user_id='00000000-0000-0000-0000-000000000001';
DO $$ BEGIN
  IF (SELECT count(*) FROM public.mail_messages) != 1
     OR (SELECT count(*) FROM public.analysis_results) != 1 THEN
    RAISE EXCEPTION 'cascade deleted wrong number of rows'; END IF;
  IF (SELECT user_id FROM public.mail_messages) != '00000000-0000-0000-0000-000000000002'::uuid THEN
    RAISE EXCEPTION 'other user was deleted'; END IF;
END $$;
SELECT 'RLS, composite ownership and cascade passed' AS result;
