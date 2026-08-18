# TURN server setup (coturn)

This closes the #1 P0 gap from the risk register: without a TURN relay,
a meaningful share of real calls -- especially from mobile networks and
hospital/corporate wifi -- will simply fail to connect, because the two
devices can't find a direct peer-to-peer path through their NATs.

This guide is for you to run once, on your own infrastructure -- I don't
have an account or server to provision this on. The app-side code
(`app/config.py`, `/calls/ice-servers`) is already built to use whatever
you stand up here; you just need to fill in three environment variables
at the end.

## What you'll end up with

One small VPS, reachable at a domain name you control (e.g.
`turn.yourapp.com`), running coturn, handing out short-lived TURN
credentials to the app's users. Nothing long-lived or secret is ever
shipped in the mobile app itself.

## 1. Provision a server

Any VPS provider works (DigitalOcean, Linode, AWS Lightsail, Hetzner,
etc.) -- pick whichever you already use for the rest of this app's
infrastructure, so it's one less vendor relationship to manage. A small
instance (1 vCPU, 1-2GB RAM) is enough to start; TURN relay is bandwidth-
bound, not CPU-bound, so scale up by bandwidth/concurrent-call needs
later, not preemptively. Check current pricing with your chosen provider
directly, since it changes over time.

Use Ubuntu 22.04 LTS (matches what this guide's commands assume).

## 2. Point a domain at it

Create an A record: `turn.yourapp.com -> <server's public IP>`. This is
needed for the TLS certificate in step 4 -- TURN can technically run on a
bare IP, but you should have TLS in production, and Let's Encrypt needs a
domain name.

## 3. Install coturn

```bash
ssh you@turn.yourapp.com
sudo apt update
sudo apt install -y coturn
```

Enable it as a real service (the Debian/Ubuntu package ships disabled by
default):

```bash
sudo sed -i 's/#TURNSERVER_ENABLED=1/TURNSERVER_ENABLED=1/' /etc/default/coturn
```

## 4. Configure it

Copy this repo's `infra/coturn/turnserver.conf` to the server:

```bash
scp infra/coturn/turnserver.conf you@turn.yourapp.com:/tmp/turnserver.conf
ssh you@turn.yourapp.com
sudo mv /tmp/turnserver.conf /etc/turnserver.conf
```

Edit `/etc/turnserver.conf` and fill in:

- `realm` / `server-name` -- your actual domain (`turn.yourapp.com`)
- `static-auth-secret` -- generate a strong random value, e.g. `openssl rand -hex 32`. **Save this** -- it goes into the backend's `.env` as `TURN_SHARED_SECRET` in step 6, and both sides must match exactly.

## 5. TLS certificate (recommended, not optional for production)

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone -d turn.yourapp.com
```

Then uncomment the `cert=` / `pkey=` lines in `/etc/turnserver.conf` and
point them at the paths certbot printed (typically
`/etc/letsencrypt/live/turn.yourapp.com/fullchain.pem` and `privkey.pem`).
Set up certbot's renewal timer (installed by default on Ubuntu) so this
doesn't silently expire.

## 6. Firewall / security group

Open these on the server's firewall (`ufw`) and your cloud provider's
security group, if it has a separate one:

| Port | Protocol | Purpose |
|---|---|---|
| 3478 | UDP + TCP | STUN/TURN |
| 5349 | TCP | TURN over TLS |
| 49160-49200 | UDP | Relay media (matches `min-port`/`max-port` in the conf) |

```bash
sudo ufw allow 3478
sudo ufw allow 5349/tcp
sudo ufw allow 49160:49200/udp
```

## 7. Start it

```bash
sudo mkdir -p /var/log/turnserver
sudo systemctl enable coturn
sudo systemctl restart coturn
sudo systemctl status coturn   # should show "active (running)"
```

## 8. Test it actually works before wiring up the app

coturn ships a client test tool:

```bash
turnutils_uclient -T -u test -w test turn.yourapp.com
```

(This will fail auth since we're using time-limited secret-based
credentials, not a static test user -- that's expected. What you're
checking here is that it *connects* and coturn responds, not a full
successful relay.) For a real end-to-end check including a genuine relay
candidate, use a WebRTC ICE test page such as Trickle ICE
(https://webrtc.github.io/samples/src/content/peerconnections/trickle-ice/)
with a manually generated short-lived credential -- or once the backend
env vars below are set, just place a real call from two devices on
different, NAT'd networks and confirm it connects; that's the test that
actually matters.

## 9. Connect it to the backend

In the backend's `.env`:

```
TURN_URLS=turn:turn.yourapp.com:3478?transport=udp,turn:turn.yourapp.com:3478?transport=tcp,turns:turn.yourapp.com:5349?transport=tcp
TURN_SHARED_SECRET=<the exact value you put in static-auth-secret>
TURN_CREDENTIAL_TTL_SECONDS=3600
```

Restart the backend. `/calls/ice-servers` will now include a TURN entry
with a fresh, short-lived credential on every call, automatically -- no
further app changes needed.

## Ongoing operational notes

Monitor bandwidth on this box -- TURN relay carries full call media for
any call that needs it, so it's the one piece of this feature's
infrastructure that scales directly with usage (see the risk register's
Scalability section). Rotate `static-auth-secret` periodically like any
other credential, updating both sides together. If you outgrow one
server, coturn supports running multiple TURN servers behind the same
`TURN_URLS` list, or you can move to a managed TURN provider later
without changing any client code -- the app only cares that
`/calls/ice-servers` returns valid entries, not where they came from.
