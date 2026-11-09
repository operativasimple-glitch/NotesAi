-- Radar Ballena: servidor de avisos 24/7.
-- Todo lleva el prefijo rb_ para no mezclarse con el resto del proyecto.
-- Solo la función rb (con la clave de servicio) puede leer o escribir estas tablas.

create extension if not exists pg_cron;
create extension if not exists pg_net;

create table if not exists public.rb_config (
  key text primary key,
  value text not null
);

create table if not exists public.rb_devices (
  id uuid primary key default gen_random_uuid(),
  endpoint text not null unique,
  p256dh text not null,
  auth text not null,
  ua text,
  fails int not null default 0,
  created_at timestamptz not null default now(),
  last_seen timestamptz not null default now()
);

create table if not exists public.rb_follows (
  device_id uuid not null references public.rb_devices(id) on delete cascade,
  address text not null,
  chain text not null default 'hl' check (chain in ('hl', 'eth')),
  label text,
  created_at timestamptz not null default now(),
  primary key (device_id, address, chain)
);
create index if not exists rb_follows_address_idx on public.rb_follows (address, chain);

-- Última foto conocida de cada cartera (posiciones en Hyperliquid, último bloque en Ethereum)
-- y caché de datos de tokens (chain = 'token').
create table if not exists public.rb_state (
  address text not null,
  chain text not null,
  data jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (address, chain)
);

create table if not exists public.rb_events (
  id bigint generated always as identity primary key,
  address text not null,
  chain text not null,
  title text not null,
  body text not null,
  kind text,
  url text,
  created_at timestamptz not null default now()
);
create index if not exists rb_events_address_idx on public.rb_events (address, chain, created_at desc);

alter table public.rb_config enable row level security;
alter table public.rb_devices enable row level security;
alter table public.rb_follows enable row level security;
alter table public.rb_state enable row level security;
alter table public.rb_events enable row level security;
revoke all on public.rb_config, public.rb_devices, public.rb_follows, public.rb_state, public.rb_events from anon, authenticated;

-- Secreto que usa la tarea programada para llamar a la función. Se genera aquí y nunca sale de la base de datos.
insert into public.rb_config (key, value)
values ('cron_secret', encode(extensions.gen_random_bytes(24), 'hex'))
on conflict (key) do nothing;

-- Cada minuto: revisar las carteras seguidas y mandar los avisos.
select cron.schedule(
  'rb-check',
  '* * * * *',
  $$
  select net.http_post(
    url := 'https://tdgorosrvhilownrkvls.supabase.co/functions/v1/rb',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'x-rb-cron', (select value from public.rb_config where key = 'cron_secret')
    ),
    body := '{"action":"check"}'::jsonb,
    timeout_milliseconds := 55000
  );
  $$
);

-- La limpieza de avisos antiguos (más de 14 días) la hace la propia función rb una vez al día.

-- Evita que dos revisiones se pisen si una tarda más de un minuto.
create or replace function public.rb_try_lock(secs int) returns boolean
language plpgsql security definer set search_path = public as $$
declare ok boolean;
begin
  insert into rb_config (key, value) values ('lock', '0') on conflict (key) do nothing;
  update rb_config
     set value = extract(epoch from now() + make_interval(secs => secs))::text
   where key = 'lock' and value::double precision < extract(epoch from now())
  returning true into ok;
  return coalesce(ok, false);
end $$;
revoke execute on function public.rb_try_lock(int) from public, anon, authenticated;
grant execute on function public.rb_try_lock(int) to service_role;
