# Circle workshop referrals

Run `python3 server.py` and open http://127.0.0.1:4174. The console shows the generated local admin password. Use username `admin` at `/admin`.

For HTTPS hosting, build the Dockerfile, attach a persistent disk at `/data`, set `CIRCLE_PUBLIC_URL` to the actual HTTPS origin and set a strong `CIRCLE_ADMIN_PASSWORD` secret. Keep the disk private and back it up. Use one running instance with SQLite.

This MVP supports one referral account. Public activity shows anonymous workshop and credit records. Admin verification grants Blessing Points once per unique mobile number and workshop. Multi-account login, WhatsApp automation and reward fulfillment are future work.
