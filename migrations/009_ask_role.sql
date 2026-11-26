-- gcw_ask, the Ask page's role (D127). Only its existence is set up here, with no login, password
-- or grant, so that dbt can grant on the relations it rebuilds in any warehouse. `gcw
-- dashboard-services` gives it the rest. Roles belong to the whole cluster, so it may exist already:
-- another database's migration or an earlier dashboard-services made it.
DO $$
BEGIN
    CREATE ROLE gcw_ask NOLOGIN;
EXCEPTION
    WHEN duplicate_object OR unique_violation THEN NULL;
END
$$;
