-- Dev seed (idempotent). One org, three users, their groups. No documents: ingestion seeds those.
--
-- Roles are NOT a column. A role is a group whose external_id is 'role:<role>' (role:viewer,
-- role:analyst, role:admin). authz/group_cache.py splits a user's group list into
-- Principal.groups (plain names) and Principal.roles (the role:* tail, prefix stripped).
-- A document ACL may name a role group too, e.g. "every analyst may read this filing".
--
--   alice  groups finance, analysts  role analyst
--   bob    group  engineering        role viewer
--   carol  (no groups)               role admin
INSERT INTO orgs (id, slug, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'acme', 'Acme Corp')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO principals (org_id, kind, external_id, display_name) VALUES
  ('00000000-0000-0000-0000-000000000001', 'user',  'alice',        'Alice (analyst, finance)'),
  ('00000000-0000-0000-0000-000000000001', 'user',  'bob',          'Bob (viewer, engineering)'),
  ('00000000-0000-0000-0000-000000000001', 'user',  'carol',        'Carol (admin)'),
  ('00000000-0000-0000-0000-000000000001', 'group', 'finance',      'Finance'),
  ('00000000-0000-0000-0000-000000000001', 'group', 'analysts',     'Analysts'),
  ('00000000-0000-0000-0000-000000000001', 'group', 'engineering',  'Engineering'),
  ('00000000-0000-0000-0000-000000000001', 'group', 'role:viewer',  'Role: viewer'),
  ('00000000-0000-0000-0000-000000000001', 'group', 'role:analyst', 'Role: analyst'),
  ('00000000-0000-0000-0000-000000000001', 'group', 'role:admin',   'Role: admin')
ON CONFLICT (org_id, kind, external_id) DO NOTHING;

INSERT INTO group_members (group_id, member_id)
SELECT g.id, u.id
FROM (VALUES
  ('alice', 'finance'), ('alice', 'analysts'), ('alice', 'role:analyst'),
  ('bob',   'engineering'), ('bob', 'role:viewer'),
  ('carol', 'role:admin')
) AS m(user_ext, group_ext)
JOIN principals u ON u.kind = 'user'  AND u.external_id = m.user_ext
                 AND u.org_id = '00000000-0000-0000-0000-000000000001'
JOIN principals g ON g.kind = 'group' AND g.external_id = m.group_ext
                 AND g.org_id = '00000000-0000-0000-0000-000000000001'
ON CONFLICT DO NOTHING;
