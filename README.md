# Circle workshop referrals

Run `python3 server.py` and open http://127.0.0.1:4174. Members create a username and password at `/signup`. The local console shows the generated admin password for `/admin/login`.

For HTTPS hosting, keep `/data` on a persistent disk and back it up. Set `CIRCLE_ORIGIN` to the public HTTPS URL in the Compose environment and use the existing private `SERVICE_PASSWORD_64_ADMIN` secret. Run one application instance with SQLite.

Each member has a general referral link and can create a separate link for each recipient, optionally limited to one workshop. Circle records link opens, copy and WhatsApp actions, and workshop registrations. Recipient labels are supplied by members. WhatsApp does not report who received or read a message. Points become available after admin verification. Each mobile number can register once per workshop.

The admin panel reports members, links, registrations, and reward requests. The database migrates at startup. Historical registrations and reward requests remain visible to admins with a legacy label where appropriate. Existing YOGESH24 links still accept registrations and are reported as legacy referrals.
